"""
db/recurring.py — Gastos Fixos / Recorrentes (Sprint 4).

Pro-only. Cobrança automática no dia `due_day` de cada mês via cron.
- `payment_type='account'`     → cria launch despesa (não interno).
- `payment_type='credit_card'` → cria credit_transaction na bill open atual.

Idempotência: `last_charged_ym` impede cobrar 2x no mesmo mês.
Reajuste: ao editar `amount`, guarda `last_amount` + timestamp pra UI mostrar a variação.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .categories import ensure_user_category, resolve_category_input
from .connection import get_conn
from .users import ensure_user


def _parse_start_date(value: Any, default: date | None = None) -> date | None:
    """Aceita date, 'YYYY-MM-DD' ou None. Vazio/None → default. Inválido → erro."""
    if value is None or value == "":
        return default
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError("DATA_INICIO_INVALIDA")


# Frequências suportadas. 'once' = pagamento único (não recorre); 'weekly'/
# 'daily' ancoram no start_date (a cada 7 dias / todo dia). Só 'annual' usa o mês.
VALID_FREQUENCIES = ("once", "daily", "weekly", "monthly", "annual")


def validate_frequency(frequency: Any, month: Any) -> tuple[str, int | None]:
    """Valida frequency (once|daily|weekly|monthly|annual) + o mês. Anual exige
    mês 1-12; as demais ignoram o mês (retorna None). Levanta ValueError se inválido."""
    freq = (str(frequency) if frequency is not None else "monthly").strip().lower() or "monthly"
    if freq not in VALID_FREQUENCIES:
        raise ValueError("FREQUENCIA_INVALIDA")
    if freq == "annual":
        try:
            m = int(month)
        except (TypeError, ValueError):
            raise ValueError("MES_INVALIDO")
        if not (1 <= m <= 12):
            raise ValueError("MES_INVALIDO")
        return "annual", m
    return freq, None


def list_recurring_expenses(user_id: int, include_inactive: bool = False) -> list[dict[str, Any]]:
    """Lista todos os gastos fixos do user."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.name, r.amount, r.category, r.due_day,
                       r.payment_type, r.card_id, c.name as card_name,
                       r.is_essential, r.is_active,
                       r.last_amount, r.last_amount_changed_at,
                       r.last_charged_ym, r.notes, r.created_at, r.start_date,
                       r.frequency, r.due_month, r.payment_mode, r.variable_amount
                from recurring_expenses r
                left join credit_cards c on c.id = r.card_id
                where r.user_id = %s
                  and (%s::boolean = true or r.is_active = true)
                order by r.is_essential desc, r.due_day asc, lower(r.name) asc
                """,
                (user_id, include_inactive),
            )
            rows = cur.fetchall() or []
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r["id"],
            "name": r["name"],
            "amount": float(r["amount"]),
            "category": r["category"],
            "due_day": int(r["due_day"]),
            "payment_type": r["payment_type"],
            "card_id": r["card_id"],
            "card_name": r["card_name"],
            "is_essential": bool(r["is_essential"]),
            "is_active": bool(r["is_active"]),
            "last_amount": float(r["last_amount"]) if r["last_amount"] is not None else None,
            "last_amount_changed_at": r["last_amount_changed_at"].isoformat() if r["last_amount_changed_at"] else None,
            "last_charged_ym": r["last_charged_ym"],
            "notes": r["notes"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "start_date": r["start_date"].isoformat() if r["start_date"] else None,
            "frequency": r["frequency"] or "monthly",
            "due_month": int(r["due_month"]) if r["due_month"] is not None else None,
            "payment_mode": r["payment_mode"] or "autopay",
            "variable_amount": bool(r["variable_amount"]),
        })
    return out


def get_recurring_expense(user_id: int, rec_id: int) -> dict[str, Any] | None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.name, r.amount, r.category, r.due_day,
                       r.payment_type, r.card_id, c.name as card_name,
                       r.is_essential, r.is_active,
                       r.last_amount, r.last_amount_changed_at,
                       r.last_charged_ym, r.notes, r.created_at, r.start_date,
                       r.frequency, r.due_month, r.payment_mode, r.variable_amount
                from recurring_expenses r
                left join credit_cards c on c.id = r.card_id
                where r.user_id = %s and r.id = %s
                """,
                (user_id, int(rec_id)),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {
                "id": r["id"], "name": r["name"], "amount": float(r["amount"]),
                "category": r["category"], "due_day": int(r["due_day"]),
                "payment_type": r["payment_type"], "card_id": r["card_id"],
                "card_name": r["card_name"],
                "is_essential": bool(r["is_essential"]), "is_active": bool(r["is_active"]),
                "last_amount": float(r["last_amount"]) if r["last_amount"] is not None else None,
                "last_amount_changed_at": r["last_amount_changed_at"].isoformat() if r["last_amount_changed_at"] else None,
                "last_charged_ym": r["last_charged_ym"],
                "notes": r["notes"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "start_date": r["start_date"].isoformat() if r["start_date"] else None,
                "frequency": r["frequency"] or "monthly",
                "due_month": int(r["due_month"]) if r["due_month"] is not None else None,
                "payment_mode": r["payment_mode"] or "autopay",
                "variable_amount": bool(r["variable_amount"]),
            }


def count_active_recurring_expenses(user_id: int) -> int:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from recurring_expenses where user_id=%s and is_active=true",
                (user_id,),
            )
            return int(cur.fetchone()["n"] or 0)


