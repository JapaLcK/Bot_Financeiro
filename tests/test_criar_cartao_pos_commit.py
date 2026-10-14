"""WhatsApp, criar cartão: falha DEPOIS do commit nunca vira "erro" nem ecoa `{e}`.

`create_card` commita; set_default_card, a pendência do lembrete, `_card_summary` e a
mensagem vêm depois. Antes, o sítio da frase única devolvia "❌ Erro criando cartão: {e}"
(com o DETAIL do psycopg) com o cartão já criado, e os outros três (comando completo,
nome com dias inline, step `due_day`) subiam como "erro interno". Mesma classe do #984
(`tests/test_credit_pos_commit.py`). A conversa vai pelo `handle_incoming` (`diga`),
com banco real; a falha é injetada DEPOIS da gravação, ou na própria gravação.
"""
from __future__ import annotations

import logging

import pytest

import db
# Importado ANTES de qualquer monkeypatch em `db.*` (credit faz `from db import ...` no topo).
import core.handlers.credit as credit
from core.services.plan_limits import PlanLimitExceeded
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401 (fixtures)
from tests._of_cash_helpers import q
from tests._pendencia_credito_helpers import diga

SENTINELA = "DETAIL: Key (valor)=(50)"
PROIBIDO = ("Erro", "erro interno", "Tenta", "DETAIL", "Key (valor)")
FRASE_A = "criar cartao nubank fecha 10 vence 17"
FRASE_B = "criar cartao nubank vence 17 fecha 10"  # não casa o regex da frase única


def _boom(*_a, **_k):
    raise RuntimeError(SENTINELA)


def _cartoes(uid):
    return q("select count(*) as n from credit_cards where user_id=%s", (uid,), True)[0]["n"]


def _step(uid):
    p = db.get_pending_action(uid)
    return p and p["payload"].get("step")


def _log_ok(caplog, op):
    """Uma linha do op, com a causa, sem o texto da exceção nem traceback."""
    linhas = [r for r in caplog.records
              if r.name == "core.observability" and r.getMessage().startswith(f"{op}: falha")]
    assert len(linhas) == 1, [r.getMessage() for r in caplog.records]
    assert "causa=RuntimeError" in linhas[0].getMessage()
    assert SENTINELA not in linhas[0].getMessage() and linhas[0].exc_info is None


def _sucesso(r):
    assert r.startswith("✅"), r
    assert not any(p in r for p in PROIBIDO), r


@pytest.fixture(autouse=True)
def _ia(ia_fora):  # noqa: F811
    return ia_fora


# ── DEPOIS do commit ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("frase", [FRASE_A, FRASE_B], ids=["sitio_A_frase_unica", "sitio_B_comando_completo"])
def test_resumo_quebrado_nao_vira_erro(uid_pro, monkeypatch, caplog, frase):
    monkeypatch.setattr(credit, "_card_summary", _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, frase)
    _sucesso(r)
    assert _cartoes(uid_pro) == 1
    _log_ok(caplog, "create_card_msg")


def test_step_due_day_resumo_quebrado_nao_vira_erro_interno(uid_pro, monkeypatch, caplog):
    monkeypatch.setattr(credit, "_card_summary", _boom)
    with caplog.at_level(logging.ERROR):
        for t in ("criar cartao", "nubank", "10"):
            diga(uid_pro, t)
        r = diga(uid_pro, "17")
    _sucesso(r)
    assert _cartoes(uid_pro) == 1
    _log_ok(caplog, "create_card_msg")
    # A pendência já era a do lembrete (set_pending_action vem antes do resumo): nunca o `due_day` velho.
    assert _step(uid_pro) in (None, "reminder_opt_in")
    assert "Erro" not in diga(uid_pro, "oi")


def test_step_due_day_falha_antes_da_pendencia_consome_o_step_velho(uid_pro, monkeypatch, caplog):
    """`set_default_card` quebra antes de `set_pending_action`: o step `due_day` seguiria vivo e o
    próximo texto seria lido como resposta dele. O helper consome a pendência lida."""
    monkeypatch.setattr(credit, "set_default_card", _boom)
    with caplog.at_level(logging.ERROR):
        for t in ("criar cartao", "nubank", "10"):
            diga(uid_pro, t)
        r = diga(uid_pro, "17")
    _sucesso(r)
    assert _cartoes(uid_pro) == 1 and _step(uid_pro) is None
    _log_ok(caplog, "create_card_msg")


def test_sitio_C_nome_com_dias_inline_resumo_quebrado(uid_pro, monkeypatch, caplog):
    """Nubank já existe -> aviso de duplicata guarda fecha/vence -> 'visa' cria direto (sítio C)."""
    db.create_card(uid_pro, "Nubank", 1, 8)
    assert "Já existe" in diga(uid_pro, FRASE_A)
    monkeypatch.setattr(credit, "_card_summary", _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, "visa")
    _sucesso(r)
    assert _cartoes(uid_pro) == 2 and _step(uid_pro) == "reminder_opt_in"
    _log_ok(caplog, "create_card_msg")


# ── ANTES do commit (sítio A): a gravação falha, nada gravado, sem a exceção ──

def test_falha_na_gravacao_nao_ecoa_a_excecao(uid_pro, monkeypatch, caplog):
    monkeypatch.setattr(credit, "create_card", _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, FRASE_A)
    assert r.startswith("❌"), r
    assert "DETAIL" not in r and "Key (valor)" not in r, r
    assert _cartoes(uid_pro) == 0
    _log_ok(caplog, "create_card")


