# core/handlers/categories.py
from __future__ import annotations
import db


def list_categories(user_id: int) -> str:
    cats  = db.list_categories(user_id) or []
    rules = db.list_category_rules(user_id) or []   # lista de (keyword, category)

    by_cat: dict[str, list[str]] = {}
    for kw, c in rules:
        by_cat.setdefault(c, []).append(kw)

    if not cats and not rules:
        return (
            "Você ainda não tem categorias criadas.\n"
            "Exemplo: *criar categoria mercado linkar destinatario Carrefour*"
        )

    cats_all = sorted(set(list(cats) + list(by_cat.keys())))
    lines = ["📚 **Categorias**"]
    for c in cats_all:
        kws = by_cat.get(c, [])
        lines.append(f"• **{c}** ({len(kws)} regras)")
        if kws:
            lines.append("  └ " + ", ".join(kws))

    lines.append("")
    lines.append("Criar: `criar categoria <X> linkar destinatario <Y>`")
    lines.append("Remover: `remover destinatario <Y>`")
    return "\n".join(lines)


def create(user_id: int, text: str) -> str:
    """
    Aceita: "criar categoria X linkar destinatario Y"
    ou:     "criar categoria X linkar Y"
    """
    t = text.strip()
    if not t.lower().startswith("criar categoria "):
        return "Formato: `criar categoria <X> linkar destinatario <Y>`"

    rest = t[len("criar categoria "):].strip()
    sep1 = " linkar destinatario "
    sep2 = " linkar "

    if sep1.lower() in rest.lower():
        idx = rest.lower().index(sep1.lower())
        cat = rest[:idx].strip()
        kw  = rest[idx + len(sep1):].strip().strip('"').strip("'")
    elif sep2.lower() in rest.lower():
        idx = rest.lower().index(sep2.lower())
        cat = rest[:idx].strip()
        kw  = rest[idx + len(sep2):].strip().strip('"').strip("'")
    else:
        return "Formato: `criar categoria <X> linkar destinatario <Y>`"

    if not cat or not kw:
        return "Formato: `criar categoria <X> linkar destinatario <Y>`"

    db.add_category_rule(user_id, kw, cat)
    return f"✅ Regra criada: **{kw}** → **{cat}**"


def delete(user_id: int, keyword: str) -> str:
    if not keyword or not keyword.strip():
        return "Qual destinatário remover? Tente: *remover destinatario iFood*"
    kw = keyword.strip().strip('"').strip("'")
    n  = db.delete_category_rule(user_id, kw)
    if n:
        return f"✅ Regra removida: **{kw}**"
    return f"⚠️ Não encontrei regra para: **{kw}**"
