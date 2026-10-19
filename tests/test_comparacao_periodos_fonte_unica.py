"""P1-B: o mês aberto da `compare_periods` é o MESMO número do `resumo_do_mes` (o card Resumo do mês).

Relógio congelado em 12/10/2026. A fatura que fecha DEPOIS de hoje (dia 20) e o lançamento com data futura (dia 25)
só entram se a janela for o mês inteiro: com "os mesmos N dias" o teste fica vermelho (controle negativo).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal as D

import pytest

import db
from conftest import usuario_pagante
from core.services import comparacao_periodos as cp
from db import resumo_mes
from tests._patrimonio_helpers import q
from utils_date import _tz

AGORA = datetime(2026, 10, 12, 10, 0, tzinfo=_tz())


@pytest.fixture(autouse=True)
def _relogio(monkeypatch):
    monkeypatch.setattr(cp, "now_tz", lambda: AGORA)


def _lanc(uid, tipo, valor, mes, dia):
    q("insert into launches (user_id, tipo, valor, criado_em) values (%s, %s, %s, %s)",
      (uid, tipo, valor, datetime(2026, mes, dia, 12, tzinfo=_tz())))


def test_mes_aberto_da_comparacao_bate_com_o_resumo_do_mes():
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 10, 10, 3)
    _lanc(uid, "despesa", 33, 10, 25)          # data futura: o mês aberto inteiro a conta
    _lanc(uid, "receita", 1000, 10, 4)
    _lanc(uid, "despesa", 200, 9, 10)
    _lanc(uid, "despesa", 50, 9, 28)           # setembro depois do dia 12: o anterior é inteiro
    _lanc(uid, "receita", 900, 9, 2)
    cartao = db.create_card(uid, "Nubank", closing_day=20, due_day=27)
    db.add_credit_purchase(uid, cartao, 80, "Mercado", "fecha dia 20", date(2026, 10, 5))   # fatura de 20/10 > hoje
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    res = resumo_mes.resumo_do_mes(uid, 2026, 10, AGORA)
    cent = lambda v: D(str(v)).quantize(D("0.01"))  # noqa: E731
    assert r["despesa"]["total_atual"] == cent(res["saiu"]) == D("123.00")   # 10 + 33 + 80
    assert r["receita"]["total_atual"] == cent(res["entrou"]) == D("1000.00")
    assert r["despesa"]["total_anterior"] == cent(res["anterior"]["saiu"]) == D("250.00")
    assert r["receita"]["total_anterior"] == cent(res["anterior"]["entrou"]) == D("900.00")
