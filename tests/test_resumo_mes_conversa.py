"""O "Gastos em <mês>" do `saldo` pela CONVERSA (`handle_incoming`, banco real): gasto
avulso, compra parcelada no cartão, gasto com acento e vírgula, duas despesas na mesma
frase, depois "saldo". O número é a regra única do mês (`db/resumo_mes.py`): os gastos +
a parcela da fatura que fecha no mês.

Controle NEGATIVO: `balance.py` de volta a `db.get_summary_by_period(...)["despesa"]`
deixa este teste vermelho (R$ 112,50 no lugar de R$ 212,50) — a diferença é a parcela.
"""
from __future__ import annotations

import uuid

import core.handle_incoming as hi
import db
from conftest import usuario_pagante
from core.types import IncomingMessage
from utils_date import today_tz

_MESES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
          "Setembro", "Outubro", "Novembro", "Dezembro"]


def _diga(uid: int, texto: str) -> str:
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text=texto,
        message_id=uuid.uuid4().hex, attachments=[], external_id="", raw={},
    ))
    return "\n".join(m.text for m in out)


def test_saldo_soma_o_gasto_e_a_parcela_da_fatura_do_mes():
    uid = usuario_pagante()
    # fecha no dia 31 (= último dia do mês): a compra de hoje cai na fatura deste mês
    db.set_default_card(uid, db.create_card(uid, "Nubank", closing_day=31, due_day=10))

    assert "50" in _diga(uid, "gastei 50 no mercado")
    _diga(uid, "parcelei 300 em 3x celular")
    _diga(uid, "gastei 12,50 no açaí")
    _diga(uid, "gastei 20 no café e 30 na padaria")

    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from credit_transactions where user_id=%s", (uid,))
        assert cur.fetchone()["n"] == 3, "o parcelamento não virou 3 parcelas"

    saldo = _diga(uid, "saldo")
    mes = _MESES[today_tz().month - 1]
    assert f"📊 *Gastos em {mes}* (com o cartão pela fatura do mês): R$ 212,50" in saldo, saldo
