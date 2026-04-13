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
import db_support as _db_support
from utils_date import _tz, today_tz, billing_period_for_close_day
from utils_phone import normalize_phone_e164
from uuid import uuid4
import calendar
import secrets
import hashlib
import bcrypt




def get_conn():
    database_url = os.getenv("DATABASE_URL")  # Railway injeta isso quando você adiciona Postgres

    if not database_url:
        raise RuntimeError("DATABASE_URL não está definido.")
    return psycopg.connect(database_url, row_factory=dict_row)

def init_db():
    ddl_statements = [
        # -----------------------------
    # Core
    # -----------------------------
    """
    create table if not exists users (
      id bigint primary key,
      created_at timestamptz default now(),
      default_card_id bigint,
      reminders_enabled boolean not null default false,
      reminders_days_before int not null default 3
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
      categoria text,
      criado_em timestamptz not null default now(),
      efeitos jsonb,

      -- OFX fields
      source text not null default 'manual',
      external_id text,
      posted_at date,
      currency text,
      imported_at timestamptz,

      -- Movimentação interna: não entra nos cálculos de receita/despesa do dashboard
      is_internal_movement boolean not null default false
    )
    """,
    """
    create index if not exists idx_launches_user_time
      on launches(user_id, criado_em desc)
    """,
    """
    -- garante dedupe: (user_id, source, external_id)
    create unique index if not exists uq_launches_user_source_external
      on launches(user_id, source, external_id)
    """,
    """
    -- migration: adiciona coluna is_internal_movement se ainda não existe
    alter table launches add column if not exists
      is_internal_movement boolean not null default false
    """,
    """
    -- migration: marca retroativamente aportes, resgates e categoria investimentos como movimentações internas
    update launches set is_internal_movement = true
    where (
      tipo in ('aporte_investimento', 'resgate_investimento')
      or categoria = 'investimentos'
    )
    and is_internal_movement = false
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

# report diário (preferências do usuário)
    """
    create table if not exists daily_report_prefs (
    user_id bigint primary key references users(id) on delete cascade,
    enabled boolean not null default true,
    hour int not null default 9,
    minute int not null default 0,
    last_sent_date date
    );
    """,
    """
    alter table daily_report_prefs add column if not exists last_sent_date date;
    """,

    # -----------------------------
    # Credit cards
    # -----------------------------
    """
    create table if not exists credit_cards (
      id bigserial primary key,
      user_id bigint not null references users(id) on delete cascade,
      name text not null,
      closing_day int not null check (closing_day between 1 and 28),
      due_day int not null check (due_day between 1 and 28),
      reminders_enabled boolean not null default false,
      reminders_days_before int not null default 3,
      reminder_last_sent_on date,
      created_at timestamptz default now(),
      unique(user_id, name)
    )
    """,
    """
    alter table credit_cards add column if not exists reminders_enabled boolean not null default false
    """,
    """
    alter table credit_cards add column if not exists reminders_days_before int not null default 3
    """,
    """
    alter table credit_cards add column if not exists reminder_last_sent_on date
    """,
    """
    create table if not exists credit_bills (
      id bigserial primary key,
      user_id bigint references users(id) on delete cascade,
      card_id bigint not null references credit_cards(id) on delete cascade,
      period_start date not null,
      period_end date not null,
      status text not null default 'open', -- open | closed | paid
      total numeric not null default 0,
      paid_amount numeric not null default 0,
      paid_at timestamptz,
      closed_at timestamptz,
      created_at timestamptz default now(),
      unique(card_id, period_start, period_end)
    )
    """,
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
      created_at timestamptz default now(),
      group_id uuid,
      installment_no int,
      installments_total int,
      is_refund boolean not null default false
    )
    """,
    """
    create index if not exists idx_credit_tx_user_date
      on credit_transactions(user_id, purchased_at desc)
    """,

    # -----------------------------
    # OFX import log (auditoria)
    # -----------------------------
    """
    create table if not exists ofx_imports (
      id bigserial primary key,
      user_id bigint not null references users(id) on delete cascade,
      file_hash text not null,
      bank_id text,
      acct_id text,
      acct_type text,
      dt_start date,
      dt_end date,
      total_transactions int not null,
      inserted_count int not null default 0,
      duplicate_count int not null default 0,
      imported_at timestamptz not null default now(),
      unique(user_id, file_hash)
    )
    """,

    # -----------------------------
    # Identity link (Discord/WhatsApp)
    # -----------------------------
    """
    create table if not exists user_identities (
      provider text not null,
      external_id text not null,
      user_id bigint not null references users(id) on delete cascade,
      created_at timestamptz not null default now(),
      primary key (provider, external_id)
    )
    """,
    """
    create table if not exists link_codes (
      code text primary key,
      user_id bigint not null references users(id) on delete cascade,
      expires_at timestamptz not null,
      created_at timestamptz not null default now()
    )
    """,
    """
    create index if not exists idx_link_codes_expires on link_codes (expires_at)
    """,
    """
    create table if not exists platform_onboarding_tokens (
      token text primary key,
      provider text not null,
      user_id bigint not null references users(id) on delete cascade,
      expires_at timestamptz not null,
      consumed_at timestamptz,
      created_at timestamptz not null default now()
    )
    """,
    """
    create index if not exists idx_platform_onboarding_tokens_lookup
      on platform_onboarding_tokens (provider, expires_at)
    """,
    """
    create table if not exists auth_accounts (
      id bigserial primary key,
      user_id bigint not null references users(id) on delete cascade,
      email text not null unique,
      password_hash text not null,
      phone_e164 text,
      phone_status text not null default 'pending',
      phone_confirmed_at timestamptz,
      whatsapp_verified_at timestamptz,
      plan text not null default 'free',
      plan_expires_at timestamptz,
      created_at timestamptz not null default now()
    )
    """,
    """
    create index if not exists idx_auth_accounts_email on auth_accounts (email)
    """,
    """
    alter table auth_accounts add column if not exists phone_e164 text
    """,
    """
    alter table auth_accounts add column if not exists phone_status text not null default 'pending'
    """,
    """
    alter table auth_accounts add column if not exists phone_confirmed_at timestamptz
    """,
    """
    alter table auth_accounts add column if not exists whatsapp_verified_at timestamptz
    """,
    """
    alter table auth_accounts add column if not exists stripe_customer_id text unique
    """,
    """
    create unique index if not exists idx_auth_accounts_phone_unique
      on auth_accounts (phone_e164)
      where phone_e164 is not null
    """,
    """
    alter table credit_bills add column if not exists user_id bigint references users(id) on delete cascade
    """,
    """
    create table if not exists dashboard_sessions (
      code text primary key,
      user_id bigint not null references users(id) on delete cascade,
      expires_at timestamptz not null,
      created_at timestamptz not null default now()
    )
    """,
    """
    create index if not exists idx_dashboard_sessions_expires on dashboard_sessions (expires_at)
    """,
    """
    create table if not exists email_verification_codes (
      id bigserial primary key,
      email text not null,
      code text not null,
      password_hash text not null,
      phone_e164 text,
      expires_at timestamptz not null,
      used_at timestamptz,
      created_at timestamptz not null default now()
    )
    """,
    """
    create index if not exists idx_email_verification_email on email_verification_codes (email, expires_at)
    """,
    """
    alter table email_verification_codes add column if not exists phone_e164 text
    """,
    """
    create table if not exists password_reset_tokens (
      token text primary key,
      user_id bigint not null references users(id) on delete cascade,
      expires_at timestamptz not null,
      used_at timestamptz,
      created_at timestamptz not null default now()
    )
    """,
    """
    create index if not exists idx_password_reset_tokens_expires on password_reset_tokens (expires_at)
    """,
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

def ensure_user_tx(cur, user_id: int):
    cur.execute("insert into users(id) values (%s) on conflict do nothing", (user_id,))
    cur.execute("insert into accounts(user_id, balance) values (%s, 0) on conflict do nothing", (user_id,))

def ensure_user(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user_tx(cur, user_id)
        conn.commit()

def merge_users(from_user_id: int, to_user_id: int) -> None:
    """
    Move TODOS os dados de from_user_id -> to_user_id, e atualiza identidades.

    FIX CRÍTICO:
    - Antes de mover launches, remove duplicatas que colidem na unique:
      uq_launches_user_source_external (user_id, source, external_id)

    Regra de dedupe:
    - Se já existe no TO um launch com mesmo (source, external_id),
      apagamos o launch do FROM (não dá pra "update" porque estoura unique).
    """
    if from_user_id == to_user_id:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user_tx(cur, to_user_id)
            ensure_user_tx(cur, from_user_id)

            # =========================
            # 1) DEDUPE de launches (evita UniqueViolation)
            # =========================
            # Remove do FROM qualquer lançamento que já exista no TO
            # com o mesmo (source, external_id). Só faz sentido quando external_id não é NULL.
            cur.execute(
                """
                delete from launches lf
                using launches lt
                where lf.user_id = %s
                  and lt.user_id = %s
                  and lf.external_id is not null
                  and lt.external_id is not null
                  and lf.source = lt.source
                  and lf.external_id = lt.external_id
                """,
                (from_user_id, to_user_id),
            )

            # =========================
            # 2) launches: move o resto
            # =========================
            cur.execute(
                "update launches set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # =========================
            # 3) accounts: soma saldos (seguro)
            # =========================
            cur.execute("select balance from accounts where user_id=%s", (to_user_id,))
            row_to = cur.fetchone()
            bal_to = float(row_to["balance"]) if row_to else 0.0

            cur.execute("select balance from accounts where user_id=%s", (from_user_id,))
            row_from = cur.fetchone()
            bal_from = float(row_from["balance"]) if row_from else 0.0

            new_bal = bal_to + bal_from
            cur.execute(
                "update accounts set balance=%s where user_id=%s",
                (new_bal, to_user_id),
            )
            cur.execute("delete from accounts where user_id=%s", (from_user_id,))

            # =========================
            # 4) identidades / link_codes
            # =========================
            cur.execute(
                "update user_identities set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )
            cur.execute(
                "update link_codes set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # =========================
            # 5) OUTRAS tabelas que tem user_id (forte recomendação)
            # =========================
            # Essas aqui não costumam quebrar fácil e evitam metade dos bugs:
            cur.execute("update user_category_rules set user_id=%s where user_id=%s", (to_user_id, from_user_id))
            cur.execute("update pending_actions set user_id=%s where user_id=%s", (to_user_id, from_user_id))
            cur.execute("update pockets set user_id=%s where user_id=%s", (to_user_id, from_user_id))
            cur.execute("update investments set user_id=%s where user_id=%s", (to_user_id, from_user_id))
            cur.execute("update credit_transactions set user_id=%s where user_id=%s", (to_user_id, from_user_id))
            cur.execute("update ofx_imports set user_id=%s where user_id=%s", (to_user_id, from_user_id))

            # =========================
            # 6) credit_cards: merge seguro por nome (evita unique(user_id, name))
            # =========================
            cur.execute("select id, name from credit_cards where user_id=%s", (from_user_id,))
            from_cards = cur.fetchall()

            for from_card in from_cards:
                from_card_id = from_card["id"]
                from_card_name = from_card["name"]

                cur.execute(
                    "select id from credit_cards where user_id=%s and name=%s",
                    (to_user_id, from_card_name),
                )
                to_card_row = cur.fetchone()

                if to_card_row:
                    # Cartão com mesmo nome já existe no destino — precisa redirecionar
                    to_card_id = to_card_row["id"]

                    # Remove bills do from_card que colidem por período com bills do to_card
                    # (cascade deleta as credit_transactions dessas bills)
                    cur.execute(
                        """
                        delete from credit_bills fb
                        using credit_bills tb
                        where fb.card_id = %s
                          and tb.card_id = %s
                          and fb.period_start = tb.period_start
                          and fb.period_end = tb.period_end
                        """,
                        (from_card_id, to_card_id),
                    )

                    # Move bills restantes pro cartão destino
                    cur.execute(
                        "update credit_bills set card_id=%s where card_id=%s",
                        (to_card_id, from_card_id),
                    )

                    # Move transactions restantes pro cartão destino
                    cur.execute(
                        "update credit_transactions set card_id=%s where card_id=%s",
                        (to_card_id, from_card_id),
                    )

                    # Deleta o cartão duplicado (sem bills/transactions restantes)
                    cur.execute("delete from credit_cards where id=%s", (from_card_id,))
                else:
                    # Sem colisão de nome — só troca o dono
                    cur.execute(
                        "update credit_cards set user_id=%s where id=%s",
                        (to_user_id, from_card_id),
                    )

            # =========================
            # 7) credit_bills.user_id
            # =========================
            cur.execute(
                "update credit_bills set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # =========================
            # 8) auth_accounts: migra conta de email se from_user tem e to_user não tem
            # =========================
            cur.execute("select id from auth_accounts where user_id=%s limit 1", (to_user_id,))
            to_has_auth = cur.fetchone() is not None

            if not to_has_auth:
                cur.execute(
                    "update auth_accounts set user_id=%s where user_id=%s",
                    (to_user_id, from_user_id),
                )
            # Se to_user já tem auth, a conta do from_user será removida por cascade

        conn.commit()
def choose_primary_user(a_user_id: int, b_user_id: int) -> tuple[int, int]:
    """
    Retorna (primary, secondary) baseado em score.
    Primary = mais dados.
    """
    if a_user_id == b_user_id:
        return a_user_id, b_user_id

    sa = user_score(a_user_id)
    sb = user_score(b_user_id)

    if sa >= sb:
        return a_user_id, b_user_id
    return b_user_id, a_user_id


# analisa qual dados será o primario
def user_score(user_id: int) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s", (user_id,))
        n_launches = cur.fetchone()["n"]
        # pode somar outras coisas depois (pockets/investments)
        return int(n_launches)

# cria usuario unico entre discord e whatsapp
def get_or_create_canonical_user(provider: str, external_id: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id from user_identities where provider=%s and external_id=%s",
                (provider, external_id),
            )
            row = cur.fetchone()
            if row:
                return int(row["user_id"])

            # tenta até achar um id livre
            base = f"{provider}:{external_id}".encode("utf-8")
            for i in range(20):
                digest = hashlib.sha256(base + f":{i}".encode("utf-8")).digest()
                new_id = int.from_bytes(digest[:8], "big") % 2_000_000_000 + 1

                # garante user/account na MESMA transação
                ensure_user_tx(cur, new_id)

                # tenta inserir identidade; se colidir por (provider, external_id), ok
                # se colidir por id já usado por outro, isso aqui ainda funciona porque
                # users.id pode existir (ok), o que importa é inserir a identidade.
                try:
                    cur.execute(
                        "insert into user_identities(provider, external_id, user_id) values (%s,%s,%s)",
                        (provider, external_id, new_id),
                    )
                    conn.commit()
                    return new_id
                except Exception:
                    conn.rollback()
                    # tenta outro i
                    with get_conn() as conn2:
                        with conn2.cursor() as cur2:
                            # recheck se alguém criou nesse meio tempo
                            cur2.execute(
                                "select user_id from user_identities where provider=%s and external_id=%s",
                                (provider, external_id),
                            )
                            r2 = cur2.fetchone()
                            if r2:
                                return int(r2["user_id"])
                    continue

            raise RuntimeError("Falha ao criar user_id canônico (colisão repetida)")
        
# cria link para configurar ambas plataformas no mesmo usuario
def create_link_code(user_id: int, minutes_valid: int = 10) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into link_codes(code, user_id, expires_at) values (%s,%s,%s) "
                "on conflict (code) do update set user_id=excluded.user_id, expires_at=excluded.expires_at",
                (code, user_id, expires_at),
            )
        conn.commit()
    return code


def create_platform_onboarding_token(user_id: int, provider: str, minutes_valid: int = 15) -> str:
    token = f"pbw_{secrets.token_urlsafe(18)}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into platform_onboarding_tokens(token, provider, user_id, expires_at)
                values (%s, %s, %s, %s)
                on conflict (token) do update
                set provider=excluded.provider,
                    user_id=excluded.user_id,
                    expires_at=excluded.expires_at,
                    consumed_at=null
                """,
                (token, provider, user_id, expires_at),
            )
        conn.commit()
    return token


def consume_platform_onboarding_token(token: str, provider: str) -> int | None:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                delete from platform_onboarding_tokens
                where token = %s
                  and provider = %s
                  and expires_at > %s
                  and consumed_at is null
                returning user_id
                """,
                (token, provider, now),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["user_id"]) if row else None

# funcao para confirmar link/codigo
def consume_link_code(code: str) -> int | None:
    now = datetime.now(timezone.utc)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select user_id, expires_at from link_codes where code=%s", (code,))
            row = cur.fetchone()
            if not row:
                return None
            if row["expires_at"] < now:
                cur.execute("delete from link_codes where code=%s", (code,))
                return None

            user_id = int(row["user_id"])
            cur.execute("delete from link_codes where code=%s", (code,))
            return user_id

# junta os dois usuarios em um unico id 
def bind_identity(provider: str, external_id: str, user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into user_identities(provider, external_id, user_id) values (%s,%s,%s) "
                "on conflict (provider, external_id) do update set user_id=excluded.user_id",
                (provider, external_id, user_id),
            )
        conn.commit()

def link_platform_identity(provider: str, external_id: str, target_user_id: int) -> int:
    """
    Liga (provider, external_id) ao target_user_id.

    REGRA IMPORTANTE (seu requisito):
    - O user_id do CÓDIGO (target_user_id) é SEMPRE o PRIMARY.
    - A conta que DIGITA o código (current_user_id) entra nela (vira secondary e é merged).

    Retorna o user_id final (primary = target_user_id).
    """
    current_user_id = get_or_create_canonical_user(provider, external_id)

    if current_user_id == target_user_id:
        # já está linkado
        return target_user_id

    # O primary é sempre o dono do código
    primary = target_user_id
    secondary = current_user_id

    merge_users(secondary, primary)

    # garante que essa identidade (da plataforma que digitou o código) aponta pro primary
    bind_identity(provider, external_id, primary)

    return primary

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

def add_launch_and_update_balance(
    user_id: int,
    tipo: str,
    valor: float,
    alvo: str | None,
    nota: str | None,
    categoria: str | None = None,
    criado_em: datetime | None = None,
    is_internal_movement: bool = False,
):
    """
    Lança registro em launches e atualiza saldo em accounts na mesma transação.
    Regra:
      - despesa: saldo -= valor
      - receita: saldo += valor
    Movimentações internas (aportes/resgates de investimento, transferências entre contas
    próprias) devem ser registradas com is_internal_movement=True: aparecem na lista de
    lançamentos mas não entram nos cálculos de receita/despesa do dashboard.
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

    # normaliza categoria
    cat = (categoria or "").strip() or "outros"

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
                returning id
                """,
                (user_id, tipo, v, alvo, nota, cat, criado_em, Json({"delta_conta": float(delta)}), is_internal_movement),
            )
            launch_id = cur.fetchone()["id"]

        conn.commit()

    return launch_id, new_bal

# ---------------- OFX import (idempotente) ----------------
def get_ofx_import_by_hash(user_id: int, file_hash: str):
    """
    Se o mesmo arquivo já foi importado, devolve o log pra evitar retrabalho.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select file_hash, dt_start, dt_end, total_transactions, inserted_count, duplicate_count, imported_at
                from ofx_imports
                where user_id=%s and file_hash=%s
                """,
                (user_id, file_hash),
            )
            row = cur.fetchone()
            return row

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
    Importa várias transações OFX de uma vez, de forma IDEMPOTENTE:
      - insere launch com (source='ofx', external_id=FITID)
      - ON CONFLICT DO NOTHING evita duplicata
      - saldo só é ajustado SOMENTE pelas inseridas de verdade
    """
    ensure_user(user_id)

    total = len(launches_rows)

    # se já importou o mesmo arquivo, retorna rápido (saldo atual)
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
            pipe_ctx = None

            if pipe_ctx:
                with pipe_ctx:
                    for r in launches_rows:
                        cur.execute(
                            """
                            insert into launches(
                                user_id, tipo, valor, categoria, alvo, nota, criado_em, efeitos,
                                source, external_id, posted_at, currency, imported_at, is_internal_movement
                            )
                            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
                            on conflict (user_id, source, external_id) do nothing
                            returning id
                            """,
                            (
                                user_id,
                                r["tipo"],
                                r["valor"],
                                r.get("categoria"),
                                None,
                                r.get("nota"),
                                r["criado_em"],
                                Json({"delta_conta": float(r["delta"]), "ofx": r.get("ofx_meta", {})}),
                                "ofx",
                                r["external_id"],
                                r.get("posted_at"),
                                r.get("currency", "BRL"),
                                r.get("is_internal_movement", False),
                            ),
                        )
                        # rowcount=1 se inseriu, 0 se foi conflito
                        if (cur.rowcount or 0) == 1:
                            inserted += 1
                            delta_total += r["delta"]
                        else:
                            duplicates += 1
            else:
                # fallback (se pipeline não existir)
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
                            user_id,
                            r["tipo"],
                            r["valor"],
                            r.get("categoria"),
                            r.get("nota"),
                            r["criado_em"],
                            Json({"delta_conta": float(r["delta"]), "ofx": r.get("ofx_meta", {})}),
                            "ofx",
                            r["external_id"],
                            r.get("posted_at"),
                            r.get("currency", "BRL"),
                            r.get("is_internal_movement", False),
                        ),
                    )
                    if (cur.rowcount or 0) == 1:
                        inserted += 1
                        delta_total += r["delta"]
                    else:
                        duplicates += 1

            # atualiza saldo UMA vez com o delta total inserido
            if inserted:
                cur.execute(
                    "update accounts set balance = balance + %s where user_id=%s returning balance",
                    (delta_total, user_id),
                )
                new_bal = cur.fetchone()["balance"]
            else:
                cur.execute("select balance from accounts where user_id=%s", (user_id,))
                new_bal = cur.fetchone()["balance"]

            # grava log da importação
            cur.execute(
                """
                insert into ofx_imports(
                    user_id, file_hash, bank_id, acct_id, acct_type, dt_start, dt_end,
                    total_transactions, inserted_count, duplicate_count
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict do nothing
                """,
                (
                    user_id, file_hash, bank_id, acct_id, acct_type,
                    dt_start, dt_end, total, inserted, duplicates
                ),
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

def update_launch_categories_bulk(user_id: int, items: list[tuple[int, str]]) -> int:
    """
    items: [(launch_id, categoria_norm), ...]
    Retorna quantos updates foram aplicados.
    """
    ensure_user(user_id)
    if not items:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                update launches
                   set categoria=%s
                 where user_id=%s and id=%s
                """,
                [(cat, user_id, lid) for (lid, cat) in items],
            )
            n = cur.rowcount or 0
        conn.commit()
    return n

def list_launches(user_id: int, limit: int = 10):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, tipo, valor, alvo, nota, categoria, source, criado_em
                from launches
                where user_id=%s
                order by criado_em desc, id desc
                limit %s
                """,
                (user_id, limit),
            )
            return cur.fetchall()
        
def update_launch_category(user_id: int, launch_id: int, categoria: str | None) -> bool:
    """Atualiza a categoria de um lançamento (launches).

    Retorna True se atualizou (rowcount==1), False caso contrário.
    """
    ensure_user(user_id)
    cat = (categoria or "").strip() or None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update launches
                   set categoria=%s
                 where user_id=%s and id=%s
                """,
                (cat, user_id, launch_id),
            )
            changed = (cur.rowcount or 0) == 1
        conn.commit()

    return changed
        
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
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement)
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "resgate_investimento", v, inv_name_canon, nota, criado_em, Jsonb(efeitos), True),
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
                insert into launches(user_id, tipo, valor, alvo, nota, criado_em, efeitos, is_internal_movement)
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                returning id
                """,
                (user_id, "aporte_investimento", v, inv_name_canon, nota, criado_em, Jsonb(efeitos), True),
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
    return _db_support.get_launches_by_period_impl(get_conn, ensure_user, user_id, start_date, end_date)
        

# pega o resumo de lancamentos por periodo
def get_summary_by_period(user_id: int, start_date: date, end_date: date):
    """Retorna soma por tipo no período [start_date, end_date] (inclusive)."""
    return _db_support.get_summary_by_period_impl(get_conn, ensure_user, user_id, start_date, end_date)

# Puxa regas somente 1 vez na hora do import ofx
def list_category_rules(user_id: int) -> list[tuple[str, str]]:
    """
    Carrega as regras do usuário 1x (pra usar em batch sem ficar batendo no DB).
    """
    ensure_user(user_id)
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

    # seu cursor parece retornar dict_row, mas aqui funciona tanto com dict quanto tuple
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append((r["keyword"], r["category"]))
        else:
            out.append((r[0], r[1]))
    return out

def list_category_rules(user_id: int) -> list[tuple[str, str]]:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select keyword, category
                from user_category_rules
                where user_id=%s
                order by length(keyword) desc
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    return [(r["keyword"], r["category"]) for r in rows]

#adiciona uma regra de categoria
def add_category_rule(user_id: int, keyword: str, category: str) -> None:
    ensure_user(user_id)
    keyword = (keyword or "").strip()
    category = (category or "").strip()

    if not keyword:
        raise ValueError("keyword vazio")
    if not category:
        raise ValueError("category vazia")

    with get_conn() as conn:
        with conn.cursor() as cur:
            # evita duplicar a mesma regra
            cur.execute(
                """
                INSERT INTO user_category_rules (user_id, keyword, category)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, keyword) DO UPDATE
                SET category = EXCLUDED.category
                """,
                (user_id, keyword, category),
            )
        conn.commit()

# deleta as categorias criadas
def delete_category_rule(user_id: int, keyword: str) -> int:
    ensure_user(user_id)
    keyword = (keyword or "").strip()
    if not keyword:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM user_category_rules
                WHERE user_id=%s AND keyword=%s
                """,
                (user_id, keyword),
            )
            n = cur.rowcount
        conn.commit()
    return n

