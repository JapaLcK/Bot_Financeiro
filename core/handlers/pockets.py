# core/handlers/pockets.py
from __future__ import annotations
import logging
import re
import db
from core.handlers import pending as h_pending
from utils_text import fmt_brl, parse_pocket_deposit_natural

logger = logging.getLogger(__name__)


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

    # As duas perguntas GUARDAM o contexto (#136). Sem isso a resposta seguinte
    # era classificada do zero: "200 reais" virava `launches.add` com confiança
    # 0,95 e gravava uma despesa que ninguém pediu, com a caixinha intacta.
    if not pocket_name:
        return h_pending.pergunta_guardando_contexto(
            user_id, "pockets.deposit", entities,
            "Qual caixinha? Tente: *coloquei 200 na caixinha viagem*", text, falta="pocket_name")
    if not amount or float(amount) <= 0:
        return h_pending.pergunta_guardando_contexto(
            user_id, "pockets.deposit", {**(entities or {}), "pocket_name": pocket_name},
            "Qual o valor? Tente: *coloquei 200 na caixinha viagem*", text, falta="amount")

    from core.handlers.investments import _pergunta_origem
    from core.services import funding

    # De onde sai o dinheiro — mesma regra do aporte em investimento.
    escolha = funding.resolve(user_id, float(amount))
    if "ask" in escolha:
        return _pergunta_origem(
            user_id, escolha["ask"], float(amount),
            {"fluxo": "pocket_deposit", "name": pocket_name, "text": text},
        )
    if "insufficient" in escolha:
        return funding.msg_insuficiente(
            user_id, float(amount), acao="depósito",
            sources=escolha["insufficient"]["sources"])

    return deposita_com_origem(user_id, pocket_name, float(amount), text, escolha["source"])


def deposita_com_origem(user_id: int, pocket_name: str, amount: float, text: str, source: dict) -> str:
    """Depósito com a origem já decidida — retomado também pela resposta da pergunta."""
    from core.services import funding

    # ── ANTES do commit: nada se moveu ───────────────────────────────────────
    # As três recusas abaixo viram TEXTO: repetir a escolha daria o mesmo
    # resultado, então re-armar a pergunta só prenderia o usuário no fluxo.
    # O resto SOBE. `pocket_deposit_from_account` é uma transação só, com o
    # commit no fim: o que estoura nela não tirou dinheiro de lugar nenhum, e
    # quem chamou precisa da exceção para devolver a pergunta "de onde sai?"
    # (core/handlers/investments.py::resolve_funding_choice). Enquanto isto
    # devolvia `f"Erro ao depositar: {e}"`, o `restore_pending_on_error` daquele
    # site nunca via falha nenhuma — a pendência ficava consumida e responder a
    # origem de novo não repetia o depósito. Apontado pelo Codex no PR #144.
    try:
        launch_id, new_acc, new_pocket, canon = db.pocket_deposit_from_account(
            user_id, pocket_name, float(amount), text,
            funding_source=funding.to_db_arg(source),
        )
    except LookupError:
        return f"Caixinha **{pocket_name}** não encontrada. Use *criar caixinha {pocket_name}*."
    except ValueError as e:
        if "OF_POCKET_READONLY" in str(e):
            return "Essa caixinha é sincronizada com seu banco — mova o dinheiro pelo app do banco."
        if "INSUFFICIENT_ACCOUNT" in str(e):
            return funding.msg_insuficiente(user_id, float(amount), acao="depósito")
        return "Valor inválido."

    # ── DEPOIS do commit: o dinheiro JÁ ANDOU ────────────────────────────────
    # Aqui a exceção NÃO pode subir: a devolução re-armaria a pergunta e a
    # próxima resposta do usuário depositaria de novo. `db.display_id_for` lê o
    # banco, então este bloco falha pela mesma causa transitória do de cima.
    try:
        msg = (
            f"✅ Depósito na caixinha **{canon}**: +{fmt_brl(float(amount))}"
            f"{funding.origem_txt(source)}\n"
            f"🏦 Conta: {fmt_brl(float(new_acc))} • 📦 Caixinha: {fmt_brl(float(new_pocket))}\n"
            f"ID: **#{db.display_id_for(user_id, launch_id)}**"
        )
        if source["kind"] == funding.BANK:
            msg += "\n\n" + funding.nota_sync()
        return msg
    except Exception:
        logger.exception(
            "depósito %s registrado, mas a mensagem falhou (user=%s)", launch_id, user_id)
        return (f"✅ Depósito de {fmt_brl(float(amount))} na caixinha **{canon}** "
                f"registrado (ID interno #{launch_id}).")


