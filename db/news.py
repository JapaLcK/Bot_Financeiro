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
    image_url: str | None = None,
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
                    (source, source_url, title, summary, category, thumb_emoji, image_url, published_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (source_url) do nothing
                returning id
                """,
                (source, source_url, title, summary, category, thumb_emoji, image_url, published_at),
            )
            return cur.fetchone() is not None


def backfill_news_image(source_url: str, image_url: str) -> bool:
    """
    Preenche a imagem de uma notícia que já existe mas está SEM imagem
    (self-heal): usado quando a notícia foi salva antes da feature de foto, ou
    quando a fonte não trouxe imagem na 1ª vez. Só toca linhas com image_url
    nulo/vazio — idempotente, não sobrescreve. Retorna True se atualizou.
    """
    if not image_url:
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update news_posts
                   set image_url = %s
                 where source_url = %s
                   and (image_url is null or image_url = '')
                returning id
                """,
                (image_url, source_url),
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
                       thumb_emoji, image_url, published_at
                from news_posts
                order by published_at desc nulls last, id desc
                limit %s
                """,
                (limit,),
            )
            return list(cur.fetchall())
