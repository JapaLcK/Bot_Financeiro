# core/services/statement_service.py
"""
Importação de extrato bancário em CSV/PDF pelo chat.

Mesmo papel do ofx_service, mas para os formatos que o usuário consegue
exportar mais fácil no celular (o PDF do extrato, o CSV do internet banking).
"""
from __future__ import annotations

from typing import Any

from statement_import import import_statement_bytes, StatementParseError

try:
    from utils_text import fmt_brl
except Exception:
    fmt_brl = None


def _fmt_money(v: Any) -> str:
    if v is None:
        return "-"
    if fmt_brl:
        try:
            return fmt_brl(v)
        except Exception:
            pass
    return f"R$ {v}"


_PRO_REQUIRED_STATEMENT_MSG = (
    "📄 **Importar extrato é uma feature do PigBank+ (Pro).**\n\n"
    "No plano Free voce pode lançar gastos manualmente pelo chat ou pelo dashboard. "
    "Pra importar extratos e faturas em lote, faça upgrade pelo `pigbankai.com/precos` "
    "ou diga `/assinar` aqui mesmo.\n\n"
    "🐷 — Piggy"
)

_KIND_LABEL = {"csv": "CSV", "pdf": "PDF"}


def format_statement_report(rep: dict) -> str:
    total = rep.get("total", 0)
    inserted = rep.get("inserted", 0)
    duplicates = rep.get("duplicates", 0)
    dt_start = rep.get("dt_start")
    dt_end = rep.get("dt_end")
    filename = rep.get("filename") or ""
    new_balance = rep.get("new_balance")
    skipped_same = rep.get("skipped_same_file")
    kind_label = _KIND_LABEL.get(rep.get("source") or "", "arquivo")

    blocks: list[str] = []
    blocks.append(f"✅ **Extrato {kind_label} importado com sucesso!**")

    if filename:
        blocks.append("📄 **Arquivo**\n" f"`{filename}`")

    if dt_start or dt_end:
        blocks.append("📅 **Período**\n" f"{dt_start} → {dt_end}")

    if skipped_same:
        blocks.append("⚠️ Esse arquivo parece já ter sido importado antes.")

    tx_lines = [
        "🧾 **Transações**",
        f"• Inseridas: **{inserted}**",
        f"• Total no arquivo: {total}",
    ]
    if duplicates:
        tx_lines.append(f"• Duplicadas ignoradas: {duplicates}")
    blocks.append("\n".join(tx_lines))

    blocks.append("💰 **Saldo atual**\n" f"{_fmt_money(new_balance)}")

    # CSV/PDF não trazem o saldo oficial do banco (o OFX traz LEDGERBAL) — o
    # saldo acima foi ajustado só pelas transações inseridas.
    blocks.append(
        "💡 Se o saldo não bater com o do banco, é só dizer, por exemplo: "
        "`saldo é 1500`."
    )

    return "\n\n".join(blocks)


def handle_statement_import(
    user_id: str, attachment_bytes: bytes, filename: str, kind: str
) -> str:
    """Importa extrato CSV ('csv') ou PDF ('pdf'). Retorna a resposta do bot."""
    uid = int(user_id)
    from core.services.plan_service import is_pro
    if not is_pro(uid):
        return _PRO_REQUIRED_STATEMENT_MSG
    try:
        rep = import_statement_bytes(uid, attachment_bytes, filename, kind)
    except StatementParseError as e:
        kind_label = _KIND_LABEL.get(kind, "arquivo")
        return f"❌ Não consegui importar esse {kind_label}: {e}"
    return format_statement_report(rep)
