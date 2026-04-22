# core/handlers/categories.py
from __future__ import annotations

import re

import db
from core.services.category_service import learn_from_signals
from utils_text import normalize_text


def list_categories(user_id: int) -> str:
    cats  = db.list_categories(user_id) or []
    rules = db.list_category_rules(user_id) or []   # lista de (keyword, category)

    by_cat: dict[str, list[str]] = {}
    for kw, c in rules:
        by_cat.setdefault(c, []).append(kw)

    if not cats and not rules:
        return (
            "Você ainda não tem regras de categoria.\n"
            "Exemplos:\n"
            "• `aprender ifood como alimentacao`\n"
            "• `aprender rifa como aposta`"
        )

    cats_all = sorted(set(list(cats) + list(by_cat.keys())))
    lines = ["🧠 **Regras de categoria**"]
    for c in cats_all:
        kws = by_cat.get(c, [])
        lines.append(f"• **{c}** ({len(kws)} regras)")
        if kws:
            lines.append("  └ " + ", ".join(kws))

    lines.append("")
    lines.append("Aprender: `aprender <gasto/palavra-chave> como <categoria>`")
    lines.append("Remover: `remover regra <gasto/palavra-chave>`")
    lines.append("")
    lines.append("Dica: o bot também aprende sozinho conforme você lança e corrige categorias.")
    return "\n".join(lines)


def create(user_id: int, text: str) -> str:
    t = text.strip()
    lower = normalize_text(t)

    kw = ""
    cat = ""

    m = re.match(r"^aprender\s+(.+?)\s+como\s+(.+)$", t, flags=re.IGNORECASE)
    if m:
        kw = m.group(1).strip().strip('"').strip("'")
        cat = m.group(2).strip().strip('"').strip("'")
    elif lower.startswith("linkar "):
        rest = t[len("linkar "):].strip()
        parts = rest.rsplit(" ", 1)
        if len(parts) == 2:
            kw = parts[0].strip().strip('"').strip("'")
            cat = parts[1].strip().strip('"').strip("'")
    elif lower.startswith("criar categoria "):
        rest = t[len("criar categoria "):].strip()
        rest_low = rest.lower()
        sep1 = " linkar destinatario "
        sep2 = " linkar "
        if sep1 in rest_low:
            idx = rest_low.index(sep1)
            cat = rest[:idx].strip()
            kw = rest[idx + len(sep1):].strip().strip('"').strip("'")
        elif sep2 in rest_low:
            idx = rest_low.index(sep2)
            cat = rest[:idx].strip()
            kw = rest[idx + len(sep2):].strip().strip('"').strip("'")

    if not cat or not kw:
        return (
            "Formato: `aprender <gasto/palavra-chave> como <categoria>`\n"
            "Exemplo: `aprender rifa como aposta`"
        )

    learn_from_signals(user_id, cat, kw)
    return f"✅ Aprendido: sempre que aparecer **{kw}**, vou usar **{cat}**"


def delete(user_id: int, text: str) -> str:
    keyword = re.sub(
        r"^(remove|remover|apagar|excluir|deletar)\s+(regra|destinatario)\s+",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    ).strip()
    if not keyword:
        return "Qual regra você quer remover? Tente: *remover regra ifood*"
    kw = keyword.strip().strip('"').strip("'")
    n  = db.delete_category_rule(user_id, kw)
    if n:
        return f"✅ Regra removida: **{kw}**"
    return f"⚠️ Não encontrei regra para: **{kw}**"