def create_recurring_expense(
    user_id: int,
    name: str,
    amount: float,
    category: str,
    due_day: int,
    payment_type: str,
    card_id: int | None = None,
    is_essential: bool = False,
    notes: str | None = None,
    start_date: date | str | None = None,
    frequency: str = "monthly",
    due_month: int | None = None,
    payment_mode: str = "autopay",
    variable_amount: bool = False,
) -> dict[str, Any]:
    """Cria gasto fixo. Levanta ValueError se input inválido.

    `start_date` = a partir de quando a recorrência vale (default: hoje). A
    primeira cobrança é a primeira ocorrência de `due_day` em/depois dessa data.
    `frequency` = 'monthly' (todo mês no due_day) ou 'annual' (1x/ano no
    `due_month`/due_day).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("NOME_INVALIDO")
    # Conta a pagar de valor variável (água/luz) pode não ter estimativa — guarda 0.
    # Nos demais casos o valor é obrigatório (> 0).
    if variable_amount and (amount is None or float(amount) <= 0):
        amount = 0
    elif amount is None or float(amount) <= 0:
        raise ValueError("VALOR_INVALIDO")
    freq, month = validate_frequency(frequency, due_month)
    mode = (payment_mode or "autopay").strip().lower()
    if mode not in ("autopay", "manual"):
        raise ValueError("MODO_PAGAMENTO_INVALIDO")
    start = _parse_start_date(start_date, default=date.today())
    # due_day só é obrigatório (1-31) pra mensal/anual. Em única/semanal/diária o
    # vencimento é ancorado no start_date, então derivamos due_day dele (satisfaz
    # a check 1-31 do banco e serve de referência).
    if freq in ("monthly", "annual"):
        try:
            due_day = int(due_day)
        except (TypeError, ValueError):
            raise ValueError("DIA_INVALIDO")
        if due_day < 1 or due_day > 31:
            raise ValueError("DIA_INVALIDO")
    else:
        due_day = (start or date.today()).day
    if payment_type not in ("account", "credit_card"):
        raise ValueError("FORMA_PAGAMENTO_INVALIDA")
    if payment_type == "credit_card" and not card_id:
        raise ValueError("CARTAO_OBRIGATORIO")
    if payment_type == "account":
        card_id = None  # ignora card_id quando não é cartão

    # #147: mesma porta de correção do PATCH /launches — o usuário DIGITA esta
    # categoria. Sem o resolver, "McDonald's" nascia cru aqui e o cobrador o
    # copiava pra `launches.categoria` todo mês, abrindo fatia gêmea no donut.
    # `or cat` mantém o texto quando o resolver recusa (nome longo demais).
    cat = (category or "").strip() or "outros"
    cat = resolve_category_input(user_id, cat, create=True) or cat
    note = (notes or "").strip() or None

    with get_conn() as conn:
        with conn.cursor() as cur:
            if card_id:
                cur.execute(
                    "select id from credit_cards where id=%s and user_id=%s",
                    (card_id, user_id),
                )
                if not cur.fetchone():
                    raise ValueError("CARTAO_NAO_ENCONTRADO")

            cur.execute(
                """
                insert into recurring_expenses (
                    user_id, name, amount, category, due_day, payment_type,
                    card_id, is_essential, is_active, notes, start_date,
                    frequency, due_month, payment_mode, variable_amount
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    user_id, name, Decimal(str(amount)), cat, int(due_day),
                    payment_type, card_id, bool(is_essential), note, start,
                    freq, month, mode, bool(variable_amount),
                ),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    ensure_user_category(user_id, cat)  # só DEPOIS do insert (não deixa categoria órfã)
    return get_recurring_expense(user_id, new_id)


