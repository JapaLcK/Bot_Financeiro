"""
db/accounts.py — Saldo, lançamentos e importação OFX.
"""
import json
from datetime import datetime, date, timedelta
from decimal import Decimal

from psycopg.types.json import Json, Jsonb

import db_support as _db_support
from utils_date import _tz

from .connection import get_conn
from .users import ensure_user, ensure_user_tx


# ──────────────────────────────────────────────────────────────────────────────
# Saldo
# ──────────────────────────────────────────────────────────────────────────────

def get_balance(user_id: int) -> Decimal:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s", (user_id,))
            row = cur.fetchone()
            return row["balance"] if row else Decimal("0")


def set_balance(user_id: int, new_balance: Decimal) -> Decimal:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET balance=%s WHERE user_id=%s RETURNING balance",
                (new_balance, user_id),
            )
            bal = cur.fetchone()["balance"]
        conn.commit()
    return bal


# ──────────────────────────────────────────────────────────────────────────────
# Lançamentos
# ──────────────────────────────────────────────────────────────────────────────

def add_launch_and_update_balance(
    user_id: int,
    tipo: str,
    valor: float,
    alvo: str | None,
    nota: str | None,
    categoria: str | None = None,
    criado_em: datetime | None = None,
    is_internal_movement: bool = False,
    extra_efeitos: dict | None = None,
):
    """
    Lança em launches e atualiza saldo em accounts na mesma transação.
    Regra: despesa → saldo -= valor; receita → saldo += valor.

    `extra_efeitos` é mesclado dentro de `efeitos` jsonb. Use pra que
    `delete_launch_and_rollback` consiga reverter side-effects além do
    saldo (ex: `bill_id` pra pagamento de fatura).
    """
    ensure_user(user_id)

    v = Decimal(str(valor))
    if tipo == "despesa":
        delta = -v
    elif tipo == "receita":
        delta = +v
    else:
        raise ValueError(f"tipo inválido: {tipo}")

    if criado_em is None:
        criado_em = datetime.now(_tz())

    cat = (categoria or "").strip() or "outros"

    efeitos = {"delta_conta": float(delta)}
    if extra_efeitos:
        efeitos.update(extra_efeitos)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (delta, user_id),
            )
            new_bal = cur.fetchone()["balance"]

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, categoria, criado_em, efeitos, is_internal_movement)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id, user_seq
                """,
                (user_id, tipo, v, alvo, nota, cat, criado_em,
                 Json(efeitos), is_internal_movement),
            )
            row = cur.fetchone()
            launch_id = row["id"]
            user_seq = row["user_seq"]

        conn.commit()

    return launch_id, user_seq, new_bal


def list_launches(user_id: int, limit: int = 10):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, user_seq, tipo, valor, alvo, nota, categoria, source, criado_em
                from launches
                where user_id=%s
                order by criado_em desc, id desc
                limit %s
                """,
                (user_id, limit),
            )
            return cur.fetchall()


def resolve_user_seq_to_id(user_id: int, user_seq: int) -> int | None:
    """Converte o `#N` que o usuário digita (user_seq) no id interno do lançamento.

    Retorna None se não houver lançamento com esse user_seq pra esse usuário.
    """
    if not user_seq or user_seq <= 0:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from launches where user_id=%s and user_seq=%s",
                (user_id, int(user_seq)),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else None


def get_launch_user_seq(user_id: int, launch_id: int) -> int | None:
    """Inverso de resolve_user_seq_to_id: pega o user_seq de um id interno."""
    if not launch_id:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_seq from launches where id=%s and user_id=%s",
                (int(launch_id), user_id),
            )
            row = cur.fetchone()
            seq = row.get("user_seq") if row else None
            return int(seq) if seq else None


def display_id_for(user_id: int, launch_id: int) -> int:
    """Retorna o user_seq pra exibir; cai no id interno se não encontrar."""
    seq = get_launch_user_seq(user_id, launch_id)
    return seq if seq is not None else int(launch_id)


