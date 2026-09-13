"""Cota mensal compartilhada pelos chats: leitura, reserva e restituição."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .connection import get_conn


# ─── Rate limit mensal ──────────────────────────────────────────────────────

def _current_month_start() -> date:
    today = date.today()
    return today.replace(day=1)


def get_usage_this_month(user_id: int) -> int:
    """
    Retorna quantas mensagens o user mandou pra IA no mês atual.
    Somente leitura: mês anterior equivale a zero. Apenas o consumo reseta
    o contador, atomicamente; uma leitura atrasada nunca apaga uma reserva.
    """
    month_start = _current_month_start()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select ai_messages_this_month, ai_month_reset_at
            from auth_accounts
            where user_id = %s
            """,
            (int(user_id),),
        )
        row = cur.fetchone()
        if not row:
            return 0

        used = row["ai_messages_this_month"]
        reset_at = row["ai_month_reset_at"]
        if reset_at is None or reset_at < month_start:
            return 0
        return int(used or 0)


def increment_usage(user_id: int) -> int:
    """
    Incrementa o contador mensal (com reset lazy) e retorna o NOVO valor.
    Chamar APÓS processar a mensagem do user com sucesso.
    """
    month_start = _current_month_start()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            update auth_accounts
            set
              ai_messages_this_month = case
                when ai_month_reset_at is null or ai_month_reset_at < %s then 1
                else ai_messages_this_month + 1
              end,
              ai_month_reset_at = case
                when ai_month_reset_at is null or ai_month_reset_at < %s then %s
                else ai_month_reset_at
              end
            where user_id = %s
            returning ai_messages_this_month
            """,
            (month_start, month_start, month_start, int(user_id)),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row["ai_messages_this_month"]) if row else 0


@dataclass(frozen=True)
class UsageReservation:
    """Linhas e mês incrementados pela mesma operação atômica de consumo."""

    month: date
    account_ids: tuple[int, ...]
    used: int


def try_consume_usage(user_id: int, monthly_limit: int) -> int | None:
    """Desconta uma resposta na cota compartilhada sem ultrapassar o teto.

    O reset e a comparação acontecem no mesmo UPDATE, inclusive entre workers.
    Especialistas consomem após responder; o chat geral reserva antes das tools.
    """
    reservation = reserve_usage(user_id, monthly_limit)
    return reservation.used if reservation is not None else None


def reserve_usage(user_id: int, monthly_limit: int) -> UsageReservation | None:
    """Reserva uma vaga e identifica exatamente as contas que a receberam."""
    return _consume_usage(user_id, monthly_limit, _current_month_start())


def _consume_usage(user_id: int, monthly_limit: int, month_start: date) -> UsageReservation | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            update auth_accounts
            set ai_messages_this_month = case
                  when ai_month_reset_at is null or ai_month_reset_at < %s then 1
                  else coalesce(ai_messages_this_month, 0) + 1 end,
                ai_month_reset_at = %s
            where user_id = %s
              and (ai_month_reset_at is null or ai_month_reset_at <= %s)
              and (case
                  when ai_month_reset_at is null or ai_month_reset_at < %s then 0
                  else coalesce(ai_messages_this_month, 0) end) < %s
            returning id, ai_messages_this_month
            """,
            (month_start, month_start, int(user_id), month_start, month_start, int(monthly_limit)),
        )
        rows = cur.fetchall()
        conn.commit()
        if not rows:
            return None
        return UsageReservation(
            month=month_start,
            account_ids=tuple(int(row['id']) for row in rows),
            used=max(int(row['ai_messages_this_month']) for row in rows),
        )


def refund_usage(user_id: int, reservation: UsageReservation) -> None:
    """Devolve somente às contas reservadas, sem tocar meses posteriores.

    user_id não é único em auth_accounts. Uma conta já no teto, criada após a
    reserva ou pertencente a outro usuário não pode receber essa restituição.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """update auth_accounts
               set ai_messages_this_month = greatest(0, coalesce(ai_messages_this_month, 0) - 1)
               where user_id = %s and id = any(%s) and ai_month_reset_at = %s""",
            (int(user_id), list(reservation.account_ids), reservation.month),
        )
        conn.commit()
