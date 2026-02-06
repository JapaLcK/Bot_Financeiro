# db.py
import os
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb   # <-- ADICIONA ISSO
from decimal import Decimal
from datetime import datetime


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
                (user_id, tipo, v, alvo, nota, criado_em, Jsonb({"delta_conta": float(delta)})),
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

