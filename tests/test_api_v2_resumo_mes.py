"""`GET /api/v2/resumo-do-mes` (`db/resumo_mes.py`) pelo monólito real: sessão, banco real.

Isolamento A/B pela HTTP (inclusive `?user_id=A` e compra de A pendurada em fatura de B),
422 (formato e mês futuro), mês padrão com o relógio do app CONGELADO (virada de mês, de
ano e a meia-noite do fuso do app × UTC), a janela do plano igual à do /app com
`inicio_do_historico`, os motivos herdados do bloco de contas e dinheiro como texto.
Controle NEGATIVO do `b.user_id` e do `ct.user_id`: `test_compra_de_a_em_fatura_de_b_nao_entra`
(rodado à mão, reportado no PR). Os da regra em si: `tests/test_resumo_mes_regra.py`.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2 import resumo_mes as rota
from api.v2.resumo_mes import ResumoDoMes
from conftest import usuario_pagante
from core.services import plan_service
from tests._patrimonio_helpers import conexao, conta, horas_atras, lancamento, q
from tests.test_resumo_mes_regra import ENTROU, INICIO, SAIU, semeia_a, semeia_a_em_fatura_de_b, semeia_b
from utils_date import _tz

D = Decimal
URL = "/api/v2/resumo-do-mes"
M = f"{INICIO:%Y-%m}"
SP = _tz()


@pytest.fixture
def libera(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")

    def _libera(*uids):
        monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", ",".join(map(str, uids)))
        return uids
    return _libera


def pede(quem, **params):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    return client.get(URL, params=params)


def ok(quem, **params) -> dict:
    r = pede(quem, **params)
    assert r.status_code == 200, r.text
    return ResumoDoMes.model_validate(r.json()).model_dump() | {"_texto": r.text}


def congela(monkeypatch, instante: datetime):
    monkeypatch.setattr(rota, "now_tz", lambda: instante.astimezone(SP))


# ── isolamento ───────────────────────────────────────────────────────────────

def test_b_nao_soma_nada_de_a_nem_com_user_id_na_query(libera):
    a, b = libera(usuario_pagante(), usuario_pagante())
    semeia_a(a)
    semeia_b(b)
    de_b = ok(b, mes=M, user_id=a, uid=a)
    assert (de_b["entrou"], de_b["saiu"]) == (D("5555"), D("8665"))
    de_a = ok(a, mes=M)  # controle positivo: A vê o dele
    assert (de_a["entrou"], de_a["saiu"]) == (ENTROU, SAIU)


def test_compra_de_a_em_fatura_de_b_nao_entra(libera):
    """Linha corrompida (nenhum escritor grava): a compra é de A, a fatura é de B. O
    `b.user_id` de `TOTAIS_SQL` a deixa fora de A; o `ct.user_id`, fora de B. Sem um ou
    outro, os 4321 aparecem em A ou em B."""
    a, b = libera(usuario_pagante(), usuario_pagante())
    semeia_a_em_fatura_de_b(a, b)
    assert ok(a, mes=M)["saiu"] == D("80")  # controle positivo: a compra dele entra
    assert ok(b, mes=M)["saiu"] == D("1")


# ── formato e validação ─────────────────────────────────────────────────────

def test_dinheiro_sai_como_texto_decimal(libera):
    (a,) = libera(usuario_pagante())
    semeia_a(a)
    r = pede(a, mes=M).json()
    assert isinstance(r["entrou"], str) and isinstance(r["saiu"], str)
    assert isinstance(r["anterior"]["saiu"], str)
    assert (D(r["entrou"]), D(r["saiu"])) == (ENTROU, SAIU)


@pytest.mark.parametrize("mes", ["2026-13", "2026-00", "26-01", "2026-1", "abcd-ef", "0999-01",
                                 "２０２６-０１", "٢٠٢٦-٠١", "2026-01-01", "2026/01", ""])
def test_formato_invalido_e_422_no_envelope(libera, mes):
    (a,) = libera(usuario_pagante())
    r = pede(a, mes=mes)
    assert r.status_code == 422, r.text
    erro = r.json()["error"]
    assert erro["code"] == "validation_error" and erro["details"][0]["loc"] == ["query", "mes"]


def test_mes_futuro_e_422_e_o_corrente_passa(libera, monkeypatch):
    (a,) = libera(usuario_pagante())
    congela(monkeypatch, datetime(2026, 12, 31, 23, 59, tzinfo=SP))
    r = pede(a, mes="2027-01")
    assert r.status_code == 422 and r.json()["error"]["details"][0]["loc"] == ["query", "mes"]
    assert ok(a, mes="2026-12")["ate"] == "2026-12-31"


# `ate` é o último dia do mês também no corrente: a soma cobre o mês inteiro.
@pytest.mark.parametrize("instante, mes, ate, anterior", [
    # 23:30 em São Paulo já é dia 1 em UTC: o mês é o do app, não o do UTC
    (datetime(2026, 12, 1, 2, 30, tzinfo=timezone.utc), "2026-11", "2026-11-30", "2026-10"),
    (datetime(2027, 1, 1, 0, 0, 30, tzinfo=SP), "2027-01", "2027-01-31", "2026-12"),  # virada de ano
    (datetime(2026, 10, 1, 0, 5, tzinfo=SP), "2026-10", "2026-10-31", "2026-09"),     # virada de mês
])
def test_padrao_e_o_mes_corrente_do_app(libera, monkeypatch, instante, mes, ate, anterior):
    (a,) = libera(usuario_pagante())
    congela(monkeypatch, instante)
    r = ok(a)
    assert (r["mes"], r["ate"], r["anterior"]["mes"]) == (mes, ate, anterior)


def test_mes_passado_vai_ate_o_ultimo_dia(libera, monkeypatch):
    (a,) = libera(usuario_pagante())
    congela(monkeypatch, datetime(2026, 10, 15, 12, tzinfo=SP))
    r = ok(a, mes="2026-02")
    assert (r["ate"], r["anterior"]["mes"]) == ("2026-02-28", "2026-01")


def test_mes_sem_dados_da_zero(libera):
    (a,) = libera(usuario_pagante())
    r = ok(a, mes=M)
    assert (r["entrou"], r["saiu"], r["anterior"]) == (0, 0, {"mes": f"{INICIO - timedelta(days=1):%Y-%m}",
                                                               "entrou": 0, "saiu": 0})


# ── janela do plano (Q20) ───────────────────────────────────────────────────

def test_janela_que_corta_o_mes_marca_e_bate_com_o_app(libera, monkeypatch):
    (a,) = libera(usuario_pagante())
    semeia_a(a)
    corte = INICIO.replace(day=5)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda uid, now=None: corte)
    r = ok(a, mes=M)
    app = asyncio.run(dashboard.get_financial_data(a, year=INICIO.year, month=INICIO.month))
    assert (float(r["entrou"]), float(r["saiu"])) == (app["monthly_income"], app["monthly_expense"])
    assert (r["entrou"], r["saiu"]) == (0, D("360"))  # do dia 5 em diante: 70 + 30 + 30 + cartão 230
    assert "inicio_do_historico" in r["motivos"] and r["anterior"] is None
    antes = ok(a, mes=f"{INICIO - timedelta(days=1):%Y-%m}")  # mês inteiro antes da janela
    assert (antes["entrou"], antes["saiu"], antes["anterior"]) == (0, 0, None)
    assert "inicio_do_historico" in antes["motivos"]


@pytest.mark.parametrize("dias_apos_o_anterior, cortou", [(10, True), (0, False), (-40, False)])
def test_corte_so_no_anterior_marca_e_tira_o_anterior(libera, monkeypatch, dias_apos_o_anterior, cortou):
    """O corte dentro do mês anterior não toca o pedido, mas tira o `anterior`: o motivo
    diz por quê (senão `null` lê como "sem dados"). Corte no dia 1 dele ou antes: nada."""
    (a,) = libera(usuario_pagante())
    semeia_a(a)
    ant_inicio = (INICIO - timedelta(days=1)).replace(day=1)
    corte = ant_inicio + timedelta(days=dias_apos_o_anterior)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda uid, now=None: corte)
    r = ok(a, mes=M)
    assert (r["entrou"], r["saiu"]) == (ENTROU, SAIU)
    assert ("inicio_do_historico" in r["motivos"], r["anterior"] is None) == (cortou, cortou)


def test_sem_corte_nao_marca_e_traz_o_anterior(libera):
    (a,) = libera(usuario_pagante())
    semeia_a(a)
    r = ok(a, mes=M)
    assert "inicio_do_historico" not in r["motivos"] and r["anterior"] is not None


# ── motivos (os do bloco de contas, reusados) ───────────────────────────────

def test_motivos_do_bloco_de_contas(libera):
    limpo, pendente, velho, movimento = libera(*(usuario_pagante() for _ in range(4)))
    semeia_a(pendente)  # tem conciliação pendente
    conta(conexao(velho, f"item-{velho}", sync=horas_atras(72)), "acc-1", "10")
    conta(conexao(movimento, f"item-{movimento}"), "acc-1", "10")
    q("""insert into bank_movement_declarations (launch_id, user_id, amount, declared_at)
         values (%s, %s, -30, now())""", (lancamento(movimento), movimento))
    assert ok(limpo, mes=M)["motivos"] == []  # o `carteira_nao_confirmada` das contas não vaza
    assert ok(pendente, mes=M)["motivos"] == ["conciliacao_pendente"]
    assert ok(velho, mes=M)["motivos"] == ["banco_desatualizado"]
    assert ok(movimento, mes=M)["motivos"] == ["movimentos_pendentes"]


def test_mes_corrente_soma_a_fatura_que_fecha_depois_de_hoje(libera, monkeypatch):
    """Mês-calendário inteiro, como o /app: a compra de hoje numa fatura que fecha no fim
    do mês (dia 31 → último dia) entra no mês corrente."""
    (a,) = libera(usuario_pagante())
    hoje = date.today()
    cartao = db.create_card(a, "Nubank", closing_day=31, due_day=10)
    db.add_credit_purchase(a, cartao, 45, "mercado", "hoje", hoje)
    congela(monkeypatch, datetime.combine(hoje, datetime.min.time(), tzinfo=SP) + timedelta(hours=12))
    assert ok(a)["saiu"] == D("45")
