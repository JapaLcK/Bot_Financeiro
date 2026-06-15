"""
core/services/ai_chat/tools/investments.py — tools de investimentos.

Read:
  - list_investments: lista investimentos cadastrados com saldo, taxa, vencimento
  - get_investment_summary: totais agregados (valor aplicado, número de ativos)

Write (precisam de confirmação humana):
  - create_investment:     cadastra um investimento novo
  - investment_deposit:    aporta na conta corrente → investimento
  - investment_withdraw:   resgata investimento → conta corrente
  - delete_investment:     apaga (só com saldo zero, regra do db)

Importante: a regra "NUNCA dê conselho de investimento específico" (regra 4
do system prompt) continua valendo. Listar carteira, ver saldo, aportar
quantia que o user pediu — nada disso é dar conselho. Conselho seria
"compre Petrobras". As tools aqui são operações sobre os dados do user.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import db
from utils_text import fmt_rate

from ._base import Tool


_PERIODS = ("daily", "monthly", "yearly")


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


# ─── Read ───────────────────────────────────────────────────────────────────

def _list_investments(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    # accrue_all_investments aplica juros ate hoje E retorna a lista — usa
    # ele em vez de db.list_investments pra evitar mostrar saldo defasado.
    rows = db.accrue_all_investments(user_id)
    return {
        "investments": [
            {
                "id": r["id"],
                "name": r["name"],
                "balance": float(
                    r.get("projected_balance")
                    if r.get("projected_days") and r.get("projected_balance")
                    else r["balance"] or 0
                ),
                # rate_display ja formatado (ex: "116% CDI", "13,78% a.a.",
                # "IPCA + 7,62% a.a."). Use ele direto na resposta — NAO
                # interprete `rate` cru, ele varia de significado (multiplier
                # CDI vs % anual) conforme `period`/`indexer`.
                "rate_display": fmt_rate(r.get("rate"), r.get("period")),
                "asset_type": r.get("asset_type"),
                "indexer": r.get("indexer"),
                "issuer": r.get("issuer"),
                "purchase_date": r["purchase_date"].isoformat() if r.get("purchase_date") else None,
                "maturity_date": r["maturity_date"].isoformat() if r.get("maturity_date") else None,
            }
            for r in rows
        ]
    }


def _get_investment_summary(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    # Mesma logica do _list_investments: aplica accrual antes de somar.
    rows = db.accrue_all_investments(user_id)
    total = 0.0
    for r in rows:
        if r.get("projected_days") and r.get("projected_balance"):
            total += float(r["projected_balance"])
        else:
            total += float(r["balance"] or 0)
    return {
        "total_invested": total,
        "investment_count": len(rows),
        "investments_with_balance": sum(1 for r in rows if float(r["balance"] or 0) > 0),
    }


def _get_investment_contributions(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Conta aportes em investimentos num período. NÃO usa get_summary_by_period
    porque essa filtra is_internal_movement=false e aportes sao internal."""
    today = date.today()
    start = _parse_iso_date(args.get("start_date")) or today.replace(day=1)
    end = _parse_iso_date(args.get("end_date")) or today

    if end < start:
        return {"error": "end_date anterior a start_date"}

    launches = db.get_launches_by_period(user_id, start, end)

    total = 0.0
    count = 0
    by_investment: dict[str, float] = {}
    for l in launches:
        if l.get("tipo") != "aporte_investimento":
            continue
        val = float(l.get("valor") or 0)
        total += val
        count += 1
        name = l.get("alvo") or "?"
        by_investment[name] = by_investment.get(name, 0.0) + val

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "total_contributed": total,
        "contribution_count": count,
        "by_investment": [
            {"name": k, "total": v} for k, v in sorted(by_investment.items())
        ],
    }


# ─── Write: create_investment ───────────────────────────────────────────────

