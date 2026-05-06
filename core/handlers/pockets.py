# core/handlers/pockets.py
from __future__ import annotations
import db
from utils_text import fmt_brl, parse_pocket_deposit_natural


def list_pockets(user_id: int) -> str:
    rows = db.list_pockets(user_id)
    if not rows:
        return "Você ainda não tem caixinhas.\nCrie uma: *criar caixinha viagem*"
    lines = [f"• **{r['name']}**: {fmt_brl(float(r['balance']))}" for r in rows]
    total = sum(float(r["balance"]) for r in rows)
    return (
        "📦 **Caixinhas**:\n"
        + "\n".join(lines)
        + f"\n\nTotal nas caixinhas: **{fmt_brl(total)}**"
    )


def create(user_id: int, name: str, nota: str | None = None) -> str:
    if not name or not name.strip():
        return "Qual o nome da caixinha?"
    try:
        launch_id, _pocket_id, canon = db.create_pocket(user_id, name.strip(), nota=nota)
    except Exception:
        return "Deu erro ao criar caixinha. Veja os logs."
    if launch_id is None:
        return f"ℹ️ A caixinha **{canon}** já existe."
    return f"✅ Caixinha criada: **{canon}** (ID: **#{db.display_id_for(user_id, launch_id)}**)"


def propose_delete(user_id: int, pocket_name: str) -> str:
    rows = db.list_pockets(user_id)
    pocket = next((r for r in rows if r["name"].lower() == pocket_name.lower()), None)
    if not pocket:
        return f"Não achei essa caixinha: **{pocket_name}**"

    canon_name = pocket["name"]
    saldo = float(pocket["balance"])
    if saldo != 0.0:
        return (
            f"⚠️ Não posso excluir a caixinha **{canon_name}** "
            f"porque o saldo não é zero ({fmt_brl(saldo)}).\n"
            f"Retire o valor antes e tente novamente."
        )

    db.set_pending_action(user_id, "delete_pocket", {"pocket_name": canon_name}, minutes=10)
    return (
        f"⚠️ Você está prestes a excluir esta caixinha:\n"
        f"• **{canon_name}** • saldo: **{fmt_brl(0.0)}**\n\n"
        f"Responda **sim** para confirmar ou **não** para cancelar. (expira em 10 min)"
    )


def deposit(user_id: int, text: str, entities: dict) -> str:
    """
    Tenta parsear texto natural primeiro (parse_pocket_deposit_natural).
    Se falhar, usa entidades passadas via `entities`.
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
        launch_id, new_acc, new_pocket, canon = db.pocket_deposit_from_account(
            user_id, pocket_name, float(amount), text
        )
        return (
            f"✅ Depósito na caixinha **{canon}**: +{fmt_brl(float(amount))}\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))} • 📦 Caixinha: {fmt_brl(float(new_pocket))}\n"
            f"ID: **#{db.display_id_for(user_id, launch_id)}**"
        )
    except LookupError:
        return f"Caixinha **{pocket_name}** não encontrada. Use *criar caixinha {pocket_name}*."
    except ValueError as e:
        if "INSUFFICIENT_ACCOUNT" in str(e):
            return "Saldo insuficiente na conta para esse depósito."
        return "Valor inválido."
    except Exception as e:
        return f"Erro ao depositar: {e}"


_WITHDRAW_VERBS = ["retirei", "retirar", "sacar", "saquei", "resgatei", "resgatar", "tirei", "tirar"]


def _parse_pocket_withdraw_natural(text: str):
    """Extrai (amount, pocket_name) de frases de saque como 'retirei 50 da caixinha viagem'."""
    import re
    from utils_text import parse_money, normalize_spaces
    raw = normalize_spaces(text.lower())
    if not any(v in raw for v in _WITHDRAW_VERBS):
        return None, None
    amount = parse_money(raw)
    if amount is None:
        return None, None
    if "caixinha" in raw:
        pocket = raw.split("caixinha", 1)[1].strip()
        pocket = re.sub(r"^(da|do|de|na|no|para|pra)\s+", "", pocket).strip()
        if pocket:
            return amount, pocket
    return None, None


def withdraw(user_id: int, text: str, entities: dict) -> str:
    pocket_name = entities.get("pocket_name")
    amount      = entities.get("amount")

    # tenta extrair do texto se as entidades não trouxerem
    if not pocket_name or not amount:
        _a, _p = _parse_pocket_withdraw_natural(text)
        if not _a and not _p:
            _a, _p = parse_pocket_deposit_natural(text)
        pocket_name = pocket_name or _p
        amount      = amount or _a

    if not pocket_name:
        return "Qual caixinha? Tente: *retirei 100 da caixinha viagem*"
    if not amount or float(amount) <= 0:
        return "Qual o valor? Tente: *retirei 100 da caixinha viagem*"

    try:
        launch_id, new_acc, new_pocket, canon = db.pocket_withdraw_to_account(
            user_id, pocket_name, float(amount), text
        )
        return (
            f"📤 Caixinha **{canon}**: -{fmt_brl(float(amount))}\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))} • 📦 Caixinha: {fmt_brl(float(new_pocket))}\n"
            f"ID: **#{db.display_id_for(user_id, launch_id)}**"
        )
    except LookupError:
        return f"Caixinha **{pocket_name}** não encontrada. Use *listar caixinhas* para ver as disponíveis."
    except ValueError as e:
        if "INSUFFICIENT_POCKET" in str(e):
            return f"Saldo insuficiente na caixinha **{pocket_name}**."
        return "Valor inválido."
    except Exception as e:
        return f"Erro ao retirar: {e}"
