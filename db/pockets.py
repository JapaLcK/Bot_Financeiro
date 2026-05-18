"""
db/pockets.py — Caixinhas (pockets): criar, depositar, sacar e excluir.
"""
from datetime import date, datetime
from decimal import Decimal

from psycopg.types.json import Jsonb

from utils_date import _tz

from .connection import get_conn
from .investments import (
    LOT_EPSILON,
    ZERO,
    _growth_for_period,
    _iof_rate_for_days,
    _ir_rate_for_days,
    _taxes_for_gain,
)
from .users import ensure_user


POCKET_COLUMNS = """
    id, name, balance, description,
    target_amount, target_date, emoji, color, status,
    interest_enabled, interest_rate, interest_period,
    interest_tax_profile, last_interest_date
"""


def _today() -> date:
    return datetime.now(_tz()).date()


def _insert_pocket_lot(
    cur,
    user_id: int,
    pocket_id: int,
    amount: Decimal,
    opened_at: date,
    last_date: date | None = None,
) -> int:
    applied_date = last_date or opened_at
    cur.execute(
        """
        insert into pocket_lots(
            user_id, pocket_id, principal_initial, principal_remaining,
            balance, opened_at, last_date, status
        )
        values (%s,%s,%s,%s,%s,%s,%s,'open')
        returning id
        """,
        (user_id, pocket_id, amount, amount, amount, opened_at, applied_date),
    )
    return cur.fetchone()["id"]


def _ensure_pocket_lots(cur, user_id: int, pocket: dict) -> None:
    cur.execute(
        "select count(*) as total from pocket_lots where user_id=%s and pocket_id=%s",
        (user_id, pocket["id"]),
    )
    if int(cur.fetchone()["total"] or 0) > 0:
        return

    balance = Decimal(str(pocket["balance"] or 0))
    if balance <= 0:
        return

    opened_at = pocket.get("last_interest_date") or _today()
    _insert_pocket_lot(cur, user_id, pocket["id"], balance, opened_at, opened_at)


def _sync_pocket_from_lots(cur, user_id: int, pocket_id: int) -> Decimal:
    cur.execute(
        """
        select coalesce(sum(balance), 0) as balance, max(last_date) as last_date
        from pocket_lots
        where user_id=%s and pocket_id=%s and status='open'
        """,
        (user_id, pocket_id),
    )
    totals = cur.fetchone()
    new_balance = Decimal(str(totals["balance"] or 0))
    new_last_date = totals["last_date"] or _today()
    cur.execute(
        "update pockets set balance=%s, last_interest_date=%s where id=%s and user_id=%s",
        (new_balance, new_last_date, pocket_id, user_id),
    )
    return new_balance


def accrue_pocket_db(cur, user_id: int, pocket_id: int, today: date | None = None) -> Decimal:
    """Aplica rendimento da caixinha por lote, reaproveitando a regra de CDI dos investimentos."""
    if today is None:
        today = _today()

    cur.execute(
        """
        select id, balance, interest_enabled, interest_rate,
               interest_period, interest_tax_profile, last_interest_date
        from pockets
        where user_id=%s and id=%s for update
        """,
        (user_id, pocket_id),
    )
    pocket = cur.fetchone()
    if not pocket:
        raise LookupError("POCKET_NOT_FOUND")

    _ensure_pocket_lots(cur, user_id, pocket)
    if not pocket.get("interest_enabled"):
        return Decimal(str(pocket["balance"] or 0))

    period = pocket.get("interest_period") or "cdi"
    rate = Decimal(str(pocket.get("interest_rate") or 1))

    cur.execute(
        """
        select id, balance, last_date
        from pocket_lots
        where user_id=%s and pocket_id=%s and status='open'
        order by opened_at, id
        for update
        """,
        (user_id, pocket_id),
    )
    lots = cur.fetchall()
    if not lots:
        cur.execute("update pockets set balance=0 where id=%s and user_id=%s", (pocket_id, user_id))
        return ZERO

    for lot in lots:
        new_balance, applied_until = _growth_for_period(
            cur,
            Decimal(str(lot["balance"] or 0)),
            period,
            rate,
            lot["last_date"],
            today,
        )
        if new_balance != lot["balance"] or applied_until != lot["last_date"]:
            cur.execute(
                "update pocket_lots set balance=%s, last_date=%s where id=%s and user_id=%s",
                (new_balance, applied_until or lot["last_date"], lot["id"], user_id),
            )

    return _sync_pocket_from_lots(cur, user_id, pocket_id)


