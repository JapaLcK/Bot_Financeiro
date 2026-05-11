# core/handlers/launches.py
from __future__ import annotations
import re
from datetime import date, timedelta

import db
from utils_text import fmt_brl, is_internal_category
from utils_date import extract_date_from_text, today_tz
from core.services.category_service import infer_category, learn_from_inference
from parsers import parse_receita_despesa_natural


# ---------------------------------------------------------------------------
# Helpers de data
# ---------------------------------------------------------------------------

def _parse_date_entity(entities: dict, original_text: str) -> date | None:
    """
    Tenta obter uma data de:
      1. entities["date_filter"] — pode ser ISO "2026-04-03", "hoje", "ontem" ou "dia 4"
      2. texto original via extract_date_from_text
    Retorna um objeto date ou None.
    """
    raw = entities.get("date_filter")
    if raw:
        raw_s = str(raw).strip().lower()

        # palavras especiais
        today = today_tz()
        if raw_s == "hoje":
            return today
        if raw_s == "ontem":
            return today - timedelta(days=1)

        # ISO direto
        try:
            return date.fromisoformat(raw_s)
        except ValueError:
            pass

        # tenta extrair do valor em si ("dia 4", "03/04", etc.)
        dt, _ = extract_date_from_text(raw_s)
        if dt:
            return dt.date()

    # fallback: extrai do texto original
    dt, _ = extract_date_from_text(original_text)
    if dt:
        return dt.date()

    return None


def _fmt_date_label(d: date) -> str:
    today = today_tz()
    if d == today:
        return "hoje"
    if d == today - timedelta(days=1):
        return "ontem"
    return d.strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# list_launches — com suporte a filtro de data
# ---------------------------------------------------------------------------

# Tipos que são ações internas de gerenciamento (não movimentações financeiras)
# Esses registros existem na tabela launches para fins de rollback/auditoria,
# mas não devem aparecer na listagem do usuário.
_INTERNAL_TIPOS = {
    "criar_caixinha", "delete_pocket",
    "create_investment", "delete_investment",
}


