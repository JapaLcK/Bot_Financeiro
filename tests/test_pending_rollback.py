"""A pendência volta quando o trabalho reivindicado estoura.

O PR #141 trocou `clear_pending_action` (depois do trabalho) por
`consume_pending_action` (antes do trabalho). Onde o trabalho pode levantar, a
pergunta passou a morrer junto com a exceção: o usuário perde a confirmação,
não recebe nada útil e refaz o fluxo do zero. Antes o `clear` vinha depois, e a
pergunta sobrevivia — é regressão.

`db.restore_pending_on_error` devolve, e devolve CONDICIONALMENTE
(`create_pending_action_if_absent`): se outra tarefa armou uma pergunta nova
entre a reivindicação e a falha, a devolução não escreve — operação antiga não
sobrescreve pendência mais nova.

Cada site tem os três controles:

- **negativo** — o trabalho levanta → a pendência voltou com o mesmo
  `action_type` e `payload`. Desligado o conserto (tirar o `with` do site), o
  caso fica vermelho.
- **positivo** — caminho feliz continua consumindo e não deixa pendência.
- **corrida** — o trabalho levanta E outra tarefa já ocupou a linha → a
  devolução não escreve, a pergunta nova sobrevive intacta.

Os quatro sites de crédito rodam pela CONVERSA (`handle_incoming`), não pela
função isolada: é o caminho que o "sim" do usuário percorre de verdade.
"""
import uuid

import pytest

import db
import db.cards
from core.handlers import credit as H_credit
from core.handlers import investments as H_inv


class Estourou(RuntimeError):
    pass


def _uid() -> int:
    """Abaixo de 2 bilhões DE PROPÓSITO.

    `core.handle_incoming._normalize_user_id` comprime qualquer id acima disso
    (regra dos ids gigantes do WhatsApp), então um uid da fixture `user_id`
    (`% 10_000_000_000`) roteia para OUTRO usuário: o `handle_incoming` não vê a
    pendência, responde o fallback genérico e o teste passa sem exercitar nada.
    Mesma escolha de tests/test_bill_amount_pending.py.
    """
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    return uid


def _diga(uid: int, texto: str) -> str:
    from core.types import IncomingMessage
    import core.handle_incoming as HI

    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id=f"m{uuid.uuid4().hex[:8]}", attachments=[],
                          external_id="e", raw={})
    saida = HI.handle_incoming(msg)
    return "\n".join(m.text for m in (saida or []) if getattr(m, "text", None))


def _cartao(uid: int) -> int:
    return int(db.cards.create_card(user_id=uid, name="Nubank",
                                    closing_day=1, due_day=8))


# ── os quatro sites de crédito ───────────────────────────────────────────────
# (nome, action_type, monta_payload(card_id), nome_da_funcao_que_estoura)
SITES_CREDITO = [
    ("delete_card",
     "credit_delete_card",
     lambda cid: {"card_id": cid, "card_name": "Nubank"},
     "delete_card"),
    ("set_primary",
     "credit_card_set_primary",
     lambda cid: {"card_id": cid},
     "set_default_card"),
    ("setup/confirm_delete_existing_card",
     "credit_card_setup",
     lambda cid: {"step": "confirm_delete_existing_card", "existing_card_id": cid,
                  "existing_card_name": "Nubank", "card_name": "Nubank",
                  "closing_day": 1, "due_day": 8, "ask_primary": True},
     "delete_card"),
    ("setup/set_primary",
     "credit_card_setup",
     lambda cid: {"step": "set_primary", "card_id": cid, "ask_primary": True},
     "set_default_card"),
]

IDS = [s[0] for s in SITES_CREDITO]


@pytest.mark.parametrize("nome,tipo,monta,alvo", SITES_CREDITO, ids=IDS)
def test_credito_devolve_pendencia_quando_o_trabalho_estoura(
        monkeypatch, nome, tipo, monta, alvo):
    """Negativo: sem o `with` no site, a pendência some e este caso fica vermelho."""
    user_id = _uid()
    cid = _cartao(user_id)
    payload = monta(cid)
    db.set_pending_action(user_id, tipo, payload, minutes=20)

    def _estoura(*a, **k):
        raise Estourou("banco caiu no meio")

    monkeypatch.setattr(H_credit, alvo, _estoura)

    _diga(user_id, "sim")  # handle_incoming engole a exceção e responde genérico

    volta = db.get_pending_action(user_id) or {}
    assert volta.get("action_type") == tipo, (
        f"{nome}: a pendência não voltou — ficou {volta.get('action_type')!r}")
    assert volta.get("payload") == payload, f"{nome}: payload perdido: {volta.get('payload')}"


