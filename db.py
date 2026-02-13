# db.py
import os
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb, Json  # <-- ADICIONA ISSO
from decimal import Decimal
from datetime import datetime, date
import math
from datetime import timedelta, timezone
import requests
from utils_date import _tz, today_tz, billing_period_for_close_day
from uuid import uuid4
import calendar




def get_conn():
    database_url = os.getenv("DATABASE_URL")  # Railway injeta isso quando você adiciona Postgres

    if not database_url:
        raise RuntimeError("DATABASE_URL não está definido.")
    return psycopg.connect(database_url, row_factory=dict_row)

def init_db():
    ddl_statements = [
        """
        create table if not exists users (
          id bigint primary key,
          created_at timestamptz default now()
        )
        """,
        """
        create table if not exists accounts (
          user_id bigint primary key references users(id) on delete cascade,
          balance numeric not null default 0
        )
        """,
        """
        create table if not exists pockets (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          name text not null,
          balance numeric not null default 0,
          created_at timestamptz default now(),
          unique(user_id, name)
        )
        """,
        """
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
        )
        """,
        """
        create table if not exists launches (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          tipo text not null,
          valor numeric not null,
          alvo text,
          nota text,
          criado_em timestamptz not null default now(),
          efeitos jsonb
        )
        """,
        """
        create index if not exists idx_launches_user_time
          on launches(user_id, criado_em desc)
        """,
        """
        create table if not exists pending_actions (
          user_id bigint primary key references users(id) on delete cascade,
          action_type text not null,
          payload jsonb not null,
          created_at timestamptz not null default now(),
          expires_at timestamptz not null
        )
        """,
        """
        create table if not exists user_category_rules (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          keyword text not null,
          category text not null,
          created_at timestamptz default now(),
          unique (user_id, keyword)
        )
        """,
        """
        create table if not exists market_rates (
          code text not null,
          ref_date date not null,
          value numeric not null,
          created_at timestamptz default now(),
          primary key (code, ref_date)
        )
        """,

        """ 
        create table if not exists credit_cards (
        id bigserial primary key,
        user_id bigint not null references users(id) on delete cascade,
        name text not null,
        closing_day int not null,  -- 1..28
        due_day int not null,      -- 1..28
        created_at timestamptz default now(),
        unique(user_id, name)
        )
        """,
        
        """ 
        create table if not exists credit_bills (
        id bigserial primary key,
        card_id bigint not null references credit_cards(id) on delete cascade,
        period_start date not null,
        period_end date not null,
        status text not null default 'open', -- open|closed|paid
        total numeric not null default 0,
        paid_at timestamptz,
        created_at timestamptz default now(),
        unique(card_id, period_start, period_end)
        );
        """,
        
        """ 
        create table if not exists credit_transactions (
        id bigserial primary key,
        bill_id bigint not null references credit_bills(id) on delete cascade,
        user_id bigint not null references users(id) on delete cascade,
        card_id bigint not null references credit_cards(id) on delete cascade,
        tipo text not null default 'credito', -- ou 'estorno'
        valor numeric not null,
        categoria text,
        nota text,
        purchased_at date not null,
        created_at timestamptz default now()
        );
        """
        
        """ create index if not exists idx_credit_tx_user_date on credit_transactions(user_id, purchased_at desc); """

        """
        create table if not exists credit_cards (
        id bigserial primary key,
        user_id bigint not null references users(id) on delete cascade,
        name text not null,
        closing_day int not null check (closing_day between 1 and 28),
        due_day int not null check (due_day between 1 and 28),
        created_at timestamptz default now(),
        unique(user_id, name)
        );
        """

        """ 
        create table if not exists credit_bills (
        id bigserial primary key,
        card_id bigint not null references credit_cards(id) on delete cascade,
        period_start date not null,
        period_end date not null,
        status text not null default 'open', -- open | closed | paid
        total numeric not null default 0,
        paid_at timestamptz,
        created_at timestamptz default now(),
        unique(card_id, period_start, period_end)
        );
        """

        """
        create table if not exists credit_transactions (
        id bigserial primary key,
        bill_id bigint not null references credit_bills(id) on delete cascade,
        user_id bigint not null references users(id) on delete cascade,
        card_id bigint not null references credit_cards(id) on delete cascade,
        tipo text not null default 'credito', -- credito | estorno
        valor numeric not null,
        categoria text,
        nota text,
        purchased_at date not null,
        created_at timestamptz default now()
        );
        """

        """ 
        create index if not exists idx_credit_tx_user_date
        on credit_transactions(user_id, purchased_at desc);
        """

        """
        alter table users add column if not exists default_card_id bigint;
        """

        """
        alter table credit_bills add column if not exists paid_amount numeric not null default 0;
        alter table credit_bills add column if not exists closed_at timestamptz;
        """

        """
        alter table credit_transactions add column if not exists group_id uuid;
        alter table credit_transactions add column if not exists installment_no int;
        alter table credit_transactions add column if not exists installments_total int;
        alter table credit_transactions add column if not exists is_refund boolean not null default false;
        """

        """
        alter table users add column if not exists default_card_id bigint;
        alter table users add column if not exists reminders_enabled boolean not null default false;
        alter table users add column if not exists reminders_days_before int not null default 3;    
        """



]

    with get_conn() as conn:
        with conn.cursor() as cur:
            for i, stmt in enumerate(ddl_statements, 1):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    # loga qual statement quebrou (pra aparecer no Railway)
                    print(f"[init_db] erro no statement #{i}: {e}")
                    print(stmt)
                    raise
        conn.commit()
    print("[init_db] OK")


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

