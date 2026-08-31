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
    STOPWORDS_PT,
    MEMORY_NOISE_TOKENS,
    MEMORY_STOP_TOKENS,
)
from db import (
    get_memorized_category,
    get_memorized_rule,
    upsert_category_rule,
    list_custom_category_names,
    resolve_category_input,
)


# Tokens genéricos que aparecem em NOMES de categoria mas não identificam a
# categoria por si só ("gastos com minha namorada" → o que identifica é
# "namorada", não "gastos"/"com"/"minha"). Somados aos stopwords/ruído de fala,
# evitam que uma categoria custom bata em qualquer lançamento só por conter
# "gastos". "outros" fica de fora de propósito (é o próprio fallback).
# Inclui também palavras curtas de tempo/genéricas ("dia", "mes", "ano") pra
# não deixar uma categoria como "gastos do dia" casar qualquer lançamento.
_CUSTOM_CATEGORY_GENERIC_TOKENS = {
    "gasto", "gastos", "gasta", "gastar",
    "despesa", "despesas", "conta", "contas", "custo", "custos",
    "coisa", "coisas", "geral", "gerais", "diverso", "diversos",
    "outros", "outro", "outra", "outras",
    "dia", "dias", "mes", "meses", "ano", "anos", "vez", "vezes",
}

# Comprimento mínimo de um token distintivo. 3 é seguro porque o match é sempre
# palavra inteira (contains_word) — permite categorias reais e curtas como
# "gastos com minha mãe", "pai", "pet", "bar" sem casar substrings.
_CUSTOM_CATEGORY_MIN_TOKEN_LEN = 3


def _distinctive_tokens(category_name: str) -> list[str]:
    """Tokens que de fato identificam uma categoria custom pelo nome.

    Remove verbos/conectores (STOPWORDS_PT), jargão bancário/de fala
    (MEMORY_*_TOKENS) e nomes genéricos de gasto (_CUSTOM_CATEGORY_GENERIC_TOKENS),
    mantendo só tokens com pelo menos `_CUSTOM_CATEGORY_MIN_TOKEN_LEN` letras.
    """
    out: list[str] = []
    for tok in normalize_text(category_name).split():
        if len(tok) < _CUSTOM_CATEGORY_MIN_TOKEN_LEN:
            continue
        if tok.isdigit():
            continue
        if (
            tok in STOPWORDS_PT
            or tok in MEMORY_NOISE_TOKENS
            or tok in MEMORY_STOP_TOKENS
            or tok in _CUSTOM_CATEGORY_GENERIC_TOKENS
        ):
            continue
        out.append(tok)
    return out


def custom_category_match(user_id: int, text_norm: str) -> str | None:
    """Casa um texto JÁ normalizado com uma categoria CUSTOM do usuário.

    O usuário pode criar uma categoria na tela (ex.: "gastos com minha
    namorada") sem cadastrar uma regra de keyword. Quando um lançamento menciona
    um token distintivo do nome dela ("namorada"), o gasto deve ir pra essa
    categoria — não pra "outros".

    Escolhe a categoria com MAIS tokens distintivos presentes no texto; empate é
    desfeito pelo maior comprimento total casado (nome mais específico vence).
    Retorna o nome da categoria (como cadastrado) ou None.
    """
    if not text_norm:
        return None

    best_name: str | None = None
    best_score = 0
    best_len = 0
    for name in list_custom_category_names(user_id) or []:
        tokens = _distinctive_tokens(name)
        if not tokens:
            continue
        matched = [tok for tok in tokens if contains_word(text_norm, tok)]
        if not matched:
            continue
        score = len(matched)
        matched_len = sum(len(tok) for tok in matched)
        if score > best_score or (score == best_score and matched_len > best_len):
            best_name = name
            best_score = score
            best_len = matched_len

    return best_name


def _regra_ficou_obsoleta(user_id: int, keyword: str, destino: str, criada_em) -> bool:
    """A regra aprendida envelheceu porque o usuário criou a categoria DEPOIS?

    Duas condições, e as duas importam:

    1. **colide** — `custom_category_match` sobre o KEYWORD devolve uma categoria
       diferente do destino da regra. É exatamente a função que o passo B2 usa
       para reconhecer categoria custom num texto: mesmo critério, sem segunda
       heurística.
    2. **é posterior** — essa categoria nasceu depois da regra. Sem isto o guard
       derrubaria também a regra DELIBERADA: quem tem a categoria "gastos com
       minha namorada" e linkou `namorada -> lazer` de propósito fez uma escolha,
       e o `test_regra_de_keyword_vence_categoria_custom` afirma que ela vence.
       Regra obsoleta é a que já existia quando a categoria apareceu.

    A 2ª consulta só roda quando há colisão — no caminho quente, sem categoria
    custom conflitante, o custo é o de sempre.
    """
    dona = custom_category_match(user_id, normalize_text(keyword or ""))
    if not dona or normalize_text(dona) == normalize_text(destino or ""):
        return False
    from db.categories import categoria_criada_depois_de

    return categoria_criada_depois_de(user_id, dona, criada_em)


