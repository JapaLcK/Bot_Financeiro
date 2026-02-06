# db.py
import os
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb, Json  # <-- ADICIONA ISSO
from decimal import Decimal
from datetime import datetime, date
import math


def get_conn():
    database_url = os.getenv("DATABASE_URL")  # Railway injeta isso quando você adiciona Postgres

    if not database_url:
        raise RuntimeError("DATABASE_URL não está definido.")
    return psycopg.connect(database_url, row_factory=dict_row)

def init_db():
    ddl = """
    create table if not exists users (
      id bigint primary key,
      created_at timestamptz default now()
    );

    create table if not exists accounts (
      user_id bigint primary key references users(id) on delete cascade,
      balance numeric not null default 0
    );

    create table if not exists pockets (
      id bigserial primary key,
      user_id bigint not null references users(id) on delete cascade,
      name text not null,
      balance numeric not null default 0,
      created_at timestamptz default now(),
      unique(user_id, name)
    );

    create table if not exists investments (
      id bigserial primary key,
      user_id bigint not null references users(id) on delete cascade,
      name text not null,
      balance numeric not null default 0,
      rate numeric not null,
      period text not null, -- daily|monthly|yearly
      last_date date not null,
      created_at timestamptz default now(),
      unique(user_id, name)
    );

    create table if not exists launches (
      id bigserial primary key,
      user_id bigint not null references users(id) on delete cascade,
      tipo text not null,         -- "despesa" | "receita" | etc
      valor numeric not null,
      alvo text,                  -- categoria/caixinha/investimento
      nota text,
      criado_em timestamptz not null default now(),
      efeitos jsonb
    );

    create index if not exists idx_launches_user_time on launches(user_id, criado_em desc);
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

def ensure_user(user_id: int):
    """Garante que user e account existam."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("insert into users(id) values (%s) on conflict do nothing", (user_id,))
            cur.execute("insert into accounts(user_id, balance) values (%s, 0) on conflict do nothing", (user_id,))
        conn.commit()

def get_balance(user_id: int) -> Decimal:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s", (user_id,))
            row = cur.fetchone()
            return row["balance"] if row else Decimal("0")

def add_launch_and_update_balance(user_id: int, tipo: str, valor: float, alvo: str | None, nota: str | None):
    """
    Lança registro em launches e atualiza saldo em accounts na mesma transação.
    Regra:
      - despesa: saldo -= valor
      - receita: saldo += valor
    """
    ensure_user(user_id)

    v = Decimal(str(valor))
    if tipo == "despesa":
        delta = -v
    elif tipo == "receita":
        delta = +v
    else:
        # se tiver outros tipos depois, você decide a regra
        raise ValueError(f"tipo inválido: {tipo}")

    criado_em = datetime.now().isoformat(timespec="seconds")

    with get_conn() as conn:
        with conn.cursor() as cur:
            # atualiza saldo
            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (delta, user_id),
            )
            new_bal = cur.fetchone()["balance"]

            # grava lançamento
            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, tipo, v, alvo, nota, criado_em, Json({"delta_conta": float(delta)})),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_bal