def add_launch_and_update_balance(
    user_id: int,
    tipo: str,
    valor: float,
    alvo: str | None,
    nota: str | None,
    criado_em: datetime | None = None,   # 👈 novo
):
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
        raise ValueError(f"tipo inválido: {tipo}")

    # 👇 se o parser não mandou data, usa agora
    if criado_em is None:
        criado_em = datetime.now(_tz())

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

    criado_em = datetime.now(_tz())


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

    criado_em = datetime.now(_tz())


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

    criado_em = datetime.now(_tz())


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

    criado_em = datetime.now(_tz())


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

    criado_em = datetime.now(_tz())

    last_date = datetime.now(_tz()).date()

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

def _fmt_ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")

def _fetch_sgs_series_json(series_code: int, start: date, end: date) -> list[dict]:
    # BCB SGS JSON interface (semppre com filtro de datas)
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{series_code}/dados"
    params = {
        "formato": "json",
        "dataInicial": _fmt_ddmmyyyy(start),
        "dataFinal": _fmt_ddmmyyyy(end),
    }

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[WARN] Falha ao buscar série SGS {series_code} no BCB: {e}")
        return []  # não quebra o bot

def _get_cdi_daily_map(cur, start: date, end: date) -> dict[date, float]:
    """
    Retorna dict {date: cdi_percent_per_day}
    - usa cache em market_rates
    - busca no BCB o que estiver faltando
    """
    if end <= start:
        return {}

    # 1) pega o que já tem no cache
    cur.execute(
        """
        select ref_date, value
        from market_rates
        where code='CDI' and ref_date >= %s and ref_date <= %s
        order by ref_date
        """,
        (start, end),
    )
    cached = {row["ref_date"]: float(row["value"]) for row in cur.fetchall()}

    # 2) se faltou algo, busca do BCB e salva
    # (buscar o range inteiro é simples e barato; o BCB devolve só dias úteis/feriados úteis)
    data = _fetch_sgs_series_json(12, start, end)  # série 12 = CDI (% p.d.)

    if not isinstance(data, list):
        print(f"[WARN] Resposta inesperada do BCB (tipo={type(data)}): {data}")
        return cached

    # se o BCB falhar/voltar vazio, só usa o cache e não quebra o bot
    if not data:
        return cached

    to_upsert = []
    for item in data:
        # garante que item é dict (às vezes pode vir lixo)
        if not isinstance(item, dict):
            print(f"[WARN] Item inválido do BCB ignorado (tipo={type(item)}): {item}")
            continue

        try:
            raw_date = item.get("data")
            raw_val = item.get("valor")

            if not raw_date or raw_val is None:
                continue

            d = datetime.strptime(raw_date, "%d/%m/%Y").date()
            v = float(str(raw_val).replace(",", "."))

            if d not in cached:
                to_upsert.append((d, v))
            cached[d] = v
        except Exception as e:
            print(f"[WARN] Item inválido do BCB ignorado: {item} | erro={e}")
            continue

    if to_upsert:
        cur.executemany(
            """
            insert into market_rates(code, ref_date, value)
            values ('CDI', %s, %s)
            on conflict (code, ref_date) do update set value=excluded.value
            """,
            to_upsert,
        )

    return cached

def get_latest_cdi(cur) -> tuple[date, float] | None:
    """
    Retorna (data, valor_percent_ao_dia) da CDI mais recente no cache.
    Se não houver cache recente, busca do BCB (últimos 10 dias) e salva.
    """
    # tenta pegar do cache
    cur.execute(
        """
        select ref_date, value
        from market_rates
        where code='CDI'
        order by ref_date desc
        limit 1
        """
    )
    row = cur.fetchone()
    if row:
        return row["ref_date"], float(row["value"])

    # fallback: busca últimos 10 dias do BCB e cacheia
    today = datetime.now(_tz()).date()
    start = today - timedelta(days=10)

    data = _fetch_sgs_series_json(12, start, today)  # série 12 = CDI (% a.d.)
    if not data:
        return None

    latest = None
    for item in data:
        d = datetime.strptime(item["data"], "%d/%m/%Y").date()
        v = float(str(item["valor"]).replace(",", "."))
        latest = (d, v)

    if latest:
        cur.execute(
            """
            insert into market_rates(code, ref_date, value)
            values ('CDI', %s, %s)
            on conflict (code, ref_date) do update set value=excluded.value
            """,
            latest,
        )
        return latest

    return None

