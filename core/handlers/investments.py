# core/handlers/investments.py
from __future__ import annotations
import re
import db
from utils_text import fmt_brl, fmt_rate
from core.dashboard_links import build_dashboard_link
from core.services.plan_limits import PlanLimitExceeded
import logging

logger = logging.getLogger(__name__)


def _investment_dashboard_link(user_id: int) -> str:
    link = build_dashboard_link(user_id, view="investments")
    if not link:
        return "⚠️ Não consegui gerar o link do dashboard agora. Tente novamente em instantes."
    return (
        "💡 No dashboard você gerencia com mais detalhes (taxas indexadas, lotes, datas):\n"
        f"{link}\n"
        "⏱️ Link mágico de uso único, expira em 5 minutos."
    )


_INV_NAME_UPPERCASE_TOKENS = {
    "CDB", "CRA", "CRI", "LCI", "LCA", "LF", "LCD", "LFT", "LTN",
    "IPCA", "SELIC", "CDI", "ETF", "FII", "FIAGRO",
    "BTG", "XP", "BB", "USA",
}
_INV_NAME_LOWERCASE_TOKENS = {"de", "da", "do", "das", "dos", "e"}


def _format_inv_name(name: str) -> str:
    """Capitaliza nome de investimento preservando siglas (CDB, IPCA, etc).

    "cdb banco luso"        → "CDB Banco Luso"
    "reserva de emergencia" → "Reserva de Emergencia"
    "Tesouro IPCA+ 2032"    → "Tesouro IPCA+ 2032"
    """
    parts = name.split()
    out: list[str] = []
    for i, w in enumerate(parts):
        # Trata sufixo "+" (ex: "IPCA+", "IPCA+2032")
        if "+" in w:
            head, _, tail = w.partition("+")
            if head.upper() in _INV_NAME_UPPERCASE_TOKENS:
                out.append(f"{head.upper()}+{tail}")
                continue
        upper = w.upper()
        lower = w.lower()
        if upper in _INV_NAME_UPPERCASE_TOKENS:
            out.append(upper)
        elif w.isdigit():
            out.append(w)
        elif i > 0 and lower in _INV_NAME_LOWERCASE_TOKENS:
            out.append(lower)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def list_investments(user_id: int, intro: str | None = None, rows: list | None = None) -> str:
    if rows is None:
        rows = db.accrue_all_investments(user_id)
    header = intro or "📈 **Sua carteira**"
    if not rows:
        return (
            f"{header}\n"
            "Você ainda não tem investimentos cadastrados.\n\n"
            f"{_investment_dashboard_link(user_id)}"
        )

    total = 0.0
    lines: list[str] = []
    for r in rows:
        rate_txt = fmt_rate(r.get("rate"), r.get("period"))
        projected_balance = r.get("projected_balance")
        projected_days = r.get("projected_days") or 0
        if projected_days > 0 and projected_balance:
            value = float(projected_balance)
        else:
            value = float(r["balance"] or 0)
        total += value
        name_pretty = _format_inv_name(r["name"])
        rate_part = f" ({rate_txt})" if rate_txt else ""
        lines.append(f"• **{name_pretty}** — {fmt_brl(value)}{rate_part}")

    return (
        f"{header}\n\n"
        f"**{fmt_brl(total)}** no total\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _investment_dashboard_link(user_id)
    )


def _investment_not_found(user_id: int, investment_name: str, *, action: str) -> str:
    """Resposta para INV_NOT_FOUND (nome não bate com nada cadastrado).

    O cadastro de investimento é feito só pelo dashboard (ver `create`), então
    a resposta precisa levar o usuário até lá. Só dizer "não encontrei" — ou
    pior, devolver o código cru — deixa ele sem saída nenhuma.
    """
    rows = db.accrue_all_investments(user_id)
    pretty = _format_inv_name(investment_name)
    if not rows:
        return (
            f"Você ainda não tem nenhum investimento cadastrado, "
            f"por isso não dá para {action} **{pretty}**.\n"
            "Cadastre ele primeiro no dashboard — depois é só mandar o comando aqui pelo WhatsApp.\n\n"
            + _investment_dashboard_link(user_id)
        )
    return list_investments(
        user_id,
        f"Não encontrei **{pretty}**. Estes são seus investimentos:",
        rows=rows,
    )


def create(user_id: int, raw_name: str, original_text: str) -> str:
    return list_investments(
        user_id,
        "📈 A criação de investimentos agora é feita pelo dashboard.",
    )


def propose_delete(user_id: int, investment_name: str) -> str:
    db.set_pending_action(user_id, "delete_investment", {"investment_name": investment_name})
    return (
        f"⚠️ Isso vai deletar o investimento **{investment_name}** permanentemente.\n"
        "Confirma? Responda **sim** ou **não**."
    )