def update_recurring_expense(
    user_id: int,
    rec_id: int,
    *,
    name: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    due_day: int | None = None,
    payment_type: str | None = None,
    card_id: int | None = None,
    is_essential: bool | None = None,
    is_active: bool | None = None,
    notes: str | None = None,
    start_date: date | str | None = None,
    frequency: str | None = None,
    due_month: int | None = None,
    payment_mode: str | None = None,
    variable_amount: bool | None = None,
) -> dict[str, Any]:
    """PATCH. Quando amount muda, registra `last_amount` + timestamp (detector de reajuste)."""
    ensure_user(user_id)
    current = get_recurring_expense(user_id, rec_id)
    if not current:
        raise ValueError("RECORRENTE_NAO_ENCONTRADO")

    sets: list[str] = []
    params: list[Any] = []

    if name is not None:
        v = (name or "").strip()
        if not v:
            raise ValueError("NOME_INVALIDO")
        sets.append("name = %s")
        params.append(v)
    if amount is not None:
        if float(amount) <= 0:
            raise ValueError("VALOR_INVALIDO")
        if abs(float(amount) - float(current["amount"])) > 0.005:
            # Reajuste detectado: guarda valor anterior + timestamp
            sets.append("last_amount = %s")
            params.append(Decimal(str(current["amount"])))
            sets.append("last_amount_changed_at = now()")
        sets.append("amount = %s")
        params.append(Decimal(str(amount)))
    cat: str | None = None
    if category is not None:
        cat = (category or "").strip() or "outros"
        cat = resolve_category_input(user_id, cat, create=True) or cat
        sets.append("category = %s")
        params.append(cat)
    if due_day is not None:
        if int(due_day) < 1 or int(due_day) > 31:
            raise ValueError("DIA_INVALIDO")
        sets.append("due_day = %s")
        params.append(int(due_day))
    if payment_type is not None:
        if payment_type not in ("account", "credit_card"):
            raise ValueError("FORMA_PAGAMENTO_INVALIDA")
        sets.append("payment_type = %s")
        params.append(payment_type)
        if payment_type == "account":
            sets.append("card_id = NULL")
    if card_id is not None and (payment_type or current["payment_type"]) == "credit_card":
        sets.append("card_id = %s")
        params.append(int(card_id))
    if is_essential is not None:
        sets.append("is_essential = %s")
        params.append(bool(is_essential))
    if is_active is not None:
        sets.append("is_active = %s")
        params.append(bool(is_active))
    if notes is not None:
        sets.append("notes = %s")
        params.append((notes or "").strip() or None)
    if start_date is not None:
        sets.append("start_date = %s")
        params.append(_parse_start_date(start_date, default=date.today()))
    if frequency is not None:
        # valida frequency + mês juntos; se mudar pra anual, precisa do mês
        # (usa o due_month enviado ou o atual do registro).
        month_src = due_month if due_month is not None else current.get("due_month")
        freq, month = validate_frequency(frequency, month_src)
        sets.append("frequency = %s")
        params.append(freq)
        sets.append("due_month = %s")
        params.append(month)
    elif due_month is not None and (current.get("frequency") == "annual"):
        # só mudou o mês de um anual já existente
        _f, month = validate_frequency("annual", due_month)
        sets.append("due_month = %s")
        params.append(month)
    if payment_mode is not None:
        mode = (payment_mode or "autopay").strip().lower()
        if mode not in ("autopay", "manual"):
            raise ValueError("MODO_PAGAMENTO_INVALIDO")
        sets.append("payment_mode = %s")
        params.append(mode)
    if variable_amount is not None:
        sets.append("variable_amount = %s")
        params.append(bool(variable_amount))

    if not sets:
        return current

    params.append(user_id)
    params.append(int(rec_id))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"update recurring_expenses set {', '.join(sets)} where user_id=%s and id=%s",
                params,
            )
        conn.commit()
    if cat:
        ensure_user_category(user_id, cat)  # só DEPOIS do update
    return get_recurring_expense(user_id, rec_id)


def delete_recurring_expense(user_id: int, rec_id: int) -> None:
    ensure_user(user_id)
    current = get_recurring_expense(user_id, rec_id)
    if not current:
        raise ValueError("RECORRENTE_NAO_ENCONTRADO")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from recurring_expenses where user_id=%s and id=%s",
                (user_id, int(rec_id)),
            )
        conn.commit()