# lista as categorias
def list_categories(user_id: int) -> list[str]:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT category
                FROM user_category_rules
                WHERE user_id=%s
                ORDER BY category
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append(r["category"])
        else:
            out.append(r[0])
    return out
        
# Busca uma categoria memorizada pelo user_id com base no texto (keyword contida no texto)
def get_memorized_category(user_id: int, memo: str) -> str | None:
    """
    Retorna a categoria memorizada (user_category_rules) se alguma keyword
    bater com o texto (memo). Dá match por "contains_word" OU substring.
    """
    from utils_text import normalize_text, contains_word  # import local pra evitar loop

    ensure_user(user_id)

    memo_norm = normalize_text(memo or "")
    if not memo_norm:
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

    for r in rows:
        if isinstance(r, dict):
            keyword = r.get("keyword")
            category = r.get("category")
        else:
            keyword, category = r[0], r[1]

        kw_norm = normalize_text(keyword or "")
        if not kw_norm:
            continue

        # ✅ direção correta: keyword dentro do memo
        if contains_word(memo_norm, kw_norm) or (kw_norm in memo_norm):
            return (category or "").strip() or None

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

def list_user_category_rules(user_id: int) -> list[tuple[str, str]]:
    """
    Lista regras (keyword -> category) do usuário uma vez só.
    Retorna ordenado por keyword maior primeiro (melhor match).
    """
    ensure_user(user_id)
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
            rows = cur.fetchall() or []

    out: list[tuple[str, str]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append((r.get("keyword") or "", r.get("category") or ""))
        else:
            out.append((r[0] or "", r[1] or ""))
    return out

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
            row = cur.fetchone()
            bill_total = Decimal(str(row["total"]))

            # buscar quanto já foi pago nessa fatura
            cur.execute(
                """
                select coalesce(paid_amount, 0) as paid_amount
                from credit_bills
                where id=%s and user_id=%s
                """,
                (bill_id, user_id),
            )
            row2 = cur.fetchone()
            bill_paid = Decimal(str(row2["paid_amount"]))

            bill_due = bill_total - bill_paid

        conn.commit()

    return tx_id, float(bill_due), bill_id

def undo_credit_transaction(user_id: int, ct_id: int):
    """
    Desfaz um crédito específico CT#.
    Se ele pertence a um parcelamento (group_id), desfaz o GRUPO inteiro.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, bill_id, valor, group_id, installment_no, installments_total
                from credit_transactions
                where user_id=%s and id=%s
                """,
                (user_id, ct_id),
            )
            tx = cur.fetchone()
            if not tx:
                return None

            group_id = tx.get("group_id")
            installments_total = int(tx.get("installments_total") or 0)

    # se é parcelamento, desfaz o grupo inteiro
    if group_id and installments_total > 1:
        return undo_installment_group(user_id, group_id)

    # senão, desfaz só o CT
    with get_conn() as conn:
        with conn.cursor() as cur:
            # trava linha
            cur.execute(
                """
                select id, bill_id, valor
                from credit_transactions
                where user_id=%s and id=%s
                for update
                """,
                (user_id, ct_id),
            )
            tx2 = cur.fetchone()
            if not tx2:
                return None

            bill_id = tx2["bill_id"]
            v = Decimal(str(tx2["valor"]))

            cur.execute(
                "delete from credit_transactions where user_id=%s and id=%s",
                (user_id, ct_id),
            )

            cur.execute(
                """
                update credit_bills
                set total = greatest(0, total - %s)
                where id=%s and user_id=%s
                returning total, coalesce(paid_amount, 0) as paid_amount
                """,
                (float(v), bill_id, user_id),
            )
            b = cur.fetchone()
            if b:
                total = Decimal(str(b["total"]))
                paid = Decimal(str(b["paid_amount"]))
                if paid >= total:
                    cur.execute(
                        """
                        update credit_bills
                        set status='paid', paid_at=now()
                        where id=%s and user_id=%s
                        """,
                        (bill_id, user_id),
                    )

            conn.commit()

    return {
        "mode": "single",
        "ct_id": ct_id,
        "removed_total": float(v),
        "removed_count": 1,
    }



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

            # 3) garante que a fatura do período exista (idempotente)
            bill_id = get_or_create_open_bill(user_id, card_id, as_of)

            # 4) busca a fatura do período (não depende de status=open)
            cur.execute(
                """
                select id, period_start, period_end, total,
                       coalesce(paid_amount, 0) as paid_amount,
                       status
                from credit_bills
                where user_id=%s
                  and card_id=%s
                  and period_start=%s
                  and period_end=%s
                limit 1
                """,
                (user_id, card_id, period_start, period_end),
            )
            bill = cur.fetchone()
            if not bill:
                return None

            # 5) itens da fatura
            cur.execute(
                """
                select id, valor, categoria, nota, purchased_at,
                       installment_no, installments_total, group_id, is_refund
                from credit_transactions
                where user_id=%s
                  and bill_id=%s
                order by purchased_at desc, id desc
                limit 50
                """,
                (user_id, bill["id"]),
            )
            items = cur.fetchall()

    return bill, items

