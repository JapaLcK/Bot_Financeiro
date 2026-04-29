# core/handlers/investments.py
from __future__ import annotations
import db
from utils_text import fmt_brl, fmt_rate
from investment_parse import parse_initial_amount, parse_interest, parse_investment_spec
import logging

logger = logging.getLogger(__name__)


def list_investments(user_id: int) -> str:
    rows = db.accrue_all_investments(user_id)
    if not rows:
        return "Você ainda não tem investimentos.\nCrie um: *criar investimento CDB 1% ao mês*"

    last_dates = [r.get("last_date") for r in rows if r.get("last_date")]
    base_date_txt = ""
    if last_dates:
        base_date = max(last_dates)
        base_date_txt = f"Atualizado até {base_date.strftime('%d/%m/%Y')}\n"

    lines = []
    for r in rows:
        rate_txt = fmt_rate(r.get("rate"), r.get("period"))
        asset = r.get("asset_type") or "CDB"
        lines.append(f"• **{r['name']}** [{asset}]: {fmt_brl(float(r['balance']))} ({rate_txt})")
    return "📈 **Investimentos**:\n" + base_date_txt + "\n".join(lines)


def create(user_id: int, raw_name: str, original_text: str) -> str:
    """
    Extrai nome e taxa do texto.
    Formato esperado: "criar investimento CDB Nubank 1% ao mês"
    raw_name: tudo que vem depois de "criar investimento"
    """
    spec = parse_investment_spec(raw_name) or parse_investment_spec(original_text)

    if not spec:
        return (
            "Não identifiquei a taxa. Tente:\n"
            "*criar investimento CDB Banco 110% CDI*\n"
            "*criar investimento CDB Banco CDI + 2,5% a.a.*\n"
            "*criar investimento Tesouro IPCA+ 2029 IPCA + 7,43% a.a.*\n"
            "*criar investimento Tesouro Prefixado 13,59% a.a.*"
        )

    taxa = spec["rate"]
    period = spec["period"]
    name = spec["name"] or raw_name.strip()

    try:
        initial_amount = parse_initial_amount(original_text)
        kwargs = {
            "nota": original_text,
            "asset_type": spec.get("asset_type"),
            "indexer": spec.get("indexer"),
            "tax_profile": spec.get("tax_profile"),
        }
        if initial_amount is not None:
            kwargs["initial_amount"] = initial_amount
        launch_id, _inv_id, canon = db.create_investment_db(
            user_id,
            name,
            taxa,
            period,
            **kwargs,
        )
        if launch_id is None:
            return f"Já existe um investimento com esse nome. Use outro nome."

        rate_txt = fmt_rate(taxa, period)
        return f"✅ Investimento criado: **{canon}** — {rate_txt} (id {launch_id})"
    except Exception as e:
        if "already exists" in str(e).lower() or "unique" in str(e).lower():
            return f"Já existe um investimento com esse nome. Use outro nome."
        return f"Erro ao criar investimento: {e}"


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
        return "Em qual investimento? Tente: *apliquei 500 no CDB Nubank*"
    if not amount or float(amount) <= 0:
        return "Qual o valor? Tente: *apliquei 500 no CDB Nubank*"

    try:
        launch_id, _new_acc, _new_inv, canon = db.investment_deposit_from_account(
            user_id, investment_name, float(amount), text
        )
        return f"✅ Aporte de **{fmt_brl(float(amount))}** em **{canon}**. ID #{launch_id}."
    except Exception as e:
        err = str(e)
        if "not found" in err.lower():
            return f"Investimento **{investment_name}** não encontrado. Use *listar investimentos* para ver os disponíveis."
        if "saldo insuficiente" in err.lower() or "insufficient" in err.lower():
            return "Saldo insuficiente na conta para esse aporte."
        return f"Erro ao aportar: {err}"


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
            f"💡 Para criar um investimento atrelado ao CDI:\n"
            f"_criar investimento CDB 110% CDI_"
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
        return "De qual investimento? Tente: *resgatei 200 do CDB Nubank*"
    if not amount or float(amount) <= 0:
        return "Qual o valor? Tente: *resgatei 200 do CDB Nubank*"

    try:
        launch_id, _new_acc, _new_inv, canon = db.investment_withdraw_to_account(
            user_id, investment_name, float(amount), text
        )
        return f"✅ Resgate de **{fmt_brl(float(amount))}** de **{canon}**. ID #{launch_id}."
    except Exception as e:
        err = str(e)
        if "not found" in err.lower():
            return f"Investimento **{investment_name}** não encontrado. Use *listar investimentos* para ver os disponíveis."
        if "saldo insuficiente" in err.lower() or "insufficient" in err.lower():
            return f"Saldo insuficiente no investimento **{investment_name}**."
        return f"Erro ao resgatar: {err}"
