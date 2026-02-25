# core/services/ofx_service.py
from __future__ import annotations
from typing import Any
from ofx_import import import_ofx_bytes
import asyncio
try:
    # se você já tem isso no projeto (você citou)
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
    # fallback simples
    return f"R$ {v}"


def format_ofx_report(rep: dict) -> str:
    total = rep.get("total", 0)
    inserted = rep.get("inserted", 0)
    duplicates = rep.get("duplicates", 0)
    dt_start = rep.get("dt_start")
    dt_end = rep.get("dt_end")
    filename = rep.get("filename") or ""
    new_balance = rep.get("new_balance")
    skipped_same = rep.get("skipped_same_file")

    # Layout em blocos (mais respiro)
    blocks: list[str] = []

    # Título
    blocks.append("✅ **OFX importado com sucesso!**")

    # Arquivo
    if filename:
        blocks.append(
            "📄 **Arquivo**\n"
            f"`{filename}`"
        )

    # Período
    if dt_start or dt_end:
        blocks.append(
            "📅 **Período**\n"
            f"{dt_start} → {dt_end}"
        )

    # Aviso (se repetido)
    if skipped_same:
        blocks.append("⚠️ Esse arquivo parece já ter sido importado antes.")

    # Transações (com linhas separadas)
    tx_lines = [
        "🧾 **Transações**",
        f"• Inseridas: **{inserted}**",
        f"• Total no arquivo: {total}",
    ]
    if duplicates:
        tx_lines.append(f"• Duplicadas ignoradas: {duplicates}")
    blocks.append("\n".join(tx_lines))

    # Saldo
    blocks.append(
        "💰 **Saldo final**\n"
        f"{_fmt_money(new_balance)}"
    )

    # separa blocos com linha em branco (respiro)
    return "\n\n".join(blocks)

def handle_ofx_import(user_id: str, attachment_bytes: bytes, filename: str) -> str:
    uid = int(user_id)
    rep = import_ofx_bytes(uid, attachment_bytes, filename)
    return format_ofx_report(rep)