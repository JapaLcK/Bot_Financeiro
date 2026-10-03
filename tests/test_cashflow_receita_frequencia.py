"""Frequência dos recorrentes na previsão, e cadastro e edição da receita (feature Pro).

Receita só aceita mensal e anual, mas a previsão contava as outras três como
mensais: uma receita única de R$5.000 somava R$15.000 em 90 dias. Gasto fixo aceita
as cinco frequências e, desde a Q42 (recorrente só prevê), todas entram na previsão
na data de cada ocorrência. Medido aqui pelas rotas de produção, com Postgres real:

* a previsão ignora receita que não seja mensal/anual (registros legados);
* o cadastro (POST/PATCH `/recurring-incomes`) recusa essas frequências;
* a edição pelo modal não converte o legado em mensal (issue #454, item 1). O
  `<select id="recurring-income-frequency">` só tem mensal/anual, então o modal
  manda `frequency=""` para o legado, e o PATCH antes gravava "monthly": o bônus
  único passava a ser creditado todo mês. Agora frequência vazia na edição
  significa "não mudar" (`update_recurring_income`).

Controle NEGATIVO (medido): volte `if frequency is not None and
str(frequency).strip():` para `if frequency is not None:` em
`db/recurring_income.py`. Ficam vermelhos o `test_patch_do_modal_mantem_*`, o
`test_edicao_aceita_anual_*` e os dois casos de `test_frequencia_vazia_*` (a anual
vira mensal).

Controle NEGATIVO da Q42 (medido): reponha em `_cashflow_events` o filtro
`if (e.get("frequency") or "monthly") not in ("monthly", "annual"): continue` dos
gastos fixos. Ficam vermelhos os três casos de `test_gasto_fixo_*_entra_na_previsao`,
o `test_gasto_fixo_semanal_com_inicio_futuro_*` e o `test_gasto_fixo_mensal_e_semanal_*`;
as bordas (`test_gasto_fixo_unico_fora_da_janela_*`) seguem verdes.

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


def _patch_do_modal(uid: int, inc_id: int, frequency: str) -> None:
    """Corpo inteiro de `saveRecurringIncome` (frontend/dashboard.js)."""
    _edita_receita(uid, inc_id, name="Bônus ajustado", amount=5000, category="salário",
                   pay_day=10, start_date="2026-10-10", frequency=frequency, pay_month=None,
                   is_primary=False, notes=None)


@pytest.fixture()
def pro_max_uid():
    """60/90 dias da previsão são do Pro (`pro_max`); o Plus só vê 30."""
    from conftest import usuario_pagante
    return usuario_pagante("pro_max")


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
def test_receita_legada_fora_de_mensal_anual_nao_entra_na_previsao(pro_max_uid, freq, amount, pay_day, start):
    _receita_legada(pro_max_uid, freq, amount, pay_day, start)

    assert _previsao(pro_max_uid, "receitas_previstas") == (0, 0, 0)
    assert _previsao(pro_max_uid, "projetado") == (0, 0, 0)


@pytest.mark.parametrize("kw, esperado", [
    (dict(amount=2000, pay_day=5), (2000, 4000, 6000)),
    (dict(amount=1200, pay_day=20, frequency="annual", pay_month=10), (0, 1200, 1200)),
])
def test_receita_mensal_e_anual_continuam_na_previsao(pro_max_uid, kw, esperado):
    """POSITIVO: o conserto não pode zerar o caminho legítimo."""
    _cria_receita(pro_max_uid, **kw)

    assert _previsao(pro_max_uid, "receitas_previstas") == esperado
    assert _previsao(pro_max_uid, "projetado") == esperado


def _gasto_fixo(uid: int, freq: str, amount: float, start: date, nome: str = "Gasto") -> None:
    create_recurring_expense(uid, nome, amount, "outros", start.day, "account",
                             start_date=start, frequency=freq)


# Janelas: 30 → 16/10, 60 → 15/11, 90 → 15/12 (ocorrências em (16/09, alvo]).
@pytest.mark.parametrize("freq, amount, start, esperado", [
    ("weekly", 80, HOJE, (320, 640, 960)),                # 23/09…14/10 · …11/11 · …09/12
    ("daily", 10, HOJE, (300, 600, 900)),                 # 17/09 em diante, um por dia
    ("once", 5000, date(2026, 10, 10), (5000, 5000, 5000)),
])
def test_gasto_fixo_fora_de_mensal_anual_entra_na_previsao(pro_max_uid, freq, amount, start, esperado):
    """Q42 (P2): gasto fixo semanal/diário/único entra na previsão. Sem isto
    (filtro de volta), os três casos leem (0, 0, 0)."""
    _gasto_fixo(pro_max_uid, freq, amount, start)

    assert _previsao(pro_max_uid, "gastos_fixos_previstos") == esperado
    assert _previsao(pro_max_uid, "projetado") == tuple(-v for v in esperado)


def test_gasto_fixo_mensal_e_semanal_somam_juntos(pro_max_uid):
    """POSITIVO: o mensal segue como era (500/1000/1500) e o semanal entra junto."""
    create_recurring_expense(pro_max_uid, "Aluguel", 500, "outros", 20, "account", start_date=HOJE)
    semanal = create_recurring_expense(pro_max_uid, "Feira", 80, "outros", 20, "account",
                                       start_date=HOJE, frequency="weekly")

    assert semanal["frequency"] == "weekly"
    assert _previsao(pro_max_uid, "gastos_fixos_previstos") == (820, 1640, 2460)
    assert _previsao(pro_max_uid, "projetado") == (-820, -1640, -2460)


def test_gasto_fixo_semanal_com_inicio_futuro_so_conta_a_partir_do_inicio(pro_max_uid):
    """01/10 em diante: 01, 08, 15/10 · + 22, 29/10, 05, 12/11 · + 19, 26/11, 03, 10/12."""
    _gasto_fixo(pro_max_uid, "weekly", 80, date(2026, 10, 1))

    assert _previsao(pro_max_uid, "gastos_fixos_previstos") == (240, 560, 880)


@pytest.mark.parametrize("start", [
    date(2026, 9, 10),   # passado: não vira "vencido" nem pesa no saldo de partida
    HOJE,                # hoje: a previsão conta de amanhã em diante
    date(2027, 1, 10),   # além da janela de 90 dias
])
def test_gasto_fixo_unico_fora_da_janela_nao_entra(pro_max_uid, start):
    _gasto_fixo(pro_max_uid, "once", 5000, start, nome="Único")

    assert _previsao(pro_max_uid, "gastos_fixos_previstos") == (0, 0, 0)
    out = cff.forecast_with_trajectory(pro_max_uid, days=90)
    nomes = {c["nome"] for c in out["vencidos"] + out["vencem_hoje"]}
    nomes |= {c["nome"] for item in out["trajectory"] for c in item["compromissos"]}
    assert "Único" not in nomes
    assert out["trajectory"][0]["saldo_projetado"] == 0  # saldo de partida intacto
    assert _previsao(pro_max_uid, "projetado") == (0, 0, 0)


def test_cenario_combinado_so_a_mensal_soma(pro_max_uid):
    _cria_receita(pro_max_uid, 2000, 5)
    _receita_legada(pro_max_uid, "once", 5000, 10, "2026-10-10")
    _receita_legada(pro_max_uid, "weekly", 300, 17, "2026-09-17")

    assert _previsao(pro_max_uid, "receitas_previstas") == (2000, 4000, 6000)


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
    uma receita legada (`frequency=""`) grava o que o usuário editou, sem 400, e
    mantém a frequência do legado."""
    mensal = _cria_receita(pro_user_id, 2000, 5)
    legada = _receita_legada(pro_user_id, "once", 4500, 10, "2026-10-10")

    _edita_receita(pro_user_id, mensal, frequency="annual", pay_month=3)
    _patch_do_modal(pro_user_id, legada, "")

    rec = get_recurring_income(pro_user_id, mensal)
    assert (rec["frequency"], rec["pay_month"]) == ("annual", 3)
    rec = get_recurring_income(pro_user_id, legada)
    assert (rec["name"], rec["amount"], rec["frequency"]) == ("Bônus ajustado", 5000.0, "once")