def list_launches(user_id: int, limit: int = 10):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, tipo, valor, alvo, nota, criado_em
                from launches
                where user_id=%s
                order by criado_em desc, id desc
                limit %s
                """,
                (user_id, limit),
            )
            return cur.fetchall()
        
def list_pockets(user_id: int):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, balance from pockets where user_id=%s order by lower(name)",
                (user_id,),
            )
            return cur.fetchall()

def pocket_withdraw_to_account(user_id: int, pocket_name: str, amount: float, nota: str | None = None):
    """
    Move dinheiro da caixinha -> conta.
    Retorna: (launch_id, new_account_balance, new_pocket_balance, pocket_name_canon)
    """
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # trava caixinha
            cur.execute(
                """
                select id, name, balance
                from pockets
                where user_id=%s and lower(name)=lower(%s)
                for update
                """,
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id = p["id"]
            pocket_name_canon = p["name"]
            pocket_balance = Decimal(str(p["balance"]))

            if pocket_balance < v:
                raise ValueError("INSUFFICIENT_POCKET")

            # debita caixinha
            cur.execute(
                "update pockets set balance = balance - %s where id=%s returning balance",
                (v, pocket_id),
            )
            new_pocket_balance = cur.fetchone()["balance"]

            # (opcional) trava conta
            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))

            # credita conta
            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_account_balance = cur.fetchone()["balance"]

            efeitos = {
                "delta_conta": float(+v),
                "delta_pocket": {"nome": pocket_name_canon, "delta": float(-v)},
                "delta_invest": None,
                "create_pocket": None,
                "create_investment": None,
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "saque_caixinha", v, pocket_name_canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_account_balance, new_pocket_balance, pocket_name_canon

def create_pocket(user_id: int, name: str, nota: str | None = None):
    """
    Cria caixinha (pockets) e registra launch criar_caixinha.
    Retorna: (launch_id, pocket_id, pocket_name)
      - se já existir: (None, pocket_id, pocket_name)
    """
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")

    criado_em = datetime.now()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # tenta criar (sem exceção): se existir, não cria
            cur.execute(
                """
                insert into pockets(user_id, name, balance)
                values (%s, %s, 0)
                on conflict (user_id, name) do nothing
                returning id, name
                """,
                (user_id, name),
            )
            row = cur.fetchone()

            if row:
                pocket_id = row["id"]
                pocket_name = row["name"]
                created = True
            else:
                created = False
                # pega a existente (case-insensitive)
                cur.execute(
                    """
                    select id, name
                    from pockets
                    where user_id=%s and lower(name)=lower(%s)
                    """,
                    (user_id, name),
                )
                r = cur.fetchone()
                if not r:
                    raise RuntimeError("POCKET_LOOKUP_FAILED")
                pocket_id = r["id"]
                pocket_name = r["name"]

            if not created:
                conn.commit()
                return None, pocket_id, pocket_name

            efeitos = {
                "delta_conta": 0.0,
                "delta_pocket": None,
                "delta_invest": None,
                "create_pocket": {"nome": pocket_name},
                "create_investment": None,
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "criar_caixinha", Decimal("0"), pocket_name, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, pocket_id, pocket_name


def pocket_deposit_from_account(user_id: int, pocket_name: str, amount: float, nota: str | None = None):
    """
    Move dinheiro da conta -> caixinha.
    Retorna: (launch_id, new_account_balance, new_pocket_balance, pocket_name_canon)
    """
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # trava conta
            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
            acc = cur.fetchone()
            if not acc:
                raise RuntimeError("ACCOUNT_MISSING")

            acc_balance = Decimal(str(acc["balance"]))
            if acc_balance < v:
                raise ValueError("INSUFFICIENT_ACCOUNT")

            # trava caixinha
            cur.execute(
                """
                select id, name, balance
                from pockets
                where user_id=%s and lower(name)=lower(%s)
                for update
                """,
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id = p["id"]
            pocket_name_canon = p["name"]

            # debita conta
            cur.execute(
                "update accounts set balance = balance - %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_account_balance = cur.fetchone()["balance"]

            # credita caixinha
            cur.execute(
                "update pockets set balance = balance + %s where id=%s returning balance",
                (v, pocket_id),
            )
            new_pocket_balance = cur.fetchone()["balance"]

            efeitos = {
                "delta_conta": float(-v),
                "delta_pocket": {"nome": pocket_name_canon, "delta": float(+v)},
                "delta_invest": None,
                "create_pocket": None,
                "create_investment": None,
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "deposito_caixinha", v, pocket_name_canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_account_balance, new_pocket_balance, pocket_name_canon

def delete_pocket(user_id: int, pocket_name: str):
    """
    Exclui caixinha se saldo for zero.
    Registra launch delete_pocket.
    Retorna: (launch_id, pocket_name_canon)
    """
    ensure_user(user_id)
    pocket_name = (pocket_name or "").strip()
    if not pocket_name:
        raise ValueError("EMPTY_NAME")

    criado_em = datetime.now()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, balance
                from pockets
                where user_id=%s and lower(name)=lower(%s)
                for update
                """,
                (user_id, pocket_name),
            )
            p = cur.fetchone()
            if not p:
                raise LookupError("POCKET_NOT_FOUND")

            pocket_id = p["id"]
            pocket_name_canon = p["name"]
            bal = Decimal(str(p["balance"]))

            if bal != Decimal("0"):
                raise ValueError("POCKET_NOT_ZERO")

            # apaga
            cur.execute("delete from pockets where id=%s", (pocket_id,))

            # ✅ guarda informação pra poder DESFAZER (recriar)
            efeitos = {
                "delta_conta": 0.0,
                "delta_pocket": None,
                "delta_invest": None,
                "create_pocket": None,
                "create_investment": None,
                "delete_pocket": {"nome": pocket_name_canon, "balance": 0.0},
                "delete_investment": None,
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "delete_pocket", Decimal("0"), pocket_name_canon, None, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, pocket_name_canon


def create_investment(user_id: int, name: str, rate: float, period: str, nota: str | None = None):
    """
    Cria investimento em investments e registra um launch create_investment.
    period: 'daily'|'monthly'|'yearly'
    rate: taxa do período em decimal (ex: 0.01 = 1%)
    Retorna: (launch_id, investment_name_canon)
    """
    ensure_user(user_id)

    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")

    if period not in ("daily", "monthly", "yearly"):
        raise ValueError("BAD_PERIOD")

    r = Decimal(str(rate))
    if r <= 0:
        raise ValueError("BAD_RATE")

    criado_em = datetime.now()
    last_date = date.today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # tenta inserir (unique user_id+name)
            try:
                cur.execute(
                    """
                    insert into investments(user_id, name, balance, rate, period, last_date)
                    values (%s,%s,0,%s,%s,%s)
                    returning name
                    """,
                    (user_id, name, r, period, last_date),
                )
                inv_name = cur.fetchone()["name"]
                created = True
            except psycopg.errors.UniqueViolation:
                conn.rollback()
                created = False
                # pega o nome canônico existente
                with get_conn() as conn2:
                    with conn2.cursor() as cur2:
                        cur2.execute(
                            "select name from investments where user_id=%s and lower(name)=lower(%s)",
                            (user_id, name),
                        )
                        row = cur2.fetchone()
                        if not row:
                            raise
                        inv_name = row["name"]

            if not created:
                return None, inv_name  # launch_id None = já existia

            efeitos = {
                "delta_conta": 0.0,
                "delta_pocket": None,
                "delta_invest": {"nome": inv_name, "delta": 0.0},
                "create_pocket": None,
                "create_investment": {"nome": inv_name, "rate": float(r), "period": period},
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "create_investment", Decimal("0"), inv_name, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, inv_name

def _business_days_between(d1: date, d2: date) -> int:
    """Número de dias úteis entre d1 (exclusive) e d2 (inclusive), assumindo seg-sex."""
    if d2 <= d1:
        return 0
    days = 0
    cur = d1
    while cur < d2:
        cur = cur.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            days += 1
    return days
def accrue_investment_db(cur, user_id: int, inv_id: int, today: date | None = None):
    """
    Atualiza (balance, last_date) do investment aplicando juros por dias úteis.
    Usa:
      daily  -> rate por dia útil
      monthly-> rate distribuído em 21 dias úteis
      yearly -> rate distribuído em 252 dias úteis
    """
    if today is None:
        today = date.today()

    cur.execute(
        "select id, balance, rate, period, last_date from investments where id=%s and user_id=%s for update",
        (inv_id, user_id),
    )
    inv = cur.fetchone()
    if not inv:
        raise LookupError("INV_NOT_FOUND")

    last_date = inv["last_date"]
    n = _business_days_between(last_date, today)
    if n <= 0:
        return inv["balance"]  # nada a fazer

    bal = Decimal(inv["balance"])
    rate = float(inv["rate"])
    period = inv["period"]

    if period == "daily":
        daily_rate = rate
    elif period == "monthly":
        daily_rate = (1.0 + rate) ** (1.0 / 21.0) - 1.0
    elif period == "yearly":
        daily_rate = (1.0 + rate) ** (1.0 / 252.0) - 1.0
    else:
        daily_rate = 0.0

    if daily_rate > 0:
        factor = (1.0 + daily_rate) ** n
        new_bal = Decimal(str(float(bal) * factor))
    else:
        new_bal = bal

    # salva
    cur.execute(
        "update investments set balance=%s, last_date=%s where id=%s",
        (new_bal, today, inv_id),
    )
    return new_bal

def investment_withdraw_to_account(user_id: int, investment_name: str, amount: float, nota: str | None = None):
    """
    Resgate em investimento (INVESTIMENTO -> CONTA), com juros acumulados antes.
    Retorna: (launch_id, new_account_balance, new_invest_balance, inv_name_canon)
    """
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now()
    today = date.today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # trava investimento e pega id
            cur.execute(
                """
                select id, name
                from investments
                where user_id=%s and lower(name)=lower(%s)
                for update
                """,
                (user_id, investment_name),
            )
            inv = cur.fetchone()
            if not inv:
                raise LookupError("INV_NOT_FOUND")

            inv_id = inv["id"]
            inv_name_canon = inv["name"]

            # ✅ aplica juros antes de mexer
            new_bal_before = accrue_investment_db(cur, user_id, inv_id, today=today)

            # saldo suficiente no investimento (depois dos juros)
            if new_bal_before < v:
                raise ValueError("INSUFFICIENT_INVEST")

            # debita investimento
            cur.execute(
                "update investments set balance = balance - %s where id=%s returning balance",
                (v, inv_id),
            )
            new_invest_balance = cur.fetchone()["balance"]

            # credita conta
            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_account_balance = cur.fetchone()["balance"]

            efeitos = {
                "delta_conta": +float(v),
                "delta_pocket": None,
                "delta_invest": {"nome": inv_name_canon, "delta": -float(v)},
                "create_pocket": None,
                "create_investment": None,
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "resgate_investimento", v, inv_name_canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_account_balance, new_invest_balance, inv_name_canon

def list_investments(user_id: int):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, balance, rate, period, last_date
                from investments
                where user_id=%s
                order by lower(name)
                """,
                (user_id,),
            )
            return cur.fetchall()


def accrue_all_investments(user_id: int):
    """
    Aplica juros em TODOS os investimentos do usuário (até hoje) e salva no DB.
    Retorna lista dos investimentos já atualizados.
    """
    ensure_user(user_id)
    today = date.today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from investments where user_id=%s for update",
                (user_id,),
            )
            rows = cur.fetchall()

            for r in rows:
                accrue_investment_db(cur, user_id, r["id"], today=today)

            # devolve dados atualizados
            cur.execute(
                """
                select id, name, balance, rate, period, last_date
                from investments
                where user_id=%s
                order by lower(name)
                """,
                (user_id,),
            )
            out = cur.fetchall()

        conn.commit()

    return out

def _canon_investment_name(cur, user_id: int, name: str) -> str | None:
    """Retorna o nome canônico (com caixa original) se existir."""
    cur.execute(
        """
        select name
        from investments
        where user_id = %s and lower(name) = lower(%s)
        """,
        (user_id, name),
    )
    row = cur.fetchone()
    return row["name"] if row else None

def _accrue_investment_row(cur, user_id: int, inv_name: str):
    """
    Atualiza juros do investimento no banco (composto pelo período).
    - daily: composta por dia
    - monthly: composta por mês
    - yearly: composta por ano
    """
    cur.execute(
        """
        select id, balance, rate, period, last_date
        from investments
        where user_id=%s and name=%s
        for update
        """,
        (user_id, inv_name),
    )
    inv = cur.fetchone()
    if not inv:
        raise LookupError("INV_NOT_FOUND")

    bal = Decimal(inv["balance"])
    rate = Decimal(inv["rate"])
    period = inv["period"]
    last = inv["last_date"]
    today = date.today()

    if last >= today:
        return bal  # nada a fazer

    # quantos "passos" de capitalização
    steps = 0
    if period == "daily":
        steps = (today - last).days
    elif period == "monthly":
        steps = (today.year - last.year) * 12 + (today.month - last.month)
    elif period == "yearly":
        steps = today.year - last.year
    else:
        steps = 0

    if steps <= 0:
        return bal

    # juros compostos: bal *= (1+rate)^steps
    factor = (Decimal("1") + rate) ** Decimal(steps)
    new_bal = (bal * factor)

    cur.execute(
        """
        update investments
        set balance=%s, last_date=%s
        where id=%s
        """,
        (new_bal, today, inv["id"]),
    )
    return new_bal

def investment_deposit_from_account(user_id: int, investment_name: str, amount: float, nota: str | None = None):
    """
    Aporte em investimento (CONTA -> INVESTIMENTO), com juros acumulados antes.
    Retorna: (launch_id, new_account_balance, new_invest_balance, inv_name_canon)
    """
    ensure_user(user_id)
    v = Decimal(str(amount))
    if v <= 0:
        raise ValueError("AMOUNT_INVALID")

    criado_em = datetime.now()
    today = date.today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # trava conta
            cur.execute("select balance from accounts where user_id=%s for update", (user_id,))
            acc = cur.fetchone()
            if not acc:
                raise RuntimeError("ACCOUNT_MISSING")
            if acc["balance"] < v:
                raise ValueError("INSUFFICIENT_ACCOUNT")

            # trava investimento e pega id
            cur.execute(
                """
                select id, name
                from investments
                where user_id=%s and lower(name)=lower(%s)
                for update
                """,
                (user_id, investment_name),
            )
            inv = cur.fetchone()
            if not inv:
                raise LookupError("INV_NOT_FOUND")

            inv_id = inv["id"]
            inv_name_canon = inv["name"]

            # ✅ aplica juros antes do aporte
            new_bal_before = accrue_investment_db(cur, user_id, inv_id, today=today)

            # debita conta
            cur.execute(
                "update accounts set balance = balance - %s where user_id=%s returning balance",
                (v, user_id),
            )
            new_account_balance = cur.fetchone()["balance"]

            # credita investimento
            cur.execute(
                "update investments set balance = balance + %s where id=%s returning balance",
                (v, inv_id),
            )
            new_invest_balance = cur.fetchone()["balance"]

            efeitos = {
                "delta_conta": -float(v),
                "delta_pocket": None,
                "delta_invest": {"nome": inv_name_canon, "delta": +float(v)},
                "create_pocket": None,
                "create_investment": None,
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "aporte_investimento", v, inv_name_canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_account_balance, new_invest_balance, inv_name_canon

def delete_launch_and_rollback(user_id: int, launch_id: int):
    """
    Deleta um lançamento e reverte seus efeitos no banco (atomicamente).
    Requer que launches.efeitos tenha os deltas no formato:
      {
        "delta_conta": -100.0,
        "delta_pocket": {"nome": "viagem", "delta": +100.0} | None,
        "delta_invest": {"nome": "cdb", "delta": +100.0} | None,
        "create_pocket": {"nome": "viagem"} | None,
        "create_investment": {"nome": "cdb"} | None
      }
    """
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) pega o lançamento
            cur.execute(
                """
                select id, tipo, valor, alvo, efeitos
                from launches
                where id=%s and user_id=%s
                """,
                (launch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                raise LookupError("NOT_FOUND")

            efeitos = row.get("efeitos")
            if efeitos is None:
                raise ValueError("lançamento sem 'efeitos' (não dá pra desfazer com segurança).")

            # psycopg geralmente já devolve jsonb como dict; se vier string, tenta parse
            if isinstance(efeitos, str):
                import json
                efeitos = json.loads(efeitos)

            delta_conta = Decimal(str(efeitos.get("delta_conta", 0)))
            delta_pocket = efeitos.get("delta_pocket")
            delta_invest = efeitos.get("delta_invest")
            create_pocket = efeitos.get("create_pocket")
            create_invest = efeitos.get("create_investment")
            delete_pocket = efeitos.get("delete_pocket")
            
            if delete_pocket:
                nome = delete_pocket.get("nome")
                bal0 = Decimal(str(delete_pocket.get("balance", 0)))
                if nome:
                    # desfazer delete_pocket = recriar a caixinha
                    cur.execute(
                        """
                        insert into pockets(user_id, name, balance)
                        values (%s,%s,%s)
                        on conflict (user_id, name) do nothing
                        """,
                        (user_id, nome, bal0),
                    )

            # 2) reverte conta: desfazer = subtrair o delta que foi aplicado
            if delta_conta != 0:
                cur.execute(
                    "update accounts set balance = balance - %s where user_id=%s",
                    (delta_conta, user_id),
                )

            # 3) reverte caixinha
            if delta_pocket:
                nome = delta_pocket.get("nome")
                dp = Decimal(str(delta_pocket.get("delta", 0)))
                if not nome:
                    raise ValueError("delta_pocket inválido (sem nome).")

                # desfazer = balance - dp
                cur.execute(
                    """
                    update pockets
                    set balance = balance - %s
                    where user_id=%s and lower(name)=lower(%s)
                    """,
                    (dp, user_id, nome),
                )

            # 4) reverte investimento
            if delta_invest:
                nome = delta_invest.get("nome")
                di = Decimal(str(delta_invest.get("delta", 0)))
                if not nome:
                    raise ValueError("delta_invest inválido (sem nome).")

                # desfazer = balance - di
                cur.execute(
                    """
                    update investments
                    set balance = balance - %s
                    where user_id=%s and lower(name)=lower(%s)
                    """,
                    (di, user_id, nome),
                )

            # 5) se o lançamento foi criação de caixinha/investimento, desfazer = deletar o registro criado
            # (isso só funciona se você registrar create_pocket/create_investment nos efeitos quando criar)
            if create_pocket:
                nome = create_pocket.get("nome")
                if nome:
                    cur.execute(
                        "delete from pockets where user_id=%s and lower(name)=lower(%s)",
                        (user_id, nome),
                    )

            if create_invest:
                nome = create_invest.get("nome")
                if nome:
                    cur.execute(
                        "delete from investments where user_id=%s and lower(name)=lower(%s)",
                        (user_id, nome),
                    )

            # 6) apaga o lançamento
            cur.execute(
                "delete from launches where id=%s and user_id=%s",
                (launch_id, user_id),
            )

        conn.commit()

