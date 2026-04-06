# core/handlers/launches.py
from __future__ import annotations
import re
import db
from utils_text import fmt_brl, is_internal_category
from core.services.category_service import infer_category
from parsers import parse_receita_despesa_natural


def list_launches(user_id: int, limit: int = 10) -> str:
    rows = db.list_launches(user_id, limit=limit)
    if not rows:
        return "Você ainda não tem lançamentos."

    lines = []
    for r in rows:
        tipo = r.get("tipo", "")
        valor = r.get("valor")
        alvo = r.get("alvo") or "-"
        nota = r.get("nota") or ""
        criado = r.get("criado_em")

        # limpa nota de investimento
        if tipo == "create_investment" and nota and "taxa=" in nota:
            try:
                m_taxa = re.search(r"taxa=([0-9.]+)", nota)
                m_per = re.search(r"periodo=(\w+)", nota)
                taxa = float(m_taxa.group(1)) * 100 if m_taxa else None
                per = m_per.group(1) if m_per else ""
                per = "ao mês" if per.startswith("month") else "ao dia" if per.startswith("day") else per
                nota = f"{taxa:.4g}% {per}" if taxa is not None else nota
            except Exception:
                pass

        valor_str = fmt_brl(float(valor)) if valor is not None else "-"
        nota_part = f" • {nota}" if nota else ""
        created_str = str(criado) if criado is not None else "-"
        lines.append(f"#{r['id']} • {tipo} • {valor_str} • {alvo}{nota_part} • {created_str}")

    return "🧾 **Últimos lançamentos**:\n" + "\n".join(lines)


def add(user_id: int, text: str, entities: dict) -> str:
    """
    Tenta parsear via parser local primeiro (determinístico).
    Se falhar, usa as entidades vindas da IA.
    """
    parsed = parse_receita_despesa_natural(user_id, text)

    if parsed:
        tipo      = parsed["tipo"]
        valor     = float(parsed["valor"])
        categoria = parsed.get("categoria") or "outros"
        alvo      = parsed.get("alvo") or ""
        nota      = parsed.get("nota") or text
        criado_em = parsed.get("criado_em")
        is_int    = parsed.get("is_internal_movement", False)
    else:
        # fallback: entidades da IA
        tipo  = entities.get("tipo", "despesa")
        valor = float(entities.get("valor", 0))
        if valor <= 0:
            return "Não consegui identificar o valor. Tente: *gastei 50 no mercado*"
        alvo  = entities.get("alvo") or ""
        nota  = text
        res   = infer_category(user_id, nota, entities.get("categoria"))
        categoria = res.category
        criado_em = None
        is_int    = is_internal_category(categoria)

    launch_id, new_balance = db.add_launch_and_update_balance(
        user_id=user_id,
        tipo=tipo,
        valor=valor,
        alvo=alvo or None,
        nota=nota,
        categoria=categoria,
        criado_em=criado_em,
        is_internal_movement=is_int,
    )

    emoji = "💸" if tipo == "despesa" else "💰"
    return (
        f"{emoji} **{tipo.capitalize()} registrada**: {fmt_brl(valor)}\n"
        f"🏷️ Categoria: {categoria}\n"
        f"🏦 Saldo: {fmt_brl(float(new_balance))}\n"
        f"ID: #{launch_id}"
    )


def propose_delete(user_id: int, launch_id: int) -> str:
    db.set_pending_action(user_id, "delete_launch", {"launch_id": launch_id})
    return (
        f"⚠️ Isso vai apagar o lançamento **#{launch_id}** e desfazer seus efeitos no saldo.\n"
        "Confirma? Responda **sim** ou **não**."
    )


def undo(user_id: int) -> str:
    rows = db.list_launches(user_id, limit=1)
    if not rows:
        return "Não há lançamentos para desfazer."
    last_id = rows[0]["id"]
    db.set_pending_action(user_id, "delete_launch", {"launch_id": last_id})
    tipo  = rows[0].get("tipo", "")
    valor = fmt_brl(float(rows[0].get("valor") or 0))
    return (
        f"⚠️ Desfazer o último lançamento: **#{last_id}** ({tipo} {valor})?\n"
        "Confirma? Responda **sim** ou **não**."
    )
