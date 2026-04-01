import re
from utils_text import normalize_text, is_internal_category
from utils_date import extract_date_from_text
from core.services.category_service import infer_category, learn_from_explicit_category


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
    if raw_norm.startswith("gastei ") or raw_norm.startswith("gasto "):
        tipo = "despesa"
    elif raw_norm.startswith("recebi ") or raw_norm.startswith("receita "):
        tipo = "receita"
    else:
        return None

    # valor
    m = re.search(r"(\d+[.,]?\d*)", text_base)
    if not m:
        return None

    valor_txt = m.group(1).replace(".", "").replace(",", ".")
    try:
        valor = float(valor_txt)
    except Exception:
        return None

    # inferência única (A > B > C)
    res = infer_category(user_id=user_id, text_base=raw_norm, explicit_category=explicit_cat)
    categoria = res.category

    # aprendizado automático (somente se explícita)
    if explicit_cat:
        inferred_no_explicit = infer_category(
            user_id=user_id,
            text_base=raw_norm,
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

    alvo = ""

    return {
        "tipo": tipo,
        "valor": valor,
        "categoria": categoria,
        "alvo": alvo,
        "nota": text_base.strip(),
        "criado_em": dt_evento,
        "is_internal_movement": is_internal_category(categoria),
    }