# core/services/ofx_service.py
from __future__ import annotations
from typing import Any
from ofx_import import import_ofx_bytes
import asyncio
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


# ─────────────────────────────────────────────────────────────────────────────
# Extrato bancário
# ─────────────────────────────────────────────────────────────────────────────

def format_ofx_report(rep: dict) -> str:
    total = rep.get("total", 0)
    inserted = rep.get("inserted", 0)
    duplicates = rep.get("duplicates", 0)
    dt_start = rep.get("dt_start")
    dt_end = rep.get("dt_end")
    filename = rep.get("filename") or ""
    new_balance = rep.get("new_balance")
    skipped_same = rep.get("skipped_same_file")

    blocks: list[str] = []
    blocks.append("✅ **OFX importado com sucesso!**")

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

    blocks.append("💰 **Saldo final**\n" f"{_fmt_money(new_balance)}")

    return "\n\n".join(blocks)


def handle_ofx_import(user_id: str, attachment_bytes: bytes, filename: str) -> str:
    uid = int(user_id)
    rep = import_ofx_bytes(uid, attachment_bytes, filename)
    return format_ofx_report(rep)


# ─────────────────────────────────────────────────────────────────────────────
# Fatura de cartão de crédito
# ─────────────────────────────────────────────────────────────────────────────

def format_credit_ofx_report(rep: dict, card_name: str) -> str:
    """Formata o relatório de importação de fatura OFX."""
    total = rep.get("total", 0)
    inserted = rep.get("inserted", 0)
    duplicates = rep.get("duplicates", 0)
    dt_start = rep.get("dt_start")
    dt_end = rep.get("dt_end")
    filename = rep.get("filename") or ""
    credit_limit = rep.get("credit_limit")
    available_credit = rep.get("available_credit")
    ledger_balance = rep.get("ledger_balance")
    installments_detected = rep.get("installments_detected", 0)
    skipped_same = rep.get("skipped_same_file")

    blocks: list[str] = []
    blocks.append(f"✅ **Fatura importada — {card_name}**")

    if filename:
        blocks.append("📄 **Arquivo**\n" f"`{filename}`")

    if dt_start or dt_end:
        blocks.append("📅 **Período da fatura**\n" f"{dt_start} → {dt_end}")

    if skipped_same:
        blocks.append("⚠️ Esse arquivo já foi importado anteriormente.")

    tx_lines = [
        "🧾 **Transações**",
        f"• Inseridas: **{inserted}**",
        f"• Total no arquivo: {total}",
    ]
    if duplicates:
        tx_lines.append(f"• Duplicadas ignoradas: {duplicates}")
    if installments_detected:
        tx_lines.append(f"• Parcelamentos detectados: {installments_detected}")
    blocks.append("\n".join(tx_lines))

    # Dados financeiros do cartão (se disponíveis no OFX)
    fin_lines = []
    if ledger_balance is not None:
        fatura_val = abs(ledger_balance)
        fin_lines.append(f"• Valor da fatura: **{_fmt_money(fatura_val)}**")
    if credit_limit is not None and credit_limit > 0:
        fin_lines.append(f"• Limite total: {_fmt_money(credit_limit)}")
    if available_credit is not None:
        fin_lines.append(f"• Limite disponível: {_fmt_money(abs(available_credit))}")

    if fin_lines:
        blocks.append("💳 **Cartão**\n" + "\n".join(fin_lines))

    return "\n\n".join(blocks)


def handle_credit_ofx_import(user_id: str, attachment_bytes: bytes, filename: str) -> str:
    """
    Importa um OFX de fatura de cartão de crédito.

    Lógica de seleção de cartão:
      - 0 cartões → instrui o usuário a cadastrar um
      - 1 cartão  → usa automaticamente
      - N cartões com padrão definido → usa o padrão
      - N cartões sem padrão          → instrui a definir um cartão padrão
    """
    from db import list_cards, get_default_card_id
    from ofx_credit_import import import_credit_ofx_bytes

    uid = int(user_id)
    cards = list_cards(uid)

    # Sem cartões cadastrados
    if not cards:
        return (
            "💳 **Fatura de cartão detectada!**\n\n"
            "Mas você ainda não tem nenhum cartão cadastrado.\n"
            "Cadastre um cartão primeiro:\n\n"
            "`criar cartão [nome] fechamento [dia] vencimento [dia]`\n\n"
            "Exemplo: `criar cartão Nubank fechamento 18 vencimento 25`\n\n"
            "Depois é só reenviar o arquivo OFX."
        )

    # Determina qual cartão usar
    card = None
    if len(cards) == 1:
        card = dict(cards[0])
    else:
        default_id = get_default_card_id(uid)
        if default_id:
            card = next((dict(c) for c in cards if c["id"] == default_id), None)

        if not card:
            names = "\n".join(f"  • `{c['name']}`" for c in cards)
            return (
                "💳 **Fatura de cartão detectada!**\n\n"
                f"Você tem {len(cards)} cartões cadastrados:\n{names}\n\n"
                "Para importar automaticamente, defina um cartão padrão:\n"
                "`cartão padrão [nome]`\n\n"
                "Depois reenvie o arquivo OFX."
            )

    try:
        rep = import_credit_ofx_bytes(uid, card["id"], attachment_bytes, filename)
        return format_credit_ofx_report(rep, card["name"])
    except Exception as e:
        return f"❌ Erro ao importar fatura do {card['name']}: {e}"