# ── Controles positivos: o conserto não recusa o caminho legítimo ────────────

def test_caminho_feliz_mantem_os_textos(uid_pro):
    r = diga(uid_pro, FRASE_A)
    assert "registrado com sucesso" in r and "Gostaria de receber notificações" in r
    assert _step(uid_pro) == "reminder_opt_in" and _cartoes(uid_pro) == 1
    db.clear_pending_action(uid_pro)
    assert "criado!" in diga(uid_pro, "criar cartao visa vence 20 fecha 5")
    db.clear_pending_action(uid_pro)
    for t in ("criar cartao", "elo", "10"):
        diga(uid_pro, t)
    r = diga(uid_pro, "17")
    assert "registrado com sucesso" in r and "Gostaria de receber notificações" in r
    assert _cartoes(uid_pro) == 3


@pytest.mark.parametrize("frase", [FRASE_A, FRASE_B], ids=["A", "B"])
def test_limite_do_plano_segue_devolvendo_a_mensagem_do_plano(uid_pro, monkeypatch, frase):
    def limite(*_a, **_k):
        raise PlanLimitExceeded("cards", "MSG-DO-PLANO")
    monkeypatch.setattr(credit, "create_card", limite)
    assert diga(uid_pro, frase) == "MSG-DO-PLANO"
    assert _cartoes(uid_pro) == 0


def test_limite_do_plano_no_step_due_day_consome_a_pendencia(uid_pro, monkeypatch):
    def limite(*_a, **_k):
        raise PlanLimitExceeded("cards", "MSG-DO-PLANO")
    monkeypatch.setattr(credit, "create_card", limite)
    for t in ("criar cartao", "nubank", "10"):
        diga(uid_pro, t)
    assert diga(uid_pro, "17") == "MSG-DO-PLANO" and _step(uid_pro) is None


def test_nome_duplicado_segue_no_aviso(uid_pro):
    db.create_card(uid_pro, "Nubank", 1, 8)
    assert "Já existe" in diga(uid_pro, FRASE_A)
    assert _cartoes(uid_pro) == 1


# ── Falha em set_default_card / set_pending_action DEPOIS do commit ──────────

@pytest.mark.parametrize("frase,quebrado", [
    (FRASE_A, "set_default_card"), (FRASE_A, "set_pending_action"),
    (FRASE_B, "set_default_card"), (FRASE_B, "set_pending_action"),
], ids=["A-default", "A-pendencia", "B-default", "B-pendencia"])
def test_sitios_A_B_gravacao_pos_commit_quebrada(uid_pro, monkeypatch, caplog, frase, quebrado):
    """Usuário SEM cartões: o `set_default_card` roda de fato (só roda no primeiro)."""
    monkeypatch.setattr(credit, quebrado, _boom)
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, frase)
    _sucesso(r)
    assert _cartoes(uid_pro) == 1
    _log_ok(caplog, "create_card_msg")


@pytest.mark.parametrize("quebrado", ["set_default_card", "set_pending_action"])
def test_sitio_C_gravacao_pos_commit_quebrada_consome_o_aviso(uid_pro, monkeypatch, caplog, quebrado):
    """Sem `pending` no helper, `duplicate_card_name` fica vivo e 'minha fatura do nubank'
    viraria um cartão fantasma."""
    db.create_card(uid_pro, "Nubank", 1, 8)
    assert "Já existe" in diga(uid_pro, FRASE_A)
    # `set_default_card` só roda com existing_count == 0, e o aviso de duplicata só nasce com
    # cartão existente (>= 1): esse ramo é inalcançável hoje. Zerar o contador no payload trava
    # a ORDEM do código (set_default_card dentro do try pós-commit), não um caminho real.
    p = db.get_pending_action(uid_pro)
    p["payload"]["existing_count"] = 0
    db.set_pending_action(uid_pro, "credit_card_setup", p["payload"], minutes=20)
    with monkeypatch.context() as mp, caplog.at_level(logging.ERROR):
        mp.setattr(credit, quebrado, _boom)
        r = diga(uid_pro, "visa")
    _sucesso(r)
    assert _cartoes(uid_pro) == 2 and _step(uid_pro) in (None, "reminder_opt_in")
    _log_ok(caplog, "create_card_msg")
    diga(uid_pro, "minha fatura do nubank")
    assert _cartoes(uid_pro) == 2


# ── Dia fora de 1..31 no sítio A: manda reenviar o comando, sem armar pendência ──

@pytest.mark.parametrize("frase", [
    "criar cartao nubank fecha 45 vence 17", "criar cartao nubank fecha 0 vence 17",
    "criar cartao nubank fecha 10 vence 32", "criar cartao nubank fecha 10 vence 0",
])
def test_dia_fora_de_1_a_31_manda_reenviar_o_comando(uid_pro, caplog, frase):
    with caplog.at_level(logging.ERROR):
        r = diga(uid_pro, frase)
    assert "entre *1* e *31*" in r and "Manda o comando de novo" in r, r  # diga() converte ** em *
    assert not any(p in r for p in PROIBIDO), r
    assert _cartoes(uid_pro) == 0
    assert not [x for x in caplog.records if "falha" in x.getMessage()]
    if "fecha 45" in frase:  # nenhuma pendência: o texto não promete pergunta que ninguém recebe
        r2 = diga(uid_pro, "10")
        assert _cartoes(uid_pro) == 0 and _step(uid_pro) is None
        assert "Quando vence" not in r2 and "Quando fecha" not in r2, r2
