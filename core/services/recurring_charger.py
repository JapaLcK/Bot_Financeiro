"""
core/services/recurring_charger.py — Contas a pagar e avisos de vencimento dos recorrentes.

Roda como background task no startup. A cada hora, `sync_manual_bills_once`
cria as `bill_instances` do próximo ciclo dos recorrentes `payment_mode='manual'`
(é delas que sai o lembrete de vencimento), e `sync_autopay_notices_once` grava
em `recurring_charges`, SEM lançamento, o aviso do autopay que vence hoje (o
banner do dashboard mostra "dia de débito no banco"). Não debita nada.
Logo depois, `notify_autopay_notices_whatsapp_once` manda os avisos NOVOS do dia
uma vez só pro WhatsApp do dono, numa mensagem de texto livre por usuário, só
para quem falou com o Piggy nas últimas 23h (`WA_AUTOPAY_NOTICE_ENABLED`; sem
a env, não faz nada).

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
import os
import sys
import traceback
from datetime import date, datetime, timedelta

from utils_date import now_tz


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
        # Uma leitura do relógio pros dois passos: a volta que cruza a meia-noite
        # grava e envia no mesmo dia lógico (senão o aviso de D some ou sai em D+1).
        agora = now_tz()
        try:
            await asyncio.to_thread(sync_autopay_notices_once, agora.date())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[autopay_notice] erro: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        try:
            await asyncio.to_thread(notify_autopay_notices_whatsapp_once, agora)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[autopay_wa] erro: {type(exc).__name__}", file=sys.stderr)
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


def _vence_hoje(rec: dict, today: date) -> bool:
    """Regra única do "vence hoje" do aviso de autopay: a sync grava por ela e o
    notify reconfere no recorrente ATUAL (edição entre os dois não manda aviso
    falso). Diário não avisa (seria todo dia). A data sai de
    `_recurring_occurrence_dates`, a mesma da Previsão."""
    from core.services.cashflow import _recurring_occurrence_dates

    freq = rec.get("frequency") or "monthly"
    return freq != "daily" and bool(_recurring_occurrence_dates(
        rec.get("due_day"), freq, rec.get("due_month"), rec.get("start_date"),
        today - timedelta(days=1), today))


def sync_autopay_notices_once(today: date | None = None) -> int:
    """Grava o aviso de vencimento de cada gasto fixo autopay que vence HOJE
    (conta ou cartão), por `_vence_hoje`. Não lança nada. A chave é a do
    cobrador antigo (mensal/anual `YYYY-MM`, semanal `w:`, único `o:`), então o
    período que ele já lançou não ganha aviso duplicado. Retorna quantos criou
    ou reagendou (`ensure_autopay_notice`)."""
    from db.recurring import ensure_autopay_notice, list_active_autopay_recurrings

    today = today or date.today()
    n = 0
    for rec in list_active_autopay_recurrings():
        if not _vence_hoje(rec, today):
            continue
        freq = rec.get("frequency") or "monthly"
        key = {"weekly": f"w:{today.isoformat()}",
               "once": f"o:{today.isoformat()}"}.get(freq, today.strftime("%Y-%m"))
        try:
            n += ensure_autopay_notice(int(rec["id"]), int(rec["user_id"]), float(rec["amount"]), key, today)
        except Exception as exc:
            print(f"[autopay_notice] falhou rec={rec.get('id')}: {exc}", file=sys.stderr)
    return n


# Janela de 24h da Meta para texto livre, com 1h de folga.
# ponytail: por usuário (last_activity_at), não por telefone; upgrade = coluna em user_identities.
# Só o `handle_incoming` escreve essa coluna (texto e áudio): quem só aperta
# botão, lista ou manda imagem fica fora da janela — erra para o lado seguro.
_JANELA_WA = timedelta(hours=23)


def _mensagem_autopay(avisos: list[dict]) -> str:
    """Texto aprovado pelo dono: singular com 1 aviso, lista com 2+."""
    from core.response_formatter import wrap_wa_markup
    from utils_text import fmt_brl

    def partes(a: dict) -> tuple[str, str, str]:
        nome = " ".join(str(a["name"] or "").split())[:60] or "seu gasto fixo"
        meio = "cobrança no cartão" if a["payment_type"] == "credit_card" else "débito na conta"
        return wrap_wa_markup(nome), fmt_brl(float(a["amount"])), meio

    rodape = "\n\n💡É só um lembrete: não lancei nada no app."
    if len(avisos) == 1:
        nome, valor, meio = partes(avisos[0])
        return f"🐷 Hoje vence o seu gasto fixo {nome}, de {valor}, com {meio}.{rodape}"
    linhas = "\n".join("• {}, {}, {}".format(*partes(a)) for a in avisos)
    return f"🐷 Hoje vencem {len(avisos)} gastos fixos seus:\n{linhas}{rodape}"


def notify_autopay_notices_whatsapp_once(now: datetime | None = None) -> int:
    """Manda pro WhatsApp do DONO os avisos de autopay de hoje ainda não
    reservados, a partir de WA_BILL_REMINDER_HOUR, numa mensagem de texto livre
    por usuário — só para quem está na janela (`_JANELA_WA`). Reserva em lote
    ANTES de enviar (no máximo uma tentativa; falha não desfaz): sem acesso,
    opt-out ou sem telefone também consomem o aviso. Retorna quantos destinos
    aceitaram."""
    if (os.getenv("WA_AUTOPAY_NOTICE_ENABLED") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return 0  # desligado: nem lê o banco
    now = now or now_tz()
    try:
        hora = int(os.getenv("WA_BILL_REMINDER_HOUR", "9") or 9)
    except ValueError:
        hora = 9  # env inválida vale o padrão, em vez de derrubar o tick
    if not 0 <= hora <= 23:
        hora = 9  # -1 mandaria de madrugada e 25 nunca mandaria, em silêncio
    if now.hour < hora:
        return 0

    from adapters.whatsapp.wa_app import _dedupe_whatsapp_targets
    from adapters.whatsapp.wa_client import send_text
    from core.observability import log_system_event_sync
    from core.reports.reports_daily import filtrar_por_acesso
    from db import get_whatsapp_updates_opt_out, list_identities_by_user
    from db.recurring import claim_autopay_notices_whatsapp, list_autopay_notices_for_whatsapp

    por_usuario: dict[int, list[dict]] = {}
    for row in list_autopay_notices_for_whatsapp(now.date(), now - _JANELA_WA):
        if not _vence_hoje(row, now.date()):
            continue  # editado depois da gravação: não reserva, e amanhã o due_on já não bate
        por_usuario.setdefault(int(row["user_id"]), []).append(row)

    sent = 0
    for uid, avisos in por_usuario.items():
        try:
            ok = bool(filtrar_por_acesso([uid])) and not get_whatsapp_updates_opt_out(uid)
            targets = _dedupe_whatsapp_targets(list_identities_by_user(uid)) if ok else []
        except Exception as exc:
            # Fail-closed sem reservar: a leitura falhou, tenta de novo na próxima hora.
            print(f"[autopay_wa] leitura falhou user_id={uid} erro={type(exc).__name__}", file=sys.stderr)
            continue
        reservados = set(claim_autopay_notices_whatsapp(uid, [a["id"] for a in avisos]))
        avisos = [a for a in avisos if a["id"] in reservados]
        if not avisos or not targets:
            continue
        corpo = _mensagem_autopay(avisos)
        enviou = False
        for to in targets:
            try:
                if send_text(to, corpo) is None:
                    # `None` é o 401 da Meta, que `send_text` não levanta.
                    print(f"[autopay_wa] recusado user_id={uid} erro=token_invalido", file=sys.stderr)
                    continue
                sent += 1
                enviou = True
            except Exception as exc:
                # Só o TIPO: `str(exc)` pode ecoar o número (PII).
                print(f"[autopay_wa] envio falhou user_id={uid} erro={type(exc).__name__}", file=sys.stderr)
        if enviou:
            log_system_event_sync(
                "info", "whatsapp_autopay_notice_sent",
                "Aviso de gasto fixo autopay enviado via texto livre.",
                source="recurring_charger", user_id=uid,
                details={"charge_ids": [a["id"] for a in avisos]},
            )
    return sent


__all__ = [
    "run_recurring_charger_loop",
    "sync_manual_bills_once",
    "sync_autopay_notices_once",
    "notify_autopay_notices_whatsapp_once",
]