@pytest.mark.parametrize("nome,tipo,monta,alvo", SITES_CREDITO, ids=IDS)
def test_credito_caminho_feliz_consome_e_nao_deixa_pendencia(
        monkeypatch, nome, tipo, monta, alvo):
    """Positivo: sem falha o site continua consumindo. Sem isto o grupo passaria
    num código que só devolve pendência e nunca executa."""
    user_id = _uid()
    cid = _cartao(user_id)
    db.set_pending_action(user_id, tipo, monta(cid), minutes=20)

    resposta = _diga(user_id, "sim")

    assert db.get_pending_action(user_id) is None, (
        f"{nome}: pendência ficou para trás no caminho feliz")
    assert "✅" in resposta, f"{nome}: não confirmou nada — {resposta!r}"


@pytest.mark.parametrize("nome,tipo,monta,alvo", SITES_CREDITO, ids=IDS)
def test_credito_devolucao_nao_atropela_pergunta_mais_nova(
        monkeypatch, nome, tipo, monta, alvo):
    """Corrida: prova que a devolução é condicional, não upsert."""
    user_id = _uid()
    cid = _cartao(user_id)
    db.set_pending_action(user_id, tipo, monta(cid), minutes=20)

    def _estoura(*a, **k):
        # a outra tarefa armou a pergunta dela enquanto esta trabalhava
        db.set_pending_action(user_id, "bill_amount_expected",
                              {"bill_id": 4242, "bill_name": "luz"})
        raise Estourou("banco caiu no meio")

    monkeypatch.setattr(H_credit, alvo, _estoura)

    _diga(user_id, "sim")

    volta = db.get_pending_action(user_id) or {}
    assert volta.get("action_type") == "bill_amount_expected", (
        f"{nome}: a devolução atropelou a pergunta mais nova — ficou "
        f"{volta.get('action_type')!r}")
    assert (volta.get("payload") or {}).get("bill_id") == 4242


# ── investimentos: o que sobe até o resolver é o que estourou ANTES de o
#    dinheiro andar (teto de plano, falha ao ler a carteira) ──────────────────

def _pending_pick(uid: int) -> dict:
    db.set_pending_action(uid, "investment_pick",
                          {"amount": 500.0, "text": "aportar 500",
                           "nomes": ["CDB Nubank", "Tesouro"]})
    return db.get_pending_action(uid)


def _pending_funding(uid: int) -> dict:
    db.set_pending_action(uid, "funding_source_choice", {
        "amount": 500.0,
        "retomar": {"fluxo": "investment_deposit", "name": "CDB Nubank",
                    "text": "aportar 500"},
        "fontes": [{"kind": "account", "of_account_id": None,
                    "label": "Carteira", "balance": 900.0}],
    })
    return db.get_pending_action(uid)


def test_investment_pick_devolve_pergunta_quando_o_aporte_estoura(monkeypatch):
    user_id = _uid()
    pending = _pending_pick(user_id)

    def _estoura(*a, **k):
        raise Estourou("teto de plano")

    monkeypatch.setattr(H_inv, "deposit", _estoura)

    with pytest.raises(Estourou):
        H_inv.resolve_investment_pick(user_id, "1", pending)

    volta = db.get_pending_action(user_id) or {}
    assert volta.get("action_type") == "investment_pick", volta
    assert (volta.get("payload") or {}).get("amount") == 500.0


def test_investment_pick_caminho_feliz_consome(monkeypatch):
    user_id = _uid()
    pending = _pending_pick(user_id)
    monkeypatch.setattr(H_inv, "deposit", lambda *a, **k: "✅ aportado")

    assert H_inv.resolve_investment_pick(user_id, "1", pending) == "✅ aportado"
    assert db.get_pending_action(user_id) is None


def test_investment_pick_devolucao_nao_atropela_pergunta_mais_nova(monkeypatch):
    user_id = _uid()
    pending = _pending_pick(user_id)

    def _estoura(*a, **k):
        db.set_pending_action(user_id, "bill_amount_expected", {"bill_id": 4242})
        raise Estourou("teto de plano")

    monkeypatch.setattr(H_inv, "deposit", _estoura)

    with pytest.raises(Estourou):
        H_inv.resolve_investment_pick(user_id, "1", pending)

    volta = db.get_pending_action(user_id) or {}
    assert volta.get("action_type") == "bill_amount_expected", volta