def accrue_all_pockets(user_id: int, today: date | None = None):
    ensure_user(user_id)
    if today is None:
        today = _today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from pockets where user_id=%s for update", (user_id,))
            rows = cur.fetchall()
            for r in rows:
                accrue_pocket_db(cur, user_id, r["id"], today=today)

            cur.execute(
                f"""
                select {POCKET_COLUMNS}
                  from pockets
                where user_id=%s
                order by balance desc, lower(name)
                """,
                (user_id,),
            )
            out = cur.fetchall()
        conn.commit()
    return out


def list_pockets(user_id: int, *, accrue: bool = True):
    ensure_user(user_id)
    if accrue:
        return accrue_all_pockets(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select {POCKET_COLUMNS}
                  from pockets
                 where user_id=%s
                 order by balance desc, lower(name)
                """,
                (user_id,),
            )
            return cur.fetchall()


def update_pocket_meta(
    user_id: int,
    pocket_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    target_amount: float | None = None,
    target_date: str | None = None,  # ISO 'YYYY-MM-DD' ou None pra limpar
    emoji: str | None = None,
    color: str | None = None,
    status: str | None = None,
    interest_enabled: bool | None = None,
    interest_rate: float | None = None,
    clear_target: bool = False,
):
    """PATCH em metadata da caixinha (não mexe em balance — usar deposit/withdraw)."""
    ensure_user(user_id)
    sets: list[str] = []
    params: list = []
    if name is not None:
        sets.append("name = %s")
        params.append(name.strip())
    if description is not None:
        sets.append("description = %s")
        params.append(description.strip() or None)
    if clear_target:
        sets.append("target_amount = NULL")
        sets.append("target_date = NULL")
    else:
        if target_amount is not None:
            sets.append("target_amount = %s")
            params.append(Decimal(str(target_amount)) if target_amount is not None else None)
        if target_date is not None:
            sets.append("target_date = %s")
            params.append(target_date or None)
    if emoji is not None:
        sets.append("emoji = %s")
        params.append(emoji.strip() or None)
    if color is not None:
        sets.append("color = %s")
        params.append(color.strip() or None)
    if status is not None:
        if status not in ("active", "achieved", "abandoned"):
            raise ValueError("STATUS_INVALIDO")
        sets.append("status = %s")
        params.append(status)
    if interest_enabled is not None:
        sets.append("interest_enabled = %s")
        params.append(bool(interest_enabled))
    if interest_rate is not None:
        rate = Decimal(str(interest_rate))
        if rate <= 0:
            raise ValueError("INTEREST_RATE_INVALID")
        sets.append("interest_rate = %s")
        params.append(rate)
    if not sets:
        return None
    params.extend([user_id, int(pocket_id)])
    with get_conn() as conn:
        with conn.cursor() as cur:
            if interest_enabled is not None:
                cur.execute(
                    """
                    select id, balance, interest_enabled, interest_rate,
                           interest_period, interest_tax_profile, last_interest_date
                      from pockets
                     where user_id=%s and id=%s
                     for update
                    """,
                    (user_id, int(pocket_id)),
                )
                pocket = cur.fetchone()
                if not pocket:
                    return None
                _ensure_pocket_lots(cur, user_id, pocket)
                if bool(interest_enabled):
                    today = _today()
                    cur.execute(
                        """
                        update pocket_lots
                           set last_date=%s
                         where user_id=%s and pocket_id=%s and status='open'
                        """,
                        (today, user_id, int(pocket_id)),
                    )
                    sets.append("last_interest_date = %s")
                    params.insert(-2, today)
                else:
                    accrue_pocket_db(cur, user_id, int(pocket_id))
            cur.execute(
                f"update pockets set {', '.join(sets)} "
                "where user_id=%s and id=%s "
                f"returning {POCKET_COLUMNS}",
                params,
            )
            row = cur.fetchone()
        conn.commit()
    return row


def pocket_withdraw_to_account(
    user_id: int, pocket_name: str, amount: float, nota: str | None = None
):
    """Caixinha → Conta via FIFO. Retorna (launch_id, new_acc, new_pocket, canon, tax_summary)."""
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, balance, interest_tax_profile from pockets "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id = p["id"]
            canon = p["name"]
            new_bal_before = accrue_pocket_db(cur, user_id, pocket_id, today=criado_em.date())
            if new_bal_before < v:
                raise ValueError("INSUFFICIENT_POCKET")

            cur.execute(
                """
                select id, principal_remaining, balance, opened_at, last_date, status
                from pocket_lots
                where user_id=%s and pocket_id=%s and status='open' and balance > 0
                order by opened_at, id
                for update
                """,
                (user_id, pocket_id),
            )
            lots = cur.fetchall()
            remaining = v
            total_gross = ZERO
            total_net = ZERO
            total_iof = ZERO
            total_ir = ZERO
            lot_effects = []
            breakdown = []
            tax_profile = p.get("interest_tax_profile") or "regressive_ir_iof"

            for lot in lots:
                if remaining <= 0:
                    break

                lot_balance = Decimal(str(lot["balance"] or 0))
                if lot_balance <= 0:
                    continue
                lot_principal = Decimal(str(lot["principal_remaining"] or 0))
                take = min(lot_balance, remaining)

                if lot_balance <= lot_principal or lot_balance <= 0:
                    principal_part = min(take, lot_principal)
                    gain_part = ZERO
                else:
                    ratio = take / lot_balance
                    principal_part = min(lot_principal, lot_principal * ratio)
                    gain_part = max(take - principal_part, ZERO)

                age_days = max(0, (criado_em.date() - lot["opened_at"]).days)
                iof, ir = _taxes_for_gain(gain_part, age_days, tax_profile)
                net = take - iof - ir

                new_lot_balance = lot_balance - take
                new_lot_principal = max(lot_principal - principal_part, ZERO)
                closes = new_lot_balance <= LOT_EPSILON
                after_status = "closed" if closes else "open"
                after_balance = ZERO if closes else new_lot_balance
                after_principal = ZERO if closes else new_lot_principal

                lot_effects.append({
                    "lot_id": int(lot["id"]),
                    "before": {
                        "balance": float(lot_balance),
                        "principal_remaining": float(lot_principal),
                        "status": lot["status"],
                        "closed_at": None,
                    },
                    "after": {
                        "balance": float(after_balance),
                        "principal_remaining": float(after_principal),
                        "status": after_status,
                        "closed_at": criado_em.date().isoformat() if closes else None,
                    },
                })
                breakdown.append({
                    "lot_id": int(lot["id"]),
                    "opened_at": lot["opened_at"].isoformat(),
                    "age_days": age_days,
                    "gross": float(take),
                    "principal": float(principal_part),
                    "gain": float(gain_part),
                    "iof": float(iof),
                    "ir": float(ir),
                    "net": float(net),
                    "ir_rate": float(_ir_rate_for_days(age_days, tax_profile)),
                    "iof_rate": float(_iof_rate_for_days(age_days, tax_profile)),
                })

                cur.execute(
                    """
                    update pocket_lots
                       set balance=%s, principal_remaining=%s, status=%s,
                           closed_at=%s, last_date=%s
                     where id=%s and user_id=%s
                    """,
                    (
                        after_balance,
                        after_principal,
                        after_status,
                        criado_em.date() if closes else None,
                        criado_em.date(),
                        lot["id"],
                        user_id,
                    ),
                )

                remaining -= take
                total_gross += take
                total_net += net
                total_iof += iof
                total_ir += ir

            if remaining > LOT_EPSILON:
                raise ValueError("INSUFFICIENT_POCKET")

            new_pocket = _sync_pocket_from_lots(cur, user_id, pocket_id)

            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (total_net, user_id),
            )
            new_acc = cur.fetchone()["balance"]

            tax_summary = {
                "gross": float(total_gross),
                "net": float(total_net),
                "iof": float(total_iof),
                "ir": float(total_ir),
                "tax_profile": tax_profile,
                "method": "FIFO",
                "lots": breakdown,
            }
            efeitos = {
                "delta_conta": float(+total_net),
                "delta_pocket": {"nome": canon, "delta": float(-total_gross)},
                "delta_invest": None, "create_pocket": None, "create_investment": None,
                "pocket_lot_withdrawals": lot_effects,
                "tax_summary": tax_summary,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "saque_caixinha", total_gross, canon, nota, criado_em, Jsonb(efeitos), True),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_pocket, canon, tax_summary


def create_pocket(
    user_id: int,
    name: str,
    nota: str | None = None,
    description: str | None = None,
    *,
    interest_enabled: bool = True,
    interest_rate: float = 1.0,
):
    """
    Cria caixinha. Retorna (launch_id, pocket_id, pocket_name).
    Se já existir, retorna (None, pocket_id, pocket_name).

    `nota` vira o texto do lançamento (audit log).
    `description` é a descrição visível da caixinha (mostrada no dashboard).
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")

    # Plan gate: blinda todos os canais (HTTP, bot, IA). Levanta
    # PlanLimitExceeded com mensagem amigável; callers decidem se mostram em
    # texto (bot) ou em 403 (HTTP).
    from core.services.plan_service import check_can_create_pocket
    check_can_create_pocket(user_id)

    desc = (description or "").strip() or None
    rate = Decimal(str(interest_rate))
    if rate <= 0:
        raise ValueError("INTEREST_RATE_INVALID")
    criado_em = datetime.now(_tz())
    today = _today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into pockets(
                    user_id, name, balance, description,
                    interest_enabled, interest_rate, interest_period,
                    interest_tax_profile, last_interest_date
                )
                values (%s, %s, 0, %s, %s, %s, 'cdi', 'regressive_ir_iof', %s)
                """
                "on conflict (user_id, name) do nothing returning id, name",
                (user_id, name, desc, bool(interest_enabled), rate, today),
            )
            row = cur.fetchone()

            if row:
                pocket_id = row["id"]
                pocket_name = row["name"]
            else:
                cur.execute(
                    "select id, name from pockets where user_id=%s and lower(name)=lower(%s)",
                    (user_id, name),
                )
                r = cur.fetchone()
                if not r:
                    raise RuntimeError("POCKET_LOOKUP_FAILED")
                pocket_id, pocket_name = r["id"], r["name"]
                conn.commit()
                return None, pocket_id, pocket_name

            efeitos = {
                "delta_conta": 0.0, "delta_pocket": None, "delta_invest": None,
                "create_pocket": {
                    "nome": pocket_name,
                    "interest_enabled": bool(interest_enabled),
                    "interest_rate": float(rate),
                    "interest_period": "cdi",
                },
                "create_investment": None,
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "criar_caixinha", Decimal("0"), pocket_name, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, pocket_id, pocket_name


def pocket_deposit_from_account(
    user_id: int, pocket_name: str, amount: float, nota: str | None = None
):
    """Conta → Caixinha. Retorna (launch_id, new_account_balance, new_pocket_balance, canon_name)."""
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
            acc = cur.fetchone()
            if not acc:
                raise RuntimeError("ACCOUNT_MISSING")
            if Decimal(str(acc["balance"])) < v:
                raise ValueError("INSUFFICIENT_ACCOUNT")

            cur.execute(
                "select id, name from pockets "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id, canon = p["id"], p["name"]
            accrue_pocket_db(cur, user_id, pocket_id, today=criado_em.date())

            cur.execute(
                "update accounts set balance = balance - %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_acc = cur.fetchone()["balance"]

            lot_id = _insert_pocket_lot(cur, user_id, pocket_id, v, criado_em.date(), criado_em.date())
            new_pocket = _sync_pocket_from_lots(cur, user_id, pocket_id)

            efeitos = {
                "delta_conta": float(-v),
                "delta_pocket": {"nome": canon, "delta": float(+v)},
                "delta_invest": None, "create_pocket": None, "create_investment": None,
                "pocket_lot_create": {"lot_id": lot_id, "pocket_id": pocket_id},
            }
            cur.execute(
                "insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (user_id, "deposito_caixinha", v, canon, nota, criado_em, Jsonb(efeitos), True),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_acc, new_pocket, canon


def delete_pocket(user_id: int, pocket_name: str):
    """Exclui caixinha (so se saldo=0) e remove o historico relacionado.

    Apaga todos os launches que referenciam esta caixinha por nome
    (deposito_caixinha, saque_caixinha, criar_caixinha, delete_pocket
    de eventuais re-criacoes anteriores). Sem isso, recriar uma caixinha
    com o mesmo nome ressuscita o historico antigo (a query de historico
    filtra por `alvo`, nao por pocket_id).

    Saldo zero garante que os deposito/saque se cancelam — apagar nao
    afeta o saldo da conta principal. Retorna (None, canon_name) — o
    `launch_id` antigo de auditoria foi removido junto com o resto.
    """
    ensure_user(user_id)
    pocket_name = (pocket_name or "").strip()
    if not pocket_name:
        raise ValueError("EMPTY_NAME")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, balance from pockets "
                "where user_id=%s and lower(name)=lower(%s) for update",
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id, canon = p["id"], p["name"]
            if Decimal(str(p["balance"])) != Decimal("0"):
                raise ValueError("POCKET_NOT_ZERO")

            cur.execute(
                """
                delete from launches
                 where user_id = %s
                   and lower(alvo) = lower(%s)
                   and tipo in ('deposito_caixinha', 'saque_caixinha',
                                'criar_caixinha', 'delete_pocket')
                """,
                (user_id, canon),
            )
            cur.execute("delete from pockets where id=%s", (pocket_id,))

        conn.commit()

    return None, canon
