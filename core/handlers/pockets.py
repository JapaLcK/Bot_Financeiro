# core/handlers/pockets.py
from __future__ import annotations
import re
import db
from utils_text import fmt_brl, parse_pocket_deposit_natural


def list_pockets(user_id: int) -> str:
    rows = db.list_pockets(user_id)
    if not rows:
        return "Você ainda não tem caixinhas.\nCrie uma: *criar caixinha viagem*"
    lines = [f"• **{r['name']}**: {fmt_brl(float(r['balance']))}" for r in rows]
    return "📦 **Caixinhas**:\n" + "\n".join(lines)


def create(user_id: int, name: str) -> str:
    if not name or not name.strip():
        return "Qual o nome da caixinha?"
    _launch_id, pocket_id, canon = db.create_pocket(user_id, name.strip())
    return f"✅ Caixinha criada: **{canon}** (id {pocket_id})"


def propose_delete(user_id: int, pocket_name: str) -> str:
    db.set_pending_action(user_id, "delete_pocket", {"pocket_name": pocket_name})
    return (
        f"⚠️ Isso vai deletar a caixinha **{pocket_name}** permanentemente.\n"
        "Confirma? Responda **sim** ou **não**."
    )


def deposit(user_id: int, text: str, entities: dict) -> str:
    """
    Tenta parsear texto natural primeiro (parse_pocket_deposit_natural).
    Se falhar, usa entidades da IA.
    """
    amount, pocket_name = parse_pocket_deposit_natural(text)

    if not pocket_name or not amount:
        pocket_name = entities.get("pocket_name")
        amount      = entities.get("amount")

    if not pocket_name:
        return "Qual caixinha? Tente: *coloquei 200 na caixinha viagem*"
    if not amount or float(amount) <= 0:
        return "Qual o valor? Tente: *coloquei 200 na caixinha viagem*"

    try:
        launch_id, _new_acc, _new_pocket, canon = db.pocket_deposit_from_account(
            user_id, pocket_name, float(amount), text
        )
        return f"✅ **{fmt_brl(float(amount))}** depositado na caixinha **{canon}**. ID #{launch_id}."
    except Exception as e:
        err = str(e)
        if "not found" in err.lower() or "nao encontrada" in err.lower():
            return f"Caixinha **{pocket_name}** não encontrada. Use *listar caixinhas* para ver as disponíveis."
        if "saldo insuficiente" in err.lower() or "insufficient" in err.lower():
            return "Saldo insuficiente na conta para esse depósito."
        return f"Erro ao depositar: {err}"


def withdraw(user_id: int, text: str, entities: dict) -> str:
    pocket_name = entities.get("pocket_name")
    amount      = entities.get("amount")

    # tenta extrair do texto se a IA não trouxe
    if not pocket_name or not amount:
        _a, _p = parse_pocket_deposit_natural(text)
        pocket_name = pocket_name or _p
        amount      = amount or _a

    if not pocket_name:
        return "Qual caixinha? Tente: *retirei 100 da caixinha viagem*"
    if not amount or float(amount) <= 0:
        return "Qual o valor? Tente: *retirei 100 da caixinha viagem*"

    try:
        launch_id, _new_acc, _new_pocket, canon = db.pocket_withdraw_to_account(
            user_id, pocket_name, float(amount), text
        )
        return f"✅ **{fmt_brl(float(amount))}** retirado da caixinha **{canon}**. ID #{launch_id}."
    except Exception as e:
        err = str(e)
        if "not found" in err.lower():
            return f"Caixinha **{pocket_name}** não encontrada. Use *listar caixinhas* para ver as disponíveis."
        if "saldo insuficiente" in err.lower() or "insufficient" in err.lower():
            return f"Saldo insuficiente na caixinha **{pocket_name}**."
        return f"Erro ao retirar: {err}"
