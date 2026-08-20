"""Conversas de aporte, replicadas mensagem a mensagem.

Estes testes existem porque os bugs que apareceram no WhatsApp real não eram de uma
função só — nasciam da COSTURA entre classificar, extrair entidade, armar pendência e
roteá-la de volta. Teste de unidade em cada peça passava; a conversa quebrava.

Camada escolhida: `intent_router.route()`. É onde a costura acontece, e roda tanto aqui
quanto no CI. Testar por `core.handle_incoming` cobriria mais um degrau (o gate do
fallback de IA), mas ele importa `ofxparse`, ausente neste ambiente — seriam testes
escritos às cegas, rodando só no CI.

O que esta camada NÃO cobre, e portanto não deve ser afirmado a partir daqui:
  - o desvio para a IA conversacional em mensagens de baixa confiança;
  - áudio e imagem;
  - a formatação final por plataforma.

Cada cenário abaixo é uma conversa que o usuário teve (ou tentaria ter) no WhatsApp.
"""
import pytest

import db
from core import intent_router
from core.intent_classifier import classify
from core.types import IncomingMessage


def manda(user_id: int, texto: str) -> str:
    """Uma mensagem de WhatsApp, pelo caminho real: classifica → roteia → responde."""
    msg = IncomingMessage(platform="whatsapp", user_id=user_id, text=texto)
    return intent_router.route(classify(texto, user_id=user_id), msg)


@pytest.fixture()
def carteira(user_id):
    """A carteira do relato: quatro investimentos e uma CAIXINHA de nome repetido."""
    db.add_launch_and_update_balance(user_id, "receita", 5000, None, "seed")
    for nome, taxa, periodo in [
        ("Reserva de Emergencia", 1.0, "cdi"),
        ("Tesouro IPCA+ 2032", 0.0762, "yearly"),
        ("Tesouro Prefixado 2032", 0.1378, "yearly"),
        ("Fabiana", 1.0, "cdi"),
    ]:
        db.create_investment_db(user_id, nome, taxa, periodo, "seed", initial_amount=100)
    db.create_pocket(user_id, "Reserva de Emergência")
    return user_id


def _saldo_inv(user_id: int, nome: str) -> float:
    return next(float(i["balance"]) for i in db.list_investments(user_id) if i["name"] == nome)


# ─── conversa 1: o nome está na frase ────────────────────────────────────────

def test_conversa_nome_na_frase_resolve_em_uma_mensagem(carteira):
    """Uma mensagem, uma resposta. Antes devolvia a lista e a conversa não terminava."""
    r = manda(carteira, "investi 80 em reserva de emergencia")

    assert "✅" in r
    assert "Reserva de Emergencia" in r
    assert "Em qual investimento" not in r
    assert _saldo_inv(carteira, "Reserva de Emergencia") == 180.0
    assert db.get_pending_action(carteira) is None      # não deixou pergunta pendurada


# ─── conversa 2: nome que não existe → pergunta → resposta ───────────────────

def test_conversa_pergunta_e_resposta_pelo_nome(carteira):
    """A conversa exata do relato. O terceiro turno é o que quebrava: o usuário tem
    uma CAIXINHA com o mesmo nome, e a resposta caía nela."""
    pergunta = manda(carteira, "investi 800 na carteira azul")
    assert "Não encontrei" in pergunta
    assert "Em qual investimento" in pergunta

    resposta = manda(carteira, "Reserva de Emergencia")

    assert "✅" in resposta
    assert "caixinha" not in resposta.lower()
    assert _saldo_inv(carteira, "Reserva de Emergencia") == 900.0
    pockets = {p["name"]: float(p["balance"]) for p in db.list_pockets(carteira)}
    assert pockets["Reserva de Emergência"] == 0.0     # a caixinha ficou intocada


def test_conversa_resposta_pelo_numero(carteira):
    manda(carteira, "investi 800 na carteira azul")
    r = manda(carteira, "1")

    assert "✅" in r
    assert db.get_pending_action(carteira) is None


def test_conversa_ambiguidade(carteira):
    pergunta = manda(carteira, "investi 300 no tesouro")
    assert "Tesouro IPCA+ 2032" in pergunta and "Tesouro Prefixado 2032" in pergunta
    assert "Fabiana" not in pergunta

    r = manda(carteira, "Tesouro Prefixado 2032")
    assert "✅" in r
    assert _saldo_inv(carteira, "Tesouro Prefixado 2032") == 400.0
    assert _saldo_inv(carteira, "Tesouro IPCA+ 2032") == 100.0      # o outro não moveu


def test_conversa_desistir(carteira):
    manda(carteira, "investi 800 na carteira azul")
    r = manda(carteira, "cancelar")

    assert "cancelei" in r.lower()
    assert db.get_pending_action(carteira) is None
    assert _saldo_inv(carteira, "Reserva de Emergencia") == 100.0


def test_conversa_resposta_ilegivel_nao_adivinha(carteira):
    """Quando não entende, repergunta. Adivinhar aqui investiria no ativo errado —
    e aporte errado só se desfaz com resgate, que recolhe IR/IOF."""
    manda(carteira, "investi 800 na carteira azul")
    r = manda(carteira, "aquele lá")

    assert "Não entendi qual" in r
    assert db.get_pending_action(carteira) is not None
    assert _saldo_inv(carteira, "Reserva de Emergencia") == 100.0


# ─── conversa 3: o usuário desiste no meio e faz outra coisa ─────────────────

def test_conversa_outro_comando_abandona_a_pergunta(carteira):
    """Pergunta pendente não pode sequestrar o próximo comando. Sem a escotilha de
    escape, "saldo" viraria resposta da pergunta."""
    manda(carteira, "investi 800 na carteira azul")
    r = manda(carteira, "saldo")

    assert "Em qual investimento" not in r
    assert "Não entendi qual" not in r
    assert db.get_pending_action(carteira) is None      # a pergunta foi abandonada


def test_conversa_sim_depois_de_abandonar_nao_dispara_nada(carteira):
    """Footgun histórico do repo: pendência órfã + "sim" depois executava a ação
    antiga. Aqui o "sim" não pode aportar nada."""
    manda(carteira, "investi 800 na carteira azul")
    manda(carteira, "saldo")
    manda(carteira, "sim")

    assert _saldo_inv(carteira, "Reserva de Emergencia") == 100.0


# ─── carteira vazia ──────────────────────────────────────────────────────────

def test_conversa_sem_investimento_nenhum(user_id):
    """Sem carteira não há o que perguntar — a resposta tem que dizer o caminho."""
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")

    r = manda(user_id, "investi 800 na renda fixa")

    assert "Em qual investimento você quer aportar R$" not in r   # não pergunta no vazio
    assert db.get_pending_action(user_id) is None