def _create_investment_summary(args: dict[str, Any]) -> str:
    rate = args.get("rate")
    period = args.get("period")
    period_pt = {"daily": "ao dia", "monthly": "ao mês", "yearly": "ao ano"}.get(period or "", period)
    rate_part = f" a {float(rate):.2f}% {period_pt}" if rate is not None else ""
    return f'criar o investimento "{args.get("name")}"{rate_part}'


def _create_investment_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    period = (args.get("period") or "").strip()
    try:
        rate_pct = float(args.get("rate"))  # 14.25 == 14,25%; 100 == 100% (do CDI ou a.a.)
    except (TypeError, ValueError):
        return "🐷 Informa uma taxa numérica."
    if not name:
        return "🐷 Faltou o nome do investimento."
    if period not in _PERIODS:
        return '🐷 O period precisa ser "daily", "monthly" ou "yearly".'
    if rate_pct <= 0:
        return "🐷 A taxa precisa ser maior que zero."
    # Storage: a tabela investments guarda rate como decimal/multiplier
    # (1.00 == 100%). O dashboard multiplica por 100 pra exibir. fmt_rate
    # tolera ambas as escalas pra compat, mas o caminho consistente e
    # sempre gravar decimal.
    rate_decimal = rate_pct / 100
    try:
        db.create_investment(user_id, name, rate_decimal, period)
        return f'✅ Investimento "{name}" criado.'
    except ValueError as e:
        return f"🐷 Não consegui criar: {e}"
    except Exception as e:
        return f"🐷 Não consegui criar: {e}"


# ─── Write: investment_deposit (aporte) ─────────────────────────────────────

def _investment_deposit_summary(args: dict[str, Any]) -> str:
    name = args.get("name")
    amount = args.get("amount")
    if isinstance(amount, (int, float)):
        return f'aportar R$ {amount:.2f} no investimento "{name}"'
    return f'aportar no investimento "{name}"'


def _investment_deposit_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    try:
        amount = float(args.get("amount") or 0)
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if not name or amount <= 0:
        return "🐷 Faltou o nome do investimento ou o valor."
    try:
        db.investment_deposit_from_account(user_id, name, amount)
        return f'✅ Aporte de R$ {amount:.2f} no "{name}" registrado.'
    except Exception as e:
        return f"🐷 Não consegui aportar: {e}"


# ─── Write: investment_withdraw (resgate) ───────────────────────────────────

def _investment_withdraw_summary(args: dict[str, Any]) -> str:
    name = args.get("name")
    if args.get("withdraw_all"):
        return f'resgatar tudo do investimento "{name}" (zerar o saldo)'
    amount = args.get("amount")
    if isinstance(amount, (int, float)):
        return f'resgatar R$ {amount:.2f} do investimento "{name}"'
    return f'resgatar do investimento "{name}"'


def _investment_withdraw_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    withdraw_all = bool(args.get("withdraw_all"))
    try:
        amount = float(args.get("amount") or 0)
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if not name:
        return "🐷 Faltou o nome do investimento."
    if not withdraw_all and amount <= 0:
        return "🐷 Faltou o valor (ou peça pra 'resgatar tudo')."
    try:
        _lid, _acc, _inv, canon, taxes = db.investment_withdraw_to_account(
            user_id, name, None if withdraw_all else amount, withdraw_all=withdraw_all,
        )
        gross = float(taxes.get("gross", 0)) if taxes else 0.0
        tax = (float(taxes.get("ir", 0)) + float(taxes.get("iof", 0))) if taxes else 0.0
        tax_txt = f" — IR/IOF R$ {tax:.2f}" if tax > 0 else ""
        if withdraw_all:
            return f'✅ Investimento "{canon}" zerado: resgatado R$ {gross:.2f}{tax_txt}.'
        return f'✅ Resgate de R$ {gross:.2f} do "{canon}"{tax_txt}.'
    except ValueError as e:
        if "INSUFFICIENT_INVEST" in str(e):
            return f'🐷 O investimento "{name}" não tem esse saldo.'
        return "🐷 Valor inválido pra resgate."
    except LookupError:
        return f'🐷 Não achei o investimento "{name}".'
    except Exception as e:
        return f"🐷 Não consegui resgatar: {e}"


