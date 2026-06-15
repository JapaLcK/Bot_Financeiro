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
    except Exception as exc:
        from core.services.plan_limits import PlanLimitExceeded
        if isinstance(exc, PlanLimitExceeded):
            return exc.message
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

# "sacar tudo" / "esvaziar" / "zerar a caixinha" → saca o saldo cheio e zera
_WITHDRAW_ALL_RX = re.compile(r"\b(tudo|esvaziar|esvazia|zerar|zera)\b", re.I)


def _parse_pocket_withdraw_natural(text: str):
    """Extrai (amount, pocket_name) de frases de saque como 'retirei 50 da caixinha viagem'."""
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


def _pocket_name_from_text(text: str):
    """Extrai só o nome da caixinha (sem exigir valor) de 'sacar tudo da caixinha viagem'."""
    from utils_text import normalize_spaces
    raw = normalize_spaces(text.lower())
    if "caixinha" not in raw:
        return None
    pocket = raw.split("caixinha", 1)[1].strip()
    pocket = re.sub(r"^(da|do|de|na|no|para|pra)\s+", "", pocket).strip()
    return pocket or None


def _format_withdraw_reply(user_id, canon, sacado, new_acc, new_pocket, taxes, launch_id, *, emptied=False):
    tax_note = ""
    if taxes and (taxes.get("iof", 0) or taxes.get("ir", 0)):
        tax_note = f" • IR/IOF: {fmt_brl(float(taxes.get('ir', 0) + taxes.get('iof', 0)))}"
    head = (
        f"📤 Caixinha **{canon}** esvaziada: -{fmt_brl(sacado)}"
        if emptied
        else f"📤 Caixinha **{canon}**: -{fmt_brl(sacado)}"
    )
    return (
        f"{head}\n"
        f"🏦 Conta: {fmt_brl(float(new_acc))} • 📦 Caixinha: {fmt_brl(float(new_pocket))}{tax_note}\n"
        f"ID: **#{db.display_id_for(user_id, launch_id)}**"
    )


def withdraw(user_id: int, text: str, entities: dict) -> str:
    pocket_name = entities.get("pocket_name")
    amount      = entities.get("amount")
    want_all    = bool(_WITHDRAW_ALL_RX.search(text or ""))

    # tenta extrair do texto se as entidades não trouxerem
    if not pocket_name or (not amount and not want_all):
        _a, _p = _parse_pocket_withdraw_natural(text)
        if not _a and not _p:
            _a, _p = parse_pocket_deposit_natural(text)
        pocket_name = pocket_name or _p
        amount      = amount or _a

    if not pocket_name and want_all:
        pocket_name = _pocket_name_from_text(text)

    if not pocket_name:
        return "Qual caixinha? Tente: *retirei 100 da caixinha viagem*"

    if want_all:
        try:
            launch_id, new_acc, new_pocket, canon, taxes = db.pocket_withdraw_to_account(
                user_id, pocket_name, None, text, withdraw_all=True
            )
        except LookupError:
            return f"Caixinha **{pocket_name}** não encontrada. Use *listar caixinhas* para ver as disponíveis."
        except ValueError as e:
            if "INSUFFICIENT_POCKET" in str(e):
                return f"A caixinha **{pocket_name}** já está zerada."
            return "Não consegui sacar."
        except Exception as e:
            return f"Erro ao retirar: {e}"
        sacado = float(taxes.get("gross", 0)) if taxes else 0.0
        return _format_withdraw_reply(user_id, canon, sacado, new_acc, new_pocket, taxes, launch_id, emptied=True)

    if not amount or float(amount) <= 0:
        return "Qual o valor? Tente: *retirei 100 da caixinha viagem*"

    try:
        launch_id, new_acc, new_pocket, canon, taxes = db.pocket_withdraw_to_account(
            user_id, pocket_name, float(amount), text
        )
    except LookupError:
        return f"Caixinha **{pocket_name}** não encontrada. Use *listar caixinhas* para ver as disponíveis."
    except ValueError as e:
        if "INSUFFICIENT_POCKET" in str(e):
            return f"Saldo insuficiente na caixinha **{pocket_name}**."
        return "Valor inválido."
    except Exception as e:
        return f"Erro ao retirar: {e}"
    # o backend pode sacar um pouco mais que o pedido (tolerância de zeragem)
    sacado = float(taxes.get("gross", amount)) if taxes else float(amount)
    return _format_withdraw_reply(user_id, canon, sacado, new_acc, new_pocket, taxes, launch_id)
