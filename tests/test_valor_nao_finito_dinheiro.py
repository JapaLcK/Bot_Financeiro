"""NaN, Infinity e valor acima do teto não entram em dinheiro.

`float("nan") <= 0` é False, então o guard `valor <= 0` deixava NaN passar, e
`parse_money` devolve `inf` para dígitos enormes. O `numeric` do Postgres grava
NaN/Infinity/1e308: a fatura do cartão ficava `NaN` e `pagar fatura` respondia
"Nenhuma fatura em aberto" (impagável); o saldo da conta virava 1e308.

A tool da IA e o WhatsApp rodam pelo código real, com banco. O que mede é o
estado gravado, não só a mensagem. Fonte única do critério: `utils_text.valor_valido`.
"""
import pytest

import db
from conftest import usuario_pagante
from utils_text import TETO_ENTRADA, valor_valido

HUGE = "1" * 400          # parse_money -> inf
GRANDE = "1" * 301        # parse_money -> 1e300, finito mas absurdo


def _diga(uid, texto):
    from core.types import IncomingMessage
    import core.handle_incoming as HI

    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id="x", attachments=[], external_id="e", raw={})
    saida = HI.handle_incoming(msg)
    return saida[0].text if saida else ""


def _conta(sql, uid):
    with db.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, (uid,))
        return cur.fetchall()


def _faturas(uid):
    return [r["t"] for r in _conta("select total::text t from credit_bills where user_id=%s", uid)]


def _compras(uid):
    return _conta("select 1 from credit_transactions where user_id=%s", uid)


def _usuario_com_cartao():
    uid = usuario_pagante()
    cid = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(uid, cid)
    return uid, cid


@pytest.mark.parametrize("v,ok", [
    (50, True), (0.01, True), ("44.90", True), (TETO_ENTRADA, True),
    (0, False), (-1, False), (None, False), ("abc", False),
    (float("nan"), False), (float("inf"), False), (float("-inf"), False),
    ("nan", False), ("Infinity", False), (1e308, False), (TETO_ENTRADA + 0.01, False),
])
def test_valor_valido(v, ok):
    assert valor_valido(v) is ok


# ── tool da IA: add_credit_purchase ────────────────────────────────────────

@pytest.mark.parametrize("v", [float("nan"), float("inf"), "nan", "Infinity", 1e308, TETO_ENTRADA + 0.01])
def test_tool_compra_no_cartao_recusa_valor_nao_finito(v):
    from core.services.ai_chat.tools.cards import _add_credit_purchase_execute

    uid, _ = _usuario_com_cartao()
    msg = _add_credit_purchase_execute(uid, {"valor": v, "descricao": "x"})

    assert "✅" not in msg, msg
    assert _compras(uid) == [] and _faturas(uid) == []


def test_tool_compra_no_cartao_valor_normal_segue_igual_e_a_fatura_paga():
    from core.services.ai_chat.tools.cards import _add_credit_purchase_execute

    uid, _ = _usuario_com_cartao()
    msg = _add_credit_purchase_execute(uid, {"valor": 44.90, "descricao": "claro"})

    assert "Compra no Crédito Registrada" in msg and "R$ 44,90" in msg
    assert [float(t) for t in _faturas(uid)] == [44.9]
    assert "Pagamento registrado" in _diga(uid, "pagar fatura")


# ── WhatsApp, pelo handle_incoming ─────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    f"gastei {HUGE} no cartão",
    f"credito {HUGE} mercado",
    f"gastei {GRANDE} no cartão",
    f"parcelei {HUGE} em 3x no cartão nubank",
    f"parcelei {GRANDE} em 3x no cartão nubank",
    "parcelei 100000000000000 em 3x no cartão nubank",
])
def test_whatsapp_compra_no_cartao_recusa_valor_absurdo(texto):
    uid, _ = _usuario_com_cartao()

    resposta = _diga(uid, texto)

    assert _compras(uid) == [] and _faturas(uid) == [], texto
    # recusa já na primeira mensagem: sem perguntar o nome de uma compra impossível
    assert "Valor inválido" in resposta and "erro interno" not in resposta, resposta


