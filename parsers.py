import re
from utils_text import normalize_text, is_internal_category
from utils_date import extract_date_from_text
from core.services.category_service import infer_category, learn_from_explicit_category


# ---------------------------------------------------------------------------
# Conversão de números por extenso (português) → float
# Cobre saídas comuns do Whisper: "mil e trezentos", "trinta e cinco"
# ---------------------------------------------------------------------------

_PT_HUNDREDS = {
    "cem": 100, "cento": 100,
    "duzentos": 200, "duzentas": 200,
    "trezentos": 300, "trezentas": 300,
    "quatrocentos": 400, "quatrocentas": 400,
    "quinhentos": 500, "quinhentas": 500,
    "seiscentos": 600, "seiscentas": 600,
    "setecentos": 700, "setecentas": 700,
    "oitocentos": 800, "oitocentas": 800,
    "novecentos": 900, "novecentas": 900,
}
_PT_TENS = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50,
    "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90,
}
_PT_ONES = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2,
    "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11,
    "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14,
    "quinze": 15, "dezesseis": 16, "dezessete": 17,
    "dezoito": 18, "dezenove": 19,
}
_PT_ALL = {**_PT_HUNDREDS, **_PT_TENS, **_PT_ONES}


def _words_to_number(text: str) -> float | None:
    """
    Tenta converter sequência de palavras numéricas em português para float.
    Ex: "mil e trezentos" → 1300.0, "trinta e cinco" → 35.0
    Retorna None se não reconhecer nada.
    """
    import unicodedata
    def _norm(s: str) -> str:
        s = s.lower().strip()
        return "".join(
            c for c in unicodedata.normalize("NFKD", s)
            if not unicodedata.combining(c)
        )

    tokens = re.split(r"[\s,]+", _norm(text))
    tokens = [t for t in tokens if t and t not in ("e", "de", "com")]

    if not tokens:
        return None
    if not any(t in _PT_ALL or t == "mil" for t in tokens):
        return None

    total = 0.0
    current = 0.0
    found_any = False

    for tok in tokens:
        if tok == "mil":
            current = current if current > 0 else 1
            total += current * 1000
            current = 0.0
            found_any = True
        elif tok in _PT_HUNDREDS:
            current += _PT_HUNDREDS[tok]
            found_any = True
        elif tok in _PT_TENS:
            current += _PT_TENS[tok]
            found_any = True
        elif tok in _PT_ONES:
            current += _PT_ONES[tok]
            found_any = True

    total += current
    return total if found_any and total > 0 else None


def _extract_valor(text: str) -> float | None:
    """
    Extrai o valor monetário de um texto, suportando:
      - Números: "30", "30,50", "30.50", "R$ 30,50"
      - "X reais e Y centavos": "30 reais e 50 centavos" → 30.50
      - Números por extenso: "mil e trezentos" → 1300
    """
    # 1. "X reais e Y centavos" (saída comum do Whisper)
    m = re.search(
        r"(\d+)\s+reais?\s+e\s+(\d+)\s+centavos?",
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1)) + float(m.group(2)) / 100

    # 2. Número com separador decimal ("30,50" ou "30.50")
    m = re.search(r"(\d+(?:[.,]\d{3})*[.,]\d{2})\b", text)
    if m:
        raw = m.group(1).replace(".", "").replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            pass

    # 3. Número inteiro simples
    m = re.search(r"\b(\d+)\b", text)
    if m:
        return float(m.group(1))

    # 4. Número por extenso (mil, trezentos, etc.)
    return _words_to_number(text)


def _extract_explicit_category(raw_text: str) -> tuple[str, str | None]:
    t = (raw_text or "").strip()
    if not t:
        return t, None

    # formato: #alimentacao
    m = re.search(r"(?:^|\s)#([a-zA-ZÀ-ÿ0-9_\-]+)\b", t)
    if m:
        cat = m.group(1)
        t2 = (t[: m.start()] + t[m.end() :]).strip()
        return t2, cat

    # formato: cat=alimentacao
    m = re.search(r"(?:^|\s)cat=([a-zA-ZÀ-ÿ0-9_\-]+)\b", t)
    if m:
        cat = m.group(1)
        t2 = (t[: m.start()] + t[m.end() :]).strip()
        return t2, cat

    # formato: "categoria alimentacao" ou "categoria: alimentacao"
    m = re.search(r"\bcategor(?:ia)?[:\s]+([a-zA-ZÀ-ÿ0-9_\-]+)\b", t, re.IGNORECASE)
    if m:
        cat = m.group(1)
        t2 = (t[: m.start()] + t[m.end() :]).strip()
        return t2, cat

    return t, None


def _extract_target_after_amount(text_base: str) -> str:
    t = (text_base or "").strip()
    if not t:
        return ""

    t = re.sub(r"^\s*(gastei|gasto|paguei|pagar|comprei|debitei|mandei|enviei|pixei|recebi|receita|ganhei)\b", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^\s*\d+(?:[.,]\d+)?\b", "", t, count=1).strip()
    t = re.sub(r"^\s*reais?\b", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^\s*(de|do|da|dos|das|no|na|nos|nas|em|pra|para)\b", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s+", " ", t).strip(" -:;,.")
    return t


def parse_receita_despesa_natural(user_id: int, raw_text: str) -> dict | None:
    text_clean = (raw_text or "").strip()
    if not text_clean:
        return None

    # categoria explícita (se houver)
    text_for_parse, explicit_cat = _extract_explicit_category(text_clean)

    # extrai data do texto
    dt_evento, text_without_date = extract_date_from_text(text_for_parse)

    # usa o texto sem a data para tipo/categoria/valor
    text_base = text_without_date.strip() if text_without_date else text_for_parse.strip()
    raw_norm = normalize_text(text_base)

    # tipo
    tipo = None
    if raw_norm.startswith(("gastei ", "gasto ", "paguei ", "pagar ", "comprei ", "debitei ", "mandei ", "enviei ", "pixei ")):
        tipo = "despesa"
    elif raw_norm.startswith(("recebi ", "receita ", "ganhei ")):
        tipo = "receita"
    else:
        return None

    # valor
    valor = _extract_valor(text_base)
    if valor is None or valor <= 0:
        return None

    # Passa o texto NÃO normalizado pra infer_category preservar maiúsculas
    # (a detecção de ticker BR exige uppercase: PETR4, VALE3, MXRF11...).
    res = infer_category(user_id=user_id, text_base=text_base, explicit_category=explicit_cat)
    categoria = res.category

    # aprendizado automático (somente se explícita)
    if explicit_cat:
        inferred_no_explicit = infer_category(
            user_id=user_id,
            text_base=text_base,
            explicit_category=None
        ).category
        learn_from_explicit_category(
            user_id=user_id,
            text_base=raw_norm,
            chosen_category=explicit_cat,
            inferred_category=inferred_no_explicit,
            source="manual",
            launch_id=None,
        )

    alvo = _extract_target_after_amount(text_base)

    return {
        "tipo": tipo,
        "valor": valor,
        "categoria": categoria,
        "category_reason": res.reason,
        "alvo": alvo,
        "nota": text_base.strip(),
        "criado_em": dt_evento,
        "is_internal_movement": is_internal_category(categoria),
    }
