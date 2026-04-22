# core/handlers/balance.py
from __future__ import annotations
from datetime import date
import db
from utils_text import fmt_brl
from utils_date import today_tz


def check(user_id: int) -> str:
    today = today_tz()
    lines: list[str] = []

    # ── Saldo da conta corrente ──────────────────────────────────────────
    bal = float(db.get_balance(user_id) or 0)
    lines.append(f"🏦 *Conta Corrente*: {fmt_brl(bal)}")

    # ── Gastos de hoje ───────────────────────────────────────────────────
    today_launches = db.get_launches_by_period(user_id, today, today)
    despesas_hoje = [l for l in today_launches if l["tipo"] == "despesa"]
    if despesas_hoje:
        lines.append("")
        lines.append("📋 *Hoje*")
        for l in despesas_hoje[-3:]:  # últimos 3
            nota = (l.get("nota") or l.get("alvo") or "—").capitalize()
            if len(nota) > 28:
                nota = nota[:27] + "…"
            lines.append(f"  • {nota}: {fmt_brl(float(l['valor']))}")
    else:
        lines.append("")
        lines.append("📋 *Hoje*: nenhum gasto registrado")

    # ── Gastos do mês ────────────────────────────────────────────────────
    mes_inicio = today.replace(day=1)
    summary = db.get_summary_by_period(user_id, mes_inicio, today)
    total_mes = summary.get("despesa", 0.0)
    _MESES = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
              "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    mes_nome = _MESES[today.month - 1]
    lines.append("")
    lines.append(f"📊 *Gastos em {mes_nome}*: {fmt_brl(total_mes)}")

    # ── Cartões ──────────────────────────────────────────────────────────
    cards = db.list_cards(user_id)
    if cards:
        lines.append("")
        lines.append("💳 *Cartões*")
        for card in cards:
            result = db.get_open_bill_summary(user_id, card["id"], as_of=today)
            if result is None:
                fatura_str = "sem fatura aberta"
                limite_str = ""
            else:
                bill, _ = result
                fatura = float(bill["total"] or 0)
                fatura_str = fmt_brl(fatura)
                limit_val = card.get("credit_limit")
                if limit_val:
                    usage = float(db.get_card_credit_usage(user_id, card["id"]))
                    disponivel = float(limit_val) - usage
                    limite_str = f" | disponível: {fmt_brl(disponivel)}"
                else:
                    limite_str = ""
            lines.append(f"  • *{card['name']}* — fatura: {fatura_str}{limite_str}")

    return "\n".join(lines)