def update_launch_category(user_id: int, launch_id: int, categoria: str | None) -> bool:
    from utils_text import is_internal_category

    ensure_user(user_id)
    cat = (categoria or "").strip() or None
    is_internal = is_internal_category(cat)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update launches set categoria=%s, is_internal_movement=%s where user_id=%s and id=%s",
                (cat, is_internal, user_id, launch_id),
            )
            changed = (cur.rowcount or 0) == 1
        conn.commit()
    return changed


def update_launch_fields(
    user_id: int,
    launch_id: int,
    *,
    categoria: str | None = None,
    alvo: str | None = None,
    nota: str | None = None,
    criado_em: datetime | None = None,
) -> bool:
    """Atualiza campos editáveis (categoria, alvo, nota, criado_em) de um lançamento.

    Argumentos None são ignorados (mantém valor atual). Strings vazias viram
    NULL no banco. Retorna False se não encontrou lançamento do usuário.
    """
    from utils_text import is_internal_category

    ensure_user(user_id)

    sets: list[str] = []
    params: list = []
    if categoria is not None:
        cat_clean = categoria.strip() or None
        sets.append("categoria=%s")
        params.append(cat_clean)
        sets.append("is_internal_movement=%s")
        params.append(is_internal_category(cat_clean))
    if alvo is not None:
        sets.append("alvo=%s")
        params.append((alvo.strip() or None))
    if nota is not None:
        sets.append("nota=%s")
        params.append((nota.strip() or None))
    if criado_em is not None:
        sets.append("criado_em=%s")
        params.append(criado_em)
    if not sets:
        return False

    params.extend([user_id, launch_id])
    sql = f"update launches set {', '.join(sets)} where user_id=%s and id=%s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            changed = (cur.rowcount or 0) == 1
        conn.commit()
    return changed


def update_launch_categories_bulk(user_id: int, items: list[tuple[int, str]]) -> int:
    from utils_text import is_internal_category

    ensure_user(user_id)
    if not items:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "update launches set categoria=%s, is_internal_movement=%s where user_id=%s and id=%s",
                [(cat, is_internal_category(cat), user_id, lid) for (lid, cat) in items],
            )
            n = cur.rowcount or 0
        conn.commit()
    return n


def export_launches(user_id: int, start_date: date | None = None, end_date: date | None = None):
    ensure_user(user_id)

    params = [user_id]
    where = ["user_id=%s"]

    if start_date:
        where.append("criado_em >= %s")
        params.append(datetime.combine(start_date, datetime.min.time()))
    if end_date:
        where.append("criado_em < %s")
        params.append(datetime.combine(end_date + timedelta(days=1), datetime.min.time()))

    sql = f"""
        select id, tipo, valor, alvo, nota, criado_em, efeitos
        from launches
        where {' and '.join(where)}
        order by criado_em asc, id asc
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()


def get_launches_by_period(user_id: int, start_date: date, end_date: date):
    return _db_support.get_launches_by_period_impl(get_conn, ensure_user, user_id, start_date, end_date)


def get_summary_by_period(user_id: int, start_date: date, end_date: date):
    return _db_support.get_summary_by_period_impl(get_conn, ensure_user, user_id, start_date, end_date)


def get_internal_movement_total(user_id: int, start_date: date, end_date: date) -> float:
    """Soma de saídas internas (aportes, transferências pra caixinha) no período.

    `is_internal_movement=true` marca alocação que sai do caixa corrente mas
    não é gasto. Pra projeção de saldo (`forecast_month_end`), conta junto
    com despesa porque debita a conta corrente igual.
    """
    ensure_user(user_id)
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(sum(valor), 0) as total
                from launches
                where user_id = %s
                  and tipo = 'despesa'
                  and is_internal_movement = true
                  and criado_em >= %s and criado_em < %s
                """,
                (user_id, start_dt, end_excl),
            )
            row = cur.fetchone()
    return float(row["total"] or 0) if row else 0.0