# listar cartoes cadastrados
def list_cards(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select c.id, c.name, c.closing_day, c.due_day,
                       c.reminders_enabled, c.reminders_days_before, c.reminder_last_sent_on,
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


def get_card_by_id(user_id: int, card_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select c.id, c.name, c.closing_day, c.due_day,
                       c.reminders_enabled, c.reminders_days_before, c.reminder_last_sent_on,
                       (u.default_card_id = c.id) as is_default
                from credit_cards c
                left join users u on u.id = c.user_id
                where c.user_id = %s and c.id = %s
                limit 1
                """,
                (user_id, card_id),
            )
            return cur.fetchone()


def update_card_reminder_settings(user_id: int, card_id: int, enabled: bool, days_before: int | None = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if days_before is None:
                cur.execute(
                    """
                    update credit_cards
                    set reminders_enabled=%s
                    where user_id=%s and id=%s
                    """,
                    (bool(enabled), user_id, card_id),
                )
            else:
                cur.execute(
                    """
                    update credit_cards
                    set reminders_enabled=%s,
                        reminders_days_before=%s
                    where user_id=%s and id=%s
                    """,
                    (bool(enabled), int(days_before), user_id, card_id),
                )
        conn.commit()


def mark_card_reminder_sent(user_id: int, card_id: int, sent_on: date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update credit_cards
                set reminder_last_sent_on=%s
                where user_id=%s and id=%s
                """,
                (sent_on, user_id, card_id),
            )
        conn.commit()

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
                select id
                from credit_bills
                where user_id=%s and card_id=%s and period_start=%s and period_end=%s
                """,
                (user_id, card_id, period_start, period_end),
            )
            row = cur.fetchone()
            if row:
                return int(row["id"])

            cur.execute(
                """
                insert into credit_bills (user_id, card_id, period_start, period_end, status, total, paid_amount)
                values (%s, %s, %s, %s, 'open', 0, 0)
                returning id
                """,
                (user_id, card_id, period_start, period_end),
            )
            bid = int(cur.fetchone()["id"])
        conn.commit()
    return bid

#paga a fatura atual (open), nao a mais recente.
def get_current_open_bill_id(user_id: int, card_id: int, as_of: date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select closing_day from credit_cards where id=%s and user_id=%s limit 1",
                (card_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                return None
            closing_day = int(row["closing_day"])
            ps, pe = billing_period_for_close_day(as_of, closing_day)

            cur.execute(
                """
                select id
                from credit_bills
                where user_id=%s and card_id=%s and status='open'
                  and period_start=%s and period_end=%s
                limit 1
                """,
                (user_id, card_id, ps, pe),
            )
            b = cur.fetchone()
            return int(b["id"]) if b else None



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
            cur.execute(
                "select closing_day from credit_cards where id=%s and user_id=%s",
                (card_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Cartão não encontrado ou não pertence a este usuário.")
            closing_day = row["closing_day"]

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
    bill_id = get_or_create_open_bill(user_id, card_id, purchased_at)

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
    bill_id: int | None = None,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if bill_id is not None:
                cur.execute(
                    """
                    select id, total, coalesce(paid_amount, 0) as paid_amount, status
                    from credit_bills
                    where id=%s and user_id=%s and card_id=%s
                    limit 1
                    for update
                    """,
                    (bill_id, user_id, card_id),
                )
            else:
                cur.execute(
                    """
                    select id, total, coalesce(paid_amount, 0) as paid_amount, status
                    from credit_bills
                    where user_id=%s and card_id=%s and status in ('open','closed')
                    order by period_start desc
                    limit 1
                    for update
                    """,
                    (user_id, card_id),
                )

            bill = cur.fetchone()
            if not bill:
                return None

            total = Decimal(str(bill["total"]))
            paid = Decimal(str(bill["paid_amount"]))
            due = total - paid

            if due <= 0:
                cur.execute(
                    "update credit_bills set status='paid', paid_at=now() where id=%s",
                    (bill["id"],),
                )
                conn.commit()
                return None

            # valida amount ANTES de debitar
            if amount is not None:
                pay = Decimal(str(amount))
                if pay <= 0:
                    return {"error": "invalid_amount"}
                if pay > due:
                    return {
                        "error": "amount_too_high",
                        "due": float(due),
                        "total": float(total),
                        "paid_amount": float(paid),
                    }
            else:
                pay = due

            # debita conta corrente — pagamento de fatura é movimentação interna
            # (os gastos reais já foram registrados individualmente no cartão)
            launch_id, new_balance = add_launch_and_update_balance(
                user_id=user_id,
                tipo="despesa",
                valor=float(pay),
                alvo=f"fatura:{card_name}",
                nota=f"Pagamento de fatura ({card_name})",
                categoria="pagamento_fatura",
                is_internal_movement=True,
            )

            # atualiza fatura
            cur.execute(
                """
                update credit_bills
                set paid_amount = coalesce(paid_amount, 0) + %s,
                    paid_at = now(),
                    status = case
                        when coalesce(paid_amount, 0) + %s >= total then 'paid'
                        else status
                    end
                where id=%s
                """,
                (pay, pay, bill["id"]),
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

def get_next_bill_summary(user_id, card_id: int):
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
            bill_id = get_or_create_bill_by_period(user_id, card_id, ps, pe)


            cur.execute("select id, period_start, period_end, total, paid_amount, status from credit_bills where id=%s", (bill_id,))
            bill = cur.fetchone()
    return bill

def list_open_bills(user_id: int):
    """
    Lista TODAS as faturas em aberto (status='open') do usuário, de todos os cartões.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    b.id,
                    c.name as card_name,
                    b.period_start,
                    b.period_end,
                    b.total,
                    coalesce(b.paid_amount, 0) as paid_amount,
                    b.status
                from credit_bills b
                join credit_cards c on c.id = b.card_id
                where b.user_id=%s and b.status='open'
                order by b.period_end asc, c.name asc
                """,
                (user_id,),
            )
            rows = cur.fetchall()
    return rows


def list_credit_card_due_reminders(user_id: int, today: date):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    c.id as card_id,
                    c.name as card_name,
                    c.closing_day,
                    c.due_day,
                    c.reminders_enabled,
                    c.reminders_days_before,
                    c.reminder_last_sent_on,
                    b.id as bill_id,
                    b.period_start,
                    b.period_end,
                    b.total,
                    coalesce(b.paid_amount, 0) as paid_amount
                from credit_cards c
                join credit_bills b on b.card_id = c.id and b.user_id = c.user_id
                where c.user_id = %s
                  and c.reminders_enabled = true
                  and b.status in ('open', 'closed')
                order by b.period_end asc, c.name asc
                """,
                (user_id,),
            )
            return cur.fetchall()

