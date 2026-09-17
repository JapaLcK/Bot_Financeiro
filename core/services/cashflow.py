"""
core/services/cashflow.py — projeção simples de caixa pra decisão de prazo.

Responde a pergunta do dono da farmácia: "se eu aceitar pagar até o dia X
(prazo do representante), eu fico tranquilo ou aperta?"

projetado(D) = saldo_atual
             + receitas fixas previstas em (hoje, D]
             − gastos fixos automáticos (mensal/anual) em (hoje, D]
             − boletos pendentes com vencimento até D
             − (opcional) um boleto novo que ele está considerando

`tranquilo` = projetado, em centavos (o valor exibido), >= 0. É uma estimativa:
não conta gastos avulsos futuros nem receitas e gastos fixos
semanais/diários/únicos; a ideia é dar visão de fôlego, não fechamento contábil.
"""
from __future__ import annotations

import calendar
import math
from datetime import date
from typing import Any


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _recurring_occurrence_dates(day: Any, freq: str, month: Any, start: date | None,
                                after: date, until: date) -> list[date]:
    """Datas de ocorrência de um recorrente MENSAL/ANUAL em (after, until]."""
    if until <= after:
        return []
    try:
        day = int(day or 1)
    except (TypeError, ValueError):
        day = 1
    mnum = None
    if freq == "annual":
        try:
            mnum = int(month)
        except (TypeError, ValueError):
            return []
    dates: list[date] = []
    y, m = after.year, after.month
    while (y, m) <= (until.year, until.month):
        if not (freq == "annual" and mnum and m != mnum):
            dim = calendar.monthrange(y, m)[1]
            d = date(y, m, min(day, dim))
            if after < d <= until and (start is None or d >= start):
                dates.append(d)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return dates