def test_whatsapp_compra_no_cartao_normal_segue_igual():
    uid, _ = _usuario_com_cartao()

    assert "Compra no Crédito Registrada" in _diga(uid, "gastei 50 no cartão")
    assert [float(t) for t in _faturas(uid)] == [50.0]
    assert "Pagamento registrado: R$ 50,00" in _diga(uid, "pagar fatura")


def test_whatsapp_limite_do_cartao_recusa_valor_absurdo():
    uid, cid = _usuario_com_cartao()

    _diga(uid, f"definir limite nubank {HUGE}")

    assert db.get_card_by_id(uid, cid)["credit_limit"] is None


def test_whatsapp_limite_do_cartao_legitimo_grava():
    uid, cid = _usuario_com_cartao()

    _diga(uid, "definir limite nubank 5000")

    assert float(db.get_card_by_id(uid, cid)["credit_limit"]) == 5000.0


def test_tool_compra_no_teto_passa_e_acima_do_teto_e_recusada():
    """`TETO_ENTRADA` (R$ 1 bi, decisão do dono 2026-10-15): 2 bi é recusado, 1 bi passa."""
    from core.services.ai_chat.tools.cards import _add_credit_purchase_execute

    uid, _ = _usuario_com_cartao()
    recusa = _add_credit_purchase_execute(uid, {"valor": 2_000_000_000, "descricao": "imovel"})
    assert "Compra no Crédito Registrada" not in recusa and _faturas(uid) == []

    msg = _add_credit_purchase_execute(uid, {"valor": 1_000_000_000, "descricao": "imovel"})

    assert "Compra no Crédito Registrada" in msg
    assert [float(t) for t in _faturas(uid)] == [1_000_000_000.0]


# ── camada do banco (rotas que não passam pelo handler) ────────────────────

@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_banco_recusa_compra_no_cartao_nao_finita(v):
    from datetime import date

    uid, cid = _usuario_com_cartao()
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):
        db.add_credit_purchase(uid, cid, v, "outros", "x", date(2026, 10, 14))
    with pytest.raises(ValueError, match="VALOR_INVALIDO"):
        db.add_credit_purchase_installments(uid, cid, v, "outros", "x", date(2026, 10, 14), 3)
    assert _compras(uid) == []


# ── lançamento e boleto (mesma classe, mesmo critério) ─────────────────────

@pytest.mark.parametrize("tipo", ["receita", "despesa"])
@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_tool_lancamento_recusa_valor_nao_finito(tipo, v):
    from core.services.ai_chat.tools.launches import _add_launch_execute

    uid = usuario_pagante()
    msg = _add_launch_execute(uid, {"tipo": tipo, "valor": v, "descricao": "x"})

    assert "registrada" not in msg, msg
    assert _conta("select 1 from launches where user_id=%s", uid) == []


@pytest.mark.parametrize("texto", [f"gastei {GRANDE} no mercado", f"recebi {GRANDE} de salario"])
def test_whatsapp_lancamento_recusa_valor_acima_do_teto(texto):
    uid = usuario_pagante()

    _diga(uid, texto)

    assert _conta("select 1 from launches where user_id=%s", uid) == [], texto


def test_whatsapp_lancamento_normal_segue_igual():
    uid = usuario_pagante()

    assert "Despesa registrada" in _diga(uid, "gastei 50 no mercado")
    assert len(_conta("select 1 from launches where user_id=%s", uid)) == 1


@pytest.mark.parametrize("v", [float("nan"), float("inf"), 1e308])
def test_tool_boleto_recusa_valor_nao_finito(v):
    from core.services.ai_chat.tools.bills import _add_boleto_execute

    uid = usuario_pagante()
    msg = _add_boleto_execute(uid, {"name": "IPTU", "amount": v, "due_date": "2026-12-15"})

    assert "anotado" not in msg, msg
    assert _conta("select 1 from bill_instances where user_id=%s", uid) == []


def test_tool_boleto_valor_normal_segue_igual():
    from core.services.ai_chat.tools.bills import _add_boleto_execute

    uid = usuario_pagante()
    msg = _add_boleto_execute(uid, {"name": "IPTU", "amount": 350.5, "due_date": "2026-12-15"})

    assert "Boleto anotado" in msg
    assert len(_conta("select 1 from bill_instances where user_id=%s", uid)) == 1