def get_latest_cdi_aa(cur) -> tuple[date, float] | None:
    """
    CDI a.a. (base 252) direto do SGS/BCB (série 4389).
    Cacheia em market_rates com code='CDI_AA'.
    """
    cur.execute(
        """
        select ref_date, value
        from market_rates
        where code='CDI_AA'
        order by ref_date desc
        limit 1
        """
    )
    row = cur.fetchone()
    if row:
        return row["ref_date"], float(row["value"])

    today = datetime.now(_tz()).date()
    start = today - timedelta(days=10)

    data = _fetch_sgs_series_json(4389, start, today)  # CDI a.a. :contentReference[oaicite:0]{index=0}
    if not data:
        return None

    latest = None
    for item in data:
        d = datetime.strptime(item["data"], "%d/%m/%Y").date()
        v = float(str(item["valor"]).replace(",", "."))
        latest = (d, v)

    if latest:
        cur.execute(
            """
            insert into market_rates(code, ref_date, value)
            values ('CDI_AA', %s, %s)
            on conflict (code, ref_date) do update set value=excluded.value
            """,
            latest,
        )
        return latest

    return None

def get_latest_cdi_daily_pct() -> float:
    """
    Retorna CDI diária em % ao dia (ex: 0.0550 significa 0.0550% ao dia).
    Busca do BCB série 12 e usa o último valor disponível.
    """
    today = datetime.now(_tz()).date()
    start = today - timedelta(days=10)

    data = _fetch_sgs_series_json(12, start, today)  # CDI diária % a.d.
    if not data:
        raise RuntimeError("CDI_DAILY_NOT_AVAILABLE")

    latest = None
    for item in data:
        v = float(str(item["valor"]).replace(",", "."))
        latest = v

    if latest is None:
        raise RuntimeError("CDI_DAILY_NOT_AVAILABLE")

    return float(latest)