def list_due_recurring_expenses(today: date | None = None) -> list[dict[str, Any]]:
    """Lista globais — todos os user — gastos fixos que VENCEM hoje e ainda
    não foram cobrados neste mês (`last_charged_ym != current_ym`).

    Usado pelo cron diário pra processar cobranças automáticas.
    """
    today = today or date.today()
    ym = today.strftime("%Y-%m")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.id, r.user_id, r.name, r.amount, r.category,
                       r.due_day, r.payment_type, r.card_id
                from recurring_expenses r
                where r.is_active = true
                  and r.due_day <= %s
                  and (r.last_charged_ym is null or r.last_charged_ym != %s)
                  -- Não retroagir: recorrência começa em start_date (ver charger).
                  and (
                      to_char(coalesce(r.start_date, r.created_at::date), 'YYYY-MM') < %s
                      or (
                          to_char(coalesce(r.start_date, r.created_at::date), 'YYYY-MM') = %s
                          and r.due_day >= extract(day from coalesce(r.start_date, r.created_at::date))
                      )
                  )
                """,
                (today.day, ym, ym, ym),
            )
            rows = cur.fetchall() or []
    return [dict(r) for r in rows]


def mark_recurring_charged(user_id: int, rec_id: int, ym: str) -> None:
    """Marca como cobrado neste mês (idempotência)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update recurring_expenses set last_charged_ym=%s "
                "where user_id=%s and id=%s",
                (ym, user_id, int(rec_id)),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Detecção "essa despesa se repete → sugere virar gasto fixo"
# ---------------------------------------------------------------------------

def _merchant_key(text: str) -> str:
    # import tardio: utils_text não depende de db, mas evita qualquer ordem de
    # import na carga do módulo.
    from utils_text import merchant_key
    return merchant_key(text or "")


def find_recurring_candidate(
    user_id: int,
    key: str,
    amount: float,
    *,
    current_year: int,
    current_month: int,
    exclude_launch_id: int | None = None,
) -> int:
    """Quantas ocorrências ANTERIORES desta despesa existem em meses distintos
    do atual (mesma descrição normalizada `key` + MESMO valor exato).

    Retorna o nº de meses-calendário anteriores (≠ mês atual) em que a combinação
    apareceu. `>= 1` ⇒ candidata a gasto fixo (repetiu em pelo menos 2 meses).
    Retorna 0 (não sugerir) se:
      • a combinação (merchant+valor) já foi recusada pelo usuário;
      • já existe gasto fixo ATIVO com o mesmo valor e nome equivalente;
      • não há repetição em mês distinto.

    O casamento de descrição é feito em Python (via `merchant_key`) porque a
    normalização remove acento/pontuação/TLD — o que o LOWER(TRIM) do SQL não faz.
    O filtro de valor exato roda no SQL e deixa a varredura barata.
    """
    key = (key or "").strip()
    if not key:
        return 0
    amt = Decimal(str(amount))
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) já recusado antes? não re-perguntar
            cur.execute(
                "select 1 from recurring_suggestion_dismissed "
                "where user_id=%s and merchant_key=%s and amount=%s",
                (user_id, key, amt),
            )
            if cur.fetchone():
                return 0
            # 2) já é gasto fixo ativo (mesmo valor + nome equivalente)?
            cur.execute(
                "select name from recurring_expenses "
                "where user_id=%s and is_active=true and amount=%s",
                (user_id, amt),
            )
            for r in cur.fetchall() or []:
                if _merchant_key(r["name"]) == key:
                    return 0
            # 3) despesas passadas com o MESMO valor exato (casa descrição depois)
            cur.execute(
                """
                select criado_em,
                       coalesce(nullif(alvo,''), nullif(nota,'')) as descr
                from launches
                where user_id=%s and tipo='despesa'
                  and is_internal_movement=false
                  and valor=%s
                  and (%s::bigint is null or id <> %s)
                """,
                (user_id, amt, exclude_launch_id, exclude_launch_id),
            )
            rows = cur.fetchall() or []
    months: set[tuple[int, int]] = set()
    for r in rows:
        if _merchant_key(r["descr"]) != key:
            continue
        dt = r["criado_em"]
        if dt is None:
            continue
        ym = (dt.year, dt.month)
        if ym != (current_year, current_month):
            months.add(ym)
    return len(months)


def dismiss_recurring_suggestion(user_id: int, key: str, amount: float) -> None:
    """Registra que o usuário recusou virar gasto fixo essa combinação
    (merchant + valor), pra não re-sugerir. Idempotente."""
    key = (key or "").strip()
    if not key:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into recurring_suggestion_dismissed (user_id, merchant_key, amount) "
                "values (%s, %s, %s) on conflict do nothing",
                (user_id, key, Decimal(str(amount))),
            )
        conn.commit()


__all__ = [
    "list_recurring_expenses",
    "get_recurring_expense",
    "count_active_recurring_expenses",
    "create_recurring_expense",
    "update_recurring_expense",
    "delete_recurring_expense",
    "list_due_recurring_expenses",
    "mark_recurring_charged",
    "find_recurring_candidate",
    "dismiss_recurring_suggestion",
]
