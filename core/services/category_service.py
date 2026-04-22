# core/services/category_service.py
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from utils_text import (
    normalize_text,
    LOCAL_RULES,
    contains_word,
    extract_memory_candidates,
    canonicalize_category_label,
)
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
        cat = canonicalize_category_label(explicit_category)
        return InferResult(category=cat or "outros", reason="explicit")

    t = normalize_text(text_base or "")
    if not t:
        return InferResult(category="outros", reason="default")

    # B) regras do usuário (mesma fonte do comando "criar categoria ... linkar ...")
    cat = get_memorized_category(user_id, t)
    if cat:
        return InferResult(category=canonicalize_category_label(cat), reason="user_rule")

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
                return InferResult(category=canonicalize_category_label(cat2), reason="local_rule")

    # D) fallback IA (só se OPENAI_API_KEY configurada)
    if os.getenv("OPENAI_API_KEY"):
        try:
            from ai_router import classify_category_with_gpt
            cat_ai = classify_category_with_gpt(t, user_id=user_id, source="core.services.category_service")
            if cat_ai and cat_ai != "outros":
                return InferResult(category=cat_ai, reason="ai")
        except Exception:
            pass

    return InferResult(category="outros", reason="default")


def learn_from_explicit_category(
    user_id: int,
    text_base: str,
    chosen_category: str,
    inferred_category: str | None = None,
    source: str | None = None,
    launch_id: int | None = None,
) -> None:
    learn_from_signals(user_id, chosen_category, text_base)


def learn_from_signals(
    user_id: int,
    chosen_category: str,
    *signals: str | None,
) -> None:
    cat = normalize_text(chosen_category or "")
    if not cat or cat == "outros":
        return
    if cat in {"investimento_aporte", "investimento_resgate", "transferencia_interna", "pagamento_fatura", "ajuste_saldo"}:
        return

    seen: set[str] = set()
    for signal in signals:
        for candidate in extract_memory_candidates(signal):
            if candidate in seen:
                continue
            seen.add(candidate)
            upsert_category_rule(user_id, candidate, cat)


def learn_from_inference(
    user_id: int,
    text_base: str,
    chosen_category: str,
    *,
    target_hint: str | None = None,
    reason: str | None = None,
) -> None:
    if reason in {"default", "user_rule"}:
        return
    learn_from_signals(user_id, chosen_category, target_hint, text_base)