def accrue_investment_db(cur, user_id: int, inv_id: int, today: date | None = None):
    """
    Atualiza (balance, last_date) do investment aplicando juros por dias úteis.
    daily  -> rate por dia útil
    monthly-> rate distribuído em 21 dias úteis
    yearly -> rate distribuído em 252 dias úteis
    cdi    -> aplica CDI diária do período (mapa), multiplicada pelo "mult" (ex 1.10 = 110% CDI)
    """
    if today is None:
        today = datetime.now(_tz()).date()

    cur.execute(
        "select id, balance, rate, period, last_date from investments where id=%s and user_id=%s for update",
        (inv_id, user_id),
    )
    inv = cur.fetchone()
    if not inv:
        raise LookupError("INV_NOT_FOUND")

    last_date = inv["last_date"]
    if last_date is None:
        # se quiser, você pode setar last_date=today e retornar sem render
        return Decimal(inv["balance"])

    n = _business_days_between(last_date, today)
    if n <= 0:
        return Decimal(inv["balance"])

    bal = Decimal(inv["balance"])
    period = inv["period"]
    rate = float(inv["rate"])

    # =========================
    # CDI
    # =========================
    if period == "cdi":
        mult = float(inv["rate"])  # 1.00=100% CDI, 1.10=110% CDI

        start = last_date + timedelta(days=1)
        end = today

        cdi_map = _get_cdi_daily_map(cur, start, end)  # {date: pct_ao_dia}

        factor = 1.0
        # IMPORTANTE: iterar em ordem de data
        for d in sorted(cdi_map.keys()):
            cdi_pct_per_day = cdi_map[d]
            factor *= (1.0 + (cdi_pct_per_day / 100.0) * mult)

        new_bal = Decimal(str(float(bal) * factor))

    # =========================
    # Não-CDI
    # =========================
    else:
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

    # =========================
    # salva (para TODOS os casos)
    # =========================
    cur.execute(
        "update investments set balance=%s, last_date=%s where id=%s and user_id=%s",
        (new_bal, today, inv_id, user_id),
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

    criado_em = datetime.now(_tz())

    today = datetime.now(_tz()).date()

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
    today = datetime.now(_tz()).date()

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
    today = datetime.now(_tz()).date()

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

    criado_em = datetime.now(_tz())

    today = datetime.now(_tz()).date()

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
    efeitos = {
        "delta_conta": 0.0,
        "delta_pocket": None,
        "delta_invest": None,
        "create_pocket": None,
        "create_investment": None,
        "delete_pocket": {"nome": pocket_name_canon, "balance": 0.0},
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
            delete_investment = efeitos.get("delete_investment")
            create_invest = efeitos.get("create_investment")

            if create_invest:
                nome = create_invest.get("nome")
                if nome:
                    cur.execute(
                        """
                        delete from investments
                        where user_id=%s and lower(name)=lower(%s) and balance=0
                        """,
                        (user_id, nome),
                    )

            if delete_investment:
                nome = delete_investment.get("nome")
                bal0 = Decimal(str(delete_investment.get("balance", 0)))
                rate = Decimal(str(delete_investment.get("rate", 0)))
                period = delete_investment.get("period", "monthly")
                last_date_str = delete_investment.get("last_date")

                if nome:
                    ld = date.fromisoformat(last_date_str) if last_date_str else datetime.now(_tz()).date()
                    cur.execute(
                        """
                        insert into investments(user_id, name, balance, rate, period, last_date)
                        values (%s,%s,%s,%s,%s,%s)
                        on conflict (user_id, name) do nothing
                        """,
                        (user_id, nome, bal0, rate, period, ld),
                    )


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

def create_investment_db(user_id: int, name: str, rate: float, period: str, nota: str | None = None):
    """
    Cria investimento e registra launch create_investment.
    Retorna: (launch_id, investment_id, canon_name)
      - se já existir: (None, investment_id, canon_name)
    """
    ensure_user(user_id)

    name = (name or "").strip()
    if not name:
        raise ValueError("EMPTY_NAME")

    if period not in ("daily", "monthly", "yearly", "cdi"):
        raise ValueError("INVALID_PERIOD")


    r = Decimal(str(rate))
    if r <= 0:
        raise ValueError("INVALID_RATE")

    criado_em = datetime.now(_tz())

    today = date.today()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investments(user_id, name, balance, rate, period, last_date)
                values (%s,%s,0,%s,%s,%s)
                on conflict (user_id, name) do nothing
                returning id, name
                """,
                (user_id, name, r, period, today),
            )
            row = cur.fetchone()

            if row:
                inv_id = row["id"]
                canon = row["name"]
                created = True
            else:
                created = False
                cur.execute(
                    """
                    select id, name
                    from investments
                    where user_id=%s and lower(name)=lower(%s)
                    """,
                    (user_id, name),
                )
                r2 = cur.fetchone()
                if not r2:
                    raise RuntimeError("INVESTMENT_LOOKUP_FAILED")
                inv_id = r2["id"]
                canon = r2["name"]

            if not created:
                conn.commit()
                return None, inv_id, canon

            efeitos = {
                "delta_conta": 0.0,
                "delta_pocket": None,
                "delta_invest": None,
                "create_pocket": None,
                "create_investment": {"nome": canon},
                "delete_pocket": None,
                "delete_investment": None,
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "create_investment", Decimal("0"), canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, inv_id, canon


def delete_investment(user_id: int, investment_name: str, nota: str | None = None):
    """
    Exclui investimento se saldo for zero.
    Registra launch delete_investment.
    Retorna: (launch_id, canon_name)
    """
    ensure_user(user_id)

    investment_name = (investment_name or "").strip()
    if not investment_name:
        raise ValueError("EMPTY_NAME")

    criado_em = datetime.now(_tz())


    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, name, balance, rate, period, last_date
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
            canon = inv["name"]
            bal = Decimal(str(inv["balance"]))

            if bal != Decimal("0"):
                raise ValueError("INV_NOT_ZERO")

            # apaga
            cur.execute("delete from investments where id=%s", (inv_id,))

            # ✅ guarda dados pra poder DESFAZER (recriar igual)
            efeitos = {
                "delta_conta": 0.0,
                "delta_pocket": None,
                "delta_invest": None,
                "create_pocket": None,
                "create_investment": None,
                "delete_pocket": None,
                "delete_investment": {
                    "nome": canon,
                    "balance": 0.0,
                    "rate": float(inv["rate"]),
                    "period": inv["period"],
                    "last_date": inv["last_date"].isoformat() if inv["last_date"] else datetime.now(_tz()).date().isoformat(),
                },
            }

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos)
                values (%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "delete_investment", Decimal("0"), canon, nota, criado_em, Jsonb(efeitos)),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, canon

def set_pending_action(user_id: int, action_type: str, payload: dict, minutes: int = 10):
    """
    Cria/atualiza uma ação pendente de confirmação (persistente no Postgres).
    """
    ensure_user(user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into pending_actions (user_id, action_type, payload, expires_at)
                values (%s, %s, %s, %s)
                on conflict (user_id)
                do update set action_type = excluded.action_type,
                              payload = excluded.payload,
                              created_at = now(),
                              expires_at = excluded.expires_at
            """, (user_id, action_type, Jsonb(payload), expires_at))
        conn.commit()

def get_pending_action(user_id: int):
    """
    Retorna a ação pendente se existir e não estiver expirada. Senão, retorna None.
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select user_id, action_type, payload, created_at, expires_at
                from pending_actions
                where user_id = %s
            """, (user_id,))
            row = cur.fetchone()
        conn.commit()

    if not row:
        return None

    # expirada?
    if row["expires_at"] <= datetime.now(timezone.utc):
        clear_pending_action(user_id)
        return None

    return row

