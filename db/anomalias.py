"""Leitura e marcação do alerta de anomalia do Xerife (PL-04). A regra mora em
`core/services/anomalia.py`; aqui só o SQL. Toda query filtra por `user_id`."""
from __future__ import annotations

import json
from uuid import uuid4
from datetime import datetime, timedelta
from typing import Any

from .connection import TIPO_DESPESA_SQL, get_conn
from core.services.xerife_config import regras_publicas


def _regra_matches_sql(regras: str) -> str:
    """Predicado único: lançamento l vs regras JSONB; datas seguem o fuso da sessão/app."""
    return f"""exists (
      select 1 from jsonb_to_recordset({regras})
        as r(categoria text, descricao text, teto numeric, data_fim date)
      where lower(trim(regexp_replace(l.categoria, '[[:space:]]+', ' ', 'g'))) = r.categoria
        and lower(trim(regexp_replace(coalesce(l.alvo, l.nota, ''), '[[:space:]]+', ' ', 'g'))) = r.descricao
        and l.valor > 0 and l.valor <= r.teto and (r.data_fim is null or l.criado_em::date <= r.data_fim)
    )"""


def listar_candidatos_xerife(user_id: int, agora: datetime) -> list[dict[str, Any]]:
    """Lançamentos das últimas 24h (candidatos) com a referência da categoria: média por
    lançamento, quantos, primeira/última ocorrência e quantos "esperados" ficaram de fora,
    nos 90 dias que terminam 24h antes de `agora` (o próprio gasto não puxa a média).
    Devolve o candidato MESMO com amostra pequena: quem decide é o Python.
    "Esperado" fica fora do candidato e da referência."""
    from core.services.anomalia import JANELA_DIAS

    fim = agora - timedelta(hours=24)
    ini = agora - timedelta(days=JANELA_DIAS)
    esperado = f"(l.esperado_em is not null or {_regra_matches_sql('%(regras)s::jsonb')})"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select config from agents where user_id = %s and kind = 'xerife'", (user_id,))
            agent = cur.fetchone()
            regras = regras_publicas(agent["config"] if agent else None)
            cur.execute(
                f"""
                with base as materialized (
                  select l.*, coalesce(l.alvo, l.nota, '') as descricao,
                         {esperado} as esperado
                  from launches l
                  where l.user_id = %(u)s and l.{TIPO_DESPESA_SQL}
                    and l.is_internal_movement = false and l.criado_em >= %(ini)s
                ), hist as (
                  select lower(categoria) as cat,
                         avg(valor) filter (where not esperado) as media,
                         count(*) filter (where not esperado) as n,
                         min(criado_em) filter (where not esperado) as primeira,
                         max(criado_em) filter (where not esperado) as ultima,
                         count(*) filter (where esperado) as esperados_fora
                  from base where criado_em >= %(ini)s and criado_em < %(fim)s
                    and categoria is not null
                  group by 1
                )
                select l.id, l.valor, l.categoria, l.descricao,
                       l.criado_em, h.media, h.n, h.primeira, h.ultima,
                       coalesce(h.esperados_fora, 0) as esperados_fora,
                       (select min(criado_em) from launches where user_id = %(u)s
                          and {TIPO_DESPESA_SQL} and is_internal_movement = false) as primeira_usuario
                from base l
                left join hist h on lower(l.categoria) = h.cat
                where not l.esperado and l.categoria is not null and l.criado_em >= %(fim)s
                order by l.id
                """,
                {"u": user_id, "ini": ini, "fim": fim, "regras": json.dumps(regras)},
            )
            return list(cur.fetchall() or [])