def get_spending_trend(user_id: int, months: int = 6) -> list[dict]:
    """Tendência de gastos dos últimos N meses (default 6, contando o atual).

    Cada item: {year, month, despesa, receita}. Despesas incluem launches
    (não-internos) + compras no cartão. Receita só de launches.
    """
    ensure_user(user_id)
    months = max(1, min(int(months), 24))
    today = date.today()

    # Calcula primeiro dia do mês mais antigo a incluir
    y, m = today.year, today.month
    for _ in range(months - 1):
        if m == 1:
            m = 12
            y -= 1
        else:
            m -= 1
    range_start = datetime(y, m, 1)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select y::int as year, m::int as month,
                       sum(case when tipo = 'despesa' then valor else 0 end) as despesa,
                       sum(case when tipo = 'receita' then valor else 0 end) as receita
                from (
                    select extract(year from criado_em at time zone %s)::int as y,
                           extract(month from criado_em at time zone %s)::int as m,
                           tipo, valor
                    from launches
                    where user_id = %s
                      and criado_em >= %s
                      and is_internal_movement = false
                      and tipo in ('despesa', 'receita')
                    union all
                    select extract(year from purchased_at)::int as y,
                           extract(month from purchased_at)::int as m,
                           'despesa' as tipo, valor
                    from credit_transactions
                    where user_id = %s
                      and purchased_at >= %s::date
                      and is_refund = false
                ) agg
                group by y, m
                order by y, m
                """,
                (
                    "America/Sao_Paulo", "America/Sao_Paulo",
                    user_id, range_start,
                    user_id, range_start.date(),
                ),
            )
            rows = cur.fetchall()

    return [
        {
            "year": int(r["year"]),
            "month": int(r["month"]),
            "despesa": float(r["despesa"] or 0),
            "receita": float(r["receita"] or 0),
        }
        for r in rows
    ]


def get_largest_expenses(
    user_id: int,
    start_date: date,
    end_date: date,
    limit: int = 5,
):
    """Top N maiores gastos INDIVIDUAIS no período (não agregados por categoria).

    Difere de `get_top_expense_categories` que soma por categoria. Esta
    retorna os lançamentos/compras de maior valor, um por um.

    Fontes:
      - launches.tipo='despesa' AND is_internal_movement=false
      - credit_transactions onde is_refund=false

    Retorna lista [{valor, categoria, descricao, data, fonte}].
    `fonte` = 'launches' | 'credito' (frontend pode renderizar tag).
    `descricao` = alvo (se launches) ou nota (se credito).
    """
    ensure_user(user_id)

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select valor, categoria, descricao, dt, fonte
                from (
                    select valor,
                           coalesce(nullif(categoria, ''), 'outros') as categoria,
                           coalesce(nullif(alvo, ''), nullif(nota, ''), '—') as descricao,
                           criado_em::date as dt,
                           'launches' as fonte
                    from launches
                    where user_id = %s
                      and tipo = 'despesa'
                      and is_internal_movement = false
                      and criado_em >= %s and criado_em < %s
                    union all
                    select valor,
                           coalesce(nullif(categoria, ''), 'outros') as categoria,
                           coalesce(nullif(nota, ''), 'compra no crédito') as descricao,
                           purchased_at as dt,
                           'credito' as fonte
                    from credit_transactions
                    where user_id = %s
                      and is_refund = false
                      and purchased_at >= %s::date
                      and purchased_at <= %s::date
                ) agg
                order by valor desc
                limit %s
                """,
                (
                    user_id, start_dt, end_excl,
                    user_id, start_date, end_date,
                    int(limit),
                ),
            )
            rows = cur.fetchall()

    return [
        {
            "valor": float(r["valor"] or 0),
            "categoria": r["categoria"],
            "descricao": r["descricao"],
            "data": r["dt"].isoformat() if r.get("dt") else None,
            "fonte": r["fonte"],
        }
        for r in rows
    ]


