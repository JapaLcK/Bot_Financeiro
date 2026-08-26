"""
db/schema.py — DDL e inicialização do banco de dados.
"""
from .connection import get_conn
from .schema_repairs import ensure_plan_trials_user_fk, repair_user_fk_cascades

# Chave do advisory lock que serializa o init_db INTEIRO entre instâncias.
# Valor arbitrário e estável; só precisa não colidir com outro lock do processo.
SCHEMA_INIT_LOCK = 728_531_004


def init_db():
    ddl_statements = [
        # ─── Extensions ──────────────────────────────────────────────────────────
        # unaccent: normaliza acentos pra busca textual ("credito" casa "crédito").
        # Usado em db/analytics.py:list_history.
        """create extension if not exists unaccent""",

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

        # Push notifications (app iOS) — device token do APNs por aparelho.
        # unique(token): um device que re-registra atualiza o dono (troca de
        # conta no mesmo aparelho). environment separa sandbox/production do APNs
        # porque o mesmo token NÃO vale nos dois ambientes.
        """
        create table if not exists push_tokens (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          token text not null unique,
          platform text not null default 'ios',
          environment text not null default 'production',
          created_at timestamptz default now(),
          updated_at timestamptz default now()
        )
        """,
        """create index if not exists idx_push_tokens_user on push_tokens(user_id)""",
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

        # Sprint 5 — Metas: cada caixinha pode (opcionalmente) virar uma meta
        # com target_amount/target_date + emoji/color/status. Sem target = só
        # guardar dinheiro sem objetivo. Filtro `target_amount IS NOT NULL` na
        # view Metas, todas aparecem na view Caixinhas.
        """alter table pockets add column if not exists target_amount numeric""",
        """alter table pockets add column if not exists target_date date""",
        """alter table pockets add column if not exists emoji text""",
        """alter table pockets add column if not exists color text""",
        """alter table pockets add column if not exists status text not null default 'active'""",
        """
        create table if not exists investments (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          name text not null,
          balance numeric not null default 0,
          rate numeric not null,
          period text not null, -- daily|monthly|yearly|cdi|cdi_spread|ipca_spread|selic_spread
          last_date date not null,
          asset_type text not null default 'CDB',
          indexer text,
          issuer text,
          purchase_date date,
          maturity_date date,
          interest_payment_frequency text not null default 'maturity',
          tax_profile text not null default 'regressive_ir_iof',
          created_at timestamptz default now(),
          unique(user_id, name)
        )
        """,
        """
        create table if not exists investment_lots (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          investment_id bigint not null references investments(id) on delete cascade,
          principal_initial numeric not null,
          principal_remaining numeric not null,
          balance numeric not null,
          opened_at date not null,
          last_date date not null,
          status text not null default 'open',
          closed_at date,
          created_at timestamptz default now()
        )
        """,
        """
        create index if not exists idx_investment_lots_user_investment_opened
          on investment_lots(user_id, investment_id, status, opened_at, id)
        """,
        # Per-lot rate/period: cada aporte pode travar uma taxa diferente
        # (Tesouro IPCA+/Prefixado, Debêntures, CRI/CRA etc.).
        """
        alter table investment_lots add column if not exists rate numeric
        """,
        """
        alter table investment_lots add column if not exists period text
        """,
        """
        alter table investment_lots add column if not exists maturity_date date
        """,
        # Backfill: lotes legados herdam a taxa do investimento-pai.
        # Mantém comportamento idêntico ao do accrual antigo.
        """
        update investment_lots l
           set rate = i.rate, period = i.period
          from investments i
         where l.investment_id = i.id
           and l.rate is null
        """,
        """
        alter table investments add column if not exists asset_type text not null default 'CDB'
        """,
        """
        alter table investments add column if not exists indexer text
        """,
        """
        alter table investments add column if not exists issuer text
        """,
        """
        alter table investments add column if not exists purchase_date date
        """,
        """
        alter table investments add column if not exists maturity_date date
        """,
        """
        alter table investments add column if not exists interest_payment_frequency text not null default 'maturity'
        """,
        """
        alter table investments add column if not exists tax_profile text not null default 'regressive_ir_iof'
        """,
        """
        alter table pockets add column if not exists description text
        """,
        """
        alter table pockets add column if not exists interest_enabled boolean not null default true
        """,
        """
        alter table pockets add column if not exists interest_rate numeric not null default 1
        """,
        """
        alter table pockets add column if not exists interest_period text not null default 'cdi'
        """,
        """
        alter table pockets add column if not exists interest_tax_profile text not null default 'regressive_ir_iof'
        """,
        """
        alter table pockets add column if not exists last_interest_date date not null default current_date
        """,
        """
        create table if not exists pocket_lots (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          pocket_id bigint not null references pockets(id) on delete cascade,
          principal_initial numeric not null,
          principal_remaining numeric not null,
          balance numeric not null,
          opened_at date not null,
          last_date date not null,
          status text not null default 'open',
          closed_at date,
          created_at timestamptz default now()
        )
        """,
        """
        create index if not exists idx_pocket_lots_user_pocket_opened
          on pocket_lots(user_id, pocket_id, status, opened_at, id)
        """,
        """
        insert into pocket_lots(
          user_id, pocket_id, principal_initial, principal_remaining,
          balance, opened_at, last_date, status
        )
        select p.user_id, p.id, p.balance, p.balance, p.balance,
               coalesce(p.last_interest_date, current_date),
               coalesce(p.last_interest_date, current_date),
               'open'
          from pockets p
         where p.balance > 0
           and not exists (
             select 1 from pocket_lots l
              where l.user_id = p.user_id and l.pocket_id = p.id
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
        -- migration: marca retroativamente aportes, resgates e categorias de investimento como movimentações internas
        update launches set is_internal_movement = true
        where (
          tipo in ('aporte_investimento', 'resgate_investimento', 'deposito_caixinha', 'saque_caixinha')
          or lower(coalesce(categoria, '')) in (
            'investimentos', 'investimento',
            'criptomoedas', 'criptomoeda', 'cripto',
            'bitcoin', 'btc', 'ethereum', 'eth', 'solana', 'sol'
          )
        )
        and is_internal_movement = false
        """,
        # ──────────────────────────────────────────────────────────────────
        # user_seq: sequência por-usuário para que cada usuário veja seus
        # lançamentos como #1, #2, #3... independente do id global.
        # ──────────────────────────────────────────────────────────────────
        """
        alter table launches add column if not exists user_seq integer
        """,
        """
        -- backfill: numera lançamentos existentes na ordem de criação
        with ordered as (
          select id,
                 row_number() over (partition by user_id order by criado_em, id) as seq
          from launches
        )
        update launches l set user_seq = ordered.seq
        from ordered
        where l.id = ordered.id and l.user_seq is null
        """,
        """
        create unique index if not exists uq_launches_user_seq
          on launches(user_id, user_seq)
        """,
        """
        create or replace function assign_launch_user_seq()
        returns trigger as $$
        begin
          if new.user_seq is null then
            -- serializa por usuário pra evitar race entre INSERTs concorrentes;
            -- o lock é liberado no fim da transação.
            perform pg_advisory_xact_lock(new.user_id);
            select coalesce(max(user_seq), 0) + 1
              into new.user_seq
              from launches
              where user_id = new.user_id;
          end if;
          return new;
        end;
        $$ language plpgsql
        """,
        """
        drop trigger if exists trg_assign_launch_user_seq on launches
        """,
        """
        create trigger trg_assign_launch_user_seq
          before insert on launches
          for each row
          execute function assign_launch_user_seq()
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
        # Sugestões de "gasto fixo" que o usuário recusou. Quando uma despesa se
        # repete (mesma descrição + valor em meses distintos) a Piggy oferece
        # marcar como recorrente; se o user diz "não", grava aqui pra não
        # re-perguntar a mesma combinação (merchant + valor). Ver
        # find_recurring_candidate em db/recurring.py.
        """
        create table if not exists recurring_suggestion_dismissed (
          user_id bigint not null references users(id) on delete cascade,
          merchant_key text not null,
          amount numeric not null,
          dismissed_at timestamptz not null default now(),
          primary key (user_id, merchant_key, amount)
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
        # Notícias financeiras curadas pelo news_bot (core/services/news_bot.py).
        # Curadoria (link-out): guardamos só título + resumo ORIGINAL gerado por
        # LLM + link pra fonte. NUNCA o corpo do artigo (conteúdo de terceiros).
        # `source_url` é unique → idempotência do coletor (ON CONFLICT DO NOTHING).
        """
        create table if not exists news_posts (
          id bigserial primary key,
          source text not null,
          source_url text not null unique,
          title text not null,
          summary text not null,
          category text,
          thumb_emoji text,
          published_at timestamptz,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_news_posts_published
          on news_posts (published_at desc nulls last, id desc)
        """,
        # image_url veio depois do create original — ADD COLUMN IF NOT EXISTS
        # cobre bancos que já criaram news_posts sem a coluna (idempotente).
        """
        alter table news_posts add column if not exists image_url text
        """,
        """
        create table if not exists open_finance_connections (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          provider text not null,
          provider_item_id text not null,
          status text not null,
          institution_id text not null,
          institution_name text not null,
          consent_url text,
          consent_expires_at timestamptz,
          last_sync_at timestamptz,
          raw jsonb,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          unique(user_id, provider, provider_item_id)
        )
        """,
        """
        create table if not exists open_finance_accounts (
          id bigserial primary key,
          connection_id bigint not null references open_finance_connections(id) on delete cascade,
          provider_account_id text not null,
          name text not null,
          type text not null,
          subtype text,
          currency text not null default 'BRL',
          balance numeric not null default 0,
          raw jsonb,
          updated_at timestamptz not null default now(),
          unique(connection_id, provider_account_id)
        )
        """,
        """
        create table if not exists open_finance_transactions (
          id bigserial primary key,
          account_id bigint not null references open_finance_accounts(id) on delete cascade,
          provider_transaction_id text not null,
          description text not null,
          amount numeric not null,
          transaction_date date not null,
          category text,
          raw jsonb,
          imported_launch_id bigint references launches(id) on delete set null,
          created_at timestamptz not null default now(),
          unique(account_id, provider_transaction_id)
        )
        """,
        """
        create index if not exists idx_open_finance_connections_user
          on open_finance_connections(user_id, status)
        """,
        """
        create index if not exists idx_open_finance_transactions_account_date
          on open_finance_transactions(account_id, transaction_date desc)
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
        # resumos periódicos: dedup do envio semanal (segunda) e mensal (dia 1)
        """
        alter table daily_report_prefs add column if not exists last_weekly_sent_date date;
        """,
        """
        alter table daily_report_prefs add column if not exists last_monthly_sent_date date;
        """,
        # resumos periódicos: liga/desliga independente (default ligado, igual ao diário)
        """
        alter table daily_report_prefs add column if not exists weekly_enabled boolean not null default true;
        """,
        """
        alter table daily_report_prefs add column if not exists monthly_enabled boolean not null default true;
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
        # Open Finance ↔ crédito (Fase 1 opção a): dedup + vínculo OF→cartão
        # -----------------------------
        """
        alter table credit_transactions add column if not exists source text not null default 'manual'
        """,
        """
        alter table credit_transactions add column if not exists external_id text
        """,
        """
        create unique index if not exists uq_credit_tx_source_external
          on credit_transactions(user_id, source, external_id)
          where external_id is not null
        """,
        """
        alter table credit_cards add column if not exists open_finance_account_id bigint
          references open_finance_accounts(id) on delete set null
        """,
        """
        create unique index if not exists uq_credit_cards_of_account
          on credit_cards(open_finance_account_id)
          where open_finance_account_id is not null
        """,
        """
        alter table open_finance_transactions add column if not exists imported_credit_tx_id bigint
          references credit_transactions(id) on delete set null
        """,

        # -----------------------------
        # Open Finance — investimentos (#10): espelho pra caixinha (CDB) etc.
        # -----------------------------
        """
        create table if not exists open_finance_investments (
          id bigserial primary key,
          connection_id bigint not null references open_finance_connections(id) on delete cascade,
          provider_investment_id text not null,
          name text,
          type text,
          subtype text,
          currency text default 'BRL',
          balance numeric,
          raw jsonb,
          updated_at timestamptz default now(),
          unique(connection_id, provider_investment_id)
        )
        """,

        # -----------------------------
        # Banqueiro (agente cofre): vincula uma caixinha/meta do PigBank a um
        # investimento OF real (Caixinha do Nubank/PicPay = CDB). of_last_seen_balance
        # guarda o saldo já contabilizado pelo Banqueiro pra detectar o aporte por
        # delta entre syncs (open_finance_investments.balance é sobrescrito, sem
        # histórico). ALTER aqui (não no bloco de criação da pockets, lá em cima)
        # porque o FK exige que open_finance_investments já exista.
        # -----------------------------
        """
        alter table pockets add column if not exists of_investment_id bigint
          references open_finance_investments(id) on delete set null
        """,
        """
        alter table pockets add column if not exists of_last_seen_balance numeric
        """,
        # Baseline do RENDIMENTO acumulado (open_finance_investments.raw->amountProfit)
        # já contabilizado pelo Banqueiro. Serve pra separar aporte (você guardou) de
        # rendimento (rendeu) no delta de saldo — senão um bom mês de juros vira "aporte".
        """
        alter table pockets add column if not exists of_last_seen_profit numeric
        """,
        # source='open_finance' marca caixinhas AUTO-CRIADAS a partir do banco (via
        # sync do Open Finance): read-only, saldo espelhado, sem juros interno. As
        # criadas pelo usuário ficam 'manual' (mesmo quando vinculadas a uma caixinha OF).
        """
        alter table pockets add column if not exists source text not null default 'manual'
        """,

        # -----------------------------
        # Open Finance — reconciliação (Fase 2): dedup OF ↔ manual
        # -----------------------------
        """
        alter table open_finance_transactions add column if not exists reconciliation_status text
        """,
        """
        alter table open_finance_transactions add column if not exists match_launch_id bigint
          references launches(id) on delete set null
        """,

        # -----------------------------
        # Open Finance — instante real da transação (com hora), quando o banco
        # envia. NULL = só data (cai no fallback de meia-dia local no import).
        # -----------------------------
        """
        alter table open_finance_transactions add column if not exists transacted_at timestamptz
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
        alter table auth_accounts add column if not exists last_payment_status text not null default 'inactive'
        """,
        # Marca de "credenciais trocadas" (reset de senha). Usada pra invalidar
        # tokens legados SEM jti após um reset — os com jti já morrem via
        # revogação de sessão, mas os grandfathered sem jti não têm o que revogar.
        """
        alter table auth_accounts add column if not exists password_changed_at timestamptz
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
        alter table email_verification_codes add column if not exists display_name text
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
        """
        create table if not exists data_export_tokens (
          token text primary key,
          user_id bigint not null references users(id) on delete cascade,
          expires_at timestamptz not null,
          used_at timestamptz,
          request_ip text,
          request_user_agent text,
          delivered_to_email text,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_data_export_tokens_expires on data_export_tokens (expires_at)
        """,
        """
        create index if not exists idx_data_export_tokens_user on data_export_tokens (user_id, created_at desc)
        """,
        """
        create table if not exists auth_rate_limits (
          bucket text not null,
          identifier text not null,
          window_started_at timestamptz not null default now(),
          attempts int not null default 0,
          updated_at timestamptz not null default now(),
          primary key (bucket, identifier)
        )
        """,
        """
        create index if not exists idx_auth_rate_limits_updated_at
          on auth_rate_limits (updated_at)
        """,

        # ─── MFA (TOTP) ───────────────────────────────────────────────────────────
        """
        create table if not exists user_mfa (
          user_id bigint primary key references users(id) on delete cascade,
          secret_encrypted text not null,
          enabled boolean not null default false,
          activated_at timestamptz,
          last_used_at timestamptz,
          created_at timestamptz not null default now()
        )
        """,
        """
        create table if not exists user_mfa_backup_codes (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          code_hash text not null,
          used_at timestamptz,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_user_mfa_backup_codes_user
          on user_mfa_backup_codes (user_id, used_at)
        """,
        # Tabela de challenge: armazena state intermediario entre login (senha OK)
        # e validacao TOTP. Token de uso unico, expira em 5min.
        """
        create table if not exists mfa_login_challenges (
          token text primary key,
          user_id bigint not null references users(id) on delete cascade,
          expires_at timestamptz not null,
          used_at timestamptz,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_mfa_login_challenges_expires
          on mfa_login_challenges (expires_at)
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
        alter table auth_accounts add column if not exists
          tip_email_opt_out boolean not null default false
        """,
        """
        alter table auth_accounts add column if not exists last_insight_sent_at timestamptz
        """,
        """
        alter table auth_accounts add column if not exists
          insight_email_opt_out boolean not null default false
        """,
        """
        alter table auth_accounts add column if not exists
          whatsapp_updates_opt_out boolean not null default false
        """,
        """
        alter table auth_accounts add column if not exists deletion_requested_at timestamptz
        """,
        """
        alter table auth_accounts add column if not exists deletion_scheduled_for timestamptz
        """,
        """
        alter table auth_accounts add column if not exists deletion_status text
        """,
        """
        alter table auth_accounts add column if not exists display_name text
        """,
        """
        alter table auth_accounts add column if not exists deletion_processing_started_at timestamptz
        """,
        """
        create index if not exists idx_auth_accounts_deletion_due
          on auth_accounts (deletion_scheduled_for)
          where deletion_status = 'scheduled'
        """,
        """
        create index if not exists idx_auth_accounts_deletion_processing
          on auth_accounts (deletion_processing_started_at)
          where deletion_status = 'processing'
        """,
        """
        update auth_accounts
        set tip_email_opt_out = true,
            insight_email_opt_out = true
        where engagement_opt_out = true
          and tip_email_opt_out = false
          and insight_email_opt_out = false
        """,
        """
        alter table auth_accounts add column if not exists last_reengagement_sent_at timestamptz
        """,

        # ─── Limite de crédito ────────────────────────────────────────────────────
        """
        alter table credit_cards add column if not exists credit_limit numeric
        """,

        # ─── Personalização visual do cartão (color/flag/last4) ───────────────────
        # color: chave da paleta predefinida (purple|coral|gold|green|blue|gray)
        # flag: bandeira (Visa|Mastercard|Elo|Amex|Hipercard|Outros)
        # last4: últimos 4 dígitos (opcional, só pra distinguir cartões)
        """
        alter table credit_cards add column if not exists color text
        """,
        """
        alter table credit_cards add column if not exists flag text
        """,
        """
        alter table credit_cards add column if not exists last4 text
        """,
        """
        do $$
        begin
          if not exists (select 1 from pg_constraint where conrelid = 'credit_cards'::regclass
            and conname = 'credit_cards_last4_check') then
            alter table credit_cards add constraint credit_cards_last4_check
              check (last4 is null or last4 ~ '^[0-9]{4}$');
          end if;
        end $$;
        """,

        # ─── Ordem manual de exibição (drag-to-reorder) ───────────────────────────
        # Cartões sem display_order definido (NULL) ficam no final, ordenados por
        # nome — preserva comportamento antigo até user reordenar.
        """
        alter table credit_cards add column if not exists display_order int
        """,

        # ─── OFX import de fatura: deduplicação em credit_transactions ────────────
        """
        alter table credit_transactions add column if not exists source text not null default 'manual'
        """,
        """
        alter table credit_transactions add column if not exists external_id text
        """,
        """
        create unique index if not exists uq_credit_tx_ofx_external
          on credit_transactions(user_id, card_id, external_id)
          where source = 'ofx' and external_id is not null
        """,

        # ─── OAuth (Google login) ────────────────────────────────────────────────
        # Contas criadas via Google ficam com password_hash NULL.
        """
        alter table auth_accounts alter column password_hash drop not null
        """,
        # Onboarding MFA: vira not null timestampt apos o user ver e responder
        # (sim/nao) a tela de incentivo. NULL = ainda nao viu.
        """
        alter table auth_accounts add column if not exists mfa_onboarding_shown_at timestamptz
        """,
        """
        create table if not exists auth_identities (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          provider text not null,
          provider_sub text not null,
          email text,
          created_at timestamptz not null default now(),
          unique (provider, provider_sub)
        )
        """,
        """
        create index if not exists idx_auth_identities_user
          on auth_identities (user_id)
        """,
        """
        create table if not exists pending_google_signups (
          token text primary key,
          provider text not null,
          provider_sub text not null,
          email text not null,
          name_hint text,
          expires_at timestamptz not null,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_pending_google_signups_expires
          on pending_google_signups (expires_at)
        """,

        # ─── Sessoes ativas do dashboard ────────────────────────────────────────
        # Cada login bem-sucedido insere uma row aqui e o JWT carrega o jti.
        # Permite "lista de dispositivos" + revogacao individual / "encerrar
        # todas as outras". Helper em core/sessions.py.
        """
        create table if not exists auth_sessions (
          jti text primary key,
          user_id bigint not null references users(id) on delete cascade,
          ip text,
          user_agent text,
          created_at timestamptz not null default now(),
          last_seen_at timestamptz not null default now(),
          revoked_at timestamptz
        )
        """,
        """
        create index if not exists idx_auth_sessions_user_active
          on auth_sessions (user_id, revoked_at, last_seen_at desc)
        """,

        # ─── Alertas de orcamento ja enviados ───────────────────────────────────
        # Dedup: cada (user, categoria, mes, threshold) so dispara uma vez.
        # Helper em core/budget_alerts.py.
        """
        create table if not exists budget_alert_sent (
          user_id   bigint not null references users(id) on delete cascade,
          categoria text not null,
          ym        text not null,            -- 'YYYY-MM' do criado_em do gasto
          threshold int  not null,            -- 80, 100, 120
          sent_at   timestamptz not null default now(),
          primary key (user_id, categoria, ym, threshold)
        )
        """,

        # ─── Audit log (forense focado no usuario) ──────────────────────────────
        # Distinto de system_event_logs (operacional). Cobre acoes sensiveis:
        # senha, email, MFA, Open Finance, login de IP novo. Helper em core/audit.py.
        """
        create table if not exists audit_events (
          id bigserial primary key,
          user_id bigint references users(id) on delete cascade,
          event text not null,
          ip text,
          user_agent text,
          created_at timestamptz not null default now(),
          details jsonb not null default '{}'::jsonb
        )
        """,
        """
        create index if not exists idx_audit_events_user_created
          on audit_events (user_id, created_at desc)
        """,
        """
        create index if not exists idx_audit_events_user_event
          on audit_events (user_id, event, created_at desc)
        """,

        # ─── Orcamentos por categoria ───────────────────────────────────────────
        # Centralizada em init_db para existir tambem nos testes.
        """
        create table if not exists category_budgets (
          id        bigserial primary key,
          user_id   bigint  not null references users(id) on delete cascade,
          categoria text    not null,
          budget    numeric not null check (budget > 0),
          created_at timestamptz default now(),
          unique (user_id, categoria)
        )
        """,

        # ─── Metadata visual das categorias (Sprint 3) ──────────────────────────
        # name = chave funcional lowercase, batendo com launches.categoria (sem FK).
        # is_system = seed lazy das 14 canonicas (controle de idempotencia).
        # Rename emite UPDATE em cascata em launches/category_budgets/budget_alert_sent.
        """
        create table if not exists user_categories (
          id          bigserial primary key,
          user_id     bigint  not null references users(id) on delete cascade,
          name        text    not null,
          emoji       text    not null default '🏷️',
          color       text    not null default '#7c3aed',
          is_archived boolean not null default false,
          is_system   boolean not null default false,
          created_at  timestamptz not null default now(),
          unique (user_id, name)
        )
        """,

        # ─── Gastos Fixos / Recorrentes (Sprint 4) ──────────────────────────────
        # Pro-only. Cobrança automática via cron no dia `due_day` de cada mês.
        # `last_charged_ym` = idempotência (não cobra 2x no mesmo mês).
        # `last_amount` + `last_amount_changed_at` = detector de reajuste quando user edita.
        # `payment_type='credit_card'` → cria credit_transaction na bill open atual.
        # `payment_type='account'`     → cria launch despesa.
        """
        create table if not exists recurring_expenses (
          id          bigserial primary key,
          user_id     bigint  not null references users(id) on delete cascade,
          name        text    not null,
          amount      numeric not null check (amount > 0),
          category    text    not null,
          due_day     int     not null check (due_day between 1 and 31),
          payment_type text   not null check (payment_type in ('account', 'credit_card')),
          card_id     bigint  references credit_cards(id) on delete set null,
          is_essential boolean not null default false,
          is_active   boolean not null default true,
          last_amount numeric,
          last_amount_changed_at timestamptz,
          last_charged_ym text,
          notes       text,
          created_at  timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_recurring_user_active
          on recurring_expenses (user_id, is_active)
        """,

        # Histórico de cobranças automáticas. Garante idempotência via unique
        # (recurring_id, ym) + serve pra alertas no banner do dashboard até user
        # marcar como visto (acknowledged=true).
        """
        create table if not exists recurring_charges (
          id           bigserial primary key,
          recurring_id bigint not null references recurring_expenses(id) on delete cascade,
          user_id      bigint not null references users(id) on delete cascade,
          launch_id    bigint references launches(id) on delete set null,
          credit_tx_id bigint references credit_transactions(id) on delete set null,
          amount       numeric not null,
          ym           text not null,
          charged_at   timestamptz not null default now(),
          acknowledged boolean not null default false,
          unique (recurring_id, ym)
        )
        """,
        """
        create index if not exists idx_recurring_charges_user_ack
          on recurring_charges (user_id, acknowledged)
        """,

        # ─── Receitas Recorrentes ──────────────────────────────────────────────
        # Espelho de `recurring_expenses` do lado da entrada. Pro-only, mesma flag
        # (recurring_expenses_enabled). Lança receita na conta no dia `pay_day`.
        # Não tem payment_type/card_id: receita sempre cai na conta.
        # `is_primary` = renda principal (salário) vs extra (freela, aluguel).
        # `last_credited_ym` = idempotência (não credita 2x no mesmo mês).
        """
        create table if not exists recurring_incomes (
          id          bigserial primary key,
          user_id     bigint  not null references users(id) on delete cascade,
          name        text    not null,
          amount      numeric not null check (amount > 0),
          category    text    not null,
          pay_day     int     not null check (pay_day between 1 and 31),
          is_primary  boolean not null default false,
          is_active   boolean not null default true,
          last_amount numeric,
          last_amount_changed_at timestamptz,
          last_credited_ym text,
          notes       text,
          created_at  timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_recurring_income_user_active
          on recurring_incomes (user_id, is_active)
        """,

        # Histórico de créditos automáticos. Idempotência via unique (income_id, ym)
        # + alimenta o banner do dashboard até user marcar como visto.
        """
        create table if not exists recurring_income_credits (
          id           bigserial primary key,
          income_id    bigint not null references recurring_incomes(id) on delete cascade,
          user_id      bigint not null references users(id) on delete cascade,
          launch_id    bigint references launches(id) on delete set null,
          amount       numeric not null,
          ym           text not null,
          credited_at  timestamptz not null default now(),
          acknowledged boolean not null default false,
          unique (income_id, ym)
        )
        """,
        """
        create index if not exists idx_recurring_income_credits_user_ack
          on recurring_income_credits (user_id, acknowledged)
        """,

        # migration: data de início da recorrência (escolhida pelo user). A
        # cobrança/crédito só começa a valer a partir dela — evita retroagir um
        # vencimento que já passou antes do cadastro. Backfill = created_at::date
        # (só toca linhas legadas: novas linhas já nascem com start_date).
        """alter table recurring_expenses add column if not exists start_date date""",
        """update recurring_expenses set start_date = created_at::date where start_date is null""",
        """alter table recurring_incomes add column if not exists start_date date""",
        """update recurring_incomes set start_date = created_at::date where start_date is null""",

        # migration: frequência da recorrência. 'monthly' (default, comportamento
        # antigo) cobra/credita todo mês no due_day/pay_day; 'annual' cobra 1x por
        # ano no mês due_month/pay_month (1-12). Linhas legadas ficam 'monthly'.
        """alter table recurring_expenses add column if not exists frequency text not null default 'monthly'""",
        """alter table recurring_expenses add column if not exists due_month int""",
        """alter table recurring_incomes add column if not exists frequency text not null default 'monthly'""",
        """alter table recurring_incomes add column if not exists pay_month int""",

        # migration: modo de pagamento do recorrente.
        #   'autopay' (default, comportamento antigo) → o charger LANÇA sozinho no dia.
        #   'manual'  (conta a pagar / boleto)        → NÃO lança; a Piggy lembra e
        #     só lança quando o user confirma o pagamento. Aparece na sub-aba
        #     "Contas a pagar". Cada ciclo vira uma linha em bill_instances.
        """alter table recurring_expenses add column if not exists payment_mode text not null default 'autopay'""",

        # Conta a pagar de valor VARIÁVEL (água, luz, gás): o `amount` cadastrado
        # é só uma estimativa; o valor real é informado na hora de pagar. Muda o
        # lembrete ("~R$ 80 (varia)") e faz o botão "Já paguei" perguntar o valor
        # em vez de lançar a estimativa.
        """alter table recurring_expenses add column if not exists variable_amount boolean not null default false""",

        # Conta a pagar de valor variável pode nascer SEM estimativa (amount=0).
        # A check original exigia amount > 0 — relaxa pra >= 0. O código continua
        # exigindo > 0 pra gasto fixo (autopay), então 0 só acontece em conta a
        # pagar variável sem estimativa.
        """alter table recurring_expenses drop constraint if exists recurring_expenses_amount_check""",
        """alter table recurring_expenses add constraint recurring_expenses_amount_check check (amount >= 0)""",

        # Instâncias de conta a pagar (1 por ciclo do recorrente 'manual').
        # amount é editável (contas de luz/água variam). status: pending | paid.
        # 'vencida' é derivado (pending + due_date < hoje). reminder_last_sent_on
        # deduplica o lembrete diário (mesmo padrão do lembrete de cartão).
        """
        create table if not exists bill_instances (
          id            bigserial primary key,
          recurring_id  bigint not null references recurring_expenses(id) on delete cascade,
          user_id       bigint not null references users(id) on delete cascade,
          due_date      date   not null,
          amount        numeric not null,
          status        text   not null default 'pending',
          paid_at       timestamptz,
          paid_amount   numeric,
          launch_id     bigint references launches(id) on delete set null,
          reminder_last_sent_on date,
          created_at    timestamptz not null default now(),
          unique (recurring_id, due_date)
        )
        """,
        """
        create index if not exists idx_bill_instances_user_status
          on bill_instances (user_id, status, due_date)
        """,

        # Boleto AVULSO (agenda de boletos — futuro PigBank Enterprise): um boleto
        # não precisa vir de um recorrente. recurring_id vira nullable e o boleto
        # carrega name/category próprios. Boletos recorrentes seguem com
        # recurring_id preenchido e (opcionalmente) name/category via join.
        # UNIQUE(recurring_id, due_date) tolera vários avulsos no mesmo dia porque
        # NULL != NULL no Postgres.
        """alter table bill_instances alter column recurring_id drop not null""",
        """alter table bill_instances add column if not exists name text""",
        """alter table bill_instances add column if not exists category text""",
        """
        create index if not exists idx_bill_instances_user_due
          on bill_instances (user_id, due_date)
        """,

        # ─── Eventos de login (admin/observabilidade) ───────────────────────────
        # Antes era criada lazy em core/admin_dashboard.py:ensure_admin_tables.
        # Trazido pra schema.py pra audit/IP-tracking funcionarem nos testes.
        """
        create table if not exists auth_login_events (
          id bigserial primary key,
          user_id bigint references users(id) on delete set null,
          email text,
          success boolean not null,
          ip_address text,
          user_agent text,
          failure_reason text,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_auth_login_events_created_at
          on auth_login_events (created_at desc)
        """,
        """
        create index if not exists idx_auth_login_events_user_success
          on auth_login_events (user_id, success, created_at desc)
        """,

        # ─── Chat IA (Pro v1 Fase 2 — Bloco A) ──────────────────────────────────
        # Schema flat (sem multi-thread): cada user tem uma única conversa
        # linear. Contexto da IA = sliding window das últimas N mensagens.
        # role: 'user' | 'assistant' | 'tool'
        # tool_calls/tool_call_id seguem o protocolo da OpenAI function calling.
        """
        create table if not exists ai_messages (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          role text not null,
          content text,
          tool_calls jsonb,
          tool_call_id text,
          tool_name text,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_ai_messages_user_created
          on ai_messages (user_id, created_at desc)
        """,

        # Pending action: write proposto pela IA aguardando confirmação do user.
        # Quando o user manda "sim" / "confirma", a ação é executada e a linha
        # é apagada. Quando manda "não" / "cancela", também é apagada sem
        # executar. Expira automaticamente após 10min (limpeza lazy).
        """
        create table if not exists ai_pending_actions (
          user_id bigint primary key references users(id) on delete cascade,
          tool_name text not null,
          tool_args jsonb not null,
          summary text not null,
          created_at timestamptz not null default now()
        )
        """,

        # Telemetria de perguntas que a IA reconheceu como dentro de finanças
        # mas sem tool adequada. Alimenta decisão de quais tools criar.
        """
        create table if not exists ai_fallback_log (
          id bigserial primary key,
          user_id bigint references users(id) on delete cascade,
          question text not null,
          ai_reason text,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_ai_fallback_log_created
          on ai_fallback_log (created_at desc)
        """,
        """
        create index if not exists idx_ai_fallback_log_user
          on ai_fallback_log (user_id, created_at desc)
        """,

        # Rate limit mensal de mensagens IA por user.
        # Reset lazy: quando incrementa, checa se mudou o mês desde reset_at.
        """
        alter table auth_accounts
          add column if not exists ai_messages_this_month integer not null default 0
        """,
        """
        alter table auth_accounts
          add column if not exists ai_month_reset_at date
        """,

        # Sprint 7 — Cache de IA proativa (insights/padrões gerados via LLM).
        # `kind` ∈ {'insights', 'patterns'}. `payload` = JSON da lista de
        # narrativas. `generated_at` controla TTL (TTL é decidido em runtime).
        """
        create table if not exists ai_proactive_cache (
          user_id bigint not null references users(id) on delete cascade,
          kind text not null,
          payload jsonb not null,
          generated_at timestamptz not null default now(),
          primary key (user_id, kind)
        )
        """,
        """
        create index if not exists idx_ai_proactive_cache_generated
          on ai_proactive_cache (generated_at desc)
        """,

        # ─── Refresh tokens (auth: access curto 15min + refresh longo 14d) ─────
        # Sessão (auth_sessions) hospeda o jti. Refresh token é o "permite trocar
        # access expirado" — armazenamos só SHA-256 do plain token, nunca o valor.
        #
        # Lifecycle:
        #   - emit:    POST /auth/login → cria 1 row (issued_at=now, used_at=null)
        #   - rotate:  POST /auth/refresh → marca antigo como used_at=now,
        #              cria new row, mesma session_jti.
        #   - replay:  se refresh já com used_at tentar usar → revoga TODO o
        #              user (token roubado).
        #   - idle:    se auth_sessions.last_seen_at < now - 7d → rejeita +
        #              revoga sessão.
        #   - logout:  revoga refresh + revoga session (auth_sessions.revoked_at).
        """
        create table if not exists auth_refresh_tokens (
          token_hash text primary key,
          user_id bigint not null references users(id) on delete cascade,
          session_jti text not null,
          issued_at timestamptz not null default now(),
          expires_at timestamptz not null,
          used_at timestamptz,
          revoked_at timestamptz,
          ip text,
          user_agent text
        )
        """,
        """
        create index if not exists idx_auth_refresh_tokens_user_active
          on auth_refresh_tokens (user_id, revoked_at, used_at)
        """,
        """
        create index if not exists idx_auth_refresh_tokens_session
          on auth_refresh_tokens (session_jti)
        """,
        """
        create index if not exists idx_auth_refresh_tokens_expires
          on auth_refresh_tokens (expires_at)
        """,

        # ─── Cifragem column-level de PII (LGPD art. 46) ────────────────────────
        # Cada coluna PII pesquisável vira par (hash, enc):
        #   hash = HMAC-SHA256(plain, PEPPER) — indexável, irreversível, pro WHERE
        #   enc  = Fernet(plain, KEY) — reversível só com a chave, pro display/envio
        # Colunas só-display (display_name, name_hint) viram só *_enc.
        # Módulo: core/crypto.py. Migration de backfill: scripts/migrate_pii_to_encrypted.py
        # Durante a fase de migração, as colunas em claro (email, phone_e164,
        # external_id, display_name, name_hint) permanecem populadas em paralelo
        # — drop é Fase 5, depois de N dias rodando estável.

        # auth_accounts: email, phone_e164, display_name
        """alter table auth_accounts add column if not exists email_hash text""",
        """alter table auth_accounts add column if not exists email_enc text""",
        """alter table auth_accounts add column if not exists phone_hash text""",
        """alter table auth_accounts add column if not exists phone_enc text""",
        """alter table auth_accounts add column if not exists display_name_enc text""",
        """
        create unique index if not exists idx_auth_accounts_email_hash
          on auth_accounts (email_hash)
          where email_hash is not null
        """,
        """
        create unique index if not exists idx_auth_accounts_phone_hash_unique
          on auth_accounts (phone_hash)
          where phone_hash is not null
        """,

        # user_identities: external_id (Discord ID, WhatsApp ID)
        """alter table user_identities add column if not exists external_id_hash text""",
        """alter table user_identities add column if not exists external_id_enc text""",
        """
        create unique index if not exists idx_user_identities_provider_hash
          on user_identities (provider, external_id_hash)
          where external_id_hash is not null
        """,

        # email_verification_codes: email, phone_e164, display_name (transitório)
        """alter table email_verification_codes add column if not exists email_hash text""",
        """alter table email_verification_codes add column if not exists email_enc text""",
        """alter table email_verification_codes add column if not exists phone_hash text""",
        """alter table email_verification_codes add column if not exists phone_enc text""",
        """alter table email_verification_codes add column if not exists display_name_enc text""",
        """
        create index if not exists idx_email_verification_email_hash
          on email_verification_codes (email_hash, expires_at)
          where email_hash is not null
        """,

        # pending_google_signups: email, name_hint (transitório)
        """alter table pending_google_signups add column if not exists email_hash text""",
        """alter table pending_google_signups add column if not exists email_enc text""",
        """alter table pending_google_signups add column if not exists name_hint_enc text""",

        # auth_identities (Google OAuth): email snapshot — lookup é por provider_sub,
        # então não precisa de hash, só de cifragem.
        """alter table auth_identities add column if not exists email_enc text""",

        # auth_login_events: snapshot de email pra audit — sem hash
        # (filtros são por user_id/data, e bypassar lookup por email é parte da blindagem)
        """alter table auth_login_events add column if not exists email_enc text""",

        # data_export_tokens: snapshot delivered_to_email — sem hash
        """alter table data_export_tokens add column if not exists delivered_to_email_enc text""",

        # ─── pii_access_log (audit de acesso a PII) ─────────────────────────────
        # Toda chamada a core/crypto.decrypt_pii(ctx=...) registra aqui.
        # Pintado em /admin → aba "Acessos PII".
        # Distinto de audit_events (eventos de segurança do user) e
        # system_event_logs (operacional/erros).
        """
        create table if not exists pii_access_log (
          id bigserial primary key,
          purpose text not null,                   -- 'login' | 'send_email' | 'render_admin' | 'bot_message' | ...
          actor text not null,                     -- 'system' | 'system:<componente>' | 'admin:<user>' | 'user:<id>' | 'webhook:<provider>'
          subject_user_id bigint references users(id) on delete set null,
          field text not null,                     -- 'email' | 'phone' | 'discord_id' | 'whatsapp_id' | 'name'
          endpoint text,
          extra jsonb,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_pii_access_log_subject_created
          on pii_access_log (subject_user_id, created_at desc)
        """,
        """
        create index if not exists idx_pii_access_log_actor_created
          on pii_access_log (actor, created_at desc)
        """,
        """
        create index if not exists idx_pii_access_log_created
          on pii_access_log (created_at desc)
        """,
        """
        create index if not exists idx_pii_access_log_field_created
          on pii_access_log (field, created_at desc)
        """,

        # ─── Programa de afiliados ───────────────────────────────────────────────
        # Afiliado divulga link /r/{code}; quem cadastrar via link vira referral;
        # cada fatura Stripe paga do indicado gera comissão (commission_bps) enquanto
        # o afiliado estiver status='active'. Saque via Pix manual (admin marca pago).
        """
        create table if not exists affiliates (
          id bigserial primary key,
          user_id bigint not null unique references users(id) on delete cascade,
          code text not null unique,               -- código do link /r/{code}
          status text not null default 'active',   -- 'active' | 'disabled' (desativado não acumula comissão nova)
          commission_bps integer not null default 1000,  -- 1000 = 10% da fatura paga
          pix_key_hash text,                       -- hash_pii(chave pix) — lookup/dedupe
          pix_key_enc text,                        -- encrypt_pii(chave pix) — exibição no admin
          created_at timestamptz not null default now()
        )
        """,
        """
        create table if not exists affiliate_referrals (
          id bigserial primary key,
          affiliate_id bigint not null references affiliates(id) on delete cascade,
          referred_user_id bigint not null unique references users(id) on delete cascade,
          code_used text,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_affiliate_referrals_affiliate
          on affiliate_referrals (affiliate_id, created_at desc)
        """,
        """
        create table if not exists affiliate_payouts (
          id bigserial primary key,
          affiliate_id bigint not null references affiliates(id) on delete cascade,
          amount_cents bigint not null,
          status text not null default 'requested', -- 'requested' | 'paid' | 'rejected'
          pix_key_enc text,                         -- snapshot da chave no momento do pedido
          note text,
          requested_at timestamptz not null default now(),
          paid_at timestamptz
        )
        """,
        """
        create index if not exists idx_affiliate_payouts_affiliate
          on affiliate_payouts (affiliate_id, requested_at desc)
        """,
        """
        create index if not exists idx_affiliate_payouts_status
          on affiliate_payouts (status, requested_at desc)
        """,
        # 1 linha por fatura paga (stripe_invoice_id unique = idempotência do webhook).
        # status: 'pending' (aguardando carência ou saque) | 'paid' | 'reversed'.
        # "Disponível pra saque" = pending + available_at <= now() + payout_id is null.
        """
        create table if not exists affiliate_commissions (
          id bigserial primary key,
          affiliate_id bigint not null references affiliates(id) on delete cascade,
          referred_user_id bigint not null references users(id) on delete cascade,
          stripe_invoice_id text not null unique,
          invoice_amount_cents bigint not null,
          amount_cents bigint not null,
          status text not null default 'pending',
          available_at timestamptz not null,        -- created_at + carência (estorno/chargeback)
          payout_id bigint references affiliate_payouts(id) on delete set null,
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_affiliate_commissions_affiliate
          on affiliate_commissions (affiliate_id, status, available_at)
        """,

        # ── Planos v2 (escada Grátis/Essencial/Plus/Pro) ─────────────────────
        # Trial de 30 dias do plano escolhido, via Stripe COM CARTÃO (2026-08-06):
        # plan_trials registra a queima do trial por telefone. É keyed por
        # phone_hash e NÃO tem FK pra users de propósito: sobrevive à deleção da
        # conta — recriar conta com o mesmo número herda o started_at original
        # (trial já queimado). Regra: 30 dias únicos por telefone, na vida.
        """alter table auth_accounts add column if not exists trial_started_at timestamptz""",
        # Downsell do fim do trial: 1 e-mail por conta, na vida.
        """alter table auth_accounts add column if not exists trial_downsell_sent_at timestamptz""",
        # Gate de escolha de plano no cadastro (2026-08-11): depois de criar a
        # conta o usuário é OBRIGADO a passar pela /precos e escolher um plano
        # (mesmo o Grátis) antes de entrar no dashboard. plan_selected_at marca
        # o momento dessa escolha — NULL = ainda não escolheu → cai na /precos.
        #
        # Backfill preciso pela fronteira REAL do rollout (sem cutoff por data
        # chutado): o ADD COLUMN com DEFAULT now() carimba TODAS as contas que já
        # existem no exato instante em que a migration roda pela 1ª vez — não
        # importa quando foram criadas nem se o v2 estava ligado. Em seguida o
        # DROP DEFAULT faz TODO cadastro novo nascer com NULL (→ cai na /precos).
        # Idempotente: `if not exists` pula o ADD (e o backfill junto) em
        # redeploys, então contas novas nunca são recarimbadas; DROP DEFAULT de
        # coluna sem default é no-op. now() é STABLE → fast default (metadata),
        # avaliado uma vez, sem reescrever a tabela.
        """alter table auth_accounts add column if not exists plan_selected_at timestamptz default now()""",
        """alter table auth_accounts alter column plan_selected_at drop default""",
        # Origem do cadastro (2026-08-17): de onde a conta nasceu, pra separar
        # no painel de admin quem se cadastrou pela web (passa pelo gate da
        # /precos) de quem veio pelo app iOS (isento do gate — diretriz 3.1.1
        # da Apple). Valores: 'web' | 'app' | 'google' | 'google_app' |
        # 'whatsapp'. NULL = conta anterior a esta coluna (origem desconhecida);
        # sem backfill por data chutado — o painel mostra "—" pra elas.
        """alter table auth_accounts add column if not exists signup_source text""",
        """
        create table if not exists plan_trials (
          phone_hash text primary key,
          -- SET NULL, nunca CASCADE: a linha tem que sobreviver à deleção da
          -- conta (a trava é por telefone, na vida), mas o user_id de uma conta
          -- apagada é resíduo — nunca é lido, e as consultas casam por
          -- phone_hash. Bancos que já existiam ganham a FK pelo
          -- `ensure_plan_trials_user_fk`.
          user_id bigint references users(id) on delete set null,
          started_at timestamptz not null default now(),
          model_version smallint not null default 2
        )
        """,
        # Registros anteriores ao trial via Stripe recebem versão 1. Novos
        # claims gravam versão 2 explicitamente; o script one-time remove só v1.
        # O Postgres NÃO cria índice no lado que REFERENCIA. Sem este, todo
        # `delete from users` varre a plan_trials inteira atrás das linhas a
        # anular — e ela cresce para sempre (uma por telefone que já usou o
        # teste), com o job de exclusão processando até 50 contas por lote.
        # Parcial: as linhas já desvinculadas (user_id nulo) são a maioria com o
        # tempo e não interessam à busca.
        """
        create index if not exists idx_plan_trials_user_id
          on plan_trials (user_id) where user_id is not null
        """,
        """alter table plan_trials add column if not exists model_version smallint not null default 1""",
        """alter table plan_trials alter column model_version set default 2""",

        # ── Funil de checkout (telemetria durável, fora do log operacional) ──
        # Vive em tabela própria, NÃO em system_event_logs, por dois motivos:
        # (1) system_event_logs é purgável (o "Limpar" do painel, admin.py
        #     purge, delete individual) — telemetria de negócio não pode sumir
        #     numa limpeza de rotina;
        # (2) session_id (id da Checkout Session do Stripe) correlaciona a
        #     abertura com a conclusão da MESMA tentativa — sem isso, heurística
        #     de timestamp conta errado quem comprou, cancelou e reabriu.
        # kind: 'started' (endpoint /billing/create-checkout) | 'completed'
        # (webhook checkout.session.completed). user_id SET NULL na exclusão
        # da conta: o evento sobrevive (contamos sessões, não só pessoas vivas).
        """
        create table if not exists checkout_funnel_events (
          id bigserial primary key,
          user_id bigint references users(id) on delete set null,
          session_id text,
          kind text not null check (kind in ('started', 'completed')),
          created_at timestamptz not null default now()
        )
        """,
        """
        create index if not exists idx_checkout_funnel_kind_created
          on checkout_funnel_events (kind, created_at desc)
        """,
        """
        create index if not exists idx_checkout_funnel_session
          on checkout_funnel_events (session_id)
        """,

        # ── Agentes do Piggy (prateleira de jobs proativos) ──────────────────
        # Um agente por kind por usuário (custom multiplica no futuro via
        # config, não via linhas). status: 'active' | 'paused'.
        """
        create table if not exists agents (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          kind text not null,
          config jsonb not null default '{}'::jsonb,
          status text not null default 'active',
          created_at timestamptz not null default now(),
          unique (user_id, kind)
        )
        """,
        """
        create index if not exists idx_agents_user_status
          on agents (user_id, status)
        """,
        # Cada disparo de agente. dedupe_key torna o runner idempotente
        # (rodar o detector N vezes no dia não duplica alerta). valor_impacto
        # alimenta o placar "R$ salvos"; seen_at = lido/não lido no feed.
        """
        create table if not exists agent_events (
          id bigserial primary key,
          agent_id bigint not null references agents(id) on delete cascade,
          user_id bigint not null references users(id) on delete cascade,
          kind text not null,
          fired_at timestamptz not null default now(),
          dedupe_key text not null,
          payload jsonb not null default '{}'::jsonb,
          channel text not null default 'dashboard',
          valor_impacto numeric,
          seen_at timestamptz,
          unique (agent_id, dedupe_key)
        )
        """,
        """
        create index if not exists idx_agent_events_user_time
          on agent_events (user_id, fired_at desc)
        """,
        # Mini-digest por agente: emailed_at marca o evento já enviado por e-mail
        # (null = ainda não entrou num e-mail); last_emailed_at no agente aplica o
        # teto de cadência (intervalo mínimo entre e-mails do mesmo agente).
        """
        alter table agent_events add column if not exists emailed_at timestamptz
        """,
        """
        alter table agents add column if not exists last_emailed_at timestamptz
        """,
        """
        create index if not exists idx_agent_events_pending_email
          on agent_events (agent_id) where emailed_at is null
        """,
        # LEGADO — não é mais lida nem escrita. A supressão de e-mail deixou de
        # ser materializada: o opt-out (por agente / global) é consultado fresco
        # no run_agent_emails_once, e a fila filtra por fired_at do mês corrente.
        # Coluna mantida só por segurança de rollback; drop em migração futura.
        """
        alter table agent_events add column if not exists suppressed_at timestamptz
        """,
        # Hold de e-mail: até quando o envio dos agregados fica segurado enquanto a
        # carteira se mexe. Coluna PRÓPRIA de propósito — fired_at é dado de
        # negócio (data exibida, ordenação do feed, disparos_mes, saved_365d) e
        # não pode ser empurrado por controle técnico. Expira sozinho: nada fica
        # preso se um sync morrer no meio.
        """
        alter table agent_events add column if not exists email_hold_until timestamptz
        """,
        # Soft-delete de evento cuja condição deixou de valer. Some do feed e dos
        # contadores, mas a linha (e o emailed_at) fica: apagar de vez destruiria
        # o dedupe de entrega, e a condição voltando no mesmo mês viraria um
        # segundo e-mail de um agente que é mensal.
        """
        alter table agent_events add column if not exists stale_at timestamptz
        """,
        """
        create index if not exists idx_agent_events_pending_email3
          on agent_events (agent_id, fired_at) where emailed_at is null and stale_at is null
        """,

        # ─── Espaços financeiros (Fase 1) ───────────────────────────────────────
        # Segmentam a vida financeira em áreas (Pessoal, Casa, Fazenda). O
        # espaço default por usuário é lazy (ver db/spaces.ensure_default_space);
        # `space_id NULL` em launches/credit_transactions = espaço default. Apagar
        # um espaço NÃO apaga lançamentos (on delete set null → caem p/ default).
        #
        # ISOLAMENTO POR USUÁRIO (P1 apontado na revisão): a FK de space_id NÃO
        # pode validar só financial_spaces(id) — isso deixaria um lançamento do
        # usuário A apontar para um espaço do usuário B. Por isso:
        #   - launches / credit_transactions (têm user_id) usam FK COMPOSTA
        #     (user_id, space_id) → financial_spaces(user_id, id), com
        #     `on delete set null (space_id)` (PG15+: zera só space_id, não user_id);
        #   - open_finance_accounts (não tem user_id) usa FK simples + TRIGGER que
        #     valida que o espaço é do mesmo dono da conexão.
        """
        create table if not exists financial_spaces (
          id bigserial primary key,
          user_id bigint not null references users(id) on delete cascade,
          name text not null,
          emoji text not null default '🏦',
          color text not null default '#7c3aed',
          is_default boolean not null default false,
          is_archived boolean not null default false,
          created_at timestamptz not null default now(),
          unique (user_id, name)
        )
        """,
        # garante no máximo 1 default por usuário
        """
        create unique index if not exists uq_financial_spaces_one_default
          on financial_spaces(user_id) where is_default
        """,
        # alvo unívoco da FK composta (user_id, id). id já é PK, então (user_id, id)
        # é trivialmente único; a constraint é necessária para poder ser referenciada.
        """
        do $$ begin
          if not exists (
            select 1 from pg_constraint where conname = 'uq_financial_spaces_user_id'
          ) then
            alter table financial_spaces
              add constraint uq_financial_spaces_user_id unique (user_id, id);
          end if;
        end $$
        """,
        # colunas space_id (sem FK inline — a FK composta é adicionada abaixo)
        """alter table launches add column if not exists space_id bigint""",
        """alter table credit_transactions add column if not exists space_id bigint""",
        # open_finance_accounts não tem user_id → FK simples (existência + set null);
        # a posse é garantida pelo trigger of_account_space_same_owner (abaixo).
        """
        alter table open_finance_accounts add column if not exists space_id bigint
          references financial_spaces(id) on delete set null
        """,
        # remove FKs simples que uma versão anterior deste branch possa ter criado
        # inline em launches/credit_transactions (idempotente; no-op em banco novo).
        """alter table launches drop constraint if exists launches_space_id_fkey""",
        """alter table credit_transactions drop constraint if exists credit_transactions_space_id_fkey""",
        # FK composta launches: espaço tem de ser do MESMO usuário do lançamento.
        """
        do $$ begin
          if not exists (
            select 1 from pg_constraint where conname = 'fk_launches_space'
          ) then
            alter table launches add constraint fk_launches_space
              foreign key (user_id, space_id)
              references financial_spaces(user_id, id)
              on delete set null (space_id);
          end if;
        end $$
        """,
        # FK composta credit_transactions (mesmo racional).
        """
        do $$ begin
          if not exists (
            select 1 from pg_constraint where conname = 'fk_credit_tx_space'
          ) then
            alter table credit_transactions add constraint fk_credit_tx_space
              foreign key (user_id, space_id)
              references financial_spaces(user_id, id)
              on delete set null (space_id);
          end if;
        end $$
        """,
        # índices nas colunas de FK: sem eles o `on delete set null` faz seq scan
        # ao apagar um espaço. launches usa (user_id, space_id) leftmost do índice.
        """
        create index if not exists idx_launches_user_space
          on launches(user_id, space_id, criado_em desc)
        """,
        """
        create index if not exists idx_credit_tx_user_space
          on credit_transactions(user_id, space_id)
        """,
        """
        create index if not exists idx_of_accounts_space
          on open_finance_accounts(space_id)
        """,
        # posse do espaço em open_finance_accounts (não tem user_id p/ FK composta):
        # o espaço apontado tem de pertencer ao dono da conexão da conta.
        """
        create or replace function of_account_space_same_owner()
        returns trigger as $$
        begin
          if new.space_id is not null then
            if not exists (
              select 1
              from financial_spaces fs
              join open_finance_connections c on c.id = new.connection_id
              where fs.id = new.space_id and fs.user_id = c.user_id
            ) then
              raise exception
                'space_id % nao pertence ao dono da conta OF (connection %)',
                new.space_id, new.connection_id;
            end if;
          end if;
          return new;
        end;
        $$ language plpgsql
        """,
        """drop trigger if exists trg_of_account_space_same_owner on open_finance_accounts""",
        """
        create trigger trg_of_account_space_same_owner
          before insert or update of space_id on open_finance_accounts
          for each row execute function of_account_space_same_owner()
        """,
    ]

    # autocommit: cada DDL roda em sua propria transacao e libera locks
    # imediatamente. Sem isso, todos os ALTER/CREATE INDEX seguram ACCESS
    # EXCLUSIVE em varias tabelas ate o commit final do loop, o que:
    #   1) bloqueia queries de leitura concorrentes (ex.: /admin/api/overview)
    #      por toda a duracao do init, e
    #   2) leva a deadlocks se duas instancias rodam init_db em paralelo
    #      (deploy do Railway sobe um container novo antes do velho sair).
    # Toda a DDL eh idempotente (if not exists / or replace / where ... is null),
    # entao commit por instrucao eh seguro mesmo se o init_db rodar varias vezes.
    # O `finally` nao e decoracao: a conexao vem do POOL e volta pra ele. Sem
    # restaurar, ela fica em autocommit para sempre e toda transacao futura que
    # cair nela perde a atomicidade — o rollback vira no-op e um erro no meio
    # deixa metade da escrita gravada. Medido: depois de um init_db, 6 de 6
    # `create_investment_db` que estouraram INSUFFICIENT_ACCOUNT deixaram o
    # investimento criado no banco. (A trava definitiva esta no `reset` do pool,
    # em db/connection.py; isto aqui e a primeira linha de defesa.)
    with get_conn() as conn:
        conn.autocommit = True
        try:
            _run_ddl(conn, ddl_statements)
        finally:
            conn.autocommit = False
    print("[init_db] OK")


def _run_ddl(conn, ddl_statements) -> None:
    with conn.cursor() as cur:
        # O init INTEIRO sob advisory lock, e não só os reparos do fim.
        #
        # `if not exists` NÃO é atômico: a checagem e a criação são passos
        # separados, então duas instâncias que subam juntas — o deploy do Railway
        # levanta o container novo ANTES de o velho sair, ver o comentário logo
        # acima — podem ver a mesma tabela/índice ausente e a perdedora estoura
        # com "already exists". Isso vale para TODO `create ... if not exists`
        # deste arquivo, não só para o índice ou a FK mais recentes; no PR #152
        # o revisor apontou um statement por vez até ficar claro que a classe é o
        # bloco inteiro. Um lock, uma vez, cobre os ~200 statements.
        #
        # Não conflita com o autocommit explicado logo acima: advisory lock não
        # trava tabela nenhuma, então cada DDL continua soltando os locks DELE na
        # hora e leitura concorrente segue livre. O que ele impede é exatamente o
        # item (2) daquele comentário — duas instâncias em init paralelo. O custo
        # é a segunda esperar a primeira terminar.
        #
        # Lock de SESSÃO, não de transação: a conexão está em autocommit, e um
        # `pg_advisory_xact_lock` soltaria no fim do próprio SELECT que o toma.
        cur.execute("select pg_advisory_lock(%s)", (SCHEMA_INIT_LOCK,))
        try:
            for i, stmt in enumerate(ddl_statements, 1):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    print(f"[init_db] erro no statement #{i}: {e}")
                    print(stmt)
                    raise

            # Corrige FKs em users(id) que ficaram com on_delete errado
            # porque a tabela já existia antes da FK ser declarada no schema.
            try:
                if ensure_plan_trials_user_fk(cur):
                    print("[init_db] schema_repairs criou a FK de plan_trials.user_id")
                changes = repair_user_fk_cascades(cur)
                if changes:
                    print(f"[init_db] schema_repairs ajustou {len(changes)} FK(s): {changes}")
            except Exception as e:
                print(f"[init_db] schema_repairs falhou: {e}")
                raise
        finally:
            cur.execute("select pg_advisory_unlock(%s)", (SCHEMA_INIT_LOCK,))
