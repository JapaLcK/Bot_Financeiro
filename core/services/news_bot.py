"""
core/services/news_bot.py

Bot de notícias financeiras (curadoria / link-out). Roda 1x a cada ~12h:

  1. Busca RSS de fontes financeiras BR (InfoMoney, Exame Invest, Valor Investe).
  2. Pra cada item novo (URL ainda não vista), manda título + snippet pro
     gpt-4o-mini, que atua como EDITOR: descarta o que não é financeiro
     (política, esporte, entretenimento) e gera um resumo ORIGINAL curto no
     tom da Piggy + categoria + emoji.
  3. Grava em `news_posts`. O card no /blog leva o usuário pra ler na fonte.

IMPORTANTE (jurídico): nunca guardamos o corpo do artigo — só um resumo
reescrito e o link. Somos curadores, não republicadores.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from core.observability import log_system_event_sync

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
# (nome exibido, url do RSS)
# IMPORTANTE: só fontes ABERTAS DE VERDADE — sem paywall E sem muro de cadastro.
# O usuário clica no card pra ler a íntegra, então a fonte PRECISA abrir sem pedir
# login/assinatura. Testado em 2026-07-23 (fetch do artigo + busca por muro):
#   - InfoMoney/Money Times/Suno → empurram cadastro (fora)
#   - Valor/Exame → pagos (fora)
#   - G1 (portal aberto da Globo), Agência Brasil (EBC/público), Poder360 → abrem.
# Inclui política (economia e política andam lado a lado) — o filtro do LLM
# mantém só o que tem ângulo econômico/financeiro.
FEEDS: list[tuple[str, str]] = [
    ("G1 Economia",    "https://g1.globo.com/rss/g1/economia/"),
    ("G1 Política",    "https://g1.globo.com/rss/g1/politica/"),
    ("Agência Brasil", "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml"),
    ("Poder360",       "https://www.poder360.com.br/feed/"),
]

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CHECK_INTERVAL_HOURS = 12          # frequência do loop
MAX_ITEMS_PER_FEED   = 8           # quantos itens recentes olhar por feed
MAX_NEW_PER_RUN      = 10          # teto de notícias novas processadas por ciclo (custo LLM)
HTTP_TIMEOUT         = 12.0
USER_AGENT           = "PigBankNewsBot/1.0 (+https://pigbankai.com)"

ALLOWED_CATEGORIES = {
    "Mercados", "Economia", "Investimentos", "Cripto", "Finanças pessoais",
    "Política econômica",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE  = re.compile(r"\s+")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _strip_html(text: str | None) -> str:
    """Remove tags HTML e normaliza espaços/entidades de um snippet de RSS."""
    if not text:
        return ""
    no_tags = _TAG_RE.sub(" ", text)
    unescaped = html.unescape(no_tags)
    return _WS_RE.sub(" ", unescaped).strip()


def _parse_pubdate(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _fetch_feed(source: str, url: str) -> list[dict]:
    """Busca e parseia um feed RSS. Retorna [] em qualquer falha (feed fora do ar)."""
    import httpx

    try:
        resp = httpx.get(
            url, timeout=HTTP_TIMEOUT, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("[news] falha ao buscar feed %s (%s): %s", source, url, exc)
        return []

    try:
        root = ET.fromstring(resp.text)
    except Exception as exc:
        logger.warning("[news] feed %s não é XML válido: %s", source, exc)
        return []

    items: list[dict] = []
    for it in root.findall(".//item")[:MAX_ITEMS_PER_FEED]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append({
            "source": source,
            "title": html.unescape(title),
            "link": link,
            "snippet": _strip_html(it.findtext("description"))[:600],
            "published_at": _parse_pubdate(it.findtext("pubDate")),
        })
    return items


# ─── LLM: editor + resumidor ──────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "Você é a Piggy, editora de finanças do PigBank. Recebe o título e um trecho "
    "de uma notícia e decide se ela interessa a quem cuida das próprias finanças e "
    "investimentos no Brasil.\n\n"
    "ACEITE (relevante=true): mercado, investimentos, economia, câmbio, juros, "
    "cripto, finanças pessoais E TAMBÉM política quando tiver ângulo econômico — "
    "reforma tributária, Selic, gastos e arcabouço fiscal, orçamento, regulação de "
    "mercado, decisões que mexem com investimentos ou com o bolso do brasileiro.\n"
    "DESCARTE (relevante=false): esporte, entretenimento, celebridades, crime/"
    "policial e briga partidária pura, sem qualquer efeito econômico.\n\n"
    "Se for relevante, escreva uma CHAMADA curta (teaser), NÃO a notícia inteira: "
    "1 a 2 frases (no máximo ~40 palavras) que despertem a vontade de clicar e ler "
    "a íntegra na fonte. Dê o gancho e o essencial, mas NÃO entregue todos os "
    "detalhes e números — o usuário deve precisar abrir a notícia pra saber o "
    "resto. Tom claro, nada sensacionalista.\n"
    "O texto deve ser ORIGINAL, com SUAS palavras — NUNCA copie trechos do texto "
    "recebido. Não invente fatos que não estejam no trecho.\n\n"
    "Responda SOMENTE JSON no formato:\n"
    '{"relevante": true|false, "resumo": "…", '
    '"categoria": "Mercados|Economia|Investimentos|Cripto|Finanças pessoais|Política econômica", '
    '"emoji": "um emoji"}'
)


def _get_client():
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(api_key=api_key)
    except Exception as exc:
        logger.error("[news] falha ao inicializar OpenAI: %s", exc)
        return None


def _summarize(client, item: dict) -> dict | None:
    """Chama o LLM. Retorna dict validado ou None (erro / não-relevante)."""
    import json

    user_msg = (
        f"Fonte: {item['source']}\n"
        f"Título: {item['title']}\n"
        f"Trecho: {item['snippet'] or '(sem trecho)'}"
    )
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=0.4,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        logger.warning("[news] erro no LLM pra '%s': %s", item["title"][:60], exc)
        return None

    if not parsed.get("relevante"):
        return None

    resumo = (parsed.get("resumo") or "").strip()
    if not resumo:
        return None
    categoria = (parsed.get("categoria") or "").strip()
    if categoria not in ALLOWED_CATEGORIES:
        categoria = "Mercados"
    emoji = (parsed.get("emoji") or "📰").strip()[:4] or "📰"
    return {"resumo": resumo, "categoria": categoria, "emoji": emoji}


# ─── Coleta (síncrona — roda em thread pool) ─────────────────────────────────

def _collect_once() -> int:
    """Um ciclo de coleta. Retorna quantas notícias novas foram inseridas."""
    import db

    client = _get_client()
    if client is None:
        logger.warning("[news] OPENAI_API_KEY ausente — pulando ciclo.")
        return 0

    inserted = 0
    for source, url in FEEDS:
        if inserted >= MAX_NEW_PER_RUN:
            break
        for item in _fetch_feed(source, url):
            if inserted >= MAX_NEW_PER_RUN:
                break
            try:
                if db.news_url_exists(item["link"]):
                    continue
                summary = _summarize(client, item)
                if summary is None:
                    continue  # não-relevante ou erro
                ok = db.insert_news_post(
                    source=source,
                    source_url=item["link"],
                    title=item["title"][:300],
                    summary=summary["resumo"][:600],
                    category=summary["categoria"],
                    thumb_emoji=summary["emoji"],
                    published_at=item["published_at"],
                )
                if ok:
                    inserted += 1
            except Exception as exc:
                logger.warning("[news] erro processando item de %s: %s", source, exc)

    if inserted:
        logger.info("[news] %d notícias novas inseridas.", inserted)
        log_system_event_sync(
            "info", "news_bot_run",
            f"news_bot inseriu {inserted} notícia(s).",
            source="news_bot",
        )
    return inserted


# ─── Loop principal ───────────────────────────────────────────────────────────

async def run_news_loop() -> None:
    """Task assíncrona: coleta notícias a cada CHECK_INTERVAL_HOURS."""
    await asyncio.sleep(90)  # deixa o app subir por completo antes do 1º ciclo
    loop = asyncio.get_event_loop()

    while True:
        try:
            logger.info("[news] iniciando ciclo de coleta...")
            await loop.run_in_executor(None, _collect_once)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("[news] erro inesperado no ciclo: %s", exc, exc_info=True)
            log_system_event_sync(
                "error", "news_bot_error",
                f"Erro inesperado no news_bot: {exc}",
                source="news_bot",
            )

        try:
            await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)
        except asyncio.CancelledError:
            break