def get_top_expense_categories(
    user_id: int,
    start_date: date,
    end_date: date,
    limit: int = 5,
):
    """Top N categorias de gasto no período.

    Agrega:
      - despesas reais em launches (tipo='despesa', is_internal_movement=false)
      - compras no cartão (credit_transactions, is_refund=false)

    NÃO inclui movimentações internas (aporte, resgate, transfer caixinha)
    nem reembolsos de cartão.

    Retorna lista [{categoria, total}] ordenada desc por total.
    """
    ensure_user(user_id)

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(nullif(categoria, ''), 'outros') as categoria,
                       sum(valor) as total
                from (
                    select categoria, valor
                    from launches
                    where user_id = %s
                      and tipo = 'despesa'
                      and is_internal_movement = false
                      and criado_em >= %s and criado_em < %s
                    union all
                    select categoria, valor
                    from credit_transactions
                    where user_id = %s
                      and is_refund = false
                      and purchased_at >= %s::date
                      and purchased_at <= %s::date
                ) agg
                group by coalesce(nullif(categoria, ''), 'outros')
                order by total desc
                limit %s
                """,
                (
                    user_id, start_dt, end_excl,
                    user_id, start_date, end_date,
                    int(limit),
                ),
            )
            rows = cur.fetchall()

    return [
        {"categoria": r["categoria"], "total": float(r["total"] or 0)}
        for r in rows
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Desfazer lançamento
# ──────────────────────────────────────────────────────────────────────────────

def delete_launch_and_rollback(user_id: int, launch_id: int):
    """
    Deleta um lançamento e reverte seus efeitos no banco atomicamente.
    Usa o campo efeitos (jsonb) para saber o que reverter.
    """
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, tipo, valor, alvo, efeitos from launches where id=%s and user_id=%s",
                (launch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                raise LookupError("NOT_FOUND")

            efeitos = row.get("efeitos")
            if efeitos is None:
                raise ValueError("lançamento sem 'efeitos' (não dá pra desfazer com segurança).")

            if isinstance(efeitos, str):
                efeitos = json.loads(efeitos)

            delta_conta = Decimal(str(efeitos.get("delta_conta", 0)))
            delta_pocket = efeitos.get("delta_pocket")
            delta_invest = efeitos.get("delta_invest")
            create_pocket = efeitos.get("create_pocket")
            create_invest = efeitos.get("create_investment")
            delete_pocket = efeitos.get("delete_pocket")
            delete_investment = efeitos.get("delete_investment")
            investment_lot_create = efeitos.get("investment_lot_create")
            investment_lot_withdrawals = efeitos.get("investment_lot_withdrawals") or []
            investment_lots_handled = False
            # Pagamento de fatura: bill_id + paid_amount_added permitem
            # reverter o `paid_amount` da credit_bill correspondente.
            paid_bill_id = efeitos.get("bill_id")
            paid_amount_added = efeitos.get("paid_amount_added")

            # desfazer pagamento de fatura — reverte paid_amount e reabre se
            # necessário (paid não cobre mais o total).
            if paid_bill_id and paid_amount_added is not None:
                cur.execute(
                    """
                    update credit_bills
                    set paid_amount = greatest(0, coalesce(paid_amount, 0) - %s),
                        status = case
                            when (coalesce(paid_amount, 0) - %s) < total then 'open'
                            else status
                        end,
                        paid_at = case
                            when (coalesce(paid_amount, 0) - %s) <= 0 then null
                            else paid_at
                        end
                    where id = %s and user_id = %s
                    """,
                    (
                        Decimal(str(paid_amount_added)),
                        Decimal(str(paid_amount_added)),
                        Decimal(str(paid_amount_added)),
                        int(paid_bill_id),
                        user_id,
                    ),
                )

            # desfazer criação de investimento (zera e deleta)
            if create_invest:
                nome = create_invest.get("nome")
                if nome:
                    cur.execute(
                        "delete from investments where user_id=%s and lower(name)=lower(%s) and balance=0",
                        (user_id, nome),
                    )

            # desfazer deleção de investimento (recria)
            if delete_investment:
                nome = delete_investment.get("nome")
                bal0 = Decimal(str(delete_investment.get("balance", 0)))
                rate = Decimal(str(delete_investment.get("rate", 0)))
                period = delete_investment.get("period", "monthly")
                last_date_str = delete_investment.get("last_date")
                asset_type = delete_investment.get("asset_type") or "CDB"
                indexer = delete_investment.get("indexer")
                issuer = delete_investment.get("issuer")
                purchase_date = delete_investment.get("purchase_date")
                maturity_date = delete_investment.get("maturity_date")
                interest_payment_frequency = delete_investment.get("interest_payment_frequency") or "maturity"
                tax_profile = delete_investment.get("tax_profile") or "regressive_ir_iof"
                if nome:
                    from datetime import date as _date
                    ld = _date.fromisoformat(last_date_str) if last_date_str else datetime.now(_tz()).date()
                    cur.execute(
                        """
                        insert into investments(
                            user_id, name, balance, rate, period, last_date,
                            asset_type, indexer, issuer, purchase_date, maturity_date,
                            interest_payment_frequency, tax_profile
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict (user_id, name) do nothing
                        """,
                        (
                            user_id, nome, bal0, rate, period, ld,
                            asset_type, indexer, issuer, purchase_date, maturity_date,
                            interest_payment_frequency, tax_profile,
                        ),
                    )

            # desfazer deleção de caixinha (recria)
            if delete_pocket:
                nome = delete_pocket.get("nome")
                bal0 = Decimal(str(delete_pocket.get("balance", 0)))
                if nome:
                    cur.execute(
                        "insert into pockets(user_id, name, balance) values (%s,%s,%s) "
                        "on conflict (user_id, name) do nothing",
                        (user_id, nome, bal0),
                    )

            # reverte conta
            if delta_conta != 0:
                cur.execute(
                    "update accounts set balance = balance - %s where user_id=%s",
                    (delta_conta, user_id),
                )

            # reverte caixinha
            if delta_pocket:
                nome = delta_pocket.get("nome")
                dp = Decimal(str(delta_pocket.get("delta", 0)))
                if not nome:
                    raise ValueError("delta_pocket inválido (sem nome).")
                cur.execute(
                    "update pockets set balance = balance - %s where user_id=%s and lower(name)=lower(%s)",
                    (dp, user_id, nome),
                )

            # reverte lotes de investimento antes do saldo agregado.
            if investment_lot_create:
                lot_id = investment_lot_create.get("lot_id")
                investment_id = investment_lot_create.get("investment_id")
                if lot_id:
                    cur.execute(
                        """
                        select investment_id, principal_initial, principal_remaining, status
                        from investment_lots
                        where id=%s and user_id=%s
                        for update
                        """,
                        (lot_id, user_id),
                    )
                    lot = cur.fetchone()
                    if lot and (
                        lot["status"] != "open"
                        or Decimal(str(lot["principal_remaining"])) != Decimal(str(lot["principal_initial"]))
                    ):
                        raise ValueError("Não é possível desfazer este aporte: o lote já teve resgate.")
                    if lot and not investment_id:
                        investment_id = lot["investment_id"]
                    cur.execute(
                        "delete from investment_lots where id=%s and user_id=%s",
                        (lot_id, user_id),
                    )
                    investment_lots_handled = True
                if investment_id:
                    cur.execute(
                        """
                        update investments i
                        set balance = coalesce(l.total_balance, 0),
                            last_date = coalesce(l.max_last_date, i.last_date)
                        from (
                            select coalesce(sum(balance), 0) as total_balance, max(last_date) as max_last_date
                            from investment_lots
                            where user_id=%s and investment_id=%s and status='open'
                        ) l
                        where i.user_id=%s and i.id=%s
                        """,
                        (user_id, investment_id, user_id, investment_id),
                    )

            if investment_lot_withdrawals:
                restored_investment_ids = set()
                for effect in investment_lot_withdrawals:
                    lot_id = effect.get("lot_id")
                    before = effect.get("before") or {}
                    if not lot_id:
                        continue
                    cur.execute(
                        """
                        update investment_lots
                        set balance=%s, principal_remaining=%s, status=%s, closed_at=%s
                        where id=%s and user_id=%s
                        returning investment_id
                        """,
                        (
                            Decimal(str(before.get("balance", 0))),
                            Decimal(str(before.get("principal_remaining", 0))),
                            before.get("status") or "open",
                            before.get("closed_at"),
                            lot_id,
                            user_id,
                        ),
                    )
                    restored = cur.fetchone()
                    if restored:
                        restored_investment_ids.add(restored["investment_id"])
                        investment_lots_handled = True

                for investment_id in restored_investment_ids:
                    cur.execute(
                        """
                        update investments i
                        set balance = coalesce(l.total_balance, 0),
                            last_date = coalesce(l.max_last_date, i.last_date)
                        from (
                            select coalesce(sum(balance), 0) as total_balance, max(last_date) as max_last_date
                            from investment_lots
                            where user_id=%s and investment_id=%s and status='open'
                        ) l
                        where i.user_id=%s and i.id=%s
                        """,
                        (user_id, investment_id, user_id, investment_id),
                    )

            # reverte investimento
            if delta_invest:
                nome = delta_invest.get("nome")
                di = Decimal(str(delta_invest.get("delta", 0)))
                if not nome:
                    raise ValueError("delta_invest inválido (sem nome).")
                if not investment_lots_handled:
                    cur.execute(
                        "update investments set balance = balance - %s where user_id=%s and lower(name)=lower(%s)",
                        (di, user_id, nome),
                    )

            # desfazer criação de caixinha (deleta)
            if create_pocket:
                nome = create_pocket.get("nome")
                if nome:
                    cur.execute(
                        "delete from pockets where user_id=%s and lower(name)=lower(%s)",
                        (user_id, nome),
                    )

            # apaga o lançamento
            cur.execute("delete from launches where id=%s and user_id=%s", (launch_id, user_id))

        conn.commit()


# Lançamentos "da conta corrente" no sentido do produto: SÓ `despesa` e `receita`.
# Isso já cobre os pagamentos de fatura (gravados como tipo='despesa') e exclui
# TODO o ciclo de vida de caixinha/investimento, que usa tipos próprios
# (deposito_caixinha, aporte_investimento, criar_caixinha, create_investment,
# saque_caixinha, resgate_investimento...).
#
# NÃO use `is_internal_movement = false`: a CRIAÇÃO de caixinha/investimento gera
# um launch com is_internal_movement=false, e apagá-lo deleta a caixinha/o
# investimento junto (efeitos.create_pocket → delete from pockets). O filtro por
# tipo evita essa armadilha. Validado no staging: 0 launches despesa/receita
# carregam efeitos de caixinha/investimento.
#
# Usado por count_launches e delete_all_launches_and_rollback pra ficarem
# consistentes (o que se conta é o que se apaga).
_CONTA_CORRENTE_LAUNCH_FILTER = "tipo in ('despesa', 'receita')"


def count_launches(user_id: int) -> int:
    """Conta os lançamentos da conta corrente (despesas/receitas + pagamentos de
    fatura) — o conjunto que `delete_all_launches_and_rollback` apaga. NÃO conta
    movimentação interna de caixinha/investimento."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from launches "
                f"where user_id=%s and {_CONTA_CORRENTE_LAUNCH_FILTER}",
                (user_id,),
            )
            row = cur.fetchone()
            return int(row["n"]) if row else 0


