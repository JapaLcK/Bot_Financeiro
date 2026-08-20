"""O aporte precisa entender o nome que o usuário escreveu — e ouvir a resposta.

Três falhas reais, relatadas com print da conversa:

1. "investi 80 em reserva de emergencia" devolvia a lista de investimentos, mesmo com
   o nome escrito na frase. Causa: o Tier 2 do classificador reconhecia
   `investments.deposit` com 0.95 de confiança e devolvia `entities={}` — não havia
   ramo de extração para esse intent. E, pela confiança alta, a mensagem nunca chegava
   na IA (Tier 3) que saberia extrair. Nenhum aporte determinístico completava.

2. "Em qual investimento você quer aportar?" era pergunta sem pendência: o bot
   perguntava e não guardava nada.

3. Como consequência de (2), a resposta solta era reclassificada do zero — e o usuário,
   que tem uma CAIXINHA com o mesmo nome de um INVESTIMENTO, recebia o extrato da
   caixinha em vez do aporte.
"""
import uuid

import pytest

import db
from core.handlers import investments as h_investments
from core.intent_classifier import classify
from utils_text import parse_investment_deposit_natural


CARTEIRA = [
    ("Reserva de Emergencia", 1.0, "cdi"),
    ("Tesouro IPCA+ 2032", 0.0762, "yearly"),
    ("Tesouro Prefixado 2032", 0.1378, "yearly"),
    ("Fabiana", 1.0, "cdi"),
]


@pytest.fixture()
def carteira(user_id):
    """Replica a carteira do relato, incluindo a caixinha de nome repetido."""
    db.add_launch_and_update_balance(user_id, "receita", 5000, None, "seed")
    for nome, taxa, periodo in CARTEIRA:
        db.create_investment_db(user_id, nome, taxa, periodo, "seed", initial_amount=100)
    db.create_pocket(user_id, "Reserva de Emergência")   # mesmo nome, outro tipo
    return user_id


# ─── 1. entender o nome ──────────────────────────────────────────────────────

@pytest.mark.parametrize("frase,valor,nome", [
    ("investi 80 em reserva de emergencia", 80.0, "reserva de emergencia"),
    ("Investi 800 na renda fixa", 800.0, "renda fixa"),
    ("aportei 500 no investimento CDB Nubank", 500.0, "cdb nubank"),
    ("investi 1.200 no tesouro ipca+ 2032", 1200.0, "tesouro ipca+ 2032"),
    ("apliquei 300 na Fabiana", 300.0, "fabiana"),
])
def test_parser_extrai_valor_e_nome(frase, valor, nome):
    assert parse_investment_deposit_natural(frase) == (valor, nome)


@pytest.mark.parametrize("frase", ["investi 80", "oi tudo bem", "quanto gastei hoje"])
def test_parser_nao_inventa(frase):
    assert parse_investment_deposit_natural(frase) == (None, None)


def test_classificador_entrega_as_entidades():
    """Era aqui que quebrava: intent certo, confiança alta, `entities` vazio."""
    r = classify("investi 80 em reserva de emergencia")
    assert r.intent == "investments.deposit"
    assert r.entities == {"amount": 80.0, "investment_name": "reserva de emergencia"}


def test_nome_escrito_na_frase_aporta_direto(carteira):
    """O caso do print: nome correto na mensagem tem que virar aporte, não pergunta."""
    r = classify("investi 80 em reserva de emergencia", user_id=carteira)
    msg = h_investments.deposit(carteira, "investi 80 em reserva de emergencia", r.entities)

    assert "✅" in msg
    assert "Reserva de Emergencia" in msg
    assert "Em qual investimento" not in msg
    invs = {i["name"]: float(i["balance"]) for i in db.list_investments(carteira)}
    assert invs["Reserva de Emergencia"] == 180.0     # 100 do seed + 80


# ─── 2. a pergunta precisa esperar a resposta ────────────────────────────────

def test_nome_desconhecido_pergunta_e_arma_pendencia(carteira):
    msg = h_investments.deposit(
        carteira, "investi 800 na carteira azul",
        {"investment_name": "carteira azul", "amount": 800},
    )

    assert "Não encontrei" in msg
    assert "Em qual investimento" in msg
    pend = db.get_pending_action(carteira)
    assert pend is not None and pend["action_type"] == "investment_pick"
    assert pend["payload"]["amount"] == 800.0


