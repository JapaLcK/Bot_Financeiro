"""Escritas das tools do chat que capturam a exceção DENTRO do `execute`: a mensagem ao usuário (e ao
histórico) é fixa; a causa (`str(e)` do psycopg traz SQL/valor) fica só no log. O `delete_launch` já
fazia assim (`_log_falha` + `_ERRO_APAGAR`). Controle negativo: voltar `{e}` numa delas deixa o caso vermelho."""
import pytest

import db
import db.bills
from core.handlers import forma_pagamento as fp
from core.services.ai_chat import runner
from core.services.ai_chat._context import CURRENT_USER_MESSAGE
from core.services.ai_chat.tools import get_tool

SEGREDO = "psycopg SELECT segredo"


def _boom(*a, **k):
    raise RuntimeError(SEGREDO)


def _boom_valor(*a, **k):
    raise ValueError(SEGREDO)


def _conta(monkeypatch):
    monkeypatch.setattr(db.bills, "get_bill", lambda uid, bid: {"id": 1, "name": "Luz", "variable_amount": False})
    monkeypatch.setattr(fp, "decidir", lambda uid, forma: fp.CARTEIRA)


def _cartao(monkeypatch):
    monkeypatch.setattr(db, "get_default_card_id", lambda uid: 1)
    monkeypatch.setattr(db, "get_card_by_id", lambda uid, cid: {"name": "Nu"})
    monkeypatch.setattr(db, "fatura_a_pagar", lambda uid, cid, hoje: 5)


# (tool, args, [(objeto, atributo, função que levanta)], preparo)
CASOS = [
    ("add_boleto", {"name": "luz", "amount": 10, "due_date": "2099-01-15"}, [(db.bills, "create_boleto", _boom)], None),
    ("mark_bill_paid", {"bill_id": 1, "amount": 10}, [(fp, "quitar", _boom)], _conta),
    ("mark_bill_paid", {"bill_id": 1, "amount": 10}, [(fp, "quitar", _boom_valor)], _conta),
    ("pay_bill", {"amount": 10}, [(db, "pay_bill_amount", _boom)], _cartao),
    ("set_budget", {"categoria": "mercado", "budget": 10, "_confirmed": True}, [(db, "upsert_budget", _boom_valor)], None),
    ("create_investment", {"name": "cdb", "rate": 10, "period": "yearly"}, [(db, "create_investment", _boom)], None),
    ("create_investment", {"name": "cdb", "rate": 10, "period": "yearly"}, [(db, "create_investment", _boom_valor)], None),
    ("investment_withdraw", {"name": "cdb", "amount": 10}, [(db, "investment_withdraw_to_account", _boom)], None),
    ("create_pocket", {"name": "viagem"}, [(db, "create_pocket", _boom)], None),
    ("pocket_deposit", {"pocket_name": "viagem", "amount": 10}, [(db, "pocket_deposit_from_account", _boom)], None),
    ("pocket_withdraw", {"pocket_name": "viagem", "amount": 10}, [(db, "pocket_withdraw_to_account", _boom)], None),
    ("delete_pocket", {"pocket_name": "viagem"}, [(db, "delete_pocket", _boom_valor)], None),
    ("enable_daily_report", {}, [(db, "set_daily_report_enabled", _boom)], None),
    ("disable_daily_report", {}, [(db, "set_daily_report_enabled", _boom)], None),
    ("set_daily_report_hour", {"hour": 8}, [(db, "set_daily_report_hour", _boom)], None),
]


@pytest.mark.parametrize("nome,args,falhas,preparo", CASOS, ids=[f"{c[0]}-{c[2][0][2].__name__}" for c in CASOS])
def test_escrita_nao_devolve_a_causa_da_excecao(monkeypatch, nome, args, falhas, preparo):
    if preparo:
        preparo(monkeypatch)
    chamou = []
    for obj, attr, fn in falhas:
        monkeypatch.setattr(obj, attr, lambda *a, _fn=fn, **k: (chamou.append(1), _fn())[1])
    msg = get_tool(nome).execute(1, args)
    assert chamou, f"não chegou na escrita (recusa antes dela): {msg}"  # senão o caso passa sem medir nada
    assert msg.startswith("🐷") and SEGREDO not in msg and "segredo" not in msg, msg


def test_historico_tambem_nao_leva_a_causa(monkeypatch):
    monkeypatch.setattr(db.bills, "create_boleto", _boom)
    tok = CURRENT_USER_MESSAGE.set("boleto da luz de 10 vence 15/01")  # a âncora de valor lê o texto do usuário
    try:
        historico, _ = runner._dispatch_tool(1, "add_boleto", {"name": "luz", "amount": 10, "due_date": "2099-01-15"})
    finally:
        CURRENT_USER_MESSAGE.reset(tok)
    assert "segredo" not in historico and "Não consegui cadastrar o boleto" in historico


# ── depósito em caixinha: erro de NEGÓCIO explica a causa; só o genérico fica fixo ──

def _deposita(monkeypatch, exc):
    def falha(*a, **k):
        raise exc
    monkeypatch.setattr(db, "pocket_deposit_from_account", falha)
    return get_tool("pocket_deposit").execute(1, {"pocket_name": "viagem", "amount": 10})


def test_deposito_sem_saldo_diz_o_saldo(monkeypatch):
    from core.services import funding
    monkeypatch.setattr(funding, "msg_insuficiente", lambda uid, amount, acao="aporte": f"SALDO-CURTO {acao}")
    msg = _deposita(monkeypatch, ValueError("INSUFFICIENT_ACCOUNT"))
    assert "SALDO-CURTO depósito" in msg and "Tenta de novo" not in msg  # a mesma palavra do WhatsApp (core/handlers/pockets.py)


def test_deposito_caixinha_inexistente(monkeypatch):
    msg = _deposita(monkeypatch, LookupError("POCKET_NOT_FOUND"))
    assert 'Não achei a caixinha "viagem"' in msg
