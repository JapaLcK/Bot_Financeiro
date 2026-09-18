"""Apresentação comum dos relatórios periódicos."""

def _fmt_brl(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def finish_report(user_id: int, lines: list[str]) -> str:
    from db.bank_movements import bank_movement_summary
    if bank_movement_summary(user_id)["pending_count"]:
        lines.append("🔎 Movimentações bancárias não confirmadas: patrimônio a conferir no dashboard. Saldo acima é o último observado.")

    # Import local: reports_daily importa `finish_report` deste módulo no
    # nível do arquivo — import de módulo aqui em cima criaria ciclo.
    from db.reconciliation import reconciliation_summary
    from core.reports.reports_daily import _saldo_atual
    from core.services.funding import aviso_conferir

    aviso = aviso_conferir(_saldo_atual(user_id), reconciliation_summary(user_id))
    if aviso:
        lines.append(aviso)
    return "\n".join(lines).strip()
