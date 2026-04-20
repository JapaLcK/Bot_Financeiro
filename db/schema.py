"""
db/schema.py — DDL e inicialização do banco de dados.
"""
from .connection import get_conn


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

          -- Movimentação interna
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
          closing_day int not null check (closing_day between 1 and 31),
          due_day int not null check (due_day between 1 and 31),
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
        do $$
        declare r record;
        begin
          if to_regclass('credit_cards') is null then return; end if;
          for r in select conname from pg_constraint
            where conrelid = 'credit_cards'::regclass and contype = 'c'
              and pg_get_constraintdef(oid) ilike '%closing_day%'
          loop execute format('alter table credit_cards drop constraint %I', r.conname); end loop;
          for r in select conname from pg_constraint
            where conrelid = 'credit_cards'::regclass and contype = 'c'
              and pg_get_constraintdef(oid) ilike '%due_day%'
          loop execute format('alter table credit_cards drop constraint %I', r.conname); end loop;
        end $$;
        """,
        """
        do $$
        begin
          if to_regclass('credit_cards') is null then return; end if;
          if not exists (select 1 from pg_constraint where conrelid = 'credit_cards'::regclass
            and conname = 'credit_cards_closing_day_check') then
            alter table credit_cards add constraint credit_cards_closing_day_check check (closing_day between 1 and 31);
          end if;
          if not exists (select 1 from pg_constraint where conrelid = 'credit_cards'::regclass
            and conname = 'credit_cards_due_day_check') then
            alter table credit_cards add constraint credit_cards_due_day_check check (due_day between 1 and 31);
          end if;
        end $$;
        """,
        """
        create table if not exists credit_bills (
          id bigserial primary key,
          user_id bigint references users(id) on delete cascade,
          card_id bigint not null references credit_cards(id) on delete cascade,
          period_start date not null,
          period_end date not null,
          status text not null default 'open',
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
          tipo text not null default 'credito',
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
        # OFX import log
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
        # Identity link
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

        # ─── Engagement tracking ──────────────────────────────────────────────────
        """
        alter table auth_accounts add column if not exists
          engagement_opt_out boolean not null default false
        """,
        """
        alter table auth_accounts add column if not exists last_activity_at timestamptz
        """,
        """
        alter table auth_accounts add column if not exists last_tip_sent_at timestamptz
        """,
        """
        alter table auth_accounts add column if not exists last_insight_sent_at timestamptz
        """,
        """
        alter table auth_accounts add column if not exists last_reengagement_sent_at timestamptz
        """,

        # ─── Limite de crédito ────────────────────────────────────────────────────
        """
        alter table credit_cards add column if not exists credit_limit numeric
        """,
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            for i, stmt in enumerate(ddl_statements, 1):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    print(f"[init_db] erro no statement #{i}: {e}")
                    print(stmt)
                    raise
        conn.commit()
    print("[init_db] OK")