def reconciliar_regras_com_categoria(user_id: int, nome_categoria: str) -> int:
    """Reaponta para `nome_categoria` as regras que ela passou a possuir.

    Chamado quando a categoria é criada. Reaponta em vez de apagar porque aqui
    não há adivinhação: `custom_category_match` sobre o keyword devolveu ESTA
    categoria, então o destino novo é o mesmo match que o sistema faria pelo B2
    de qualquer forma. Quando a resposta for outra categoria, ou nenhuma, a
    regra não é tocada — ambiguidade não vira palpite.

    Devolve quantas regras foram reapontadas.
    """
    from db.categories import list_user_category_rules, upsert_category_rule

    alvo_norm = normalize_text(nome_categoria or "")
    if not alvo_norm:
        return 0

    n = 0
    for keyword, destino in list_user_category_rules(user_id) or []:
        dona = custom_category_match(user_id, normalize_text(keyword or ""))
        if not dona or normalize_text(dona) != alvo_norm:
            continue                                   # não é desta categoria
        if normalize_text(destino or "") == alvo_norm:
            continue                                   # já aponta pra cá
        upsert_category_rule(user_id, keyword, nome_categoria)
        n += 1
    return n


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
        # Texto livre do usuário (hashtag `#McDonald's`, `categoria` da tool
        # `add_launch`): tem de virar a grafia do catálogo, senão o lançamento
        # nasce em `mcdonald s` e abre fatia gêmea da `mcdonald's` que já
        # existe. `create=False`: inferência é READ-ONLY (ver
        # `user_category_display_map`); quem grava é a porta.
        cat = resolve_category_input(user_id, explicit_category, create=False) \
            or canonicalize_category_label(explicit_category)
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
    #
    # A regra mantém a precedência que sempre teve — ela só perde quando ficou
    # OBSOLETA: o usuário criou depois uma categoria custom que, pelo mesmo
    # critério que o B2 usa para reconhecer categoria (custom_category_match
    # sobre o keyword), passou a ser a dona daquele termo. Aí a regra aponta
    # para o lugar errado e cede para o B2 abaixo — sem inverter a precedência
    # global entre os dois passos.
    #
    # Isto conserta conta ANTIGA sem script: a regra envenenada há meses deixa
    # de vencer na primeira classificação depois deste código subir.
    regra = get_memorized_rule(user_id, t)
    if regra is not None and _regra_ficou_obsoleta(user_id, regra[0], regra[1], regra[2]):
        regra = None
    cat = regra[1] if regra else None
    if cat:
        # A regra guarda a categoria NORMALIZADA (sem acento) — é índice interno.
        # A forma de exibição vem de user_categories; o canonicalize é o
        # fallback de quando a regra aponta pra categoria que não existe lá.
        # ponytail: +1 query por inferência que bate em regra do usuário. Se
        # pesar, o display_map vira cache por request (os importadores já o
        # pré-carregam uma vez, que é onde o N+1 doeria).
        return InferResult(
            category=resolve_category_input(user_id, cat, create=False) or canonicalize_category_label(cat),
            reason="user_rule",
        )

    # B2) categoria CUSTOM criada pelo usuário (na tela) sem regra de keyword.
    #     Se o lançamento menciona um token distintivo do nome dela, usa. Vem
    #     antes das LOCAL_RULES porque é personalização explícita do usuário.
    try:
        custom_cat = custom_category_match(user_id, t)
    except Exception:
        custom_cat = None
    if custom_cat:
        # Retorna o nome EXATAMENTE como cadastrado em user_categories (pode ter
        # acento, ex.: "saúde da família"). Não passa por canonicalize_category_label
        # — isso removeria acentos e o lançamento deixaria de agrupar com a
        # categoria definida pelo usuário no dashboard.
        return InferResult(category=custom_cat, reason="user_category")

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
                # Não roubar um token que já pertence a uma categoria CUSTOM do
                # próprio usuário. Sem isso, "namorada cinema" (que casa
                # cinema→lazer nas LOCAL_RULES) aprendia namorada→lazer e sequestrava
                # todo lançamento com "namorada" pra lazer — a categoria custom
                # "gastos com namorada" (passo B2, depois das regras) nunca era
                # alcançada. O guard local acima não pega: "namorada" não está nas
                # LOCAL_RULES, mas é o token distintivo da categoria do usuário.
                custom_cat = custom_category_match(user_id, normalize_text(candidate))
                if custom_cat and normalize_text(custom_cat) != cat:
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
    if reason in {"default", "user_rule", "user_category"}:
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