def test_funding_choice_devolve_pergunta_quando_o_aporte_estoura(monkeypatch):
    user_id = _uid()
    pending = _pending_funding(user_id)

    def _estoura(*a, **k):
        raise Estourou("teto de plano")

    monkeypatch.setattr(H_inv, "_aporta", _estoura)

    with pytest.raises(Estourou):
        H_inv.resolve_funding_choice(user_id, "1", pending)

    volta = db.get_pending_action(user_id) or {}
    assert volta.get("action_type") == "funding_source_choice", volta
    assert (volta.get("payload") or {}).get("amount") == 500.0


def test_funding_choice_caminho_feliz_consome(monkeypatch):
    user_id = _uid()
    pending = _pending_funding(user_id)
    monkeypatch.setattr(H_inv, "_aporta", lambda *a, **k: "✅ aportado")

    assert H_inv.resolve_funding_choice(user_id, "1", pending) == "✅ aportado"
    assert db.get_pending_action(user_id) is None


def test_funding_choice_devolucao_nao_atropela_pergunta_mais_nova(monkeypatch):
    user_id = _uid()
    pending = _pending_funding(user_id)

    def _estoura(*a, **k):
        db.set_pending_action(user_id, "bill_amount_expected", {"bill_id": 4242})
        raise Estourou("teto de plano")

    monkeypatch.setattr(H_inv, "_aporta", _estoura)

    with pytest.raises(Estourou):
        H_inv.resolve_funding_choice(user_id, "1", pending)

    volta = db.get_pending_action(user_id) or {}
    assert volta.get("action_type") == "bill_amount_expected", volta


# ── o CAMINHO DE PRODUÇÃO dos dois fluxos de `funding_source_choice` ─────────
# Os seis casos acima trocam `_aporta`/`deposit` por uma função que levanta —
# o que EU escrevi, não o que roda. Em produção quem executa é
# `_pockets.deposita_com_origem` / `_aporta`, e as duas ENGOLIAM toda `Exception`
# e devolviam string: o `restore_pending_on_error` nunca via falha operacional
# nenhuma, a pendência ficava consumida e responder a origem de novo não repetia
# o depósito. P2 do Codex no PR #144.
#
# Aqui a falha é de banco DE VERDADE, dentro de `pocket_deposit_from_account` /
# `investment_deposit_from_account`: o `get_conn` do módulo de `db/` levanta
# `OperationalError`, como se o Postgres tivesse caído no meio.

def _pending_funding_para(uid: int, fluxo: str, nome: str) -> dict:
    db.set_pending_action(uid, "funding_source_choice", {
        "amount": 500.0,
        "retomar": {"fluxo": fluxo, "name": nome, "text": f"coloquei 500 em {nome}"},
        "fontes": [{"kind": "account", "of_account_id": None,
                    "label": "Carteira", "balance": 900.0}],
    })
    return db.get_pending_action(uid)


def _carteira(uid: int, valor: float = 900.0) -> None:
    db.add_launch_and_update_balance(uid, "receita", valor, None, "seed")


FLUXOS = [
    ("pocket_deposit", "Viagem", "db.pockets"),
    ("investment_deposit", "CDB Nubank", "db.investments"),
]
FLUXO_IDS = [f[0] for f in FLUXOS]


def _cria_destino(uid: int, fluxo: str, nome: str) -> None:
    if fluxo == "pocket_deposit":
        db.create_pocket(uid, nome)
    else:
        db.create_investment(uid, nome, 0.1, "yearly")