def clear_pending_action(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from pending_actions where user_id = %s", (user_id,))
        conn.commit()


def set_pending_action(user_id: int, action_type: str, payload: dict, minutes: int = 10):
    ensure_user(user_id)

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)


    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                create table if not exists pending_actions (
                    user_id bigint primary key references users(id) on delete cascade,
                    action_type text not null,
                    payload jsonb not null,
                    created_at timestamptz not null default now(),
                    expires_at timestamptz not null
                );
            """)
            cur.execute("""
                insert into pending_actions (user_id, action_type, payload, expires_at)
                values (%s, %s, %s, %s)
                on conflict (user_id)
                do update set action_type = excluded.action_type,
                              payload = excluded.payload,
                              created_at = now(),
                              expires_at = excluded.expires_at
            """, (user_id, action_type, Jsonb(payload), expires_at))
        conn.commit()

def export_launches(user_id: int, start_date: date | None = None, end_date: date | None = None):
    """
    Exporta lançamentos do usuário em um período.
    - start_date: data inicial (inclusive)
    - end_date: data final (inclusive)
    """
    ensure_user(user_id)

    params = [user_id]
    where = ["user_id=%s"]

    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        where.append("criado_em >= %s")
        params.append(start_dt)

    if end_date:
        end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
        where.append("criado_em < %s")
        params.append(end_excl)

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

#pega os lancamentos por periodo
def get_launches_by_period(user_id: int, start_date: date, end_date: date):
    ensure_user(user_id)

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    sql = """
        select id, tipo, valor, alvo, nota, criado_em
        from launches
        where user_id=%s
          and criado_em >= %s
          and criado_em < %s
        order by criado_em asc, id asc
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, start_dt, end_excl))
            return cur.fetchall()
        

# pega o resumo de lancamentos por periodo
def get_summary_by_period(user_id: int, start_date: date, end_date: date):
    """
    Retorna soma por tipo no período [start_date, end_date] (inclusive),
    usando criado_em como referência (mesma lógica de get_launches_by_period).
    """
    ensure_user(user_id)

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    sql = """
        select tipo, coalesce(sum(valor), 0) as total
        from launches
        where user_id=%s
          and criado_em >= %s
          and criado_em < %s
        group by tipo
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, start_dt, end_excl))
            rows = cur.fetchall()  # ex: [("despesa", Decimal("10.50")), ("receita", ...)]

    # defaults para não quebrar o output
    out = {"receita": 0.0, "despesa": 0.0, "aporte_investimento": 0.0}
    for tipo, total in rows:
        if tipo in out:
            out[tipo] = float(total or 0)

    return out

        
# Busca uma categoria memorizada pelo user_id com base no texto (keyword contida no texto)
def get_memorized_category(user_id: int, text: str) -> str | None:
    text = (text or "").lower()
    if not text:
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT keyword, category
                FROM user_category_rules
                WHERE user_id = %s
                ORDER BY LENGTH(keyword) DESC
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    for kw, cat in rows:
        if kw and kw.lower() in text:
            return cat
    return None


# Salva/atualiza uma regra memorizada (keyword -> category) para um usuário
def upsert_category_rule(user_id: int, keyword: str, category: str) -> None:
    keyword = (keyword or "").strip().lower()

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO user_category_rules (user_id, keyword, category)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, keyword)
        DO UPDATE SET category = EXCLUDED.category
        """,
        (user_id, keyword, category),
    )

    conn.commit()
    cur.close()