#lista parcelamentos
def list_installment_groups(user_id: int, limit: int = 15):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                    t.group_id,
                    c.name              as card_name,
                    max(t.installments_total) as n_total,
                    count(*)            as n_registered,
                    sum(t.valor)        as total,
                    -- parcelas em faturas ainda abertas (não pagas)
                    sum(case when b.status = 'open' then t.valor else 0 end) as total_pending,
                    -- quantas parcelas ainda estão em faturas abertas
                    count(case when b.status = 'open' then 1 end) as n_pending,
                    max(t.purchased_at) as last_purchase,
                    min(t.nota)         as nota
                from credit_transactions t
                join credit_cards c on c.id = t.card_id
                join credit_bills b on b.id = t.bill_id
                where t.user_id=%s
                  and t.group_id is not null
                  and t.is_refund=false
                group by t.group_id, c.name
                order by max(t.purchased_at) desc
                limit %s
                """,
                (user_id, limit),
            )
            return cur.fetchall()


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

# Desfaz lancamentos de parcelas nao pagas. 
def undo_installment_group(user_id: int, group_id: str):
    """
    Desfaz um parcelamento (grupo) removendo todas as credit_transactions do group_id
    e abatendo o total das faturas (credit_bills.total) correspondentes.

    Retorna:
      {"group_id": str, "removed_count": int, "removed_total": float}
    ou None se não achar o grupo.
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) pega tudo do grupo (travando linhas) e soma por bill_id
            cur.execute(
                """
                select id, bill_id, valor
                from credit_transactions
                where user_id = %s
                  and group_id = %s::uuid
                  and is_refund = false
                for update
                """,
                (user_id, group_id),
            )
            rows = cur.fetchall()
            if not rows:
                return None

            # soma total e por fatura
            total_removed = Decimal("0")
            by_bill = {}
            for r in rows:
                v = Decimal(str(r["valor"]))
                total_removed += v
                by_bill[r["bill_id"]] = by_bill.get(r["bill_id"], Decimal("0")) + v

            removed_count = len(rows)

            # 2) deleta as transações do grupo
            cur.execute(
                """
                delete from credit_transactions
                where user_id = %s
                  and group_id = %s::uuid
                  and is_refund = false
                """,
                (user_id, group_id),
            )

            # 3) abate o total das faturas afetadas
            for bill_id, bill_sum in by_bill.items():
                cur.execute(
                    """
                    update credit_bills
                    set total = greatest(0, total - %s)
                    where id = %s and user_id = %s
                    """,
                    (float(bill_sum), bill_id, user_id),
                )

            conn.commit()

    return {
        "group_id": group_id,
        "removed_count": removed_count,
        "removed_total": float(total_removed),
    }