def list_launches(user_id: int, limit: int = 10, entities: dict | None = None, original_text: str = "") -> str:
    entities = entities or {}

    target_date = _parse_date_entity(entities, original_text)

    if target_date:
        # busca por dia específico
        rows = db.get_launches_by_period(user_id, target_date, target_date)
        label = _fmt_date_label(target_date)

        # filtra tipos internos de gerenciamento
        rows = [r for r in rows if r.get("tipo") not in _INTERNAL_TIPOS]

        if not rows:
            return f"Nenhum lançamento encontrado em **{label}**."

        # calcula totais
        total_despesas = sum(float(r["valor"]) for r in rows if r.get("tipo") == "despesa")
        total_receitas = sum(float(r["valor"]) for r in rows if r.get("tipo") == "receita")

        lines = []
        for r in rows:
            tipo   = r.get("tipo", "")
            valor  = fmt_brl(float(r["valor"])) if r.get("valor") is not None else "-"
            nota   = r.get("nota") or r.get("alvo") or "-"
            cat    = r.get("categoria") or ""
            cat_txt = f" [{cat}]" if cat else ""
            lines.append(f"#{r.get('user_seq') or r['id']} • {tipo} • {valor} • {nota}{cat_txt}")

        header = f"🧾 **Lançamentos de {label}**"
        summary_parts = []
        if total_despesas > 0:
            summary_parts.append(f"💸 Gastos: {fmt_brl(total_despesas)}")
        if total_receitas > 0:
            summary_parts.append(f"💰 Receitas: {fmt_brl(total_receitas)}")
        summary = "\n".join(summary_parts)

        return f"{header}:\n" + "\n".join(lines) + (f"\n\n{summary}" if summary else "")

    # sem filtro de data → últimos N lançamentos (busca mais para compensar os internos filtrados)
    rows = db.list_launches(user_id, limit=limit + 20)
    rows = [r for r in rows if r.get("tipo") not in _INTERNAL_TIPOS][:limit]
    if not rows:
        return "Você ainda não tem lançamentos."

    today = today_tz()

    _TIPO_EMOJI = {
        "despesa":              "💸",
        "receita":              "💰",
        "entrada":              "💰",
        "saida":                "💸",
        "aporte_investimento":  "📈",
        "resgate_investimento": "📉",
        "create_investment":    "📈",
        "transferencia":        "↔️",
    }

    lines = []
    for r in rows:
        tipo   = r.get("tipo", "")
        valor  = r.get("valor")
        alvo   = (r.get("alvo") or "").strip()
        nota   = (r.get("nota") or "").strip()
        criado = r.get("criado_em")

        # limpa nota técnica de investimento
        if tipo in ("create_investment", "aporte_investimento") and nota and "taxa=" in nota:
            try:
                m_taxa = re.search(r"taxa=([0-9.]+)", nota)
                m_per  = re.search(r"periodo=(\w+)", nota)
                taxa   = float(m_taxa.group(1)) * 100 if m_taxa else None
                per    = m_per.group(1) if m_per else ""
                per    = "ao mês" if per.startswith("month") else "ao dia" if per.startswith("day") else per
                nota   = f"{taxa:.4g}% {per}" if taxa is not None else nota
            except Exception:
                pass

        # descrição: prefere nota se informativa, senão usa alvo
        descricao = nota if nota and nota.lower() not in ("-", alvo.lower()) else alvo
        if not descricao:
            descricao = tipo

        # formata data de forma amigável
        if criado is not None:
            try:
                d = criado.date() if hasattr(criado, "date") else __import__("datetime").datetime.fromisoformat(str(criado)).date()
                if d == today:
                    data_str = "hoje"
                elif d == today - timedelta(days=1):
                    data_str = "ontem"
                else:
                    data_str = d.strftime("%d/%m")
            except Exception:
                data_str = str(criado)[:10]
        else:
            data_str = "-"

        emoji     = _TIPO_EMOJI.get(tipo, "•")
        valor_str = fmt_brl(float(valor)) if valor is not None else "-"
        # Mostra user_seq (numeração por usuário, começa em #1) em vez do
        # id global. Fallback pro id interno enquanto o backfill não rodou.
        display_id = r.get("user_seq") or r.get("id")
        id_str    = f" [#{display_id}]" if display_id else ""
        lines.append(f"{emoji} {data_str} • {valor_str} • {descricao}{id_str}")

    # mini resumo de despesas/receitas no período exibido
    total_despesas = sum(
        float(r["valor"])
        for r in rows
        if r.get("tipo") in ("despesa", "saida") and not r.get("is_internal_movement")
    )
    total_receitas = sum(
        float(r["valor"])
        for r in rows
        if r.get("tipo") in ("receita", "entrada") and not r.get("is_internal_movement")
    )

    summary_parts = []
    if total_despesas > 0:
        summary_parts.append(f"💸 Gastos: {fmt_brl(total_despesas)}")
    if total_receitas > 0:
        summary_parts.append(f"💰 Receitas: {fmt_brl(total_receitas)}")
    summary = "  |  ".join(summary_parts)

    header = f"🧾 **Últimos {len(rows)} lançamentos**:"
    body   = "\n".join(lines)
    return f"{header}\n{body}" + (f"\n\n{summary}" if summary else "")


# ---------------------------------------------------------------------------
# add / add_from_entities — registra receita/despesa
# ---------------------------------------------------------------------------

