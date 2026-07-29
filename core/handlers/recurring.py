# core/handlers/recurring.py
"""
Cria gastos e receitas RECORRENTES (gastos fixos / rendas fixas) a partir de
linguagem natural no bot — ex: "gasto fixo de 100 todo dia 10 a partir de 10/09",
"salário de 3000 todo dia 5".

Pro-only (mesma feature `recurring_expenses` do dashboard). Não tira/credita nada
na hora — só cadastra; o charger (core/services/recurring_charger.py) lança no dia.
Ver [[project_recurring_start_date]] pra semântica de start_date.
"""
from __future__ import annotations

from datetime import date

from utils_date import extract_date_from_text
from utils_text import fmt_brl

_INCOME_WORDS = ("receita", "recebimento", "entrada", "renda", "salario", "salário")


def add(user_id: int, text: str, entities: dict) -> str:
    """Cadastra um recorrente a partir das entities classificadas pela IA."""
    # Gate Pro — recorrentes é feature do PigBank+ (igual ao dashboard).
    try:
        from core.services.plan_service import is_pro
        if not is_pro(user_id):
            return ("📅 Gastos e receitas fixas são do *PigBank+*. Assine pra Piggy "
                    "lançar tudo sozinha todo mês, no dia certo. 🐷")
    except Exception:
        pass

    tipo = (entities.get("tipo") or "despesa").strip().lower()
    is_income = tipo in _INCOME_WORDS

    # valor
    try:
        valor = float(entities.get("valor") or 0)
    except (TypeError, ValueError):
        valor = 0.0
    if valor <= 0:
        return "Qual o valor desse recorrente? Ex: *gasto fixo de 100 todo dia 10*"

    # dia do mês (vencimento/recebimento). Se o usuário não disser ("todo mês"
    # sem dia), assume o dia de HOJE — cria mesmo assim (anti-fricção); dá pra
    # editar no dashboard. Melhor que travar pedindo o dia.
    dia_raw = entities.get("dia") or entities.get("due_day") or entities.get("pay_day")
    try:
        dia = int(dia_raw)
    except (TypeError, ValueError):
        dia = 0
    if not (1 <= dia <= 31):
        dia = date.today().day

    # data de início — "a partir de 10/09". Tenta extrair do hint da IA, senão do
    # texto todo; fallback None = default do banco (hoje). extract_date_from_text
    # resolve o ano relativo a HOJE (a IA não sabe a data atual).
    inicio_hint = str(entities.get("inicio") or entities.get("start_date") or "").strip()
    start_date: date | None = None
    dt, _ = extract_date_from_text(inicio_hint or text)
    if dt:
        start_date = dt.date()
    elif inicio_hint:
        try:
            start_date = date.fromisoformat(inicio_hint[:10])
        except ValueError:
            start_date = None

    categoria = (entities.get("categoria") or entities.get("category") or "").strip()
    nome = (entities.get("nome") or entities.get("name") or entities.get("alvo") or "").strip()
    if nome:  # 1ª letra maiúscula pra ficar bonito no dashboard ("aluguel" → "Aluguel")
        nome = nome[0].upper() + nome[1:]

    # frequência: mensal (default) ou anual. Se anual sem mês, usa o mês do
    # start_date (ou de hoje) como o mês do vencimento.
    freq_raw = str(entities.get("frequencia") or entities.get("frequency") or "mensal").strip().lower()
    frequency = "annual" if freq_raw in ("anual", "annual", "ano", "yearly") else "monthly"
    mes = entities.get("mes") or entities.get("month") or entities.get("due_month") or entities.get("pay_month")
    try:
        mes = int(mes) if mes is not None else None
    except (TypeError, ValueError):
        mes = None
    if frequency == "annual" and not (mes and 1 <= mes <= 12):
        mes = (start_date.month if start_date else date.today().month)

    if is_income:
        from db.recurring_income import create_recurring_income
        cat = categoria or "salário"
        name = nome or cat.capitalize() or "Renda fixa"
        try:
            rec = create_recurring_income(
                user_id, name, valor, cat, dia, notes=text, start_date=start_date,
                frequency=frequency, pay_month=mes,
            )
        except ValueError as exc:
            return _err(str(exc))
        return (
            f"✅ *Receita fixa criada:* {rec['name']}\n"
            f"💰 {fmt_brl(valor)} · {_fmt_quando(frequency, dia, mes)}\n"
            f"📅 {_fmt_start(rec.get('start_date'))}\n"
            f"É só o cadastro — a Piggy credita sozinha no dia. Edite na aba *Recorrentes* do dashboard."
        )

    from db.recurring import create_recurring_expense
    # Se a IA não classificou a categoria, infere pelo nome OU pelo texto todo
    # (aluguel→moradia, agua→moradia, netflix→assinaturas) — mesmo motor dos
    # lançamentos. Usar o texto cobre quando a IA não extraiu o nome.
    if not categoria:
        from utils_text import guess_category
        guessed = guess_category(nome or text)
        if guessed and guessed != "outros":
            categoria = guessed
    cat = categoria or "outros"
    name = nome or cat.capitalize() or "Gasto fixo"
    try:
        rec = create_recurring_expense(
            user_id, name, valor, cat, dia, "account",
            notes=text, start_date=start_date,
            frequency=frequency, due_month=mes,
        )
    except ValueError as exc:
        return _err(str(exc))
    return (
        f"✅ *Gasto fixo criado:* {rec['name']}\n"
        f"💸 {fmt_brl(valor)} · débito na conta {_fmt_quando(frequency, dia, mes)}\n"
        f"📅 {_fmt_start(rec.get('start_date'))}\n"
        f"Não tira nada agora — só no dia. Edite na aba *Recorrentes* do dashboard."
    )


_MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
             "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def _fmt_quando(frequency: str, dia: int, mes: int | None) -> str:
    if frequency == "annual" and mes and 1 <= mes <= 12:
        return f"todo ano no dia {dia} de {_MESES_PT[mes - 1]}"
    return f"todo dia {dia}"


def _fmt_start(start_iso) -> str:
    if not start_iso:
        return "começa neste mês"
    try:
        d = date.fromisoformat(str(start_iso)[:10])
        return f"a partir de {d.strftime('%d/%m/%Y')}"
    except (ValueError, TypeError):
        return f"a partir de {start_iso}"


def _err(code: str) -> str:
    msgs = {
        "VALOR_INVALIDO": "O valor precisa ser maior que zero.",
        "DIA_INVALIDO": "O dia precisa estar entre 1 e 31.",
        "NOME_INVALIDO": "Preciso saber do que é o recorrente.",
        "DATA_INICIO_INVALIDA": "Não entendi a data de início.",
    }
    return "⚠️ " + msgs.get(code, f"Não consegui criar o recorrente ({code}).")
