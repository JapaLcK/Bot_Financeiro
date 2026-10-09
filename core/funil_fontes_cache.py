"""Cache em TABELA das fontes do funil (`funil_fontes_cache`): leitura, gravação e a reserva
atômica da cota diária. Só agregados, sem `user_id`. Lógica de TTL/backoff mora em `funil_fontes`.
"""
from __future__ import annotations

from datetime import date, datetime

from psycopg.types.json import Jsonb

from core.admin_dashboard import db_connect


async def ler(nome: str) -> dict | None:
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT payload, buscado_em, falha_em FROM funil_fontes_cache WHERE fonte = %s",
                (nome,))
            return await cur.fetchone()


async def gravar_ok(nome: str, payload: dict, agora: datetime) -> None:
    async with await db_connect() as conn:
        await conn.execute(
            "INSERT INTO funil_fontes_cache (fonte, payload, buscado_em) VALUES (%s, %s, %s) "
            "ON CONFLICT (fonte) DO UPDATE SET payload = EXCLUDED.payload, "
            "buscado_em = EXCLUDED.buscado_em, falha_em = NULL",
            (nome, Jsonb(payload), agora))


async def gravar_falha(nome: str, agora: datetime) -> None:
    async with await db_connect() as conn:
        await conn.execute(
            "INSERT INTO funil_fontes_cache (fonte, falha_em) VALUES (%s, %s) "
            "ON CONFLICT (fonte) DO UPDATE SET falha_em = EXCLUDED.falha_em",
            (nome, agora))


_HOJE_TESTE: date | None = None  # só os testes mexem; em produção vale o relógio do BANCO


async def reservar(nome: str, limite: int, hoje: date | None = None) -> bool:
    """Reserva UMA chamada do dia num único comando atômico (vale com N instâncias).

    O dia da cota é o do relógio do BANCO (`now() at time zone 'utc'`): instância com relógio
    adiantado não grava um `dia_utc` futuro que deixaria a fonte sem cota por anos. `hoje` (ou
    `_HOJE_TESTE`) é override só de teste. Já `buscado_em`/`falha_em` vêm do relógio da
    INSTÂNCIA que gravou; quem lê trata futuro como vencido (`funil_fontes._pronta`).

    O `WHERE` do `DO UPDATE` recusa quando o contador do dia já chegou ao limite; sem
    linha devolvida = cota esgotada. O contador só REINICIA em dia mais novo (`dia_utc`
    nulo ou menor): requisição atrasada conta contra o dia mais novo em vez de zerá-lo."""
    if limite < 1:
        raise ValueError("limite deve ser >= 1")
    hoje = hoje if hoje is not None else _HOJE_TESTE
    async with await db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO funil_fontes_cache AS c (fonte, dia_utc, chamadas_dia) "
                "SELECT %(f)s::text, COALESCE(%(d)s::date, (now() AT TIME ZONE 'utc')::date), 1 "
                "ON CONFLICT (fonte) DO UPDATE SET dia_utc = GREATEST(c.dia_utc, EXCLUDED.dia_utc), "
                "chamadas_dia = CASE WHEN c.dia_utc IS NULL OR c.dia_utc < EXCLUDED.dia_utc "
                "THEN 1 ELSE c.chamadas_dia + 1 END "
                "WHERE c.dia_utc IS NULL OR c.dia_utc < EXCLUDED.dia_utc OR c.chamadas_dia < %(l)s "
                "RETURNING chamadas_dia",
                {"f": nome, "d": hoje, "l": limite})
            return await cur.fetchone() is not None
