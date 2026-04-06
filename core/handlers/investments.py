# core/handlers/investments.py
from __future__ import annotations
import db
from utils_text import fmt_brl, fmt_rate
from investment_parse import parse_interest


def list_investments(user_id: int) -> str:
    rows = db.list_investments(user_id)
    if not rows:
        return "Você ainda não tem investimentos.\nCrie um: *criar investimento CDB 1% ao mês*"
    lines = []
    for r in rows:
        rate_txt = fmt_rate(r.get("rate"), r.get("period"))
        lines.append(f"• **{r['name']}**: {fmt_brl(float(r['balance']))} ({rate_txt})")
    return "📈 **Investimentos**:\n" + "\n".join(lines)


def create(user_id: int, raw_name: str, original_text: str) -> str:
    """
    Extrai nome e taxa do texto.
    Formato esperado: "criar investimento CDB Nubank 1% ao mês"
    raw_name: tudo que vem depois de "criar investimento"
    """
    parsed = parse_interest(raw_name)
    if not parsed:
        parsed = parse_interest(original_text)

    if not parsed:
        return (
            "Não identifiquei a taxa. Tente:\n"
            "*criar investimento CDB 1% ao mês*\n"
            "*criar investimento Tesouro 0,03% ao dia*"
        )

    taxa, period = parsed

    # nome = tudo antes da taxa
    import re
    name = re.sub(r"\s*\d+[.,]?\d*\s*%.*$", "", raw_name, flags=re.IGNORECASE).strip()
    if not name:
        name = raw_name.strip()

    try:
        inv_id, canon = db.create_investment(user_id, name, taxa, period)
        period_label = {"daily": "ao dia", "monthly": "ao mês", "yearly": "ao ano"}.get(period, period)
        return f"✅ Investimento criado: **{canon}** — {taxa*100:.4g}% {period_label} (id {inv_id})"
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