def create_card(user_id: int, name: str, closing_day: int, due_day: int) -> int:
    ensure_user(user_id)
    name = (name or "").strip()
    if not name:
        raise ValueError("nome do cartão vazio")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into credit_cards (user_id, name, closing_day, due_day)
                values (%s, %s, %s, %s)
                on conflict (user_id, name)
                do update set closing_day=excluded.closing_day, due_day=excluded.due_day
                returning id
                """,
                (user_id, name, int(closing_day), int(due_day)),
            )
            card_id = cur.fetchone()["id"]
        conn.commit()
    return card_id


def get_card_id_by_name(user_id: int, name: str) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from credit_cards where user_id=%s and name=%s", (user_id, name))
            row = cur.fetchone()
            return row["id"] if row else None


def set_default_card(user_id: int, card_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update users set default_card_id=%s where id=%s", (card_id, user_id))
        conn.commit()


def get_default_card_id(user_id: int) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select default_card_id from users where id=%s", (user_id,))
            row = cur.fetchone()
            return row["default_card_id"] if row else None

# Retorna o período (início, fim) da fatura para um mês/ano e dia de fechamento.
# Ex: closing_day=10 -> período 01/mm até 10/mm.
def _safe_date(y: int, m: int, d: int) -> date:
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d, last))


def _prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


# Período correto:
# fechamento dia X => período vai de (X+1 do mês anterior) até X do mês atual
def _bill_period_for_purchase(purchased_at: date, closing_day: int):
    y, m = purchased_at.year, purchased_at.month

    # se a compra foi depois do fechamento, cai na fatura do mês seguinte
    end_this = _safe_date(y, m, closing_day)
    if purchased_at > end_this:
        if m == 12:
            y, m = y + 1, 1

        else:
            m = m + 1

    period_end = _safe_date(y, m, closing_day)
    py, pm = _prev_month(y, m)
    prev_end = _safe_date(py, pm, closing_day)
    period_start = prev_end + timedelta(days=1)

    return period_start, period_end

def get_or_create_open_bill(user_id: int, card_id: int, ref_date: date) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            # closing_day do cartão
            cur.execute(
                """
                select closing_day, user_id
                from credit_cards
                where id=%s
                limit 1
                """,
                (card_id,),
            )
            card = cur.fetchone()
            if not card:
                raise ValueError("Cartão não encontrado.")

            # segurança extra: garante que card é do user
            if int(card["user_id"]) != int(user_id):
                raise ValueError("Cartão não pertence a este usuário.")

            closing_day = int(card["closing_day"])
            period_start, period_end = billing_period_for_close_day(ref_date, closing_day)

            # tenta criar; se já existe, pega o id (UPSERT)
            cur.execute(
                """
                insert into credit_bills (user_id, card_id, period_start, period_end, total, status)
                values (%s, %s, %s, %s, 0, 'open')
                on conflict (card_id, period_start, period_end)
                do update set
                    user_id = excluded.user_id
                returning id, status
                """,
                (user_id, card_id, period_start, period_end),
            )
            row = cur.fetchone()
            bill_id = int(row["id"])
            status = (row.get("status") or "").lower()

            # se já existia e estava paga/fechada, reabre (pra não perder compras)
            if status in ("paid", "closed"):
                cur.execute(
                    """
                    update credit_bills
                    set status='open'
                    where id=%s
                    """,
                    (bill_id,),
                )

        conn.commit()

    return bill_id

def add_credit_purchase(
    user_id: int,
    card_id: int,
    valor: float,
    categoria: str | None,
    nota: str | None,
    purchased_at: date,
):
    ensure_user(user_id)

    # ✅ pega/cria a fatura correta do ciclo do cartão
    bill_id = get_or_create_open_bill(user_id, card_id, purchased_at)

    v = Decimal(str(valor))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into credit_transactions (bill_id, user_id, card_id, valor, categoria, nota, purchased_at)
                values (%s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (bill_id, user_id, card_id, v, categoria, nota, purchased_at),
            )
            tx_id = cur.fetchone()["id"]

            cur.execute(
                """
                update credit_bills
                set total = total + %s
                where id=%s and user_id=%s
                returning total
                """,
                (v, bill_id, user_id),
            )
            total = cur.fetchone()["total"]

        conn.commit()

    return tx_id, total, bill_id


# paga fatura em aberta e nao fecha a fatura do mes
def get_open_bill_summary(user_id: int, card_id: int, as_of: date | None = None):
    if as_of is None:
        as_of = today_tz()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) pega closing_day do cartão (e garante que é do usuário)
            cur.execute(
                """
                select closing_day
                from credit_cards
                where id=%s and user_id=%s
                limit 1
                """,
                (card_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None

            closing_day = int(row["closing_day"])

            # 2) calcula período correto
            period_start, period_end = billing_period_for_close_day(as_of, closing_day)

            # 3) busca a fatura open correspondente ao período correto
            cur.execute(
                """
                select id, period_start, period_end, total
                from credit_bills
                where user_id=%s
                  and card_id=%s
                  and status='open'
                  and period_start=%s
                  and period_end=%s
                limit 1
                """,
                (user_id, card_id, period_start, period_end),
            )
            bill = cur.fetchone()
            if not bill:
                return None

            # 4) itens da fatura
            cur.execute(
                """
                select valor, categoria, nota, purchased_at
                from credit_transactions
                where user_id=%s
                  and bill_id=%s
                order by purchased_at desc
                limit 50
                """,
                (user_id, bill["id"]),
            )
            items = cur.fetchall()

    return bill, items

# paga fatura em aberto
def pay_open_bill(user_id: int, card_id: int, card_name: str, as_of: date | None = None):
    """
    Compatibilidade com o handler antigo:
    pagar fatura (sem valor) = paga tudo em aberto da fatura do período correto.
    Retorna (total_pago, launch_id, new_balance) igual antes.
    """
    res = pay_bill_amount(user_id, card_id, card_name, amount=None, as_of=as_of)
    if not res:
        return None
    if isinstance(res, dict) and res.get("error"):
        # não deveria acontecer com amount=None, mas por segurança
        return None
    return res["paid"], res["launch_id"], res["new_balance"]

def list_cards(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select c.id, c.name, c.closing_day, c.due_day,
                       (u.default_card_id = c.id) as is_default
                from credit_cards c
                left join users u on u.id = c.user_id
                where c.user_id = %s
                order by c.name
                """,
                (user_id,),
            )
            rows = cur.fetchall()
    return rows

# Soma 'delta' meses a um par (ano, mês) e retorna o novo (ano, mês).
# Ex: (2026, 12) + 1 -> (2027, 1)
def add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    m2 = m + delta
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    return y2, m2

    # Dada a data da compra e o dia de fechamento do cartão, calcula
    # em qual período de fatura a compra deve cair.
    # Regra:
    #  - até o fechamento -> fatura do mês corrente
    #  - após o fechamento -> próxima fatura