@pytest.mark.parametrize("fluxo,nome,modulo", FLUXOS, ids=FLUXO_IDS)
def test_funding_choice_devolve_quando_o_banco_cai_de_verdade(
        monkeypatch, fluxo, nome, modulo):
    """Negativo do conserto do P2: com o `except Exception` devolvendo string
    (o código de antes), nada sobe, a pendência não volta e isto fica vermelho."""
    import importlib
    import psycopg

    user_id = _uid()
    _carteira(user_id)
    _cria_destino(user_id, fluxo, nome)
    pending = _pending_funding_para(user_id, fluxo, nome)

    def _banco_fora(*a, **k):
        raise psycopg.OperationalError("connection to server was lost")

    monkeypatch.setattr(importlib.import_module(modulo), "get_conn", _banco_fora)

    with pytest.raises(psycopg.OperationalError):
        H_inv.resolve_funding_choice(user_id, "1", pending)

    volta = db.get_pending_action(user_id) or {}
    assert volta.get("action_type") == "funding_source_choice", (
        f"{fluxo}: a pendência não voltou — ficou {volta.get('action_type')!r}; "
        "responder a origem de novo não repetiria o depósito")
    assert (volta.get("payload") or {}).get("retomar", {}).get("fluxo") == fluxo
    assert float(db.get_balance(user_id)) == 900.0, "o dinheiro andou mesmo assim"


@pytest.mark.parametrize("fluxo,nome,modulo", FLUXOS, ids=FLUXO_IDS)
def test_funding_choice_caminho_feliz_de_verdade_consome(monkeypatch, fluxo, nome, modulo):
    """Positivo do mesmo grupo: sem falha o depósito acontece de verdade e a
    pendência não fica para trás. Sem isto o grupo passaria num código que só
    devolve pendência e nunca deposita."""
    user_id = _uid()
    _carteira(user_id)
    _cria_destino(user_id, fluxo, nome)
    pending = _pending_funding_para(user_id, fluxo, nome)

    resposta = H_inv.resolve_funding_choice(user_id, "1", pending)

    assert "✅" in (resposta or ""), resposta
    assert db.get_pending_action(user_id) is None
    assert float(db.get_balance(user_id)) == 400.0


# ── o outro lado: recusa legítima NÃO re-arma ────────────────────────────────
# Saldo insuficiente / destino inexistente se repetiriam igual. Re-armar
# prenderia o usuário respondendo uma escolha que nunca vai funcionar — e a
# pendência `funding_source_choice` suprime o fallback de IA por 10 minutos.

RECUSAS = [
    ("pocket_deposit", "Viagem", 100.0, True, "saldo insuficiente"),
    ("pocket_deposit", "Nao Existe", 900.0, False, "caixinha inexistente"),
    ("investment_deposit", "CDB Nubank", 100.0, True, "saldo insuficiente"),
    ("investment_deposit", "Nao Existe", 900.0, False, "investimento inexistente"),
]


@pytest.mark.parametrize("fluxo,nome,saldo,cria,caso", RECUSAS,
                         ids=[f"{r[0]}-{r[4]}" for r in RECUSAS])
def test_funding_choice_recusa_legitima_nao_rearma(fluxo, nome, saldo, cria, caso):
    user_id = _uid()
    _carteira(user_id, saldo)
    if cria:
        _cria_destino(user_id, fluxo, nome)
    pending = _pending_funding_para(user_id, fluxo, nome)

    resposta = H_inv.resolve_funding_choice(user_id, "1", pending)

    assert resposta, f"{caso}: recusa sem texto nenhum"
    assert db.get_pending_action(user_id) is None, (
        f"{caso}: a pergunta foi re-armada — o usuário fica preso repetindo "
        "uma escolha que nunca vai funcionar")
    assert float(db.get_balance(user_id)) == saldo


# ── falha DEPOIS do commit: o dinheiro andou, re-armar depositaria duas vezes ─

@pytest.mark.parametrize("fluxo,nome,modulo", FLUXOS, ids=FLUXO_IDS)
def test_funding_choice_falha_depois_do_commit_nao_rearma(monkeypatch, fluxo, nome, modulo):
    """`db.display_id_for` roda DEPOIS do commit. Se ela subisse, a devolução
    re-armaria a pergunta e a próxima resposta do usuário depositaria de novo."""
    user_id = _uid()
    _carteira(user_id)
    _cria_destino(user_id, fluxo, nome)
    pending = _pending_funding_para(user_id, fluxo, nome)

    def _estoura(*a, **k):
        raise Estourou("banco caiu logo depois do commit")

    monkeypatch.setattr(db, "display_id_for", _estoura)

    resposta = H_inv.resolve_funding_choice(user_id, "1", pending)

    assert "✅" in (resposta or ""), f"{fluxo}: o sucesso virou erro — {resposta!r}"
    assert db.get_pending_action(user_id) is None, (
        f"{fluxo}: pergunta re-armada com o dinheiro já debitado — a próxima "
        "resposta depositaria de novo")
    assert float(db.get_balance(user_id)) == 400.0
