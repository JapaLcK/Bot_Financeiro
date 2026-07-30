# core/services/category_service.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from utils_text import (
    normalize_text,
    LOCAL_RULES,
    contains_word,
    extract_memory_candidates,
    canonicalize_category_label,
    EXACT_WORD_KEYWORDS,
    KEYWORD_BLOCKERS,
)
from db import get_memorized_category, upsert_category_rule


# Tickers brasileiros (B3): 4 letras + 1 ou 2 dígitos.
# Pega ações ON/PN (PETR3, VALE3, ITUB4), units (SANB11) e FIIs/ETFs
# (MXRF11, HGLG11, BOVA11, IVVB11). Em MAIÚSCULAS aceita sem contexto;
# em minúsculas (wege3, mxrf11) exige palavra-chave de operação financeira
# na mesma frase pra evitar falsos positivos como "casa12 brinquedos".
_BR_TICKER_UPPER_RE = re.compile(r"\b[A-Z]{4}\d{1,2}\b")
_BR_TICKER_ANY_RE   = re.compile(r"\b[A-Za-z]{4}\d{1,2}\b")
_INVEST_CONTEXT_RE  = re.compile(
    r"\b(comprei|comprou|vendi|vendeu|aporte|aportei|investi|investir|"
    r"aplique[ei]|aplicacao|aplicação|cotas?|dividendos?|proventos?|"
    r"acoes|ações|acao|ação|fiis?|ticker|cot)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InferResult:
    category: str
    reason: str  # 'explicit' | 'user_rule' | 'local_rule' | 'ticker_match' | 'default'


def local_rule_category(text_norm: str) -> str | None:
    """
    Categoria canônica das LOCAL_RULES para um texto JÁ normalizado — sem regra
    de usuário, ticker ou IA. Fonte única do passo C de `infer_category` e também
    usada como guard no auto-aprendizado (não deixar um keyword canônico como
    "mercado" ser reaprendido com outra categoria).
    """
    if not text_norm:
        return None
    for keywords, cat2 in LOCAL_RULES:
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if not kw_norm:
                continue
            # Casos que devem ser palavra inteira:
            #  - keyword muito curta (≤3): evita "lca" bater em "cavalcante"
            #  - keyword na lista EXACT_WORD_KEYWORDS: evita "acoes" bater em
            #    "transações", "investi" em "investigar", etc.
            if len(kw_norm) <= 3 or kw_norm in EXACT_WORD_KEYWORDS:
                ok = contains_word(text_norm, kw_norm)
            else:
                ok = contains_word(text_norm, kw_norm) or (kw_norm in text_norm)

            # Contexto que invalida a keyword (ex.: "feira" em "sexta-feira").
            blocker = KEYWORD_BLOCKERS.get(kw_norm)
            if ok and blocker and blocker.search(text_norm):
                ok = False

            if ok:
                return canonicalize_category_label(cat2)
    return None


def infer_category(user_id: int, text_base: str, explicit_category: str | None = None, *, allow_ai: bool = True) -> InferResult:
    """
    Prioridade:
      A) explícita (hashtag/cat=)
      B) regra do usuário (user_category_rules via get_memorized_category)
      C) ticker brasileiro detectado (PETR4, MXRF11, …)
      D) heurística local (LOCAL_RULES)
      E) fallback IA (só se allow_ai e Pro)
      default: 'outros'

    `allow_ai=False` pula o passo E — usado quando já existe uma categoria da
    IA e só se quer o veredito determinístico (regra do usuário/ticker/local)
    pra fazer cross-check, sem gastar uma segunda chamada de LLM.
    """
    if explicit_category:
        cat = canonicalize_category_label(explicit_category)
        return InferResult(category=cat or "outros", reason="explicit")

    # C) ticker BR em MAIÚSCULAS — convenção do mercado, baixíssimo risco
    #    de falso positivo. Aceita direto.
    #    Ticker em minúsculas (wege3, capa15) NÃO bate aqui pra evitar falsos
    #    positivos como "comprei capa15 brinquedo"; o GPT (passo D) decide.
    if text_base and _BR_TICKER_UPPER_RE.search(text_base):
        return InferResult(
            category=canonicalize_category_label("investimento_aporte"),
            reason="ticker_match",
        )

    t = normalize_text(text_base or "")
    if not t:
        return InferResult(category="outros", reason="default")

    # B) regras do usuário (mesma fonte do comando "criar categoria ... linkar ...")
    cat = get_memorized_category(user_id, t)
    if cat:
        return InferResult(category=canonicalize_category_label(cat), reason="user_rule")

    # C) LOCAL_RULES
    local_cat = local_rule_category(t)
    if local_cat:
        return InferResult(category=local_cat, reason="local_rule")

    # D) fallback IA (só se OPENAI_API_KEY configurada e usuário Pro)
    if allow_ai and os.getenv("OPENAI_API_KEY"):
        try:
            from core.services.plan_service import is_pro
            allow_ai = is_pro(int(user_id))
        except Exception:
            allow_ai = False

        if allow_ai:
            try:
                from ai_router import classify_category_with_gpt
                cat_ai = classify_category_with_gpt(t, user_id=user_id, source="core.services.category_service")
                if cat_ai:
                    # Aceita até "outros" do GPT — significa que ele analisou e
                    # decidiu que não há categoria clara, em vez de cair em
                    # default por falta de tentativa.
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
    guard_local_conflict: bool = False,
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
            # Guard (só no auto-aprendizado): não memoriza um keyword que já tem
            # categoria canônica DIFERENTE nas LOCAL_RULES. Sem isso, "gastei no
            # mercado" classificado errado como "alimentação" aprendia
            # mercado→alimentação e travava pra sempre (regra de usuário vence a
            # LOCAL_RULE). Escolha EXPLÍCITA do usuário não passa por aqui.
            if guard_local_conflict:
                local_cat = local_rule_category(normalize_text(candidate))
                if local_cat and normalize_text(local_cat) != cat:
                    continue
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
    # Aprende do ALVO LIMPO (target_hint = `alvo` extraído pelo parser) quando ele
    # existe, e NÃO do text_base inteiro. O text_base é a mensagem/transcrição
    # crua — com wake-word ("pig"), valor por extenso ("reais"/"centavos"),
    # conectores e frases longas — que poluía user_category_rules com keywords
    # como "pig eu mercado mais" ou "reais centavos almoco". Só cai no text_base
    # quando não há alvo (o filtro de ruído em extract_memory_candidates ainda
    # limpa o resto).
    hint = (target_hint or "").strip()
    signal = target_hint if hint else text_base
    learn_from_signals(user_id, chosen_category, signal, guard_local_conflict=True)
