"""PL-06 A2: "dispensado" e "útil" de cada insight, persistidos por usuário (tabela `insight_estado`).

A linha nasce no cálculo (`registrar`, chamado por `ai_patterns._gerar_insights`) e guarda a severidade e a
medida do ÚLTIMO cálculo; o recálculo nunca toca `dispensado_*` nem `util_em`, então nada ressuscita por recálculo.
Dispensar grava como referência a severidade (e a medida) que o usuário VIU; a leitura
(`insights_contrato.projetar`) esconde o dispensado que não piorou além disso (D4). A chave é a de `insights_contrato.chave()`: opaca, comparada inteira.

`registrar`/`dispensadas` são fail-open (o feed sai, sem registro ou sem filtro); `marcar` (o PUT) levanta.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from core.services.insights_contrato import medida

from .connection import get_conn

logger = logging.getLogger(__name__)


def registrar(user_id: int, itens: list[dict]) -> None:
    """Uma linha por chave, com a severidade e a medida do último cálculo. Uma instrução, nenhuma com lista vazia."""
    if not itens:
        return
    try:
        lote: dict[str, dict] = {}
        for it in itens:
            # Duas linhas de mesma chave no mesmo upsert dão CardinalityViolation (`cat_key`/`merchant_key`
            # normalizam): fica a 1ª, a mais grave, porque `compute_active_insights` já ordena.
            lote.setdefault(it["key"], it)
        # Ordem fixa de lock entre gerações concorrentes do mesmo usuário (dois processos): por chave, depois do dedupe.
        lote = dict(sorted(lote.items()))
        vs = list(lote.values())
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into insight_estado (user_id, chave, tipo, severidade, valor)
                select %s, c, t, s, v from unnest(%s::text[], %s::text[], %s::text[], %s::numeric[]) as x(c, t, s, v)
                on conflict (user_id, chave) do update
                  set severidade = excluded.severidade, valor = excluded.valor
                  where (insight_estado.severidade, insight_estado.valor)
                        is distinct from (excluded.severidade, excluded.valor)
                """,
                (user_id, list(lote), [it["type"] for it in vs], [it["severity"] for it in vs],
                 [medida(it) for it in vs]),
            )
            conn.commit()
    except Exception as exc:  # o feed sai e o cache é gravado: dispensar dá 404 até o próximo cálculo
        logger.warning("insight_estado: registro falhou (%s)", type(exc).__name__, extra={"user_id": user_id})


def dispensadas(user_id: int, chaves: list[str]) -> dict[str, tuple[str, Decimal | None]]:
    """chave -> (severidade, medida) de quando foi dispensada, só das chaves pedidas."""
    if not chaves:
        return {}
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select chave, dispensado_severidade, dispensado_valor from insight_estado"
                " where user_id = %s and chave = any(%s) and dispensado_em is not null",
                (user_id, list(chaves)),
            )
            return {r["chave"]: (r["dispensado_severidade"], r["dispensado_valor"]) for r in cur.fetchall()}
    except Exception as exc:  # sem filtro: o pior caso é rever um dispensado
        logger.warning("insight_estado: leitura falhou (%s)", type(exc).__name__, extra={"user_id": user_id})
        return {}


class ValorObrigatorio(ValueError):
    """Dispensar sem o `valor` visto um aviso que tem medida (só na chave do próprio dono: a de outro dá None/404)."""


def marcar(user_id: int, chave: str, *, dispensado: bool | None, util: bool | None,
           severidade: str | None = None, valor: Decimal | None = None) -> dict | None:
    """Estado explícito e idempotente; None no campo = intocado. None de volta = chave inexistente ou de outro
    usuário (a rota responde o mesmo 404). Re-dispensar depois de uma piora atualiza a referência.

    `dispensado=True` grava como referência exatamente a `severidade` e o `valor` que o usuário VIU ("dispensei o
    que vi"): uma piora que outro cálculo gravou antes do clique, sem a tela mostrar, continua visível (D4). A
    linha com medida (`valor` preenchido pelo `registrar` a partir de `insights_contrato.medida`) sem o `valor`
    visto levanta `ValorObrigatorio` e não grava."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            with upd as (
            update insight_estado set
              dispensado_em = case when %(d)s::boolean is null then dispensado_em when %(d)s then now() end,
              dispensado_severidade = case when %(d)s::boolean is null then dispensado_severidade
                                           when %(d)s then %(vista)s::text end,
              dispensado_valor = case when %(d)s::boolean is null then dispensado_valor
                                      when %(d)s then %(valor)s::numeric end,
              util_em = case when %(u)s::boolean is null then util_em when %(u)s then coalesce(util_em, now()) end
            where user_id = %(uid)s and chave = %(chave)s
              and not (%(d)s::boolean is true and valor is not null and %(valor)s::numeric is null)
            returning dispensado_em is not null as dispensado, util_em is not null as util)
            select u.dispensado, u.util,
                   exists(select 1 from insight_estado where user_id = %(uid)s and chave = %(chave)s) as existe
            from (select 1) x left join upd u on true
            """,
            {"d": dispensado, "u": util, "uid": user_id, "chave": chave, "vista": severidade, "valor": valor},
        )
        row = cur.fetchone()
        conn.commit()
    if not row["existe"]:
        return None
    if row["dispensado"] is None:
        raise ValorObrigatorio(chave)
    return {"dispensado": row["dispensado"], "util": row["util"]}