def _last_day_of_month(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def bill_period_for_month(year: int, month: int, closing_day: int) -> tuple[date, date]:
    """
    Período da fatura que FECHA no dia `closing_day` do mês (year, month).

    Ex: closing_day=10, year=2026, month=4  ->  11/03/2026 a 10/04/2026
    """
    # fim = closing_day do mês (clamp se mês não tem esse dia)
    end_day = min(int(closing_day), _last_day_of_month(year, month))
    period_end = date(year, month, end_day)

    # início = closing_day+1 do mês anterior (clamp também)
    prev_y, prev_m = add_months(year, month, -1)
    start_day = min(int(closing_day) + 1, _last_day_of_month(prev_y, prev_m))
    period_start = date(prev_y, prev_m, start_day)

    return period_start, period_end

    """
    Busca uma fatura por período (card_id + period_start + period_end).
    Se não existir, cria uma fatura aberta com total=0 e retorna o id.
    """
def get_or_create_bill_by_period(user_id: int, card_id: int, period_start: date, period_end: date) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into credit_bills (user_id, card_id, period_start, period_end, status, total, paid_amount)
                values (%s, %s, %s, %s, 'open', 0, 0)
                on conflict (card_id, period_start, period_end)
                do update set user_id = excluded.user_id
                returning id
                """,
                (user_id, card_id, period_start, period_end),
            )
            bid = cur.fetchone()["id"]
        conn.commit()
    return int(bid)


    # Registra uma compra parcelada no crédito.
    # Divide o valor total em N parcelas e cria uma transação em cada
    # fatura futura correspondente (mês a mês).
def add_credit_purchase_installments(
    user_id: int,
    card_id: int,
    valor_total: float,
    categoria: str | None,
    nota: str | None,
    purchased_at: date,
    installments: int,
):
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select closing_day from credit_cards where id=%s", (card_id,))
            closing_day = cur.fetchone()["closing_day"]

    group_id = uuid4()
    vtotal = Decimal(str(valor_total))
    n = max(1, int(installments))
    vparc = (vtotal / Decimal(n)).quantize(Decimal("0.01"))

    # ajusta centavos na última parcela pra somar certinho
    parcelas = [vparc] * n
    diff = vtotal - sum(parcelas)
    parcelas[-1] = (parcelas[-1] + diff).quantize(Decimal("0.01"))

    # período base (mês/ano)
    # a 1ª parcela deve cair na MESMA fatura da compra
    ps0, pe0 = _bill_period_for_purchase(purchased_at, closing_day)
    base_y, base_m = pe0.year, pe0.month

    tx_ids = []

    with get_conn() as conn:
        with conn.cursor() as cur:
            for i in range(n):
                y2, m2 = add_months(base_y, base_m, i)
                ps, pe = bill_period_for_month(y2, m2, closing_day)
                bill_id = get_or_create_bill_by_period(user_id, card_id, ps, pe)

                cur.execute(
                    """
                    insert into credit_transactions
                      (bill_id, user_id, card_id, valor, categoria, nota, purchased_at,
                       group_id, installment_no, installments_total, is_refund)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)
                    returning id
                    """,
                    (bill_id, user_id, card_id, parcelas[i], categoria, nota, purchased_at, group_id, i+1, n),
                )
                tx_id = cur.fetchone()["id"]
                tx_ids.append(tx_id)

                cur.execute(
                    "update credit_bills set total = total + %s where id=%s",
                    (parcelas[i], bill_id),
                )
        conn.commit()

    total_bill = float(vtotal)  # total do parcelamento (valor_total)
    return {"group_id": str(group_id), "tx_ids": tx_ids}, total_bill


#estorno (transação negativa na fatura)
def add_credit_refund(
    user_id: int,
    card_id: int,
    valor: float,
    categoria: str | None,
    nota: str | None,
    purchased_at: date,
):
    ensure_user(user_id)
    bill_id = get_or_create_open_bill(card_id, purchased_at)

    v = Decimal(str(valor))
    if v <= 0:
        raise ValueError("valor do estorno deve ser > 0")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into credit_transactions
                  (bill_id, user_id, card_id, tipo, valor, categoria, nota, purchased_at, is_refund)
                values (%s,%s,%s,'estorno',%s,%s,%s,%s,true)
                returning id
                """,
                (bill_id, user_id, card_id, -v, categoria, nota, purchased_at),
            )
            tx_id = cur.fetchone()["id"]

            cur.execute(
                "update credit_bills set total = total + %s where id=%s returning total",
                (-v, bill_id),
            )
            total = cur.fetchone()["total"]
        conn.commit()

    return tx_id, total

#pagar fatura em aberto

