"""Apresentação comum dos relatórios periódicos."""

def _fmt_brl(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def finish_report(user_id: int, lines: list[str]) -> str:
    from db.bank_movements import bank_movement_summary
    if bank_movement_summary(user_id)["pending_count"]:
        lines.append("🔎 Movimentações bancárias não confirmadas: patrimônio a conferir no dashboard. Saldo acima é o último observado.")
    return "\n".join(lines).strip()
