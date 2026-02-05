# db.py
import os
import psycopg
from psycopg.rows import dict_row

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
      user_id bigint primary key references users(id),
      balance numeric not null default 0
    );

    create table if not exists pockets (
      id serial primary key,
      user_id bigint references users(id),
      name text not null,
      balance numeric not null default 0,
      unique(user_id, name)
    );

    create table if not exists investments (
      id serial primary key,
      user_id bigint references users(id),
      name text not null,
      balance numeric not null default 0,
      rate numeric not null,
      period text not null,
      last_date date not null,
      unique(user_id, name)
    );

    create table if not exists launches (
      id bigserial primary key,
      user_id bigint references users(id),
      tipo text not null,
      valor numeric,
      alvo text,
      nota text,
      criado_em timestamptz not null,
      efeitos jsonb
    );
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
