"""Q42 — recorrente só prevê (docs/plano-dashboard-v2.md §2 e §6).

Um tick do loop REAL de produção (`run_recurring_charger_loop`, o mesmo que o
startup sobe) não lança nada: nem receita na carteira, nem gasto fixo em conta,
nem gasto fixo no cartão. O recorrente continua na Previsão, e a conta a pagar
continua gerando a instância de onde sai o lembrete de vencimento.

Datas à prova de relógio: dia 1 e início no dia 1 do mês anterior — o cobrador
antigo lançava isso em qualquer dia do mês.

Controle NEGATIVO (medido): com `core/services/recurring_charger.py` da
`origin/main` 7b2f8da (o cobrador antigo) no lugar do atual, ficam vermelhos os
casos sem linha antiga do salário e da conta, e o do cartão; o lembrete (positivo)
segue verde. Os casos com linha antiga do mês ficam verdes também com o antigo —
ele já os pulava pela idempotência; estão aqui para provar que a linha antiga
não tira o recorrente da previsão.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

import core.services.recurring_charger as rc
import db
from core.services.cashflow_forecast import forecast_with_trajectory
from db.bills import list_due_bill_reminders
from db.recurring import create_recurring_expense
from db.recurring_income import create_recurring_income


class _FimDoTick(Exception):
    pass


def _um_tick(monkeypatch) -> None:
    async def sleep_falso(segundos, *a, **k):
        if segundos >= 3600:
            raise _FimDoTick

    monkeypatch.setattr(rc.asyncio, "sleep", sleep_falso)
    with pytest.raises(_FimDoTick):
        asyncio.run(rc.run_recurring_charger_loop())


def _inicio_mes_anterior() -> date:
    hoje = date.today()
    return date(hoje.year - (hoje.month == 1), (hoje.month - 2) % 12 + 1, 1)


def _conta(sql: str, uid: int) -> float:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (uid,))
        return float(next(iter(cur.fetchone().values())) or 0)


def _estado(uid: int, tabela_postagens: str) -> tuple:
    return (
        float(db.get_balance(uid)),
        _conta("select count(*) from launches where user_id=%s", uid),
        _conta(f"select count(*) from {tabela_postagens} where user_id=%s", uid),
        _conta("select count(*) from credit_transactions where user_id=%s", uid),
        _conta("select coalesce(sum(total), 0) from credit_bills where user_id=%s", uid),
    )


def _linha_antiga(tabela: str, fk: str, rec_id: int, uid: int, coluna_ym: str, tabela_rec: str) -> None:
    """O que o cobrador antigo deixou no mês corrente: a postagem e a marca de idempotência."""
    ym = date.today().strftime("%Y-%m")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"insert into {tabela} ({fk}, user_id, amount, ym) values (%s, %s, 1, %s)",
                    (rec_id, uid, ym))
        cur.execute(f"update {tabela_rec} set {coluna_ym}=%s where user_id=%s and id=%s",
                    (ym, uid, rec_id))
        conn.commit()


def _compromissos(uid: int, tipo: str) -> set[str]:
    out = forecast_with_trajectory(uid, days=90)
    return {c["nome"] for item in out["trajectory"] for c in item["compromissos"] if c["tipo"] == tipo}


@pytest.mark.parametrize("linha_antiga", [False, True])
def test_salario_recorrente_sem_banco_nao_e_lancado_e_segue_na_previsao(user_id, monkeypatch, linha_antiga):
    inc = create_recurring_income(user_id, "Salário Q42", 3000.0, "salário", 1,
                                  start_date=_inicio_mes_anterior())
    if linha_antiga:
        _linha_antiga("recurring_income_credits", "income_id", inc["id"], user_id,
                      "last_credited_ym", "recurring_incomes")
    antes = _estado(user_id, "recurring_income_credits")

    _um_tick(monkeypatch)

    assert _estado(user_id, "recurring_income_credits") == antes
    assert "Salário Q42" in _compromissos(user_id, "receita")


@pytest.mark.parametrize("linha_antiga", [False, True])
def test_conta_recorrente_sem_banco_nao_e_lancada_e_segue_na_previsao(user_id, monkeypatch, linha_antiga):
    rec = create_recurring_expense(user_id, "Aluguel Q42", 1200.0, "moradia", 1, "account",
                                   start_date=_inicio_mes_anterior())
    if linha_antiga:
        _linha_antiga("recurring_charges", "recurring_id", rec["id"], user_id,
                      "last_charged_ym", "recurring_expenses")
    antes = _estado(user_id, "recurring_charges")

    _um_tick(monkeypatch)

    assert _estado(user_id, "recurring_charges") == antes
    assert "Aluguel Q42" in _compromissos(user_id, "gasto_fixo")


def test_gasto_fixo_no_cartao_manual_nao_entra_na_fatura(user_id, monkeypatch):
    """P1 = (a): o ramo do cartão também parou. Fatura aberta existe (o cobrador
    antigo a exigia), e nada entra nela."""
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.get_or_create_open_bill(user_id, card_id, date.today())
    create_recurring_expense(user_id, "Streaming Q42", 39.9, "assinaturas", 1, "credit_card",
                             card_id=card_id, start_date=_inicio_mes_anterior())
    antes = _estado(user_id, "recurring_charges")

    _um_tick(monkeypatch)

    assert _estado(user_id, "recurring_charges") == antes


def test_conta_a_pagar_continua_gerando_o_lembrete(user_id, monkeypatch):
    """POSITIVO: o mesmo tick ainda gera a instância da conta a pagar. Sem isto,
    os testes acima passariam com o loop inteiro desligado — e o lembrete morto."""
    hoje = date.today()
    rec = create_recurring_expense(user_id, "Luz Q42", 150.0, "moradia", hoje.day, "account",
                                   start_date=hoje, payment_mode="manual")
    assert list_due_bill_reminders(user_id, hoje) == []

    _um_tick(monkeypatch)

    lembretes = list_due_bill_reminders(user_id, hoje)
    assert [(r["recurring_id"], r["name"]) for r in lembretes] == [(rec["id"], "Luz Q42")]