def deposit(user_id: int, text: str, entities: dict) -> str:
    investment_name = entities.get("investment_name")
    amount = entities.get("amount")

    if not investment_name:
        return list_investments(user_id, "Em qual investimento você quer aportar?")
    if not amount or float(amount) <= 0:
        return list_investments(user_id, "Qual valor você quer aportar?")

    # Códigos vêm de db/investments.py como str(exc) — trate por TIPO e código
    # exato. Casar substring é frágil: "not found" nunca casou com
    # "INV_NOT_FOUND" (espaço × underscore) e o código cru vazava pro WhatsApp.
    try:
        launch_id, _new_acc, _new_inv, canon = db.investment_deposit_from_account(
            user_id, investment_name, float(amount), text
        )
    except LookupError:
        return _investment_not_found(user_id, investment_name, action="aportar em")
    except ValueError as e:
        code = str(e)
        if code == "INSUFFICIENT_ACCOUNT":
            return "Saldo insuficiente na conta para esse aporte.\n\n" + _investment_dashboard_link(user_id)
        if code == "AMOUNT_INVALID":
            return "Valor inválido para aporte. Tente: *aportar 500 no CDB Nubank*."
        logger.warning("deposit: código inesperado user=%s inv=%s code=%s", user_id, investment_name, code)
        return "Não consegui registrar esse aporte. Confira o valor e tente de novo."
    except PlanLimitExceeded:
        raise  # handle_incoming responde com a mensagem amigável de upgrade
    except Exception:
        logger.exception("deposit falhou user=%s inv=%s", user_id, investment_name)
        return "Não consegui registrar esse aporte agora. Tente de novo em instantes."

    display_id = db.display_id_for(user_id, launch_id)
    return f"✅ Aporte de **{fmt_brl(float(amount))}** em **{canon}**. ID #{display_id}."


def check_cdi() -> str:
    """
    Retorna a taxa CDI anual (a.a.) mais recente do Banco Central.
    Usa a função get_latest_cdi_aa do db, que consulta o SGS/BCB.
    """
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                res = db.get_latest_cdi_aa(cur)

        if not res:
            return (
                "⚠️ Não consegui obter a taxa CDI agora.\n"
                "O Banco Central pode estar fora do ar. Tente novamente em alguns minutos."
            )

        ref_date, cdi_aa = res
        # Calcula estimativa mensal e diária para contexto
        cdi_mensal = ((1 + cdi_aa / 100) ** (1 / 12) - 1) * 100
        cdi_diaria = ((1 + cdi_aa / 100) ** (1 / 252) - 1) * 100

        return (
            f"📊 *Taxa CDI — Banco Central*\n\n"
            f"📅 Referência: {ref_date.strftime('%d/%m/%Y')}\n"
            f"📈 *CDI a.a.:* {cdi_aa:.2f}%\n"
            f"📆 CDI mensal (aprox.): {cdi_mensal:.4f}%\n"
            f"📆 CDI diário (aprox.): {cdi_diaria:.5f}%\n\n"
            f"💡 Para criar um investimento atrelado ao CDI, digite *investimentos* e abra o dashboard."
        )

    except Exception as e:
        logger.exception("check_cdi error: %s", e)
        return (
            "⚠️ Erro ao consultar a taxa CDI.\n"
            "Verifique sua conexão ou tente novamente em instantes."
        )


_WITHDRAW_ALL_RX = re.compile(r"\b(tudo|esvaziar|esvazia|zerar|zera)\b", re.I)


def withdraw(user_id: int, text: str, entities: dict) -> str:
    investment_name = entities.get("investment_name")
    amount = entities.get("amount")
    want_all = bool(_WITHDRAW_ALL_RX.search(text or ""))

    if not investment_name:
        return list_investments(user_id, "De qual investimento você quer resgatar?")
    if not want_all and (not amount or float(amount) <= 0):
        return list_investments(user_id, "Qual valor você quer resgatar?")

    try:
        launch_id, _new_acc, _new_inv, canon, taxes = db.investment_withdraw_to_account(
            user_id,
            investment_name,
            None if want_all else float(amount),
            text,
            withdraw_all=want_all,
        )
    except LookupError:
        return _investment_not_found(user_id, investment_name, action="resgatar de")
    except ValueError as e:
        code = str(e)
        if code == "INSUFFICIENT_INVEST":
            return f"Saldo insuficiente no investimento **{investment_name}**.\n\n" + list_investments(user_id)
        if code == "AMOUNT_INVALID":
            return "Valor inválido para resgate. Tente: *resgatar 500 do CDB Nubank*."
        logger.warning("withdraw: código inesperado user=%s inv=%s code=%s", user_id, investment_name, code)
        return "Não consegui registrar esse resgate. Confira o valor e tente de novo."
    except PlanLimitExceeded:
        raise  # handle_incoming responde com a mensagem amigável de upgrade
    except Exception:
        logger.exception("withdraw falhou user=%s inv=%s", user_id, investment_name)
        return "Não consegui registrar esse resgate agora. Tente de novo em instantes."

    gross = float(taxes.get("gross", amount or 0)) if taxes else float(amount or 0)
    tax_note = ""
    if taxes and float(taxes.get("iof", 0) or 0) + float(taxes.get("ir", 0) or 0) > 0:
        tax_note = f" Líquido: **{fmt_brl(float(taxes.get('net', 0)))}**."
    verb = "Resgate total" if want_all else "Resgate"
    return f"✅ {verb} de **{fmt_brl(gross)}** de **{canon}**.{tax_note} ID #{db.display_id_for(user_id, launch_id)}.\n\n" + list_investments(user_id)
