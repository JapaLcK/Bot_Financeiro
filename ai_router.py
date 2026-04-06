# ai_router.py
"""
Responsabilidade única: classificação de categorias financeiras via IA.

A IA NÃO executa mais ações diretamente.
Toda a classificação de INTENÇÃO agora está em core/intent_classifier.py.
Toda a execução está nos handlers em core/handlers/.

Este arquivo mantém apenas:
  - classify_category_with_gpt() → usado por category_service.py como fallback de categoria
  - _internal_user_id()           → converte IDs grandes (WhatsApp) em int seguro
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata

from openai import OpenAI


# ---------------------------------------------------------------------------
# Cliente OpenAI (lazy init)
# ---------------------------------------------------------------------------

_client: OpenAI | None = None
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _get_client() -> OpenAI | None:
    global _client
    if _client is not None:
        return _client
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None
    _client = OpenAI(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Categorias permitidas
# ---------------------------------------------------------------------------

ALLOWED_CATEGORIES = [
    "alimentação", "transporte", "saúde", "moradia", "lazer",
    "educação", "assinaturas", "pets", "compras online", "beleza", "outros",
]

_CATEGORY_ALIASES = {
    "alimentacao": "alimentação",
    "saude":       "saúde",
    "educacao":    "educação",
    "compra online": "compras online",
    "compras":     "compras online",
    "online":      "compras online",
    "pet":         "pets",
    "petshop":     "pets",
}


def _norm(text: str) -> str:
    t = (text or "").strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_category(cat: str) -> str:
    norm = _norm(cat)
    mapped = _CATEGORY_ALIASES.get(norm, norm)
    allowed_norm_map = {_norm(c): c for c in ALLOWED_CATEGORIES}
    return allowed_norm_map.get(_norm(mapped), "outros")


# ---------------------------------------------------------------------------
# classify_category_with_gpt
# ---------------------------------------------------------------------------

def classify_category_with_gpt(descricao: str) -> str:
    """
    Classifica uma descrição de lançamento em uma das categorias canônicas.
    Usado por category_service.py como fallback quando regras locais não batem.
    Retorna 'outros' se não houver chave ou ocorrer erro.
    """
    descricao = (descricao or "").strip()
    if not descricao:
        return "outros"

    if not os.getenv("OPENAI_API_KEY"):
        return "outros"

    current_client = _get_client()
    if current_client is None:
        return "outros"

    prompt = (
        "Você é um classificador de categorias financeiras.\n"
        "Responda com UMA única categoria exatamente dentre as opções abaixo.\n"
        "Não explique. Não use pontuação. Não escreva mais nada.\n"
        "Categorias: " + ", ".join(ALLOWED_CATEGORIES) + "\n\n"
        "Exemplos:\n"
        "petshop, ração, veterinário → pets\n"
        "psicólogo, terapia, remédio, dentista → saúde\n"
        "aluguel, condomínio, luz, internet → moradia\n"
        "uber, 99, gasolina, ônibus, metrô → transporte\n"
        "mercado, ifood, restaurante, padaria → alimentação\n"
        "amazon, shopee, compra online → compras online\n"
        "livros, curso, aulas → educação\n"
        "spotify, youtube, netflix → assinaturas\n\n"
        f"Texto: {descricao}\n"
        "Resposta:"
    )

    try:
        resp = current_client.chat.completions.create(
            model=MODEL,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
        )
        cat_raw = (resp.choices[0].message.content or "").strip()
        return _normalize_category(cat_raw)

    except Exception as e:
        print(f"[ai_router] classify_category error: {e}")
        return "outros"


# ---------------------------------------------------------------------------
# _internal_user_id
# ---------------------------------------------------------------------------

def _internal_user_id(raw_user_id: int | str) -> int:
    """
    Converte um user_id potencialmente enorme (ex: WhatsApp) em um int seguro (32-bit),
    estável entre execuções, para não estourar colunas INTEGER no banco.
    """
    s = str(raw_user_id)
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], "big")
    return int(n % 2_000_000_000) + 1
