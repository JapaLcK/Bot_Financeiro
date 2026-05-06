# core/handlers/investments.py
from __future__ import annotations
import db
from utils_text import fmt_brl, fmt_rate
from core.dashboard_links import build_dashboard_link
import logging

logger = logging.getLogger(__name__)


def _investment_dashboard_link(user_id: int) -> str:
    link = build_dashboard_link(user_id, view="investments")
    if not link:
        return "⚠️ Não consegui gerar o link do dashboard agora. Tente novamente em instantes."
    return (
        "O bot cria investimentos pelo dashboard para evitar cadastro incompleto por mensagem.\n"
        "Abra a aba de investimentos para criar, editar, aportar ou resgatar com todos os detalhes:\n"
        f"{link}\n"
        "⏱️ Link mágico de uso único, expira em 5 minutos."
    )


def list_investments(user_id: int, intro: str | None = None) -> str:
    rows = db.accrue_all_investments(user_id)
    header = intro or "📈 **Investimentos**"
    if not rows:
        return (
            f"{header}\n"
            "Você ainda não tem investimentos cadastrados.\n\n"
            f"{_investment_dashboard_link(user_id)}"
        )

    last_dates = [r.get("last_date") for r in rows if r.get("last_date")]
    base_date_txt = ""
    if last_dates:
        base_date = max(last_dates)
        base_date_txt = f"Atualizado até {base_date.strftime('%d/%m/%Y')}\n"

    projected_untils = [
        r.get("projected_until") for r in rows
        if r.get("projected_days") and r.get("projected_until")
    ]
    projection_note = ""
    if projected_untils:
        proj_date = max(projected_untils)
        projection_note = (
            f"_Saldo estimado até {proj_date.strftime('%d/%m/%Y')} "
            "usando a última taxa conhecida — será corrigido quando o BCB "
            "publicar os dados oficiais._\n"
        )

    lines = []
    for r in rows:
        rate_txt = fmt_rate(r.get("rate"), r.get("period"))
        asset = r.get("asset_type") or "CDB"
        projected_balance = r.get("projected_balance")
        projected_days = r.get("projected_days") or 0
        if projected_days > 0 and projected_balance:
            balance_txt = f"{fmt_brl(float(projected_balance))} *"
        else:
            balance_txt = fmt_brl(float(r["balance"]))
        lines.append(f"• **{r['name']}** [{asset}]: {balance_txt} ({rate_txt})")
    return (
        f"{header}\n"
        + base_date_txt
        + projection_note
        + "\n".join(lines)
        + "\n\n"
        + _investment_dashboard_link(user_id)
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

    try:
        launch_id, _new_acc, _new_inv, canon = db.investment_deposit_from_account(
            user_id, investment_name, float(amount), text
        )
        display_id = db.display_id_for(user_id, launch_id)
        return f"✅ Aporte de **{fmt_brl(float(amount))}** em **{canon}**. ID #{display_id}."
    except Exception as e:
        err = str(e)
        if "not found" in err.lower():
            return list_investments(user_id, f"Não encontrei **{investment_name}**. Estes são seus investimentos:")
        if "saldo insuficiente" in err.lower() or "insufficient" in err.lower():
            return "Saldo insuficiente na conta para esse aporte.\n\n" + _investment_dashboard_link(user_id)
        return f"Erro ao aportar: {err}"
    return f"✅ Aporte de **{fmt_brl(float(amount))}** em **{canon}**. ID #{db.display_id_for(user_id, launch_id)}.\n\n" + list_investments(user_id)


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


def withdraw(user_id: int, text: str, entities: dict) -> str:
    investment_name = entities.get("investment_name")
    amount = entities.get("amount")

    if not investment_name:
        return list_investments(user_id, "De qual investimento você quer resgatar?")
    if not amount or float(amount) <= 0:
        return list_investments(user_id, "Qual valor você quer resgatar?")

    try:
        launch_id, _new_acc, _new_inv, canon, taxes = db.investment_withdraw_to_account(
            user_id, investment_name, float(amount), text
        )
        tax_note = ""
        if taxes and float(taxes.get("iof", 0) or 0) + float(taxes.get("ir", 0) or 0) > 0:
            tax_note = f" Líquido: **{fmt_brl(float(taxes.get('net', 0)))}**."
        return f"✅ Resgate de **{fmt_brl(float(amount))}** de **{canon}**.{tax_note} ID #{db.display_id_for(user_id, launch_id)}.\n\n" + list_investments(user_id)
    except Exception as e:
        err = str(e)
        if "not found" in err.lower():
            return list_investments(user_id, f"Não encontrei **{investment_name}**. Estes são seus investimentos:")
        if "saldo insuficiente" in err.lower() or "insufficient" in err.lower():
            return f"Saldo insuficiente no investimento **{investment_name}**.\n\n" + list_investments(user_id)
        return f"Erro ao resgatar: {err}"
