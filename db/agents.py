"""Agentes do Piggy — acesso a dados (agents + agent_events).

Todas as funções são síncronas (chamar com asyncio.to_thread nos routers,
como o resto do pacote db).
"""
from __future__ import annotations

import json
from typing import Any

from .connection import get_conn

# Kinds fixos da Fase A. O catálogo de exibição (nome, descrição, arte)
# vive no frontend/router; aqui só o que o banco precisa validar.
AGENT_KINDS = ("xerife", "reporter", "carteiro")


def list_agents(user_id: int) -> list[dict[str, Any]]:
    """Agentes do usuário + contadores (disparos 30d, R$ salvos 365d)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select a.id, a.kind, a.config, a.status, a.created_at,
                       coalesce(e.fired_30d, 0)  as fired_30d,
                       coalesce(e.saved_365d, 0) as saved_365d
                from agents a
                left join (
                  select agent_id,
                         count(*) filter (where fired_at >= now() - interval '30 days')  as fired_30d,
                         coalesce(sum(valor_impacto) filter (
                           where fired_at >= now() - interval '365 days'), 0)            as saved_365d
                  from agent_events
                  group by agent_id
                ) e on e.agent_id = a.id
                where a.user_id = %s
                order by a.created_at
                """,
                (user_id,),
            )
            return list(cur.fetchall() or [])


def get_agent(user_id: int, kind: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, kind, config, status, created_at from agents"
                " where user_id=%s and kind=%s",
                (user_id, kind),
            )
            return cur.fetchone()


def count_active_agents(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from agents where user_id=%s and status='active'",
                (user_id,),
            )
            row = cur.fetchone()
            return int(row["n"]) if row else 0


def activate_agent(
    user_id: int, kind: str, config: dict | None = None, allow_multiple: bool = True
) -> dict[str, Any] | None:
    """Cria (ou reativa) o agente. Upsert idempotente por (user_id, kind).

    Com `allow_multiple=False` (plano sem direito a vários agentes), a checagem
    de limite e a ativação acontecem na MESMA transação, serializadas por um
    advisory lock por usuário — senão duas ativações concorrentes de kinds
    diferentes leem count=0 e ambas ativam, furando o teto do Free (TOCTOU).
    Retorna None quando o limite bloqueia (o usuário já tem outro agente ativo).
    Reativar o MESMO kind nunca conta como novo agente (kind <> %s no count).
    """
    if kind not in AGENT_KINDS:
        raise ValueError(f"kind inválido: {kind}")
    cfg = json.dumps(config or {})
    with get_conn() as conn:
        with conn.cursor() as cur:
            if not allow_multiple:
                # Lock por usuário (liberado no fim da transação). hashtext→int4,
                # promovido a bigint pela assinatura de 1 arg da função.
                cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"agent_activate:{user_id}",))
                cur.execute(
                    "select count(*) as n from agents"
                    " where user_id=%s and status='active' and kind <> %s",
                    (user_id, kind),
                )
                if int((cur.fetchone() or {}).get("n") or 0) >= 1:
                    conn.rollback()
                    return None
            cur.execute(
                """
                insert into agents (user_id, kind, config, status)
                values (%s, %s, %s::jsonb, 'active')
                on conflict (user_id, kind) do update
                  set status = 'active',
                      config = case when excluded.config <> '{}'::jsonb
                                    then excluded.config else agents.config end
                returning id, kind, config, status, created_at
                """,
                (user_id, kind, cfg),
            )
            row = cur.fetchone()
        conn.commit()
    return row


def pause_agent(user_id: int, kind: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update agents set status='paused' where user_id=%s and kind=%s",
                (user_id, kind),
            )
            changed = cur.rowcount > 0
        conn.commit()
    return changed


def record_agent_event(
    agent_id: int,
    user_id: int,
    kind: str,
    dedupe_key: str,
    payload: dict | None = None,
    channel: str = "dashboard",
    valor_impacto: float | None = None,
) -> bool:
    """Registra um disparo. Retorna False se a dedupe_key já existia."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into agent_events
                  (agent_id, user_id, kind, dedupe_key, payload, channel, valor_impacto)
                values (%s, %s, %s, %s, %s::jsonb, %s, %s)
                on conflict (agent_id, dedupe_key) do nothing
                """,
                (agent_id, user_id, kind, dedupe_key,
                 json.dumps(payload or {}), channel, valor_impacto),
            )
            inserted = cur.rowcount > 0
        conn.commit()
    return inserted


def list_agent_events(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, kind, fired_at, payload, channel, valor_impacto, seen_at
                from agent_events
                where user_id=%s
                order by fired_at desc
                limit %s
                """,
                (user_id, limit),
            )
            return list(cur.fetchall() or [])


def mark_agent_events_seen(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update agent_events set seen_at=now() where user_id=%s and seen_at is null",
                (user_id,),
            )
            n = cur.rowcount
        conn.commit()
    return n


def agents_summary(user_id: int) -> dict[str, Any]:
    """Contadores do topo da página: ativos, pausados, disparos do mês, salvos no ano."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  count(*) filter (where status='active') as ativos,
                  count(*) filter (where status='paused') as pausados
                from agents where user_id=%s
                """,
                (user_id,),
            )
            a = cur.fetchone() or {}
            cur.execute(
                """
                select
                  count(*) filter (where fired_at >= date_trunc('month', now())) as disparos_mes,
                  coalesce(sum(valor_impacto) filter (
                    where fired_at >= now() - interval '365 days'), 0)           as salvos_ano,
                  count(*) filter (where seen_at is null)                        as nao_lidos
                from agent_events where user_id=%s
                """,
                (user_id,),
            )
            e = cur.fetchone() or {}
    return {
        "ativos": int(a.get("ativos") or 0),
        "pausados": int(a.get("pausados") or 0),
        "disparos_mes": int(e.get("disparos_mes") or 0),
        "salvos_ano": float(e.get("salvos_ano") or 0),
        "nao_lidos": int(e.get("nao_lidos") or 0),
    }


def list_users_with_active_agents(kind: str | None = None) -> list[dict[str, Any]]:
    """(user_id, agent_id, kind, config) de todos os agentes ativos — insumo do runner."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if kind:
                cur.execute(
                    "select id as agent_id, user_id, kind, config from agents"
                    " where status='active' and kind=%s order by user_id",
                    (kind,),
                )
            else:
                cur.execute(
                    "select id as agent_id, user_id, kind, config from agents"
                    " where status='active' order by user_id",
                )
            return list(cur.fetchall() or [])