def set_daily_report_enabled(user_id: int, enabled: bool) -> None:
    return _db_support.set_daily_report_enabled_impl(get_conn, ensure_user, user_id, enabled)

def get_daily_report_prefs(user_id: int) -> dict:
    return _db_support.get_daily_report_prefs_impl(get_conn, ensure_user, user_id)

def list_users_with_daily_report_enabled(hour: int = 9, minute: int = 0) -> list[int]:
    return _db_support.list_users_with_daily_report_enabled_impl(get_conn, hour, minute)

def list_identities_by_user(user_id: int) -> list[dict]:
    return _db_support.list_identities_by_user_impl(get_conn, user_id)
        

#marcar envio de mensagem diaria
def mark_daily_report_sent(user_id: int, sent_date) -> None:
    return _db_support.mark_daily_report_sent_impl(get_conn, ensure_user, user_id, sent_date)

# confere se a mensagem ja foi enviada no dia 
def was_daily_report_sent_today(user_id: int, today) -> bool:
    return _db_support.was_daily_report_sent_today_impl(get_conn, ensure_user, user_id, today)
            
# pega a data de fim do último import OFX para o usuário, ou None se não tiver nenhum.
def get_last_ofx_import_end_date(user_id: int):
    return _db_support.get_last_ofx_import_end_date_impl(get_conn, ensure_user, user_id)