# ─── Write: delete_investment ───────────────────────────────────────────────

def _delete_investment_summary(args: dict[str, Any]) -> str:
    return f'apagar o investimento "{args.get("name")}"'


def _delete_investment_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "🐷 Faltou o nome do investimento."
    try:
        db.delete_investment(user_id, name)
        return f'✅ Investimento "{name}" apagado.'
    except Exception as e:
        return f"🐷 Não consegui apagar: {e}"


# ─── Tools registry ─────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_investments",
                "description": "Lista os investimentos cadastrados do usuário com saldo, taxa, emissor, vencimento. Use pra 'quais meus investimentos?', 'minha carteira', 'quanto tenho aplicado'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_list_investments,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_investment_summary",
                "description": "Retorna totais agregados da carteira: valor total aplicado, número de investimentos, quantos têm saldo > 0. Use pra 'quanto eu tenho investido no total?', 'quantos ativos eu tenho?'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_get_investment_summary,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_investment_contributions",
                "description": "Retorna quanto o usuário APORTOU em investimentos num período (default: mês corrente). Total, quantidade e breakdown por investimento. Use pra 'quanto aportei esse mês?', 'meus aportes em abril', 'quanto investi semana passada'. NÃO use get_period_summary pra isso — ela exclui movimentos internos como aporte.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "ISO 8601 (YYYY-MM-DD). Default: primeiro dia do mês corrente.",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "ISO 8601 (YYYY-MM-DD), inclusiva. Default: hoje.",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_get_investment_contributions,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "create_investment",
                "description": "Cria um investimento novo (sem aporte inicial — saldo começa em 0). ESCRITA — pede confirmação. Pra aportar use investment_deposit depois.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome do investimento (ex: 'Tesouro Selic 2029', 'CDB Nubank')."},
                        "rate": {
                            "type": "number",
                            "minimum": 0.01,
                            "description": "Taxa em PORCENTAGEM como o user fala (ex: 14.25 → 14,25%; 100 → 100% do CDI). NÃO converter pra decimal — passa o número literal que o user disse.",
                        },
                        "period": {"type": "string", "enum": list(_PERIODS), "description": "Periodicidade da taxa: daily, monthly ou yearly."},
                    },
                    "required": ["name", "rate", "period"],
                },
            },
        },
        is_write=True,
        summary=_create_investment_summary,
        execute=_create_investment_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "investment_deposit",
                "description": "Aporta dinheiro da conta corrente num investimento existente. Use pra 'aportar 1000 no Tesouro Selic', 'invest 500 no CDB'. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome do investimento (deve já existir)."},
                        "amount": {"type": "number", "minimum": 0.01, "description": "Valor em reais."},
                    },
                    "required": ["name", "amount"],
                },
            },
        },
        is_write=True,
        summary=_investment_deposit_summary,
        execute=_investment_deposit_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "investment_withdraw",
                "description": "Resgata dinheiro de um investimento de volta pra conta corrente. FIFO por lotes. Use pra 'resgatar 500 do CDB Nubank'. Para zerar/esvaziar o investimento (ex: 'resgata tudo do CDB', 'esvazia o Tesouro'), passe withdraw_all=true e omita amount — IR/IOF calculado automaticamente. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "amount": {"type": "number", "minimum": 0.01, "description": "Valor a resgatar. Omita quando withdraw_all=true."},
                        "withdraw_all": {"type": "boolean", "description": "true para resgatar TODO o saldo e zerar o investimento (IR/IOF sobre o rendimento calculado automaticamente)."},
                    },
                    "required": ["name"],
                },
            },
        },
        is_write=True,
        summary=_investment_withdraw_summary,
        execute=_investment_withdraw_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_investment",
                "description": "Apaga um investimento do cadastro. Só funciona se saldo for zero — se houver saldo, resgata primeiro. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        },
        is_write=True,
        summary=_delete_investment_summary,
        execute=_delete_investment_execute,
    ),
]
