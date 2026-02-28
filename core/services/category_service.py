# core/services/category_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from utils_text import normalize_text, LOCAL_RULES, contains_word, extract_keyword_for_memory
from db import get_memorized_category, upsert_category_rule


@dataclass(frozen=True)
class InferResult:
    category: str
    reason: str  # 'explicit' | 'user_rule' | 'local_rule' | 'default'


def infer_category(user_id: int, text_base: str, explicit_category: str | None = None) -> InferResult:
    """
    Prioridade:
      A) explícita (hashtag/cat=)
      B) regra do usuário (user_category_rules via get_memorized_category)
      C) heurística local (LOCAL_RULES)
      default: 'outros'
    """
    if explicit_category:
        cat = normalize_text(explicit_category)
        return InferResult(category=cat or "outros", reason="explicit")

    t = normalize_text(text_base or "")
    if not t:
        return InferResult(category="outros", reason="default")

    # B) regras do usuário (mesma fonte do comando "criar categoria ... linkar ...")
    cat = get_memorized_category(user_id, t)
    if cat:
        return InferResult(category=normalize_text(cat), reason="user_rule")

    # C) LOCAL_RULES
    for keywords, cat2 in LOCAL_RULES:
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if not kw_norm:
                continue
            # evita falso positivo tipo "cavalcante" bater em "lca"
            if len(kw_norm) <= 3:
                ok = contains_word(t, kw_norm)          # só palavra inteira
            else:
                ok = contains_word(t, kw_norm) or (kw_norm in t)

            if ok:
                return InferResult(category=normalize_text(cat2), reason="local_rule")

    return InferResult(category="outros", reason="default")


def learn_from_explicit_category(
    user_id: int,
    text_base: str,
    chosen_category: str,
    inferred_category: str | None = None,
    source: str | None = None,
    launch_id: int | None = None,
) -> None:
    """
    Aprendizado automático simples (sem candidates / sem db_category):
    - extrai uma keyword do texto
    - grava keyword -> chosen_category em user_category_rules (upsert_category_rule)

    Parâmetros extras são aceitos só pra não quebrar chamadas.
    """
    cat = normalize_text(chosen_category or "")
    if not cat:
        return

    t = normalize_text(text_base or "")
    if not t:
        return

    kw = extract_keyword_for_memory(t)
    kw = normalize_text(kw or "")

    # filtros anti-lixo
    if not kw or len(kw) < 3:
        return
    if kw in {"pix", "pagamento", "debito", "credito", "compra", "transferencia", "tarifa"}:
        return

    upsert_category_rule(user_id, kw, cat)