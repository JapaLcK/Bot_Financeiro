"""Régua de remarketing no painel do funil: só agregados (count), zero linha/id/e-mail/telefone.

Sem janela 7d/30d de propósito: a régua é pequena e o que importa é o acumulado por etapa.
"""
from __future__ import annotations

ETAPAS = (1, 2, 3)


async def fetch_remarketing(cur) -> dict:
    await cur.execute("SELECT count(*) AS n FROM remarketing_regua")
    na_regua = (await cur.fetchone())["n"]
    await cur.execute(
        """
        SELECT v.etapa,
               count(*) FILTER (WHERE e.status = 'enviado')                   AS enviados,
               count(*) FILTER (WHERE e.status = 'sem_canal')                 AS sem_canal,
               count(*) FILTER (WHERE e.status = 'enviando')                  AS enviando,
               count(*) FILTER (WHERE e.email_ok)                             AS email_ok,
               count(*) FILTER (WHERE e.email_ok = false)                     AS email_falhas,
               count(*) FILTER (WHERE e.whatsapp_ok)                          AS whatsapp_ok,
               count(*) FILTER (WHERE e.whatsapp_ok = false)                  AS whatsapp_falhas,
               count(*) FILTER (WHERE e.clicado_em IS NOT NULL)               AS cliques,
               count(*) FILTER (WHERE e.clicado_em IS NOT NULL AND EXISTS (
                   SELECT 1 FROM checkout_funnel_events c
                   WHERE c.user_id = e.user_id AND c.kind = 'completed'
                     AND c.created_at > e.clicado_em))                        AS recuperados
        FROM unnest(%s::int[]) AS v(etapa)
        LEFT JOIN remarketing_envios e ON e.etapa = v.etapa
        GROUP BY v.etapa ORDER BY v.etapa
        """, (list(ETAPAS),))
    return {"na_regua": na_regua, "etapas": [dict(r) for r in await cur.fetchall()]}