def _open_card_bills_detail(user_id: int, until: date) -> list[dict]:
    """Faturas de cartão com saldo a pagar (total − pago) e vencimento até
    `until` — compromissos que o saldo em conta ainda não reflete (dívida de
    cartão não sai do saldo). Inclui 'open' (fatura corrente) e 'closed' com saldo
    (atrasada, ainda a pagar), mesmo critério de `list_bills_with_debt`: o
    atrasado também sai do caixa antes do alvo, então conta como saída. Cada item:
    `{"due_date", "remaining", "card_name"}`."""
    from db.connection import get_conn
    from db.cards import card_bill_due_date

    items: list[dict] = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select (b.total - coalesce(b.paid_amount, 0)) as remaining,
                       b.period_end, c.closing_day, c.due_day, c.name as card_name
                from credit_bills b
                join credit_cards c on c.id = b.card_id and c.user_id = b.user_id
                where b.user_id = %s and b.status in ('open', 'closed')
                  and b.total > coalesce(b.paid_amount, 0)
                """,
                (user_id,),
            )
            for row in cur.fetchall() or []:
                pe = _as_date(row["period_end"])
                if pe is None:
                    continue
                due = card_bill_due_date(pe, int(row["closing_day"] or 1), int(row["due_day"] or 1))
                if due <= until:
                    items.append({
                        "due_date": due,
                        "remaining": float(row["remaining"] or 0),
                        "card_name": row["card_name"],
                    })
    return items


def _cashflow_events(user_id: int, today: date, until: date) -> list[tuple[date, str, str, float]]:
    """Fonte ÚNICA dos compromissos da projeção, `(data, tipo, nome, valor_com_sinal)`,
    consumida por `project` (soma até a data) e `forecast_with_trajectory` (por dia).
    Todo filtro mora aqui — filtro fora deste gerador é uma segunda versão da regra.

    - receita fixa ativa, mensal/anual, valor > 0: ocorrências em (today, until], +valor;
    - gasto fixo ativo, autopay, mensal/anual, valor > 0: idem, −valor;
    - boleto pendente com vencimento até `until` (vencidos inclusive), −valor
      com QUALQUER valor, até 0 ou negativo — regra herdada de `project`;
    - fatura de cartão com saldo e vencimento até `until` (vencidas inclusive), −saldo.
    """
    from db.recurring import list_recurring_expenses
    from db.recurring_income import list_recurring_incomes
    from db.bills import list_bills

    events: list[tuple[date, str, str, float]] = []
    for inc in list_recurring_incomes(user_id):
        if not inc.get("is_active"):
            continue
        if (inc.get("frequency") or "monthly") not in ("monthly", "annual"):
            # O cobrador de receitas só lança mensal e anual: contar once/weekly/daily
            # (registros legados) como mensal inflava a previsão.
            continue
        amount = float(inc.get("amount") or 0)
        if amount <= 0:
            continue
        nome = inc.get("name") or "Receita"
        for d in _recurring_occurrence_dates(
            inc.get("pay_day"), inc.get("frequency") or "monthly", inc.get("pay_month"),
            _as_date(inc.get("start_date")), today, until,
        ):
            events.append((d, "receita", nome, amount))

    for e in list_recurring_expenses(user_id):
        if not e.get("is_active"):
            continue
        if (e.get("payment_mode") or "autopay") != "autopay":
            continue  # 'manual' = boleto; já entra nos boletos pendentes
        if (e.get("frequency") or "monthly") not in ("monthly", "annual"):
            continue  # weekly/daily/once ficam de fora do v1 da projeção
        amount = float(e.get("amount") or 0)
        if amount <= 0:
            continue
        nome = e.get("name") or "Gasto fixo"
        for d in _recurring_occurrence_dates(
            e.get("due_day"), e.get("frequency") or "monthly", e.get("due_month"),
            _as_date(e.get("start_date")), today, until,
        ):
            events.append((d, "gasto_fixo", nome, -amount))

    for b in list_bills(user_id, include_paid=False, limit=1000):
        if b.get("status") != "pending":
            continue
        d = _as_date(b.get("due_date"))
        if d and d <= until:
            events.append((d, "boleto", b.get("name") or "Boleto", -float(b.get("amount") or 0)))

    for fatura in _open_card_bills_detail(user_id, until):
        events.append((fatura["due_date"], "fatura_cartao", fatura["card_name"] or "Cartão",
                       -fatura["remaining"]))
    return events


def _starting_balance(user_id: int) -> dict[str, Any]:
    """Saldo de partida da projeção: consolidado (carteira + bancos autorizados no
    Open Finance) quando o usuário tem banco conectado e o consolidado está
    liberado — senão a projeção de quem tem OF partiria só da carteira manual e
    ficaria errada. Mesmo critério do dashboard/relatórios (get_consolidated_balance
    + gate beta). Devolve `{"saldo", "balance_source", "of_bank_count",
    "banks_excluded"}`."""
    from db.accounts import get_balance

    saldo = float(get_balance(user_id))
    balance_source = "manual"  # carteira manual; vira "consolidated" se somar OF
    of_bank_count = 0
    try:
        from db import get_consolidated_balance
        from core.services.plan_service import consolidated_balance_enabled
        cb = get_consolidated_balance(user_id)
        of_bank_count = int(cb.get("of_bank_count") or 0)
        if of_bank_count > 0 and consolidated_balance_enabled(user_id):
            saldo = float(cb.get("consolidated") or 0)
            balance_source = "consolidated"
        else:
            # Gate desligado congela a ORIGEM (carteira), não autoriza projetar
            # a partir do cru: `cb["manual"]` é a mesma Carteira que a tela
            # mostra, com o gasto fundido devolvido.
            saldo = float(cb.get("manual") or 0)
    except Exception:
        # Falha ao consultar o consolidado (OF/gate indisponível): NÃO dá pra
        # afirmar que a carteira manual é o saldo completo — o usuário pode ter
        # bancos no Open Finance que não conseguimos ler agora. Marca a origem
        # como indisponível pra a previsão não devolver um número aparentemente
        # confiável sem aviso; o dashboard sinaliza a incerteza. Ver banks_excluded.
        balance_source = "unavailable"

    # Bancos conectados que NÃO entraram no saldo de partida (gate consolidado
    # desligado): a projeção parte só da carteira e subestima o caixa → o
    # dashboard mostra um aviso quando isso acontece. Quando o consolidado falha,
    # balance_source == "unavailable" cobre o aviso (não sabemos of_bank_count).
    banks_excluded = of_bank_count > 0 and balance_source == "manual"

    return {
        "saldo": saldo,
        "balance_source": balance_source,
        "of_bank_count": of_bank_count,
        "banks_excluded": banks_excluded,
    }


def _projection(today: date, sb: dict[str, Any], events: list[tuple[date, str, str, float]],
                target_date: date, extra_amount: float = 0.0) -> dict[str, Any]:
    """Projeção até `target_date` sobre saldo e eventos já lidos. Soma só os eventos
    com data até o alvo, então aceita os eventos de um horizonte maior."""
    saldo = sb["saldo"]
    balance_source = sb["balance_source"]
    of_bank_count = sb["of_bank_count"]
    banks_excluded = sb["banks_excluded"]

    valores: dict[str, list[float]] = {"receita": [], "gasto_fixo": [], "boleto": [], "fatura_cartao": []}
    for d, tipo, _nome, valor in events:
        if d <= target_date:
            valores[tipo].append(valor)

    # Soma exata (`math.fsum`), arredondada só na saída: não depende da ordem nem do
    # agrupamento, e é o que faz `forecast_with_trajectory` (que soma os mesmos eventos por
    # dia) bater no centavo com os horizontes mesmo com fração de centavo do banco.
    receitas = math.fsum(valores["receita"])
    gastos_fixos = math.fsum(-v for v in valores["gasto_fixo"])
    boletos = math.fsum(-v for v in valores["boleto"])
    n_boletos = len(valores["boleto"])
    faturas_cartao = math.fsum(-v for v in valores["fatura_cartao"])

    extra = float(extra_amount or 0)
    # `tranquilo` decide pelo valor em centavos, o mesmo que a resposta mostra: R$ 0,30 −
    # 0,10 − 0,20 soma −2,8e-17 em float e é R$ 0,00. `+ 0.0` troca o −0,0 do
    # arredondamento por 0,0, que é o que vai no JSON (tool de IA, dashboard).
    projetado = round(math.fsum([saldo, *(v for vs in valores.values() for v in vs), -extra]), 2) + 0.0
    return {
        "today": today.isoformat(),
        "target": target_date.isoformat(),
        "saldo_atual": round(saldo, 2),
        "balance_source": balance_source,
        "of_bank_count": of_bank_count,
        "banks_excluded": banks_excluded,
        "receitas_previstas": round(receitas, 2),
        "gastos_fixos_previstos": round(gastos_fixos, 2),
        "boletos_ate": round(boletos, 2),
        "n_boletos": n_boletos,
        "faturas_cartao": round(faturas_cartao, 2),
        "boleto_novo": round(extra, 2),
        "projetado": projetado,
        "tranquilo": projetado >= 0,
    }


def project(user_id: int, target_date: date, extra_amount: float = 0.0) -> dict[str, Any]:
    """Projeção de caixa até `target_date`, opcionalmente considerando um boleto
    novo de `extra_amount`. Ver docstring do módulo."""
    today = date.today()
    sb = _starting_balance(user_id)
    return _projection(today, sb, _cashflow_events(user_id, today, target_date), target_date, extra_amount)


__all__ = ["project"]