def delete_all_launches_and_rollback(user_id: int) -> dict:
    """Apaga os lançamentos da CONTA CORRENTE (despesas/receitas) e desfaz
    pagamentos de fatura, revertendo os efeitos de cada um no saldo da conta.

    NÃO toca em caixinhas nem investimentos: o depósito/saque de caixinha e o
    aporte/resgate de investimento são `is_internal_movement=true` sem `bill_id`,
    então ficam de fora do filtro — seus saldos e registros permanecem intactos.
    (Sem esse filtro, "apagar tudo" zerava caixinhas/investimentos junto e dava
    a sensação de resetar o usuário do zero.)

    Reusa `delete_launch_and_rollback` linha a linha (em vez de um `delete`
    em massa) porque cada lançamento guarda seus efeitos colaterais no jsonb
    `efeitos` — saldo da conta e reabertura de fatura. Apagar em massa sem
    reverter deixaria esses saldos inconsistentes.

    Ordena por `id desc` (mais novo primeiro) por segurança em reversões
    encadeadas (ex.: múltiplos pagamentos da mesma fatura).

    Retorna {"deleted": N, "failed": M}. `failed` cobre lançamentos legados
    sem `efeitos` (não dá pra reverter com segurança) — esses são mantidos
    intactos pra não corromper o saldo, em vez de apagados às cegas.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from launches "
                f"where user_id=%s and {_CONTA_CORRENTE_LAUNCH_FILTER} "
                "order by id desc",
                (user_id,),
            )
            ids = [row["id"] for row in cur.fetchall()]

    deleted = 0
    failed = 0
    for lid in ids:
        try:
            delete_launch_and_rollback(user_id, lid)
            deleted += 1
        except Exception:
            failed += 1
    return {"deleted": deleted, "failed": failed}


# ──────────────────────────────────────────────────────────────────────────────
# OFX import (idempotente)
# ──────────────────────────────────────────────────────────────────────────────

def get_ofx_import_by_hash(user_id: int, file_hash: str):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select file_hash, dt_start, dt_end, total_transactions,
                       inserted_count, duplicate_count, imported_at
                from ofx_imports
                where user_id=%s and file_hash=%s
                """,
                (user_id, file_hash),
            )
            return cur.fetchone()