def test_sem_pendencia_a_pergunta_seria_beco_sem_saida(carteira):
    """Guarda explícita do bug 2: perguntar sem armar pendência é o defeito."""
    h_investments.deposit(carteira, "investi 800 na carteira azul",
                          {"investment_name": "carteira azul", "amount": 800})
    assert db.get_pending_action(carteira) is not None


def test_ambiguidade_lista_so_os_candidatos(carteira):
    msg = h_investments.deposit(
        carteira, "investi 300 no tesouro", {"investment_name": "tesouro", "amount": 300},
    )
    assert "Tesouro IPCA+ 2032" in msg and "Tesouro Prefixado 2032" in msg
    assert "Fabiana" not in msg          # só os que casam
    assert db.get_pending_action(carteira)["action_type"] == "investment_pick"


# ─── 3. a resposta não pode virar a caixinha ─────────────────────────────────

def test_resposta_pelo_nome_vai_pro_investimento_nao_pra_caixinha(carteira):
    """O erro do print: o usuário tem CAIXINHA e INVESTIMENTO com o mesmo nome.
    Respondendo à pergunta, tem que valer o investimento."""
    h_investments.deposit(carteira, "investi 800 na carteira azul",
                          {"investment_name": "carteira azul", "amount": 800})

    msg = h_investments.resolve_investment_pick(
        carteira, "Reserva de Emergencia", db.get_pending_action(carteira))

    assert "✅" in msg
    assert "caixinha" not in msg.lower()
    invs = {i["name"]: float(i["balance"]) for i in db.list_investments(carteira)}
    assert invs["Reserva de Emergencia"] == 900.0        # 100 + 800
    pockets = {p["name"]: float(p["balance"]) for p in db.list_pockets(carteira)}
    assert pockets["Reserva de Emergência"] == 0.0       # a caixinha não foi tocada


def test_resposta_pelo_numero(carteira):
    h_investments.deposit(carteira, "investi 800 na carteira azul",
                          {"investment_name": "carteira azul", "amount": 800})
    pend = db.get_pending_action(carteira)
    primeiro = pend["payload"]["nomes"][0]

    msg = h_investments.resolve_investment_pick(carteira, "1", pend)

    assert "✅" in msg and primeiro in msg
    assert db.get_pending_action(carteira) is None


def test_resposta_ilegivel_repergunta_sem_lancar(carteira):
    h_investments.deposit(carteira, "investi 800 na carteira azul",
                          {"investment_name": "carteira azul", "amount": 800})

    msg = h_investments.resolve_investment_pick(
        carteira, "sei lá", db.get_pending_action(carteira))

    assert "Não entendi qual" in msg
    assert db.get_pending_action(carteira) is not None    # pendência preservada
    invs = {i["name"]: float(i["balance"]) for i in db.list_investments(carteira)}
    assert invs["Reserva de Emergencia"] == 100.0         # nada lançado


def test_cancelar_limpa(carteira):
    h_investments.deposit(carteira, "investi 800 na carteira azul",
                          {"investment_name": "carteira azul", "amount": 800})
    msg = h_investments.resolve_investment_pick(
        carteira, "cancelar", db.get_pending_action(carteira))

    assert "cancelei" in msg.lower()
    assert db.get_pending_action(carteira) is None


def test_pendencia_alheia_e_ignorada(carteira):
    assert h_investments.resolve_investment_pick(
        carteira, "1", {"action_type": "delete_launch", "payload": {}}) is None


def test_pendencia_bloqueia_o_fallback_de_ia():
    """Sem isso, a resposta "1" cai como baixa confiança e a IA sequestra o turno
    (core/handle_incoming.py), que foi como a caixinha entrou na conversa."""
    pytest.importorskip("ofxparse", reason="ausente localmente; presente no CI")
    from core.handle_incoming import _RESUMABLE_PENDING_TYPES
    assert "investment_pick" in _RESUMABLE_PENDING_TYPES