_WITHDRAW_VERBS = [
    "retirei", "retirar", "sacar", "saquei", "resgatei", "resgatar", "tirei", "tirar",
    # imperativo/presente anunciado no catálogo ("saca 50 da caixinha viagem")
    "saca", "saque", "tira", "tire", "retira", "retire", "resgata", "resgate",
]

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


def _format_withdraw_reply(user_id, canon, sacado, new_acc, new_pocket, taxes, launch_id, *, emptied=False, nota=""):
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
        f"ID: **#{db.display_id_for(user_id, launch_id)}**{nota}"
    )


def withdraw(user_id: int, text: str, entities: dict) -> str:
    from core.services import funding

    destino = funding.resolve_destination(user_id)["source"]
    fs = funding.to_db_arg(destino)
    nota_destino = ("\n\n" + funding.nota_sync(saida=False)) if destino["kind"] == funding.BANK else ""

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
        return h_pending.pergunta_guardando_contexto(
            user_id, "pockets.withdraw", entities,
            "Qual caixinha? Tente: *retirei 100 da caixinha viagem*", text, falta="pocket_name")

    if want_all:
        try:
            launch_id, new_acc, new_pocket, canon, taxes = db.pocket_withdraw_to_account(
                user_id, pocket_name, None, text, withdraw_all=True,
                funding_source=fs,
            )
        except LookupError:
            return f"Caixinha **{pocket_name}** não encontrada. Use *listar caixinhas* para ver as disponíveis."
        except ValueError as e:
            if "OF_POCKET_READONLY" in str(e):
                return "Essa caixinha é sincronizada com seu banco — mova o dinheiro pelo app do banco."
            if "INSUFFICIENT_POCKET" in str(e):
                return f"A caixinha **{pocket_name}** já está zerada."
            return "Não consegui sacar."
        except Exception as e:
            return f"Erro ao retirar: {e}"
        sacado = float(taxes.get("gross", 0)) if taxes else 0.0
        return _format_withdraw_reply(user_id, canon, sacado, new_acc, new_pocket, taxes, launch_id, emptied=True, nota=nota_destino)

    if not amount or float(amount) <= 0:
        return h_pending.pergunta_guardando_contexto(
            user_id, "pockets.withdraw", {**(entities or {}), "pocket_name": pocket_name},
            "Qual o valor? Tente: *retirei 100 da caixinha viagem*", text, falta="amount")

    try:
        launch_id, new_acc, new_pocket, canon, taxes = db.pocket_withdraw_to_account(
            user_id, pocket_name, float(amount), text, funding_source=fs,
        )
    except LookupError:
        return f"Caixinha **{pocket_name}** não encontrada. Use *listar caixinhas* para ver as disponíveis."
    except ValueError as e:
        if "OF_POCKET_READONLY" in str(e):
            return "Essa caixinha é sincronizada com seu banco — mova o dinheiro pelo app do banco."
        if "INSUFFICIENT_POCKET" in str(e):
            return f"Saldo insuficiente na caixinha **{pocket_name}**."
        return "Valor inválido."
    except Exception as e:
        return f"Erro ao retirar: {e}"
    # o backend pode sacar um pouco mais que o pedido (tolerância de zeragem)
    sacado = float(taxes.get("gross", amount)) if taxes else float(amount)
    return _format_withdraw_reply(user_id, canon, sacado, new_acc, new_pocket, taxes, launch_id, nota=nota_destino)