# ─────────────────────────────────────────────
# AUTH — cadastro e login via email/senha
# ─────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def _check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False

def register_auth_user(email: str, password: str) -> dict:
    return _db_support.register_auth_user_impl(
        get_conn,
        get_or_create_canonical_user,
        create_link_code,
        _hash_password,
        email,
        password,
    )


def login_auth_user(email: str, password: str) -> dict | None:
    return _db_support.login_auth_user_impl(get_conn, _check_password, email, password)


def get_auth_user(user_id: int) -> dict | None:
    return _db_support.get_auth_user_impl(get_conn, user_id)


def auto_link_auth_user(target_user_id: int, current_user_id: int) -> int:
    """
    Vincula automaticamente a conta autenticada atual ao usuário dono do link do bot.
    Mantém o target_user_id como primário para preservar os dados já existentes do bot.
    """
    if int(target_user_id) == int(current_user_id):
        return int(target_user_id)

    target_has_auth = get_auth_user(int(target_user_id)) is not None
    if target_has_auth:
        return int(target_user_id)

    merge_users(int(current_user_id), int(target_user_id))
    return int(target_user_id)


# ─── Dashboard short links ────────────────────────────────────────────────────

def create_dashboard_session(user_id: int, hours: float = 0.25) -> str:
    return _db_support.create_dashboard_session_impl(get_conn, user_id, hours)


