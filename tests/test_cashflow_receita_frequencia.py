"""Receita recorrente única/semanal/diária na previsão de saldo (feature Pro).

O cobrador (`core/services/recurring_charger.py`) só credita receita mensal e
anual, mas a previsão contava as outras três como mensais: uma receita única de
R$5.000 somava R$15.000 em 90 dias. O conserto tem duas pontas, e as duas são
medidas aqui pelas rotas de produção, com Postgres real:

* a previsão ignora receita que não seja mensal/anual (registros legados);
* o cadastro (POST/PATCH `/recurring-incomes`) recusa essas frequências.

Registro legado = nasce mensal pela rota e tem a frequência forçada por SQL, o
único jeito de existir depois que o cadastro passou a recusar.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fastapi import HTTPException

import core.services.cashflow as cf
import core.services.cashflow_forecast as cff
import db
import frontend.finance_bot_websocket_custom as dashboard
from db.recurring import create_recurring_expense
from db.recurring_income import count_active_recurring_incomes, get_recurring_income

HOJE = date(2026, 9, 16)
ERRO_FREQ = "Frequência inválida (use 'monthly' ou 'annual')."
RECUSADAS = ["weekly", "daily", "once", "WEEKLY", " once "]


@pytest.fixture(autouse=True)
def _relogio_e_rotas(monkeypatch):
    class _Hoje(date):
        @classmethod
        def today(cls):
            return HOJE

    monkeypatch.setattr(cf, "date", _Hoje)
    # A rota /forecast lê o dia em cashflow_forecast (#445); sem isto o teste só
    # passa quando o relógio real coincide com HOJE.
    monkeypatch.setattr(cff, "date", _Hoje)
    monkeypatch.setattr(dashboard, "_authorize_dashboard_access", lambda *_: None)


def _cria_receita(uid: int, amount: float, pay_day: int, start_date: str = "2026-09-16", **kw) -> int:
    payload = dashboard.RecurringIncomeCreatePayload(
        name="Receita", amount=amount, category="salário", pay_day=pay_day,
        start_date=start_date, **kw,
    )
    out = asyncio.run(dashboard.recurring_income_create_route(request=None, user_id=uid, payload=payload))
    return int(out["income"]["id"])


def _edita_receita(uid: int, inc_id: int, **kw) -> None:
    payload = dashboard.RecurringIncomeUpdatePayload(**kw)
    asyncio.run(dashboard.recurring_income_update_route(request=None, user_id=uid, inc_id=inc_id, payload=payload))


def _receita_legada(uid: int, freq: str, amount: float, pay_day: int, start_date: str) -> int:
    inc_id = _cria_receita(uid, amount, pay_day, start_date=start_date)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update recurring_incomes set frequency=%s, pay_month=null where user_id=%s and id=%s",
                (freq, uid, inc_id),
            )
        conn.commit()
    return inc_id


def _previsao(uid: int, chave: str) -> tuple[float, float, float]:
    out = asyncio.run(dashboard.forecast_route(request=None, user_id=uid))
    # Se a rota passar a ler a data em outro módulo, falha aqui sempre, não só em certas datas.
    assert out["forecast"]["today"] == HOJE.isoformat()
    hz = out["forecast"]["horizons"]
    return tuple(hz[n][chave] for n in ("30", "60", "90"))


@pytest.mark.parametrize("freq, amount, pay_day, start", [
    ("once", 5000, 10, "2026-10-10"),   # no bug: 5.000 / 10.000 / 15.000
    ("weekly", 300, 17, "2026-09-17"),  # no bug: 300 / 600 / 900
    ("daily", 50, 17, "2026-09-17"),    # no bug: 50 / 100 / 150
])
def test_receita_legada_fora_de_mensal_anual_nao_entra_na_previsao(pro_user_id, freq, amount, pay_day, start):
    _receita_legada(pro_user_id, freq, amount, pay_day, start)

    assert _previsao(pro_user_id, "receitas_previstas") == (0, 0, 0)
    assert _previsao(pro_user_id, "projetado") == (0, 0, 0)


@pytest.mark.parametrize("kw, esperado", [
    (dict(amount=2000, pay_day=5), (2000, 4000, 6000)),
    (dict(amount=1200, pay_day=20, frequency="annual", pay_month=10), (0, 1200, 1200)),
])
def test_receita_mensal_e_anual_continuam_na_previsao(pro_user_id, kw, esperado):
    """POSITIVO: o conserto não pode zerar o caminho legítimo."""
    _cria_receita(pro_user_id, **kw)

    assert _previsao(pro_user_id, "receitas_previstas") == esperado
    assert _previsao(pro_user_id, "projetado") == esperado


def test_gasto_fixo_mensal_continua_e_semanal_segue_aceito_e_fora(pro_user_id):
    """POSITIVO: a regra mora num helper que os gastos fixos também usam. Gasto
    fixo semanal continua aceito no cadastro (o cobrador o debita; fica fora
    deste conserto) e continua fora da previsão, como já era."""
    create_recurring_expense(pro_user_id, "Aluguel", 500, "outros", 20, "account", start_date=HOJE)
    semanal = create_recurring_expense(pro_user_id, "Feira", 80, "outros", 20, "account",
                                       start_date=HOJE, frequency="weekly")

    assert semanal["frequency"] == "weekly"
    assert _previsao(pro_user_id, "gastos_fixos_previstos") == (500, 1000, 1500)
    assert _previsao(pro_user_id, "projetado") == (-500, -1000, -1500)


def test_cenario_combinado_so_a_mensal_soma(pro_user_id):
    _cria_receita(pro_user_id, 2000, 5)
    _receita_legada(pro_user_id, "once", 5000, 10, "2026-10-10")
    _receita_legada(pro_user_id, "weekly", 300, 17, "2026-09-17")

    assert _previsao(pro_user_id, "receitas_previstas") == (2000, 4000, 6000)


@pytest.mark.parametrize("freq", RECUSADAS)
def test_cadastro_recusa_frequencia_fora_de_mensal_anual(pro_user_id, freq):
    with pytest.raises(HTTPException) as exc:
        _cria_receita(pro_user_id, 300, 17, frequency=freq)

    assert (exc.value.status_code, exc.value.detail) == (400, ERRO_FREQ)
    assert count_active_recurring_incomes(pro_user_id) == 0


@pytest.mark.parametrize("freq", RECUSADAS)
def test_edicao_recusa_frequencia_fora_de_mensal_anual(pro_user_id, freq):
    inc_id = _cria_receita(pro_user_id, 2000, 5)

    with pytest.raises(HTTPException) as exc:
        _edita_receita(pro_user_id, inc_id, frequency=freq)

    assert (exc.value.status_code, exc.value.detail) == (400, ERRO_FREQ)
    assert get_recurring_income(pro_user_id, inc_id)["frequency"] == "monthly"


def test_edicao_aceita_anual_e_o_patch_vazio_do_modal(pro_user_id):
    """POSITIVO. PATCH mensal→anual grava. E o PATCH que o modal manda ao salvar
    uma receita legada (`frequency=""`: o `<select id="recurring-income-frequency">`
    só tem mensal/anual, então o `.value` sai vazio) continua gravando o que o
    usuário editou, sem 400. O modal diz "atualizada" com qualquer 200.

    NÃO é garantia deste conserto o que esse PATCH faz com a frequência do legado:
    ele o converte em mensal, e um `once` editado passa a ser creditado todo mês.
    Comportamento PREEXISTENTE, registrado para correção separada; por isso não é
    afirmado aqui."""
    mensal = _cria_receita(pro_user_id, 2000, 5)
    legada = _receita_legada(pro_user_id, "once", 5000, 10, "2026-10-10")

    _edita_receita(pro_user_id, mensal, frequency="annual", pay_month=3)
    # corpo inteiro de `saveRecurringIncome` (frontend/dashboard.js)
    _edita_receita(pro_user_id, legada, name="Bônus ajustado", amount=5500, category="salário",
                   pay_day=10, start_date="2026-10-10", frequency="", pay_month=None,
                   is_primary=False, notes=None)

    rec = get_recurring_income(pro_user_id, mensal)
    assert (rec["frequency"], rec["pay_month"]) == ("annual", 3)
    rec = get_recurring_income(pro_user_id, legada)
    assert (rec["name"], rec["amount"]) == ("Bônus ajustado", 5500.0)
