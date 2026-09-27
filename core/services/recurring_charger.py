"""
core/services/recurring_charger.py — Contas a pagar e avisos de vencimento dos recorrentes.

Roda como background task no startup. A cada hora, `sync_manual_bills_once`
cria as `bill_instances` do próximo ciclo dos recorrentes `payment_mode='manual'`
(é delas que sai o lembrete de vencimento), e `sync_autopay_notices_once` grava
em `recurring_charges`, SEM lançamento, o aviso do autopay que vence hoje (o
banner do dashboard mostra "dia de débito no banco"). Não debita nada.

Recorrente só PREVÊ (docs/plano-dashboard-v2.md, Q42): gasto fixo e receita
recorrente entram na Previsão (`core/services/cashflow.py`) e nunca são lançados
sozinhos — o dinheiro de verdade vem do Open Finance ou do lançamento do usuário.
O cobrador que lançava gasto fixo (conta e cartão) e creditava receita foi
removido; as linhas que ele deixou em `recurring_charges` /
`recurring_income_credits` ficam como histórico (Q37).
"""
from __future__ import annotations

import asyncio
import calendar
import sys
import traceback
from datetime import date, timedelta


async def run_recurring_charger_loop():
    """Loop infinito: a cada hora, contas a pagar e avisos de vencimento do autopay."""
    while True:
        try:
            await asyncio.sleep(5)  # delay inicial pra não pegar startup
            # Contas a pagar (manual): gera as instâncias do ciclo. NÃO debita —
            # só cria a pendência; o lembrete sai pelo report diário e o
            # pagamento é confirmado pelo user.
            await asyncio.to_thread(sync_manual_bills_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[bills] erro: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        try:
            await asyncio.to_thread(sync_autopay_notices_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[autopay_notice] erro: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        await asyncio.sleep(60 * 60)  # 1 hora entre verificações


def _cycle_due_date(rec: dict, today: date) -> date | None:
    """PRÓXIMO vencimento de um recorrente manual, em/após a âncora
    (max(hoje, start_date)). Mensal → próxima ocorrência de due_day; anual →
    próxima ocorrência de due_day/due_month (neste ano ou no próximo). due_day é
    clampado ao último dia do mês. None se dados inválidos.

    Antes calculava só o vencimento DO MÊS ATUAL — quando o dia já tinha passado
    (ou start_date era hoje/futuro) ele caía no passado e a instância nunca era
    gerada, deixando a conta invisível. Agora rola pro próximo ciclo."""
    anchor = today
    start = rec.get("start_date")
    if start and start > anchor:
        anchor = start

    freq = (rec.get("frequency") or "monthly")

    # ── Frequências ancoradas no start_date (não usam due_day) ───────────
    if freq == "once":
        # pagamento único: vence na data escolhida (start_date). Retorna essa
        # data (a instância é gerada uma vez; idempotente).
        return start or today
    if freq == "daily":
        # todo dia: o próximo vencimento é a própria âncora (hoje ou o início).
        return anchor
    if freq == "weekly":
        # a cada 7 dias a partir do start_date. Próxima ocorrência >= âncora.
        base = start or today
        if anchor <= base:
            return base
        delta = (anchor - base).days
        bumps = (delta + 6) // 7  # arredonda pra cima em semanas
        return base + timedelta(days=7 * bumps)

    # ── Mensal / anual: dependem de due_day (1-31) ───────────────────────
    try:
        due_day = int(rec.get("due_day") or 0)
    except (TypeError, ValueError):
        return None
    if not (1 <= due_day <= 31):
        return None

    if freq == "annual":
        month = rec.get("due_month")
        try:
            month = int(month)
        except (TypeError, ValueError):
            return None
        if not (1 <= month <= 12):
            return None
        for year in (anchor.year, anchor.year + 1):
            dim = calendar.monthrange(year, month)[1]
            cand = date(year, month, min(due_day, dim))
            if cand >= anchor:
                return cand
        return None

    # mensal: primeira ocorrência de due_day em/após a âncora (este mês ou o próximo)
    y, m = anchor.year, anchor.month
    for _ in range(2):
        dim = calendar.monthrange(y, m)[1]
        cand = date(y, m, min(due_day, dim))
        if cand >= anchor:
            return cand
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return None


def sync_manual_bills_once(today: date | None = None, user_id: int | None = None) -> int:
    """Gera as instâncias (bill_instances) do próximo ciclo pros recorrentes
    'manual', respeitando start_date. Idempotente (UNIQUE recurring_id+due_date).
    `user_id` opcional restringe a um usuário (sync sob demanda ao abrir a lista).
    Retorna quantas instâncias novas foram garantidas."""
    from db.bills import list_active_manual_recurrings, ensure_bill_instance

    today = today or date.today()
    n = 0
    for rec in list_active_manual_recurrings(user_id=user_id):
        due = _cycle_due_date(rec, today)  # já respeita start_date (âncora)
        if due is None:
            continue
        try:
            ensure_bill_instance(int(rec["id"]), int(rec["user_id"]), due, float(rec["amount"]))
            n += 1
        except Exception as exc:
            print(f"[bills] falhou gerar instancia rec={rec.get('id')}: {exc}", file=sys.stderr)
    return n


def sync_autopay_notices_once(today: date | None = None) -> int:
    """Grava o aviso de vencimento de cada gasto fixo autopay que vence HOJE
    (conta ou cartão). Não lança nada. Diário não avisa (seria todo dia). A data
    sai de `_recurring_occurrence_dates`, a mesma da Previsão. A chave é a do
    cobrador antigo (mensal/anual `YYYY-MM`, semanal `w:`, único `o:`), então o
    período que ele já lançou não ganha aviso duplicado. Retorna quantos criou."""
    from core.services.cashflow import _recurring_occurrence_dates
    from db.recurring import ensure_autopay_notice, list_active_autopay_recurrings

    today = today or date.today()
    n = 0
    for rec in list_active_autopay_recurrings():
        freq = rec.get("frequency") or "monthly"
        if freq == "daily":
            continue
        if not _recurring_occurrence_dates(rec.get("due_day"), freq, rec.get("due_month"),
                                           rec.get("start_date"), today - timedelta(days=1), today):
            continue
        key = {"weekly": f"w:{today.isoformat()}",
               "once": f"o:{today.isoformat()}"}.get(freq, today.strftime("%Y-%m"))
        try:
            n += ensure_autopay_notice(int(rec["id"]), int(rec["user_id"]), float(rec["amount"]), key)
        except Exception as exc:
            print(f"[autopay_notice] falhou rec={rec.get('id')}: {exc}", file=sys.stderr)
    return n


__all__ = [
    "run_recurring_charger_loop",
    "sync_manual_bills_once",
    "sync_autopay_notices_once",
]
