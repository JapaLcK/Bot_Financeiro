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
AGENT_KINDS = ("xerife", "reporter", "carteiro", "detetive", "cofre", "barao", "faria_limer")


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
    user_id: int, kind: str, config: dict | None = None, allow_multiple: bool = True,
    energy_budget: int | None = None, energy_cost_by_kind: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """Cria (ou reativa) o agente. Upsert idempotente por (user_id, kind).

    Dois modos de gate, ambos serializados por um advisory lock por usuário pra
    fechar o TOCTOU (duas ativações concorrentes lendo o mesmo estado e furando
    o limite):

      • energy_budget != None (modelo de energia): soma o custo dos agentes já
        ativos (fora o próprio kind) e bloqueia se `usado + custo(kind) > budget`.
        Reativar o MESMO kind não recontabiliza (ele fica de fora da soma).
      • allow_multiple=False (legado v1/v2-off): teto binário — bloqueia se já
        houver QUALQUER outro agente ativo.

    Retorna None quando o gate bloqueia. Reativar o MESMO kind nunca é bloqueado
    (kind <> %s exclui o próprio da checagem).
    """
    if kind not in AGENT_KINDS:
        raise ValueError(f"kind inválido: {kind}")
    cfg = json.dumps(config or {})
    with get_conn() as conn:
        with conn.cursor() as cur:
            if energy_budget is not None:
                # Lock por usuário (liberado no fim da transação). hashtext→int4,
                # promovido a bigint pela assinatura de 1 arg da função.
                cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"agent_activate:{user_id}",))
                cur.execute(
                    "select kind from agents"
                    " where user_id=%s and status='active' and kind <> %s",
                    (user_id, kind),
                )
                costs = energy_cost_by_kind or {}
                used = sum(int(costs.get(r["kind"], 0)) for r in (cur.fetchall() or []))
                if used + int(costs.get(kind, 0)) > int(energy_budget):
                    conn.rollback()
                    return None
            elif not allow_multiple:
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


# ── Mini-digest por agente (batching de e-mail + teto de cadência) ────────────

def list_agents_pending_email() -> list[dict[str, Any]]:
    """Agentes ATIVOS que têm ≥1 evento ainda não enviado por e-mail. Traz o
    last_emailed_at pro teto de cadência e o nº de eventos pendentes."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select a.id as agent_id, a.user_id, a.kind, a.config, a.last_emailed_at,
                       count(e.id) as pendentes
                from agents a
                join agent_events e on e.agent_id = a.id and e.emailed_at is null
                where a.status = 'active'
                group by a.id, a.user_id, a.kind, a.config, a.last_emailed_at
                """,
            )
            return list(cur.fetchall() or [])


def list_unemailed_events(agent_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """Eventos do agente ainda não enviados por e-mail (mais recentes primeiro)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, kind, payload, valor_impacto, fired_at
                from agent_events
                where agent_id = %s and emailed_at is null
                order by fired_at desc
                limit %s
                """,
                (agent_id, limit),
            )
            return list(cur.fetchall() or [])


def mark_events_emailed(event_ids: list[int]) -> int:
    """Marca eventos como já enviados por e-mail (ou suprimidos por opt-out)."""
    if not event_ids:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update agent_events set emailed_at=now() where id = any(%s)",
                (list(event_ids),),
            )
            n = cur.rowcount
        conn.commit()
    return n


def touch_agent_emailed(agent_id: int) -> None:
    """Registra o momento do último e-mail do agente (teto de cadência)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update agents set last_emailed_at=now() where id=%s", (agent_id,))
        conn.commit()


def set_agent_email_enabled(user_id: int, kind: str, enabled: bool) -> bool:
    """Liga/desliga o envio de e-mail desse agente (grava em config.email_enabled).
    Feed continua sempre; só o e-mail proativo é suprimido quando desligado.
    Retorna False se o usuário não tem esse agente."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update agents set config = coalesce(config, '{}'::jsonb) || %s::jsonb "
                "where user_id=%s and kind=%s",
                (json.dumps({"email_enabled": bool(enabled)}), user_id, kind),
            )
            ok = cur.rowcount > 0
        conn.commit()
    return ok


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
