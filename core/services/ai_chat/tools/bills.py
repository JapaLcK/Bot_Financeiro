"""
core/services/ai_chat/tools/bills.py — tools de CONTAS A PAGAR (boletos) da IA.

Read:
  - get_bills_to_pay: devolve TODAS as contas a pagar (pendentes por padrão) já
    com os campos derivados que a IA precisa pra responder qualquer pergunta do
    domínio SEM inventar conta: `today`, `days_until` (0=hoje, 1=amanhã, negativo
    =vencida), `due_day` (dia do mês), agrupamento `by_due_date`, total estimado,
    quantas vencidas, etc. A IA agrega em cima disso ("quantas amanhã", "qual dia
    tem mais contas", "valor total", "quantas pro dia 6", ...).

Write:
  - pay_bill: marca uma conta a pagar como paga (por nome ou id) → cria o
    lançamento de despesa (debita + categoriza). Reversível (undo), então não
    pede confirmação — igual add_launch.

Conta a pagar = recorrente com payment_mode='manual' (ver db/bills.py). Valor
variável (água/luz): `amount` é só estimativa; pagar exige o valor real.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ._base import Tool

_WD = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _today() -> date:
    try:
        from utils_date import now_tz
        return now_tz().date()
    except Exception:
        return date.today()


def _bill_view(r: dict[str, Any], today: date) -> dict[str, Any]:
    due = r.get("due_date")
    d = None
    if due:
        try:
            d = date.fromisoformat(str(due)[:10])
        except ValueError:
            d = None
    days_until = (d - today).days if d else None
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "category": r.get("category"),
        "due_date": due,
        "due_day": d.day if d else None,
        "weekday": _WD[d.weekday()] if d else None,
        "days_until": days_until,
        "amount": r.get("amount"),
        "variable": bool(r.get("variable_amount")),
        "status": r.get("status"),
        "overdue": bool(d and r.get("status") == "pending" and d < today),
    }


# ─── Read: get_bills_to_pay ───────────────────────────────────────────────────

def _get_bills_to_pay(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    from db.bills import list_bills

    include_paid = bool(args.get("include_paid"))
    today = _today()
    # Garante que as instâncias do próximo ciclo existem (conta recém-criada).
    try:
        from core.services.recurring_charger import sync_manual_bills_once
        sync_manual_bills_once(None, user_id)
    except Exception:
        pass
    rows = list_bills(user_id, include_paid=include_paid, limit=200)
    all_bills = [_bill_view(r, today) for r in rows]
    pend = [b for b in all_bills if b["status"] == "pending"]

    total = round(sum(float(b["amount"] or 0) for b in pend), 2)
    by_due_date: dict[str, dict[str, Any]] = {}
    for b in pend:
        key = b["due_date"]
        if not key:
            continue
        slot = by_due_date.setdefault(key, {"count": 0, "total": 0.0})
        slot["count"] += 1
        slot["total"] = round(slot["total"] + float(b["amount"] or 0), 2)

    next_due = None
    upcoming = sorted((b for b in pend if b["days_until"] is not None and b["days_until"] >= 0),
                      key=lambda b: b["days_until"])
    if upcoming:
        nb = upcoming[0]
        next_due = {"name": nb["name"], "due_date": nb["due_date"], "days_until": nb["days_until"]}

    return {
        "today": today.isoformat(),
        "count_pending": len(pend),
        "total_estimated_value": total,
        "overdue_count": sum(1 for b in pend if b["overdue"]),
        "has_variable": any(b["variable"] for b in pend),
        "next_due": next_due,
        "by_due_date": by_due_date,
        "bills": all_bills if include_paid else pend,
        "note": (
            "days_until: 0=hoje, 1=amanhã, 2=daqui 2 dias, negativo=vencida. "
            "due_day é o dia do mês. Use 'today' pra qualquer cálculo de data "
            "relativa. amount de conta com variable=true é só estimativa "
            "(o valor real é informado ao pagar)."
        ),
    }


# ─── Write: pay_bill ──────────────────────────────────────────────────────────

def _resolve_bill(user_id: int, args: dict[str, Any]):
    """Retorna (bill, error_msg). bill=None + msg se não deu pra resolver."""
    from db.bills import get_bill, list_bills

    bill_id = args.get("bill_id")
    if bill_id:
        b = get_bill(user_id, int(bill_id))
        if not b:
            return None, "🐷 Não achei essa conta a pagar."
        return b, None

    name = (args.get("name") or "").strip()
    if not name:
        return None, "🐷 Qual conta você pagou? Diz o nome (ex: *luz*, *água*)."

    from utils_text import normalize_text
    nn = normalize_text(name)
    pend = [b for b in list_bills(user_id, include_paid=False) if b.get("status") == "pending"]
    matches = [b for b in pend if nn and (nn in normalize_text(b.get("name") or "")
                                          or normalize_text(b.get("name") or "") in nn)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        nomes = ", ".join(b.get("name") or "?" for b in matches[:5])
        return None, f"🐷 Tem mais de uma conta com esse nome: {nomes}. Qual delas?"
    return None, f'🐷 Não achei conta a pagar pendente com o nome "{name}".'


def _pay_bill_execute(user_id: int, args: dict[str, Any]) -> str:
    from db.bills import mark_bill_paid
    from utils_text import fmt_brl

    bill, err = _resolve_bill(user_id, args)
    if err:
        return err

    amount = args.get("amount")
    try:
        amount = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount = None

    if bill.get("variable_amount") and amount is None:
        nome = bill.get("name") or "conta"
        return (f"🐷 A conta de *{nome}* tem valor variável. Quanto veio este mês? "
                f"Ex: *paguei {str(nome).lower()} 132,50*")

    try:
        paid = mark_bill_paid(user_id, int(bill["id"]), amount)
    except Exception as e:
        return f"🐷 Não consegui registrar o pagamento: {e}"
    if paid is None:
        return "🐷 Essa conta já estava paga (ou não achei mais)."
    val = paid.get("paid_amount") or paid.get("amount") or 0
    return (f"✅ Conta paga: *{paid.get('name')}* — {fmt_brl(val)} lançado e "
            f"categorizado. Tá tudo em dia! 🐷")


# ─── Add boleto (avulso) + projeção de caixa ─────────────────────────────────

def _resolve_date(s: Any, days: Any = None) -> date | None:
    """Resolve uma data alvo a partir de 'YYYY-MM-DD' / 'DD/MM' / 'DD/MM/YYYY' ou
    de um número de dias a partir de hoje. Pra 'DD/MM' sem ano, usa o próximo
    (este ano se ainda não passou, senão o que vem) — prazo é sempre futuro."""
    today = _today()
    if days is not None:
        try:
            return today + timedelta(days=int(days))
        except (TypeError, ValueError):
            pass
    if not s:
        return None
    txt = str(s).strip()
    try:
        return date.fromisoformat(txt[:10])
    except ValueError:
        pass
    import re
    # dia do mês solto ("dia 10" → o LLM manda "10"): próxima ocorrência >= hoje
    if re.fullmatch(r"\d{1,2}", txt):
        d = int(txt)
        if 1 <= d <= 31:
            import calendar
            y, mo = today.year, today.month
            for _ in range(2):
                dim = calendar.monthrange(y, mo)[1]
                cand = date(y, mo, min(d, dim))
                if cand >= today:
                    return cand
                mo += 1
                if mo > 12:
                    mo, y = 1, y + 1
        return None
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?$", txt)
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    if m.group(3):
        y = int(m.group(3)); y = y + 2000 if y < 100 else y
    else:
        y = today.year
    try:
        cand = date(y, mo, d)
    except ValueError:
        return None
    if not m.group(3) and cand < today:
        try:
            cand = date(y + 1, mo, d)
        except ValueError:
            return None
    return cand


def _add_boleto_execute(user_id: int, args: dict[str, Any]) -> str:
    from db.bills import create_boleto
    from utils_text import fmt_brl

    name = (args.get("name") or "").strip()
    if not name:
        return "🐷 Qual a descrição do boleto? (ex: Distribuidora XYZ, IPTU)"
    amount = args.get("amount")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return "🐷 Qual o valor do boleto?"
    due = _resolve_date(args.get("due_date"), args.get("days"))
    if due is None:
        return "🐷 Qual o vencimento do boleto? (ex: 15/08)"
    try:
        b = create_boleto(user_id, name, amount, due)
    except Exception as e:
        return f"🐷 Não consegui cadastrar o boleto: {e}"
    d = date.fromisoformat(b["due_date"][:10]) if b.get("due_date") else due
    return (f"🧾 Boleto anotado: *{b.get('name')}* — {fmt_brl(amount)}, vence "
            f"{d.strftime('%d/%m')}. Eu te lembro perto do vencimento. 🐷")


def _check_cashflow(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    from core.services.cashflow import project

    target = _resolve_date(args.get("date"), args.get("days"))
    if target is None:
        return {"error": "informe a data do prazo (ex: 16/08) ou 'daqui N dias'."}
    extra = args.get("amount")
    try:
        extra = float(extra) if extra is not None else 0.0
    except (TypeError, ValueError):
        extra = 0.0
    p = project(user_id, target, extra)
    p["note"] = ("projetado = saldo + receitas previstas − gastos fixos − boletos até a data"
                 + (" − boleto novo em análise" if extra > 0 else "")
                 + ". tranquilo=true significa que o caixa fica positivo até lá.")
    return p


# ─── Tools registry ───────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_bills_to_pay",
                "description": (
                    "Lista as CONTAS A PAGAR / BOLETOS do usuário (contas que ele paga na mão, "
                    "com lembrete — não são débito automático). Use pra QUALQUER pergunta sobre "
                    "contas a pagar / boletos: quantas contas tem, quantas vencem amanhã / daqui N "
                    "dias / no dia X, qual dia tem mais contas, valor total a pagar, o que está "
                    "vencido, próxima a vencer, etc. A resposta já traz 'today', 'days_until' "
                    "(0=hoje, 1=amanhã), 'due_day', agrupamento 'by_due_date' e o total — você "
                    "agrega em cima disso. NÃO confundir com gastos fixos (débito automático) nem "
                    "com faturas de cartão."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_paid": {
                            "type": "boolean",
                            "description": "Se true, inclui também as contas já pagas. Default false (só pendentes).",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_get_bills_to_pay,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "mark_bill_paid",
                "description": (
                    "Marca uma CONTA A PAGAR (boleto) como paga → cria o lançamento de despesa "
                    "(debita e categoriza). Use quando o usuário disser que pagou uma conta a pagar "
                    "(ex: 'paguei a luz', 'quitei o boleto da internet'). Passe `name` (nome da "
                    "conta) ou `bill_id`. Se a conta tem valor variável (água/luz), passe também "
                    "`amount` com o valor real que veio — sem isso a tool pede o valor."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome da conta a pagar (ex: 'luz', 'água', 'internet')."},
                        "bill_id": {"type": "integer", "description": "ID da conta a pagar, se conhecido."},
                        "amount": {"type": "number", "description": "Valor real pago (obrigatório se a conta é de valor variável)."},
                    },
                },
            },
        },
        is_write=True,
        requires_confirmation=False,  # reversível (undo), igual add_launch
        execute=_pay_bill_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "add_boleto",
                "description": (
                    "Cadastra um BOLETO a pagar na agenda (avulso). Use quando o usuário disser "
                    "que recebeu/tem um boleto pra pagar com um vencimento específico — ex: 'boleto "
                    "de 500 da Distribuidora X vence 15/08', 'anota um boleto de 1200 do IPTU pra "
                    "dia 10', 'chegou uma conta de 350 pro dia 20'. NÃO é gasto avulso (add_launch) "
                    "nem recorrente: é uma conta pontual com data de vencimento futura."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Descrição/fornecedor (ex: 'Distribuidora XYZ', 'IPTU')."},
                        "amount": {"type": "number", "description": "Valor do boleto."},
                        "due_date": {"type": "string", "description": "Vencimento: 'AAAA-MM-DD' ou 'DD/MM' (ano inferido)."},
                        "days": {"type": "integer", "description": "Alternativa: vencimento em N dias a partir de hoje."},
                    },
                    "required": ["name", "amount"],
                },
            },
        },
        is_write=True,
        requires_confirmation=False,  # reversível (dá pra apagar); baixa fricção
        execute=_add_boleto_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "check_cashflow",
                "description": (
                    "Projeta o caixa até uma data pra responder 'tô tranquilo nesse prazo?' / 'como "
                    "tô de prazo no dia X?' / 'dá pra pegar esse boleto pra tal dia?'. Retorna saldo "
                    "atual, receitas previstas, gastos fixos e boletos até a data, e o valor PROJETADO "
                    "(sobra se >=0, falta se <0) + 'tranquilo' (bool). Use SEMPRE que o usuário quiser "
                    "saber se aguenta um prazo/compromisso — NÃO responda só listando os boletos. Passe "
                    "`amount` se ele estiver considerando pegar um boleto novo naquele prazo."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Data limite do prazo: 'AAAA-MM-DD' ou 'DD/MM'."},
                        "days": {"type": "integer", "description": "Alternativa: prazo em N dias a partir de hoje."},
                        "amount": {"type": "number", "description": "Opcional: valor de um boleto novo que ele está pensando em aceitar nesse prazo."},
                    },
                },
            },
        },
        is_write=False,
        execute=_check_cashflow,
    ),
]
