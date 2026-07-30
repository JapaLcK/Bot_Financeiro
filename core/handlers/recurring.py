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

# Marcadores de "conta a pagar / boleto" (payment_mode='manual'): a Piggy só
# LEMBRA e o débito só entra quando o usuário confirma o pagamento — diferente
# do gasto fixo (autopay), que debita sozinho no dia. Ver [[db/bills.py]].
_MANUAL_MARKERS = (
    "boleto", "boletos", "conta a pagar", "contas a pagar", "conta pra pagar",
    "conta para pagar", "me lembra", "me lembre", "me lembrar", "lembrete",
    "lembra de pagar", "lembrar de pagar", "quando vencer", "vou pagar",
    "preciso pagar", "tenho que pagar", "pagar manualmente", "aviso de conta",
)


def _norm(text: str) -> str:
    import unicodedata
    n = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in n if not unicodedata.combining(c))


def _is_manual_bill(text: str, entities: dict) -> bool:
    """Detecta se o recorrente é uma conta a pagar (manual) e não um gasto fixo
    (autopay). A IA pode marcar `pagamento='manual'`; o texto é a fonte da
    verdade (mais robusto que confiar só no LLM)."""
    pg = str(entities.get("pagamento") or entities.get("payment_mode") or "").strip().lower()
    if pg in ("manual", "boleto", "conta"):
        return True
    return any(mk in _norm(text) for mk in _MANUAL_MARKERS)


# Marcadores de valor VARIÁVEL (água/luz/gás): o valor cadastrado é estimativa.
_VARIABLE_MARKERS = (
    "valor varia", "valor variavel", "variavel", "varia todo mes", "varia por mes",
    "varia de mes", "mas varia", "que varia", "sempre varia", "varia sempre",
    "valor muda", "muda todo mes", "nao e fixo", "nunca e fixo",
    "sempre muda", "valor diferente",
)


def _is_variable_amount(text: str, entities: dict) -> bool:
    vv = str(entities.get("valor_variavel") or entities.get("variable_amount") or "").strip().lower()
    if vv in ("1", "true", "sim", "yes"):
        return True
    return any(mk in _norm(text) for mk in _VARIABLE_MARKERS)


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

    # conta a pagar de valor variável (água/luz) pode nascer SEM valor — nesse
    # caso não pedimos a estimativa (é opcional). Só receita/gasto fixo/conta
    # fixa exigem o valor.
    is_variable = (not is_income) and _is_variable_amount(text, entities)

    # valor
    try:
        valor = float(entities.get("valor") or 0)
    except (TypeError, ValueError):
        valor = 0.0
    if valor <= 0 and not is_variable:
        # Falta o valor (ex.: typo "00 reais"). Re-arma um pending de esclarecimento
        # PRA NÃO PERDER O CONTEXTO de recorrente — senão a próxima msg ("100") é
        # classificada do zero e vira despesa avulsa. A resposta cai no
        # _resolve_clarification, que reclassifica com contexto e volta pra cá.
        import db
        pergunta = "Qual o valor desse recorrente? Ex: *gasto fixo de 100 todo dia 10*"
        db.set_pending_action(user_id, "clarification", {
            "intent": "recurring.add",
            "entities": dict(entities),
            "question": pergunta,
            "orig_text": text,
        })
        return pergunta

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
    # is_variable já foi calculado no topo (antes do guard de valor). valor
    # variável implica conta a pagar (não dá pra debitar sozinho um valor que
    # muda todo mês) — força manual.
    is_manual = is_variable or _is_manual_bill(text, entities)
    name = nome or cat.capitalize() or ("Conta a pagar" if is_manual else "Gasto fixo")
    try:
        rec = create_recurring_expense(
            user_id, name, valor, cat, dia, "account",
            notes=text, start_date=start_date,
            frequency=frequency, due_month=mes,
            payment_mode="manual" if is_manual else "autopay",
            variable_amount=is_variable,
        )
    except ValueError as exc:
        return _err(str(exc))
    if is_manual:
        if is_variable:
            valor_txt = f"~{fmt_brl(valor)} (varia)" if valor > 0 else "valor a confirmar (varia)"
        else:
            valor_txt = fmt_brl(valor)
        return (
            f"🧾 *Conta a pagar criada:* {rec['name']}\n"
            f"💸 {valor_txt} · vence {_fmt_quando(frequency, dia, mes)}\n"
            f"📅 {_fmt_start(rec.get('start_date'))}\n"
            f"A Piggy vai *te lembrar* — nada é debitado até você confirmar. "
            f"Quando pagar, é só mandar *paguei {rec['name'].lower()}*. 🐷"
        )
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