def add_from_entities(
    user_id: int,
    *,
    tipo: str,
    valor: float,
    alvo: str | None = None,
    nota: str | None = None,
    categoria: str | None = None,
    category_reason: str | None = None,
    criado_em=None,
    is_internal: bool | None = None,
    platform: str = "whatsapp",
) -> str:
    """Registra um lançamento a partir de args já estruturados (sem regex).

    Chamado por:
      - `add()` quando o parser regex já extraiu (ou caiu nos entities)
      - tool de IA `add_launch` (LLM extrai os args)

    Toda lógica compartilhada (categorização, learn, DB write, botão WhatsApp,
    alerta de orçamento) vive aqui — fonte única de verdade.
    """
    if valor <= 0:
        return "Não consegui identificar o valor. Tente: *gastei 50 no mercado*"

    alvo_clean = (alvo or "").strip()
    nota_clean = (nota or "").strip() or alvo_clean

    if categoria:
        categoria_final = categoria
        reason_final = category_reason or "explicit"
    else:
        res = infer_category(user_id, nota_clean, None)
        categoria_final = res.category or "outros"
        reason_final = res.reason

    is_int = (
        is_internal if is_internal is not None
        else is_internal_category(categoria_final)
    )

    launch_id, user_seq, new_balance = db.add_launch_and_update_balance(
        user_id=user_id,
        tipo=tipo,
        valor=valor,
        alvo=alvo_clean or None,
        nota=nota_clean,
        categoria=categoria_final,
        criado_em=criado_em,
        is_internal_movement=is_int,
    )

    learn_from_inference(
        user_id,
        nota_clean,
        categoria_final,
        target_hint=alvo_clean,
        reason=reason_final,
    )

    # Botão "categoria errada?" no WhatsApp (one-shot, lido por
    # _send_reply_with_optional_buttons no wa_runtime e limpo em seguida).
    if platform == "whatsapp" and launch_id:
        try:
            db.set_pending_action(
                user_id,
                "recategorize_launch_offer",
                {"launch_id": int(launch_id), "user_seq": int(user_seq)},
            )
        except Exception:
            pass

    emoji = "💸" if tipo == "despesa" else "💰"
    resposta = (
        f"{emoji} **{tipo.capitalize()} registrada**: {fmt_brl(valor)}\n"
        f"🏷️ Categoria: {categoria_final}\n"
        f"🏦 Saldo: {fmt_brl(float(new_balance))}\n"
        f"ID: #{user_seq}"
    )

    if tipo == "despesa" and not is_int and categoria_final:
        try:
            from datetime import datetime
            from core.budget_alerts import evaluate_after_expense, format_alert_text
            when = criado_em if isinstance(criado_em, datetime) else datetime.now()
            alert = evaluate_after_expense(user_id, categoria_final, valor, when)
            if alert:
                resposta += format_alert_text(alert)
        except Exception:
            pass

    return resposta


def add(user_id: int, text: str, entities: dict, platform: str = "whatsapp") -> str:
    from core.handlers import credit as h_credit

    credit_response = h_credit.try_handle_natural_credit_purchase(user_id, text)
    if credit_response is not None:
        return credit_response

    parsed = parse_receita_despesa_natural(user_id, text)

    if parsed:
        return add_from_entities(
            user_id,
            tipo=parsed["tipo"],
            valor=float(parsed["valor"]),
            alvo=parsed.get("alvo") or "",
            nota=parsed.get("nota") or text,
            categoria=parsed.get("categoria") or "outros",
            category_reason=parsed.get("category_reason"),
            criado_em=parsed.get("criado_em"),
            is_internal=parsed.get("is_internal_movement", False),
            platform=platform,
        )

    tipo = entities.get("tipo", "despesa")
    valor = float(entities.get("valor", 0))
    return add_from_entities(
        user_id,
        tipo=tipo,
        valor=valor,
        alvo=entities.get("alvo") or "",
        nota=text,
        categoria=entities.get("categoria"),
        criado_em=None,
        platform=platform,
    )


# ---------------------------------------------------------------------------
# propose_delete / undo
# ---------------------------------------------------------------------------

def propose_delete(user_id: int, launch_id: int) -> str:
    """Propõe apagar um lançamento. `launch_id` é o id interno (PK).

    O display usa o `user_seq` desse lançamento; se não conseguir resolver,
    cai pro id interno.
    """
    display_id = launch_id
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select user_seq from launches where id=%s and user_id=%s",
                    (launch_id, user_id),
                )
                row = cur.fetchone()
                if row and row.get("user_seq"):
                    display_id = int(row["user_seq"])
    except Exception:
        pass
    db.set_pending_action(
        user_id,
        "delete_launch",
        {"launch_id": int(launch_id), "display_id": int(display_id)},
    )
    return (
        f"⚠️ Isso vai apagar o lançamento **#{display_id}** e desfazer seus efeitos no saldo.\n"
        "Confirma? Responda **sim** ou **não**."
    )


def undo(user_id: int) -> str:
    rows = db.list_launches(user_id, limit=1)
    if not rows:
        return "Não há lançamentos para desfazer."
    last_id = int(rows[0]["id"])
    display_id = int(rows[0].get("user_seq") or last_id)
    db.set_pending_action(
        user_id,
        "delete_launch",
        {"launch_id": last_id, "display_id": display_id},
    )
    tipo  = rows[0].get("tipo", "")
    valor = fmt_brl(float(rows[0].get("valor") or 0))
    return (
        f"⚠️ Desfazer o último lançamento: **#{display_id}** ({tipo} {valor})?\n"
        "Confirma? Responda **sim** ou **não**."
    )