def marcar_lancamento_esperado(user_id: int, launch_id: int, esperado: bool) -> bool:
    """Marca/desmarca UM lançamento de despesa do usuário como esperado (idempotente).
    False = não existe, é de outro usuário, ou não é despesa (a rota responde 404 igual).

    Ao marcar, o alerta que já existia sai do feed e da fila de e-mail, e uma lápide ocupa a
    chave `anomalia:{id}` para o detector não recriá-lo se leu o lançamento antes da marcação.
    Desmarcar não ressuscita alerta velho. Se o lançamento não existe mais, o alerta órfão do
    próprio usuário sai do feed e a resposta continua sendo "não achou".

    A coluna e o evento mudam na MESMA transação: ou o lançamento fica marcado e sem alerta
    vivo, ou nada muda. O SQL do evento é local (espelha `record_agent_event(silencioso=True)`
    e `mark_agent_event_stale`: só `stale_at`, sem tocar em `emailed_at`/`seen_at`/`fired_at`)."""
    from .agents import get_agent

    agent = get_agent(user_id, "xerife")              # leitura, conexão própria, antes da transação
    chave = f"anomalia:{launch_id}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                update launches
                set esperado_em = case when %s then coalesce(esperado_em, now()) end
                where id = %s and user_id = %s and {TIPO_DESPESA_SQL}
                  and is_internal_movement = false
                returning id
                """,
                (esperado, launch_id, user_id),
            )
            achou = cur.fetchone() is not None
            if agent and achou and esperado:
                cur.execute(
                    """
                    insert into agent_events
                      (agent_id, user_id, kind, dedupe_key, payload, channel, stale_at)
                    values (%s, %s, 'xerife', %s, %s::jsonb, 'dashboard', now())
                    on conflict (agent_id, dedupe_key)
                    do update set stale_at = coalesce(agent_events.stale_at, now())
                    """,
                    (agent["id"], user_id, chave,
                     json.dumps({"tipo": "anomalia", "launch_id": launch_id, "esperado": True})),
                )
            elif agent and not achou:
                # Lançamento apagado: limpa o alerta órfão do PRÓPRIO usuário (a chave é por launch_id
                # e o agente é o do dono, então id de outro usuário é no-op). Sem lápide.
                cur.execute(
                    "update agent_events set stale_at = coalesce(stale_at, now())"
                    " where agent_id = %s and dedupe_key = %s",
                    (agent["id"], chave),
                )
        conn.commit()
    return achou


def atualizar_config_xerife(user_id: int, patch: dict) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update agents set config = (case when jsonb_typeof(config) = 'object' then config else '{}'::jsonb end) || %s::jsonb "
                "where user_id = %s and kind = 'xerife' returning config",
                (json.dumps(patch), user_id),
            )
            row = cur.fetchone()
        conn.commit()
    return row["config"] if row else None


def listar_esperados_xerife(user_id: int, limit: int = 50, offset: int = 0) -> dict | None:
    from core.services.xerife_config import config_publica
    from .agents import get_agent

    agent = get_agent(user_id, "xerife")
    if not agent:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""select id, valor, categoria, coalesce(alvo, nota, '') as descricao,
                           criado_em, esperado_em
                    from launches where user_id = %s and {TIPO_DESPESA_SQL}
                      and is_internal_movement = false and esperado_em is not null
                    order by esperado_em desc, id desc limit %s offset %s""",
                (user_id, limit + 1, offset),
            )
            rows = list(cur.fetchall())
    return {"config": config_publica(agent["config"]),
            "lancamentos": rows[:limit], "has_more": len(rows) > limit,
            "regras": regras_publicas(agent["config"])}


def _lapides_regras(cur, user_id: int, agent_id: int, regras: list[dict]) -> None:
    """Supressão e lápides na transação da regra: desfazer não reenvia gastos antigos."""
    cur.execute(
        f"""insert into agent_events
              (agent_id, user_id, kind, dedupe_key, payload, channel, stale_at)
            select %(a)s, %(u)s, 'xerife', 'anomalia:' || l.id,
                   jsonb_build_object('tipo', 'anomalia', 'launch_id', l.id, 'esperado', true),
                   'dashboard', now()
            from launches l
            where l.user_id = %(u)s and l.{TIPO_DESPESA_SQL}
              and l.is_internal_movement = false and {_regra_matches_sql('%(r)s::jsonb')}
            on conflict (agent_id, dedupe_key) do update
              set stale_at = coalesce(agent_events.stale_at, now())
              where agent_events.user_id = excluded.user_id""",
        {"a": agent_id, "u": user_id, "r": json.dumps(regras)},
    )


def alterar_regra_xerife(user_id: int, regra: dict | None = None, regra_id: str | None = None) -> dict | None:
    """Append/remove sob row lock; concorre corretamente com config, e-mail e detector."""
    from core.services.xerife_config import MAX_REGRAS

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id, config from agents where user_id = %s and kind = 'xerife' for update",
                        (user_id,))
            agent = cur.fetchone()
            if not agent:
                return None
            regras = regras_publicas(agent["config"])
            if regra is not None:
                # Repetir o mesmo pedido não consome outra vaga.
                result = next((r for r in regras if {k: v for k, v in r.items() if k != "id"} == regra), None)
                if result is None:
                    if len(regras) >= MAX_REGRAS:
                        raise ValueError("Você já tem 50 regras. Desfaça uma antes de criar outra.")
                    result = {"id": str(uuid4()), **regra}
                    regras = [*regras, result]
            else:
                result = next((r for r in regras if r["id"] == regra_id), None)
                if result is None:
                    return None
                regras = [r for r in regras if r["id"] != regra_id]
            _lapides_regras(cur, user_id, agent["id"], [result])
            cur.execute("update agents set config = (case when jsonb_typeof(config) = 'object' then config else '{}'::jsonb end) || %s::jsonb "
                        "where user_id = %s and id = %s",
                        (json.dumps({"regras_esperado": regras}), user_id, agent["id"]))
        conn.commit()
    return result