@pytest.mark.parametrize("vazio", ["", "  "])
@pytest.mark.parametrize("freq", ["once", "weekly", "daily"])
def test_patch_do_modal_mantem_frequencia_do_legado(pro_user_id, freq, vazio):
    inc_id = _receita_legada(pro_user_id, freq, 300, 10, "2026-10-10")

    _patch_do_modal(pro_user_id, inc_id, vazio)

    rec = get_recurring_income(pro_user_id, inc_id)
    assert (rec["frequency"], rec["pay_month"]) == (freq, None)
    assert (rec["name"], rec["amount"]) == ("Bônus ajustado", 5000.0)


@pytest.mark.parametrize("pay_month, esperado", [(None, ("annual", 3)), (5, ("annual", 5))])
def test_frequencia_vazia_mantem_anual_e_mensal(pro_user_id, pay_month, esperado):
    """POSITIVO + mudança de contrato: `frequency=""` numa anual antes virava mensal.
    Com `pay_month` preenchido, só o mês muda (o `elif` de `update_recurring_income`)."""
    anual = _cria_receita(pro_user_id, 1200, 20, frequency="annual", pay_month=3)
    mensal = _cria_receita(pro_user_id, 2000, 5)

    _edita_receita(pro_user_id, anual, frequency="", pay_month=pay_month)
    _edita_receita(pro_user_id, mensal, name="Salário novo")

    rec = get_recurring_income(pro_user_id, anual)
    assert (rec["frequency"], rec["pay_month"]) == esperado
    rec = get_recurring_income(pro_user_id, mensal)
    assert (rec["name"], rec["frequency"], rec["pay_month"]) == ("Salário novo", "monthly", None)