def import_ofx_launches_bulk(
    user_id: int,
    launches_rows: list[dict],
    *,
    file_hash: str,
    bank_id: str | None,
    acct_id: str | None,
    acct_type: str | None,
    dt_start: date | None,
    dt_end: date | None,
):
    """
    Importa transações OFX de forma IDEMPOTENTE (ON CONFLICT DO NOTHING).
    Saldo só é ajustado pelas transações efetivamente inseridas.
    """
    ensure_user(user_id)
    total = len(launches_rows)

    prev = get_ofx_import_by_hash(user_id, file_hash)
    if prev:
        bal = get_balance(user_id)
        return {
            "skipped_same_file": True,
            "total": prev["total_transactions"],
            "inserted": prev["inserted_count"],
            "duplicates": prev["duplicate_count"],
            "dt_start": prev["dt_start"],
            "dt_end": prev["dt_end"],
            "new_balance": bal,
            "imported_at": prev["imported_at"],
        }

    inserted = 0
    duplicates = 0
    delta_total = Decimal("0")

    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in launches_rows:
                cur.execute(
                    """
                    insert into launches(
                        user_id, tipo, valor, categoria, alvo, nota, criado_em, efeitos,
                        source, external_id, posted_at, currency, imported_at, is_internal_movement
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
                    on conflict (user_id, source, external_id) do nothing
                    """,
                    (
                        user_id, r["tipo"], r["valor"], r.get("categoria"), r.get("alvo"), r.get("nota"),
                        r["criado_em"],
                        Json({"delta_conta": float(r["delta"]), "ofx": r.get("ofx_meta", {})}),
                        "ofx", r["external_id"], r.get("posted_at"), r.get("currency", "BRL"),
                        r.get("is_internal_movement", False),
                    ),
                )
                if (cur.rowcount or 0) == 1:
                    inserted += 1
                    delta_total += r["delta"]
                else:
                    duplicates += 1

            if inserted:
                cur.execute(
                    "update accounts set balance = balance + %s where user_id=%s returning balance",
                    (delta_total, user_id),
                )
                new_bal = cur.fetchone()["balance"]
            else:
                cur.execute("select balance from accounts where user_id=%s", (user_id,))
                new_bal = cur.fetchone()["balance"]

            cur.execute(
                """
                insert into ofx_imports(
                    user_id, file_hash, bank_id, acct_id, acct_type,
                    dt_start, dt_end, total_transactions, inserted_count, duplicate_count
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing
                """,
                (user_id, file_hash, bank_id, acct_id, acct_type,
                 dt_start, dt_end, total, inserted, duplicates),
            )

        conn.commit()

    return {
        "skipped_same_file": False,
        "total": total,
        "inserted": inserted,
        "duplicates": duplicates,
        "dt_start": dt_start,
        "dt_end": dt_end,
        "new_balance": new_bal,
    }