def get_dashboard_session(code: str) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id
                from dashboard_sessions
                where code = %s and expires_at > now()
                """,
                (code,),
            )
            row = cur.fetchone()
    return row["user_id"] if row else None


def consume_dashboard_session(code: str) -> int | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                delete from dashboard_sessions
                where code = %s and expires_at > now()
                returning user_id
                """,
                (code,),
            )
            row = cur.fetchone()
        conn.commit()
    return row["user_id"] if row else None


# ─── Billing / planos ─────────────────────────────────────────────────────────

def update_user_plan(user_id: int, plan: str, expires_at=None) -> None:
    return _db_support.update_user_plan_impl(get_conn, user_id, plan, expires_at)


def get_user_by_stripe_customer(stripe_customer_id: str) -> int | None:
    return _db_support.get_user_by_stripe_customer_impl(get_conn, stripe_customer_id)


def set_stripe_customer(user_id: int, stripe_customer_id: str) -> None:
    return _db_support.set_stripe_customer_impl(get_conn, user_id, stripe_customer_id)


# ─── VERIFICAÇÃO DE EMAIL NO CADASTRO ────────────────────────────────────────

def create_email_verification(email: str, password: str, phone: str, minutes_valid: int = 15) -> str:
    phone_e164 = normalize_phone_e164(phone)
    return _db_support.create_email_verification_impl(
        get_conn,
        _hash_password,
        email,
        password,
        phone_e164,
        minutes_valid,
    )


