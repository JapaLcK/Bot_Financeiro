"""Leitura e marcação do alerta de anomalia do Xerife (PL-04). A regra mora em
`core/services/anomalia.py`; aqui só o SQL. Toda query filtra por `user_id`."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from .connection import TIPO_DESPESA_SQL, get_conn


def listar_candidatos_xerife(user_id: int, agora: datetime) -> list[dict[str, Any]]:
    """Lançamentos das últimas 24h (candidatos) com a referência da categoria: média por
    lançamento, quantos, primeira/última ocorrência e quantos "esperados" ficaram de fora,
    nos 90 dias que terminam 24h antes de `agora` (o próprio gasto não puxa a média).
    Devolve o candidato MESMO com amostra pequena: quem decide é o Python.
    "Esperado" fica fora do candidato e da referência."""
    from core.services.anomalia import JANELA_DIAS

    fim = agora - timedelta(hours=24)
    ini = agora - timedelta(days=JANELA_DIAS)
    base = f"user_id = %(u)s and {TIPO_DESPESA_SQL} and is_internal_movement = false and categoria is not null"
    janela = "criado_em >= %(ini)s and criado_em < %(fim)s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                with hist as (
                  select lower(categoria) as cat, avg(valor) as media, count(*) as n,
                         min(criado_em) as primeira, max(criado_em) as ultima
                  from launches
                  where {base} and esperado_em is null and {janela}
                  group by 1
                ), esp as (
                  select lower(categoria) as cat, count(*) as n
                  from launches
                  where {base} and esperado_em is not null and {janela}
                  group by 1
                )
                select l.id, l.valor, l.categoria, coalesce(l.alvo, l.nota, '') as descricao,
                       l.criado_em, h.media, h.n, h.primeira, h.ultima,
                       coalesce(e.n, 0) as esperados_fora,
                       (select min(criado_em) from launches
                        where user_id = %(u)s and {TIPO_DESPESA_SQL}
                          and is_internal_movement = false) as primeira_usuario
                from launches l
                left join hist h on lower(l.categoria) = h.cat
                left join esp e on lower(l.categoria) = e.cat
                where l.user_id = %(u)s and l.{TIPO_DESPESA_SQL}
                  and l.is_internal_movement = false and l.categoria is not null
                  and l.esperado_em is null and l.criado_em >= %(fim)s
                order by l.id
                """,
                {"u": user_id, "ini": ini, "fim": fim},
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