def pay_bill_amount(
    user_id: int,
    card_id: int,
    card_name: str,
    amount: float | None,
    as_of: date | None = None,
):
    """
    Paga a fatura do período correto (calculado via closing_day + as_of).
    - Se amount=None: paga tudo que está em aberto.
    - Se amount > em_aberto: NÃO EXECUTA e retorna error amount_too_high.
    Retorna dict:
      {"paid": float, "launch_id": int, "new_balance": float}
    ou erro:
      {"error": "amount_too_high", "due":..., "total":..., "paid_amount":...}
      {"error": "invalid_amount"}
    """
    if as_of is None:
        as_of = today_tz()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) pega closing_day do cartão (garantindo user_id)
            cur.execute(
                """
                select closing_day
                from credit_cards
                where id=%s and user_id=%s
                limit 1
                """,
                (card_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None

            closing_day = int(row["closing_day"])
            period_start, period_end = billing_period_for_close_day(as_of, closing_day)

            # 2) pega a fatura do período (open/closed) e trava linha
            cur.execute(
                """
                select id,
                       total,
                       coalesce(paid_amount, 0) as paid_amount,
                       status
                from credit_bills
                where user_id=%s
                  and card_id=%s
                  and period_start=%s
                  and period_end=%s
                  and status in ('open','closed')
                limit 1
                for update
                """,
                (user_id, card_id, period_start, period_end),
            )
            bill = cur.fetchone()
            if not bill:
                return None

            total = Decimal(str(bill["total"]))
            paid = Decimal(str(bill["paid_amount"]))
            due = total - paid  # em aberto

            if due <= 0:
                # já quitada (por segurança)
                cur.execute(
                    "update credit_bills set status='paid', paid_at=now() where id=%s and user_id=%s",
                    (bill["id"], user_id),
                )
                conn.commit()
                return None

            # valida amount ANTES de debitar conta
            if amount is not None:
                pay = Decimal(str(amount))
                if pay <= 0:
                    return {"error": "invalid_amount"}
                if pay > due:
                    # NÃO EXECUTA
                    return {
                        "error": "amount_too_high",
                        "due": float(due),
                        "total": float(total),
                        "paid_amount": float(paid),
                    }
            else:
                pay = due

            # marca como 'open' (se estava closed) porque ainda tem pagamento rolando
            if (bill.get("status") or "").lower() == "closed":
                cur.execute(
                    "update credit_bills set status='open' where id=%s and user_id=%s",
                    (bill["id"], user_id),
                )

            conn.commit()

    # 3) debita conta corrente (fora do lock do bill, mas OK)
    launch_id, new_balance = add_launch_and_update_balance(
        user_id=user_id,
        tipo="despesa",
        valor=float(pay),
        alvo=f"fatura:{card_name}",
        nota=f"Pagamento de fatura ({card_name}) {period_start} → {period_end}",
    )

    # 4) atualiza bill (com lock novamente)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, total, coalesce(paid_amount,0) as paid_amount
                from credit_bills
                where id=%s and user_id=%s
                limit 1
                for update
                """,
                (bill["id"], user_id),
            )
            b2 = cur.fetchone()
            if not b2:
                conn.commit()
                return {"paid": float(pay), "launch_id": launch_id, "new_balance": new_balance}

            total2 = Decimal(str(b2["total"]))
            paid2 = Decimal(str(b2["paid_amount"])) + pay
            due2 = total2 - paid2

            cur.execute(
                """
                update credit_bills
                set paid_amount = coalesce(paid_amount, 0) + %s,
                    paid_at = now(),
                    status = case
                        when (coalesce(paid_amount, 0) + %s) >= total then 'paid'
                        else 'open'
                    end
                where id=%s and user_id=%s
                """,
                (pay, pay, bill["id"], user_id),
            )
        conn.commit()

    return {"paid": float(pay), "launch_id": launch_id, "new_balance": new_balance}

# fechar fatura 
def close_bill(user_id: int, card_id: int):
    # fecha a fatura mais recente em open
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update credit_bills
                set status='closed', closed_at=now()
                where id = (
                    select id from credit_bills
                    where card_id=%s and status='open'
                    order by period_start desc
                    limit 1
                )
                returning id
                """,
                (card_id,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["id"] if row else None

def get_next_bill_summary(card_id: int):
    # cria/pega o próximo período com base no último bill existente
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select closing_day from credit_cards where id=%s",
                (card_id,),
            )
            closing_day = cur.fetchone()["closing_day"]

            cur.execute(
                """
                select period_start
                from credit_bills
                where card_id=%s
                order by period_start desc
                limit 1
                """,
                (card_id,),
            )
            last = cur.fetchone()
            if last:
                y, m = last["period_start"].year, last["period_start"].month
                y2, m2 = add_months(y, m, 1)
            else:
                today = date.today()
                y2, m2 = today.year, today.month

            ps, pe = bill_period_for_month(y2, m2, closing_day)
            bid = get_or_create_bill_by_period(card_id, ps, pe)

            cur.execute("select id, period_start, period_end, total, paid_amount, status from credit_bills where id=%s", (bid,))
            bill = cur.fetchone()
    return bill

# relatorio credito x debito mensal
def monthly_summary_credit_debit(user_id: int, start: date, end: date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(sum(valor),0) as total_debito
                from launches
                where user_id=%s and tipo='despesa' and criado_em::date between %s and %s
                """,
                (user_id, start, end),
            )
            deb = cur.fetchone()["total_debito"]

            cur.execute(
                """
                select coalesce(sum(valor),0) as total_credito
                from credit_transactions
                where user_id=%s and purchased_at between %s and %s
                """,
                (user_id, start, end),
            )
            cred = cur.fetchone()["total_credito"]

            cur.execute(
                """
                select c.name, coalesce(sum(t.valor),0) as total
                from credit_transactions t
                join credit_cards c on c.id=t.card_id
                where t.user_id=%s and t.purchased_at between %s and %s
                group by c.name
                order by total desc
                """,
                (user_id, start, end),
            )
            by_card = cur.fetchall()

    return {"debito": deb, "credito": cred, "por_cartao": by_card}