def confirm_email_verification(email: str, code: str) -> dict:
    return _db_support.confirm_email_verification_impl(
        get_conn,
        get_or_create_canonical_user,
        create_link_code,
        email,
        code,
    )


def attempt_whatsapp_phone_link(wa_id: str, current_user_id: int | None = None) -> dict:
    try:
        wa_phone = normalize_phone_e164(wa_id)
    except ValueError:
        return {"status": "invalid_phone"}
    current_user_id = int(current_user_id) if current_user_id is not None else get_or_create_canonical_user("whatsapp", wa_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id, phone_e164
                from auth_accounts
                where phone_e164 = %s
                """,
                (wa_phone,),
            )
            matches = cur.fetchall() or []

            cur.execute(
                """
                select external_id
                from user_identities
                where provider = 'whatsapp' and user_id = %s
                limit 1
                """,
                (current_user_id,),
            )
            existing_current_wa = cur.fetchone()

    if not matches:
        return {"status": "no_match", "wa_phone": wa_phone}

    if len(matches) > 1:
        return {"status": "multiple_accounts", "wa_phone": wa_phone}

    target_user_id = int(matches[0]["user_id"])

    if current_user_id != target_user_id and get_auth_user(int(current_user_id)) is not None:
        return {"status": "wa_linked_other_account", "wa_phone": wa_phone}

    existing_target = get_auth_user(target_user_id)
    if existing_target:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select external_id
                    from user_identities
                    where provider = 'whatsapp' and user_id = %s
                    limit 1
                    """,
                    (target_user_id,),
                )
                target_wa = cur.fetchone()

                if target_wa and target_wa["external_id"] != wa_id:
                    return {
                        "status": "account_has_other_whatsapp",
                        "wa_phone": wa_phone,
                    }

    final_user_id = link_platform_identity("whatsapp", wa_id, target_user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update auth_accounts
                set phone_status = 'confirmed',
                    phone_confirmed_at = coalesce(phone_confirmed_at, now()),
                    whatsapp_verified_at = now()
                where user_id = %s
                """,
                (target_user_id,),
            )
        conn.commit()

    return {
        "status": "already_linked" if current_user_id == target_user_id and existing_current_wa else "linked",
        "user_id": int(final_user_id),
        "wa_phone": wa_phone,
    }


# ─── RECUPERAÇÃO DE SENHA ─────────────────────────────────────────────────────

def create_password_reset_token(email: str, minutes_valid: int = 30) -> str | None:
    return _db_support.create_password_reset_token_impl(get_conn, email, minutes_valid)


def consume_password_reset_token(token: str, new_password: str) -> bool:
    return _db_support.consume_password_reset_token_impl(get_conn, _hash_password, token, new_password)
