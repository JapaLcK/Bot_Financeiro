"""
core/services/ai_chat/tools/pockets.py — tools de caixinhas.

Read:
  - list_pockets: lista caixinhas com saldo e descrição

Write (precisam de confirmação humana):
  - create_pocket
  - pocket_deposit  (Conta → Caixinha)
  - pocket_withdraw (Caixinha → Conta)
  - delete_pocket   (só se saldo == 0; valida em runtime)
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import db
from utils_date import _tz

from ._base import Tool


# ─── Read ───────────────────────────────────────────────────────────────────

def _pocket_goal_insight(balance: float, target_amount, target_date, today: date) -> dict[str, Any]:
    """Deriva o progresso da caixinha rumo à meta pra IA comentar.

    Retorna dict com progress_pct, remaining_to_goal, achieved, days_to_target
    e monthly_needed (quanto guardar por mês pra bater no prazo). Campos ficam
    None quando não há meta/prazo definidos.
    """
    out: dict[str, Any] = {
        "target_amount": None,
        "target_date": None,
        "progress_pct": None,
        "remaining_to_goal": None,
        "achieved": None,
        "days_to_target": None,
        "monthly_needed": None,
        "target_overdue": None,
    }

    goal = None
    if target_amount is not None:
        try:
            goal = float(target_amount)
        except (TypeError, ValueError):
            goal = None

    if goal and goal > 0:
        out["target_amount"] = round(goal, 2)
        remaining = max(0.0, goal - balance)
        out["remaining_to_goal"] = round(remaining, 2)
        out["progress_pct"] = round(min(balance / goal * 100, 100.0), 1)
        out["achieved"] = balance >= goal

    if target_date is not None:
        # target_date pode vir como date ou string ISO
        td = target_date
        if isinstance(td, str):
            try:
                td = datetime.strptime(td[:10], "%Y-%m-%d").date()
            except ValueError:
                td = None
        if isinstance(td, datetime):
            td = td.date()
        if isinstance(td, date):
            out["target_date"] = td.isoformat()
            days_left = (td - today).days
            out["days_to_target"] = days_left
            out["target_overdue"] = days_left < 0 and not (out["achieved"] or False)
            # Ritmo: só faz sentido se há meta em aberto e prazo futuro.
            if (
                out.get("remaining_to_goal")
                and out["remaining_to_goal"] > 0
                and days_left > 0
            ):
                months_left = days_left / 30.44
                if months_left > 0:
                    out["monthly_needed"] = round(out["remaining_to_goal"] / months_left, 2)

    return out


def _list_pockets(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    rows = db.list_pockets(user_id)
    today = datetime.now(_tz()).date()
    pockets = []
    for r in rows:
        balance = float(r["balance"] or 0)
        insight = _pocket_goal_insight(
            balance, r.get("target_amount"), r.get("target_date"), today
        )
        pockets.append(
            {
                "id": r["id"],
                "name": r["name"],
                "balance": balance,
                "description": r.get("description"),
                "status": r.get("status") or "active",
                "interest_enabled": bool(r.get("interest_enabled")),
                "interest_rate": float(r.get("interest_rate") or 1),
                "interest_period": r.get("interest_period") or "cdi",
                **insight,
            }
        )
    return {"pockets": pockets}


# ─── Write: create_pocket ───────────────────────────────────────────────────

def _create_pocket_summary(args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    desc = (args.get("description") or "").strip()
    if desc:
        return f'criar a caixinha "{name}" ({desc})'
    return f'criar a caixinha "{name}"'


def _create_pocket_execute(user_id: int, args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip() or None
    if not name:
        return "🐷 Faltou o nome da caixinha."
    try:
        db.create_pocket(user_id, name, description=description)
        return f'✅ Caixinha "{name}" criada.'
    except Exception as e:
        from core.services.plan_limits import PlanLimitExceeded
        if isinstance(e, PlanLimitExceeded):
            return e.message
        return f"🐷 Não consegui criar a caixinha: {e}"


# ─── Helpers de status/progresso pra mensagens de depósito ───────────────────

def _brl(v: float) -> str:
    """Formata em R$ pt-BR: 4550.0 -> 'R$ 4.550,00'."""
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(p: float) -> str:
    """45.0 -> '45%'; 45.5 -> '45,5%'."""
    if p == int(p):
        return f"{int(p)}%"
    return f"{p:.1f}%".replace(".", ",")


def _pace_line(amount: float, remaining: float, today: date) -> str:
    """Projeta quanto tempo pra completar a meta MANTENDO o ritmo deste depósito
    (assume aportes mensais do mesmo valor). Ex: faltam 5.500, depositou 50 ->
    ~110 meses (~9 anos)."""
    if amount <= 0 or remaining <= 0:
        return ""
    months = remaining / amount
    months_ceil = max(1, math.ceil(months))
    projected = today + timedelta(days=round(months * 30.44))
    quando = projected.strftime("%m/%Y")
    if months_ceil <= 1:
        tempo = "~1 mês"
    elif months_ceil >= 24:
        anos = months_ceil / 12
        tempo = f"~{months_ceil} meses (~{anos:.0f} anos)"
    else:
        tempo = f"~{months_ceil} meses"
    return (
        f"⏳ Mantendo esse ritmo de {_brl(amount)}/mês, você completa a meta em "
        f"{tempo} (por volta de {quando})."
    )


def _deposit_followup_lines(
    user_id: int, canon: str, amount: float, new_balance: float
) -> str:
    """Bloco de status + ritmo mostrado após um depósito na caixinha.

    Retorna string vazia se não achar a caixinha. Sem meta → mostra só o saldo.
    """
    try:
        rows = db.list_pockets(user_id, accrue=False)
    except Exception:
        return ""
    row = next(
        (r for r in rows if (r.get("name") or "").lower() == canon.lower()), None
    )
    if not row:
        return ""
    today = datetime.now(_tz()).date()
    ins = _pocket_goal_insight(
        new_balance, row.get("target_amount"), row.get("target_date"), today
    )
    if ins["target_amount"] is None:
        return f"🐷 Saldo da caixinha: {_brl(new_balance)}."
    if ins["achieved"]:
        over = new_balance - ins["target_amount"]
        extra = f" (passou {_brl(over)} da meta)" if over > 0.005 else ""
        return (
            f"🎉 Meta batida! {_brl(new_balance)} de "
            f"{_brl(ins['target_amount'])}{extra}."
        )
    lines = [
        f"📊 Agora: {_brl(new_balance)} de {_brl(ins['target_amount'])} "
        f"({_pct(ins['progress_pct'])}). Faltam {_brl(ins['remaining_to_goal'])}."
    ]
    pace = _pace_line(amount, ins["remaining_to_goal"], today)
    if pace:
        lines.append(pace)
    if ins.get("monthly_needed") and (ins.get("days_to_target") or 0) > 0:
        lines.append(
            f"🎯 Pra bater no prazo ({ins['target_date']}), o ritmo seria "
            f"~{_brl(ins['monthly_needed'])}/mês."
        )
    return "\n".join(lines)


# ─── Write: pocket_deposit ──────────────────────────────────────────────────

def _pocket_deposit_summary(args: dict[str, Any]) -> str:
    return (
        f'depositar R$ {args.get("amount"):.2f} na caixinha '
        f'"{args.get("pocket_name")}"'
        if isinstance(args.get("amount"), (int, float))
        else f'depositar na caixinha "{args.get("pocket_name")}"'
    )


def _pocket_deposit_execute(user_id: int, args: dict[str, Any]) -> str:
    pocket_name = (args.get("pocket_name") or "").strip()
    amount = float(args.get("amount") or 0)
    if not pocket_name or amount <= 0:
        return "🐷 Faltou o nome da caixinha ou o valor."
    from core.services import funding

    source = funding.resolve_deterministic(user_id, amount)["source"]
    try:
        _lid, _acc, new_pocket, canon = db.pocket_deposit_from_account(
            user_id, pocket_name, amount, funding_source=funding.to_db_arg(source)
        )
        msg = (f'✅ Depositado {_brl(amount)} na caixinha "{canon}"'
               f'{funding.origem_txt(source)}.')
        if source["kind"] == funding.BANK:
            msg += "\n" + funding.nota_sync()
        followup = _deposit_followup_lines(user_id, canon, amount, float(new_pocket))
        if followup:
            msg += "\n" + followup
        return msg
    except Exception as e:
        if "OF_POCKET_READONLY" in str(e):
            return "🐷 Essa caixinha é sincronizada com seu banco — mova o dinheiro pelo app do banco."
        return f"🐷 Não consegui depositar: {e}"


# ─── Write: pocket_withdraw ─────────────────────────────────────────────────

def _pocket_withdraw_summary(args: dict[str, Any]) -> str:
    if args.get("withdraw_all"):
        return f'sacar tudo da caixinha "{args.get("pocket_name")}" (zerar o saldo)'
    return (
        f'sacar R$ {args.get("amount"):.2f} da caixinha '
        f'"{args.get("pocket_name")}"'
        if isinstance(args.get("amount"), (int, float))
        else f'sacar da caixinha "{args.get("pocket_name")}"'
    )


def _pocket_withdraw_execute(user_id: int, args: dict[str, Any]) -> str:
    pocket_name = (args.get("pocket_name") or "").strip()
    withdraw_all = bool(args.get("withdraw_all"))
    amount = float(args.get("amount") or 0)
    if not pocket_name:
        return "🐷 Faltou o nome da caixinha."
    if not withdraw_all and amount <= 0:
        return "🐷 Faltou o valor (ou peça pra 'sacar tudo')."
    try:
        # O destino do saque sai de `db.destination_of_lots`, dentro da transação (#282).
        # Este canal não o exibe: a resposta do chat não nomeia banco nem avisa do sync.
        _lid, _acc, _pkt, canon, taxes, _dest = db.pocket_withdraw_to_account(
            user_id, pocket_name, None if withdraw_all else amount, withdraw_all=withdraw_all,
        )
        gross = float(taxes.get("gross", 0)) if taxes else 0.0
        tax = (float(taxes.get("ir", 0)) + float(taxes.get("iof", 0))) if taxes else 0.0
        tax_txt = f" — IR/IOF R$ {tax:.2f}" if tax > 0 else ""
        if withdraw_all:
            return f'✅ Caixinha "{canon}" esvaziada: sacado R$ {gross:.2f}{tax_txt}.'
        return f'✅ Sacado R$ {gross:.2f} da caixinha "{canon}"{tax_txt}.'
    except ValueError as e:
        if "OF_POCKET_READONLY" in str(e):
            return "🐷 Essa caixinha é sincronizada com seu banco — mova o dinheiro pelo app do banco."
        if "INSUFFICIENT_POCKET" in str(e):
            return f'🐷 A caixinha "{pocket_name}" não tem esse saldo.'
        return "🐷 Valor inválido pra saque."
    except LookupError:
        return f'🐷 Não achei a caixinha "{pocket_name}".'
    except Exception as e:
        return f"🐷 Não consegui sacar: {e}"


# ─── Write: delete_pocket ───────────────────────────────────────────────────

def _delete_pocket_summary(args: dict[str, Any]) -> str:
    return f'apagar a caixinha "{args.get("pocket_name")}"'


def _delete_pocket_execute(user_id: int, args: dict[str, Any]) -> str:
    pocket_name = (args.get("pocket_name") or "").strip()
    if not pocket_name:
        return "🐷 Faltou o nome da caixinha."
    try:
        db.delete_pocket(user_id, pocket_name)
        return f'✅ Caixinha "{pocket_name}" apagada.'
    except ValueError as e:
        if "POCKET_NOT_ZERO" in str(e):
            return (
                f'🐷 A caixinha "{pocket_name}" ainda tem saldo. '
                "Saca o que tem dentro antes de apagar."
            )
        return f"🐷 {e}"
    except LookupError:
        return f'🐷 Não achei a caixinha "{pocket_name}".'


# ─── Tools registry ─────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_pockets",
                "description": "Lista as caixinhas (cofrinhos) com nome, saldo, meta (target_amount), data-alvo (target_date) e progresso: progress_pct (% da meta), remaining_to_goal (quanto falta), achieved (se já bateu), days_to_target (dias até o prazo), monthly_needed (quanto guardar por mês pra bater no prazo), target_overdue (prazo estourado sem bater). Use pra 'quais minhas caixinhas?', 'quanto tem na de viagem?', 'já bati a meta?', 'quanto falta pra minha reserva?', 'tô no ritmo?'.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_list_pockets,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "create_pocket",
                "description": "Cria uma nova caixinha (cofrinho) com nome e descrição opcional. Ação de ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome da caixinha (ex: 'Viagem', 'Reserva')."},
                        "description": {"type": "string", "description": "Descrição/objetivo opcional."},
                    },
                    "required": ["name"],
                },
            },
        },
        is_write=True,
        summary=_create_pocket_summary,
        execute=_create_pocket_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "pocket_deposit",
                "description": "Move dinheiro da conta corrente para uma caixinha (cofrinho). Use pra 'guarda 100 na viagem', 'manda 50 pra reserva'. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pocket_name": {"type": "string"},
                        "amount": {"type": "number", "minimum": 0.01},
                    },
                    "required": ["pocket_name", "amount"],
                },
            },
        },
        is_write=True,
        summary=_pocket_deposit_summary,
        execute=_pocket_deposit_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "pocket_withdraw",
                "description": "Retira dinheiro de uma caixinha (cofrinho) e devolve pra conta corrente. Para esvaziar/zerar a caixinha (ex: 'saca tudo', 'esvazia a reserva', 'tira tudo da viagem'), passe withdraw_all=true e omita amount — o IR/IOF sobre o rendimento é calculado automaticamente. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pocket_name": {"type": "string"},
                        "amount": {"type": "number", "minimum": 0.01, "description": "Valor a sacar. Omita quando withdraw_all=true."},
                        "withdraw_all": {"type": "boolean", "description": "true para sacar TODO o saldo e zerar a caixinha (IR/IOF calculado sobre o rendimento)."},
                    },
                    "required": ["pocket_name"],
                },
            },
        },
        is_write=True,
        summary=_pocket_withdraw_summary,
        execute=_pocket_withdraw_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_pocket",
                "description": "Apaga uma caixinha (cofrinho). SÓ funciona se o saldo for zero — o sistema vai recusar caso tenha dinheiro dentro. ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pocket_name": {"type": "string"},
                    },
                    "required": ["pocket_name"],
                },
            },
        },
        is_write=True,
        summary=_delete_pocket_summary,
        execute=_delete_pocket_execute,
    ),
]
