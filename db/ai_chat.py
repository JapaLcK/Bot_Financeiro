"""
db/ai_chat.py — Persistência do chat IA (Pro v1 Fase 2 Bloco A).

Esquema:
  - ai_messages: histórico flat por user (sem multi-thread)
  - ai_pending_actions: write proposto pela IA aguardando confirmação humana
  - auth_accounts.ai_messages_this_month + ai_month_reset_at: rate limit mensal

Sliding window: a IA recebe as últimas N mensagens (padrão 20) como contexto.
Mensagens antigas continuam no DB mas saem do contexto da IA.

Pending action expira após 10 minutos (limpeza lazy no get).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from .connection import get_conn


PENDING_TTL_MINUTES = 10
DEFAULT_CONTEXT_WINDOW = 20


# ─── Mensagens do chat ──────────────────────────────────────────────────────

def append_message(
    user_id: int,
    role: str,
    content: Optional[str],
    *,
    tool_calls: Optional[list[dict[str, Any]]] = None,
    tool_call_id: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> int:
    """Grava uma mensagem no histórico. Retorna o id da linha criada."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into ai_messages
                (user_id, role, content, tool_calls, tool_call_id, tool_name)
            values (%s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                int(user_id),
                role,
                content,
                json.dumps(tool_calls) if tool_calls else None,
                tool_call_id,
                tool_name,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row["id"])


def get_recent_messages(user_id: int, limit: int = DEFAULT_CONTEXT_WINDOW) -> list[dict[str, Any]]:
    """
    Retorna as últimas `limit` mensagens do user em ordem cronológica
    (mais antigas primeiro). Pronto pra montar o array `messages` da OpenAI.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, role, content, tool_calls, tool_call_id, tool_name, created_at
            from (
                select id, role, content, tool_calls, tool_call_id, tool_name, created_at
                from ai_messages
                where user_id = %s
                order by created_at desc, id desc
                limit %s
            ) recent
            order by created_at asc, id asc
            """,
            (int(user_id), int(limit)),
        )
        rows = cur.fetchall() or []

    out: list[dict[str, Any]] = []
    for r in rows:
        role = r["role"]
        content = r["content"]
        tool_calls = r["tool_calls"]
        tool_call_id = r["tool_call_id"]
        tool_name = r["tool_name"]

        msg: dict[str, Any] = {"role": role}
        if content is not None:
            msg["content"] = content
        if tool_calls:
            msg["tool_calls"] = tool_calls if isinstance(tool_calls, list) else json.loads(tool_calls)
        if tool_call_id:
            msg["tool_call_id"] = tool_call_id
        if tool_name and role == "tool":
            # OpenAI espera `name` no tool result, não `tool_name`
            msg["name"] = tool_name
        out.append(msg)
    return out


# ─── Pending action (write aguardando confirmação) ──────────────────────────

def set_pending_action(
    user_id: int,
    tool_name: str,
    tool_args: dict[str, Any],
    summary: str,
) -> None:
    """
    Grava uma ação pendente. Apenas uma por user (upsert) — se já existe,
    sobrescreve.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into ai_pending_actions (user_id, tool_name, tool_args, summary, created_at)
            values (%s, %s, %s, %s, now())
            on conflict (user_id) do update set
                tool_name = excluded.tool_name,
                tool_args = excluded.tool_args,
                summary = excluded.summary,
                created_at = excluded.created_at
            """,
            (int(user_id), tool_name, json.dumps(tool_args), summary),
        )
        conn.commit()


def get_pending_action(user_id: int) -> Optional[dict[str, Any]]:
    """
    Retorna a ação pendente do user ou None. Aplica TTL: se a linha for mais
    velha que PENDING_TTL_MINUTES, é apagada e retorna None.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=PENDING_TTL_MINUTES)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select tool_name, tool_args, summary, created_at
            from ai_pending_actions
            where user_id = %s
            """,
            (int(user_id),),
        )
        row = cur.fetchone()
        if not row:
            return None

        tool_name = row["tool_name"]
        tool_args = row["tool_args"]
        summary = row["summary"]
        created_at = row["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at < cutoff:
            cur.execute(
                "delete from ai_pending_actions where user_id = %s",
                (int(user_id),),
            )
            conn.commit()
            return None

    return {
        "tool_name": tool_name,
        "tool_args": tool_args if isinstance(tool_args, dict) else json.loads(tool_args),
        "summary": summary,
        "created_at": created_at,
    }


def clear_pending_action(user_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "delete from ai_pending_actions where user_id = %s",
            (int(user_id),),
        )
        conn.commit()


# ─── Rate limit mensal ──────────────────────────────────────────────────────

def _current_month_start() -> date:
    today = date.today()
    return today.replace(day=1)


def get_usage_this_month(user_id: int) -> int:
    """
    Retorna quantas mensagens o user mandou pra IA no mês atual.
    Aplica reset lazy: se ai_month_reset_at é de outro mês, zera antes.
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
            cur.execute(
                """
                update auth_accounts
                set ai_messages_this_month = 0,
                    ai_month_reset_at = %s
                where user_id = %s
                """,
                (month_start, int(user_id)),
            )
            conn.commit()
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
