"""
db/news.py — Persistência das notícias financeiras curadas pelo news_bot.

Modelo de curadoria (link-out): guardamos apenas título, um resumo ORIGINAL
gerado por LLM e o link pra fonte. Nunca o corpo do artigo — o card leva o
usuário pra ler no veículo original. Tabela `news_posts` definida em
`db/schema.py`.
"""
from __future__ import annotations

from datetime import datetime

from .connection import get_conn


def news_url_exists(source_url: str) -> bool:
    """True se já temos uma notícia com essa URL (evita reprocessar no LLM)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from news_posts where source_url = %s", (source_url,))
            return cur.fetchone() is not None


def insert_news_post(
    *,
    source: str,
    source_url: str,
    title: str,
    summary: str,
    category: str | None = None,
    thumb_emoji: str | None = None,
    published_at: datetime | None = None,
) -> bool:
    """
    Insere uma notícia. Idempotente: `source_url` é unique, então ON CONFLICT
    DO NOTHING. Retorna True se inseriu, False se já existia.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into news_posts
                    (source, source_url, title, summary, category, thumb_emoji, published_at)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (source_url) do nothing
                returning id
                """,
                (source, source_url, title, summary, category, thumb_emoji, published_at),
            )
            return cur.fetchone() is not None


def get_recent_news(limit: int = 12) -> list[dict]:
    """Últimas notícias, mais recentes primeiro (por data de publicação)."""
    limit = max(1, min(int(limit), 50))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select source, source_url, title, summary, category,
                       thumb_emoji, published_at
                from news_posts
                order by published_at desc nulls last, id desc
                limit %s
                """,
                (limit,),
            )
            return list(cur.fetchall())
