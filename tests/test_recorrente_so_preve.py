"""Q42 — recorrente só prevê (docs/plano-dashboard-v2.md §2 e §6).

Um tick do loop REAL de produção (`run_recurring_charger_loop`, o mesmo que o
startup sobe) não lança nada: nem receita na carteira, nem gasto fixo em conta,
nem gasto fixo no cartão. O recorrente continua na Previsão, e a conta a pagar
continua gerando a instância de onde sai o lembrete de vencimento.

Datas à prova de relógio: dia 1 e início no dia 1 do mês anterior — o cobrador
antigo lançava isso em qualquer dia do mês. No dia 1 o tick grava o AVISO de
vencimento do autopay em `recurring_charges` (sem lançamento); por isso `_estado`
conta só as linhas com lançamento (`launch_id`/`credit_tx_id`).

Aviso de vencimento do autopay (adendo do Q42): no dia do vencimento, o tick
grava uma linha em `recurring_charges` com `launch_id` e `credit_tx_id` nulos, e
o banner do dashboard a mostra como "dia de débito no banco" (`launched:false`).
Controles NEGATIVOS (medidos): sem a chamada no loop → o caso do tick fica
vermelho; chave sempre `YYYY-MM` → o semanal de duas semanas fica vermelho; sem
o pulo do diário → o caso diário fica vermelho; sem `launched` no select do
alerta → o caso do payload fica vermelho.

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
import frontend.finance_bot_websocket_custom as dashboard


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
    lancou = "launch_id is not null"
    if tabela_postagens == "recurring_charges":
        lancou += " or credit_tx_id is not null"
    return (
        float(db.get_balance(uid)),
        _conta("select count(*) from launches where user_id=%s", uid),
        _conta(f"select count(*) from {tabela_postagens} where user_id=%s and ({lancou})", uid),
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
    assert _conta("select count(*) from recurring_charges where user_id=%s", user_id) == 0


# ── Aviso de vencimento do autopay ────────────────────────────────────────────

def _avisos(uid: int) -> list[tuple]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select amount, ym, launch_id, credit_tx_id from recurring_charges "
                    "where user_id=%s order by ym", (uid,))
        return [(float(r["amount"]), r["ym"], r["launch_id"], r["credit_tx_id"]) for r in cur.fetchall()]


def test_tick_grava_o_aviso_do_autopay_que_vence_hoje_sem_lancar(user_id, monkeypatch):
    hoje = date.today()
    create_recurring_expense(user_id, "Internet Q42", 99.9, "moradia", hoje.day, "account",
                             start_date=hoje)
    antes, prev_antes = _estado(user_id, "recurring_charges"), forecast_with_trajectory(user_id, 90)
    assert _avisos(user_id) == []

    _um_tick(monkeypatch)
    _um_tick(monkeypatch)  # idempotente: o segundo tick não duplica

    assert _avisos(user_id) == [(99.9, hoje.strftime("%Y-%m"), None, None)]
    assert _estado(user_id, "recurring_charges") == antes
    assert forecast_with_trajectory(user_id, 90) == prev_antes


def _cria(uid, freq, start, due_day=1, due_month=None, payment_type="account", card_id=None):
    return create_recurring_expense(uid, f"Rec {freq}", 50.0, "outros", due_day, payment_type,
                                    card_id=card_id, start_date=start, frequency=freq,
                                    due_month=due_month)


@pytest.mark.parametrize("freq,start,due_day,dias,esperado", [
    ("weekly", date(2026, 9, 2), 1, [date(2026, 9, 2), date(2026, 9, 9)], ["w:2026-09-02", "w:2026-09-09"]),
    ("weekly", date(2026, 9, 2), 1, [date(2026, 9, 5)], []),
    ("once", date(2026, 9, 10), 1, [date(2026, 9, 10)], ["o:2026-09-10"]),
    ("once", date(2026, 9, 10), 1, [date(2026, 9, 11)], []),
    ("monthly", date(2026, 9, 1), 31, [date(2026, 9, 29)], []),
    ("monthly", date(2026, 9, 1), 31, [date(2026, 9, 30)], ["2026-09"]),  # 31 clampado
    ("annual", date(2026, 1, 1), 15, [date(2026, 9, 15), date(2026, 10, 15)], ["2026-09"]),
    ("daily", date(2026, 9, 1), 1, [date(2026, 9, 10)], []),
])
def test_aviso_so_no_dia_do_vencimento(user_id, freq, start, due_day, dias, esperado):
    _cria(user_id, freq, start, due_day=due_day, due_month=9 if freq == "annual" else None)
    for d in dias:
        rc.sync_autopay_notices_once(today=d)
    assert [ym for _, ym, _, _ in _avisos(user_id)] == esperado


def test_aviso_no_cartao_e_inativo_e_manual(user_id):
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    _cria(user_id, "monthly", date(2026, 9, 1), due_day=15, payment_type="credit_card", card_id=card_id)
    inativo = _cria(user_id, "monthly", date(2026, 9, 1), due_day=15)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update recurring_expenses set is_active=false where id=%s and user_id=%s",
                    (inativo["id"], user_id))
        conn.commit()
    create_recurring_expense(user_id, "Luz", 80.0, "moradia", 15, "account",
                             start_date=date(2026, 9, 1), payment_mode="manual")

    rc.sync_autopay_notices_once(today=date(2026, 9, 15))

    assert _avisos(user_id) == [(50.0, "2026-09", None, None)]  # só o do cartão


def _alertas(uid: int) -> list[dict]:
    hoje = date.today()
    d = asyncio.run(dashboard.get_financial_data(uid, year=hoje.year, month=hoje.month))
    return [a for a in d["alerts"] if a["type"] == "recurring_charged"]


def test_payload_do_alerta_separa_aviso_de_lancado_e_isola_usuario(user_id):
    from db.recurring import ensure_autopay_notice
    from db.users import ensure_user

    aviso = create_recurring_expense(user_id, "Aviso Q42", 10.0, "outros", 1, "account")
    antigo = create_recurring_expense(user_id, "Antigo Q42", 20.0, "outros", 1, "account")
    ensure_autopay_notice(aviso["id"], user_id, 10.0, "2026-09")
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into launches (user_id, tipo, valor, categoria, nota) "
                    "values (%s, 'despesa', 20, 'outros', 'x') returning id", (user_id,))
        lid = cur.fetchone()["id"]
        cur.execute("insert into recurring_charges (recurring_id, user_id, launch_id, amount, ym) "
                    "values (%s, %s, %s, 20, '2026-09')", (antigo["id"], user_id, lid))
        conn.commit()
    outro = user_id + 1
    ensure_user(outro)
    alheio = create_recurring_expense(outro, "Alheio Q42", 30.0, "outros", 1, "account")
    ensure_autopay_notice(alheio["id"], outro, 30.0, "2026-09")

    assert sorted((a["name"], a["launched"]) for a in _alertas(user_id)) == [
        ("Antigo Q42", True), ("Aviso Q42", False)]
    assert [a["name"] for a in _alertas(outro)] == ["Alheio Q42"]
