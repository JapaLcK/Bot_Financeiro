"""
db/users.py — Gerenciamento de usuários, identidades e link de contas.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import psycopg

from core.crypto import encrypt_pii_optional, hash_pii_optional

from .connection import get_conn

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos de usuário
# ──────────────────────────────────────────────────────────────────────────────

def ensure_user_tx(cur, user_id: int):
    cur.execute("insert into users(id) values (%s) on conflict do nothing", (user_id,))
    cur.execute(
        "insert into accounts(user_id, balance) values (%s, 0) on conflict do nothing",
        (user_id,),
    )


def ensure_user(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user_tx(cur, user_id)
        conn.commit()


def user_exists(user_id: int) -> bool:
    """A conta existe? Pergunta para quem NÃO pode criá-la ao consultar.

    Nasceu do webhook da Pluggy (`_adota_item_orfao`): lá o dono vem do
    `clientUserId` remoto, e todo caminho de escrita passa por `ensure_user_tx`,
    que INSERE a linha em vez de recusar. Conta apagada por LGPD
    (`db/privacy.py`) cujo item sobreviveu ao delete best-effort voltava a
    existir no banco por causa de um evento da Pluggy.

    LIMITE CONHECIDO, e ele NÃO é inofensivo: responde True durante a janela da
    exclusão AGENDADA (`auth_accounts.deletion_status in ('scheduled','processing')`)
    — a linha de `users` só some no fim. "A exclusão leva a conexão junto" é
    verdade e não basta: ela leva pela CASCATA do `delete from users`, fora do
    `RETURNING` que alimenta o delete remoto na Pluggy, então o item fica vivo (e
    pago) depois da exclusão LGPD (P2 do Codex na PR #539). Continua não valendo o
    join aqui — o predicado tem fonte única em `db.is_account_scheduled_for_deletion`
    e QUEM CHAMA decide: a adoção por webhook o consulta logo depois deste
    `user_exists` (`frontend/routes/open_finance.py`), e o
    `scripts/cleanup_poisoned_category_rules.py` segue querendo só "a conta
    existe?", que é o que esta função responde.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from users where id = %s", (user_id,))
            return cur.fetchone() is not None


# ──────────────────────────────────────────────────────────────────────────────
# Merge de usuários (vinculação Discord ↔ WhatsApp)
# ──────────────────────────────────────────────────────────────────────────────

class MergeRefused(Exception):
    """`merge_users` não junta (#607): dado financeiro dos dois lados, origem
    presa (Open Finance vivo, plano pago ou cliente Stripe), autoindicação de
    afiliado ou colisão de unique/FK na junção.

    `motivo` diz qual, para a resposta ao usuário ser verdadeira (D6):
    "dados_dos_dois_lados", "open_finance", "origem_presa" (plano pago ou
    cliente Stripe), "autoindicacao" ou "colisao"."""

    def __init__(self, *args, motivo: str | None = None):
        super().__init__(*args)
        self.motivo = motivo


# Onde mora "dado financeiro" para a recusa do `merge_users`. Com linha nestas
# tabelas dos DOIS lados a junção colide (user_seq, nome de caixinha/investimento,
# arquivo OFX...) — e o dono decidiu recusar em vez de escolher o que sobra.
# Recorrentes e contas a pagar entram para não duplicar previsão e lembrete — desde
# a Q42 o recorrente não lança mais (dono, 2026-09-26);
# `recurring_charges`/`recurring_income_credits` só existem com o pai.
_TABELAS_FINANCEIRAS = ("launches", "pockets", "investments", "credit_cards", "ofx_imports",
                        "recurring_expenses", "recurring_incomes", "bill_instances")

# Na colisão, o destino vence: a linha da origem que repete a chave única de uma
# linha do destino é apagada antes do update. As colunas repetem a unique/PK real
# SEM o `user_id`; vazio = a PK é o próprio `user_id` (uma linha por usuário).
_DESTINO_VENCE = (
    ("user_category_rules", ("keyword",)),
    ("pending_actions", ()),
    ("user_categories", ("name",)),
    ("category_budgets", ("categoria",)),
    ("household_budget_config", ("bucket",)),
    ("household_budget_income", ("month",)),
    ("daily_report_prefs", ()),
    ("recurring_suggestion_dismissed", ("merchant_key", "amount")),
    ("ai_pending_actions", ()),
    ("budget_alert_sent", ("categoria", "ym", "threshold")),
    ("subscription_marks", ("merchant_key",)),
)

# Movidas sem regra de colisão (#635): conversa com a IA, logs, dinheiro, Open
# Finance terminal e afiliado. Unique que colidir (Pix aberto, `affiliates.user_id`)
# vira `MergeRefused` no `merge_users`.
_MOVIDAS = ("ai_messages", "ai_fallback_log", "audit_events", "auth_login_events", "plan_grants",
            "pix_charges", "open_finance_connections", "open_finance_item_registry", "affiliates")

# FKs em users(id) cuja coluna não se chama `user_id`.
_OUTRAS_COLUNAS = (("pii_access_log", "subject_user_id"),
                   ("affiliate_referrals", "referred_user_id"),
                   ("affiliate_commissions", "referred_user_id"),
                   ("prospect_referrals", "referred_user_id"))


def _tem_dados_financeiros(cur, user_id: int) -> bool:
    # `balance <> 0`, não "tem linha": o `ensure_user_tx` cria accounts zerada.
    cur.execute(
        "select "
        + " or ".join(f"exists(select 1 from {t} where user_id = %(u)s)" for t in _TABELAS_FINANCEIRAS)
        + " or exists(select 1 from accounts where user_id = %(u)s and balance <> 0) as tem",
        {"u": user_id},
    )
    return bool(cur.fetchone()["tem"])


def _origem_presa(cur, user_id: int) -> str | None:
    """Por que a conta que some está presa: "open_finance" (Open Finance vivo),
    "origem_presa" (plano pago vigente ou cliente Stripe) ou None (não está).

    Com os dois, vale o Open Finance (dono, D6): é o que a pessoa consegue
    desfazer sozinha, desconectando o banco.

    "Vivo" é o que o código de OF considera vivo (`_TERMINAL`): PAUSED é trial
    vencido (o item nem existe mais na Pluggy) e DELETED é removido. Plano pago
    é a regra de `plan_service` — cobre também quem paga sem cliente Stripe
    (Pix vigente, grandfathered); o trial por telefone (`trial_started_at`) não.

    Qualquer `stripe_customer_id`, com qualquer status, prende (dono,
    2026-09-27): todo cliente Stripe pode gerar evento futuro — assinatura,
    checkout aberto, estorno, disputa — que o webhook resolveria para a conta
    apagada."""
    from core.services.plan_service import _tem_plano_pago_vigente  # tardio: importa `db`
    from .open_finance_state import _TERMINAL

    cur.execute(
        "select exists(select 1 from open_finance_connections where user_id = %s"
        f" and upper(coalesce(status, '')) not in {_TERMINAL}) as tem",
        (user_id,),
    )
    if cur.fetchone()["tem"]:
        return "open_finance"
    cur.execute("select plan, plan_expires_at, stripe_customer_id"
                " from auth_accounts where user_id = %s", (user_id,))
    if any(_tem_plano_pago_vigente(r) or (r["stripe_customer_id"] or "").strip()
           for r in cur.fetchall()):
        return "origem_presa"
    return None


def _viraria_autoindicacao(cur, from_user_id: int, to_user_id: int) -> bool:
    """Um lado é dono do afiliado que indicou o outro? Juntos, viram autoindicação
    (`record_referral` recusa; `record_commission_for_invoice` não rechecaria)."""
    cur.execute(
        "select exists(select 1 from affiliate_referrals r join affiliates a on a.id = r.affiliate_id"
        " where (a.user_id = %(f)s and r.referred_user_id = %(t)s)"
        " or (a.user_id = %(t)s and r.referred_user_id = %(f)s)) as tem",
        {"f": from_user_id, "t": to_user_id},
    )
    return cur.fetchone()["tem"]


def merge_users(from_user_id: int, to_user_id: int) -> None:
    """
    Move os dados de from_user_id → to_user_id e APAGA a linha `users` da origem
    no mesmo commit (#635): o que não foi movido some pelas FKs (sessões, tokens,
    push, cache, agentes; com login nos dois lados, o login da origem).

    Recusa (`MergeRefused`, nada escrito) quando os dois lados têm dados
    financeiros, quando a origem está presa (`_origem_presa`), quando viraria
    autoindicação de afiliado (`_viraria_autoindicacao`) ou quando a junção
    bate numa unique ou numa FK composta (ex.: `fk_launches_space`, lançamento da
    origem num `financial_spaces` dela). Antes de mover launches, remove duplicatas que colidem na
    unique uq_launches_user_source_external (user_id, source, external_id).
    """
    try:
        _merge_users(from_user_id, to_user_id)
    except (psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation) as exc:
        # Sem str(exc): o texto do psycopg traz o valor da linha que violou.
        logger.warning(
            "merge_users: unique/FK na junção, recusado from=%s to=%s constraint=%s",
            from_user_id, to_user_id, exc.diag.constraint_name,
            extra={"user_id": to_user_id},
        )
        raise MergeRefused(f"{from_user_id} -> {to_user_id}", motivo="colisao") from exc


def _merge_users(from_user_id: int, to_user_id: int) -> None:
    if from_user_id == to_user_id:
        return

    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_user_tx(cur, to_user_id)
            ensure_user_tx(cur, from_user_id)
            # Mutex dos DOIS lados, em ordem de id (merge A→B × B→A) e ANTES da checagem:
            # quem escreve por usuário toma o mesmo lock (ver `_lock_user`).
            from .bank_movements import _lock_user
            for u in sorted((from_user_id, to_user_id)):
                _lock_user(cur, u)
            # ponytail: escritor que NÃO toma `_lock_user` (o undo de pagamento de fatura do
            # cartão, `delete_user_data`) ainda grava entre a checagem e os updates: sem unique no
            # caminho, junta; batendo numa unique (user_seq, nome de caixinha...), volta tudo e vira
            # `MergeRefused`. Contra esses dois o merge também pode dar deadlock (exceção conhecida,
            # ver a docstring de `_lock_user`): `DeadlockDetected` não vira `MergeRefused`.
            # Mesma ordem do `or` que vivia aqui: a 1ª recusa que vale é o motivo.
            recusa = f"{from_user_id} -> {to_user_id}"
            presa = _origem_presa(cur, from_user_id)
            if presa:
                raise MergeRefused(recusa, motivo=presa)
            if _viraria_autoindicacao(cur, from_user_id, to_user_id):
                raise MergeRefused(recusa, motivo="autoindicacao")
            if _tem_dados_financeiros(cur, from_user_id) and _tem_dados_financeiros(cur, to_user_id):
                raise MergeRefused(recusa, motivo="dados_dos_dois_lados")

            # 1) dedupe de launches
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

            # 2) move launches restantes
            cur.execute(
                "update launches set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # 3) soma saldos
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

            cur.execute("select id from auth_accounts where user_id=%s limit 1", (to_user_id,))
            to_has_auth = cur.fetchone() is not None

            # 4) identidades / link_codes. Com login nos dois lados, o e-mail da
            # origem fica e some com ela: movido, um cadastro novo com ele
            # resolveria para o destino e daria uma 2ª auth_accounts lá (#635, D3).
            cur.execute(
                "update user_identities set user_id=%s where user_id=%s"
                " and (provider <> 'email' or not %s)",
                (to_user_id, from_user_id, to_has_auth),
            )
            cur.execute(
                "update link_codes set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # 5) outras tabelas com user_id — na colisão, o destino vence
            for table, cols in _DESTINO_VENCE:
                cur.execute(
                    f"delete from {table} o where o.user_id = %s and exists("
                    f"select 1 from {table} d where d.user_id = %s"
                    + "".join(f" and d.{c} = o.{c}" for c in cols) + ")",
                    (from_user_id, to_user_id),
                )
            for table in (*(t for t, _ in _DESTINO_VENCE), "pockets", "pocket_lots",
                          "investments", "investment_lots", "credit_transactions", "ofx_imports",
                          "recurring_expenses", "recurring_charges", "recurring_incomes",
                          "recurring_income_credits", "bill_instances", "bank_movement_declarations"):
                cur.execute(
                    f"update {table} set user_id=%s where user_id=%s",
                    (to_user_id, from_user_id),
                )
            # system_event_logs nasce no startup (core/admin_dashboard.py), não no init_db.
            cur.execute("select to_regclass('system_event_logs') is not null as existe")
            logs = ("system_event_logs",) if cur.fetchone()["existe"] else ()
            for table, col in (*((t, "user_id") for t in (*_MOVIDAS, *logs)), *_OUTRAS_COLUNAS):
                cur.execute(
                    f"update {table} set {col}=%s where {col}=%s",
                    (to_user_id, from_user_id),
                )

            # 6) credit_cards: merge seguro por nome
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
                    to_card_id = to_card_row["id"]
                    cur.execute(
                        """
                        delete from credit_bills fb
                        using credit_bills tb
                        where fb.card_id = %s and tb.card_id = %s
                          and fb.period_start = tb.period_start and fb.period_end = tb.period_end
                        """,
                        (from_card_id, to_card_id),
                    )
                    cur.execute(
                        "update credit_bills set card_id=%s where card_id=%s",
                        (to_card_id, from_card_id),
                    )
                    cur.execute(
                        "update credit_transactions set card_id=%s where card_id=%s",
                        (to_card_id, from_card_id),
                    )
                    cur.execute("delete from credit_cards where id=%s", (from_card_id,))
                else:
                    cur.execute(
                        "update credit_cards set user_id=%s where id=%s",
                        (to_user_id, from_card_id),
                    )

            # 7) credit_bills.user_id
            cur.execute(
                "update credit_bills set user_id=%s where user_id=%s",
                (to_user_id, from_user_id),
            )

            # 8) login (conta, Google, MFA): migra se to_user não tem
            if not to_has_auth:
                for table in ("auth_accounts", "auth_identities", "user_mfa", "user_mfa_backup_codes"):
                    cur.execute(
                        f"update {table} set user_id=%s where user_id=%s",
                        (to_user_id, from_user_id),
                    )

            # O resto segue a política das FKs de db/schema_repairs.py, a mesma do delete_user_data.
            cur.execute("delete from users where id = %s", (from_user_id,))

        conn.commit()

    # A conta pode ter trocado de user_id — os dois lados saem do cache.
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(from_user_id)
    invalidate_auth_user_cache(to_user_id)


def user_score(user_id: int) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s", (user_id,))
        return int(cur.fetchone()["n"])


def choose_primary_user(a_user_id: int, b_user_id: int) -> tuple[int, int]:
    """Retorna (primary, secondary) baseado em quem tem mais dados."""
    if a_user_id == b_user_id:
        return a_user_id, b_user_id
    sa = user_score(a_user_id)
    sb = user_score(b_user_id)
    return (a_user_id, b_user_id) if sa >= sb else (b_user_id, a_user_id)


# ──────────────────────────────────────────────────────────────────────────────
# Usuário canônico (identidade entre plataformas)
# ──────────────────────────────────────────────────────────────────────────────

def get_or_create_canonical_user(provider: str, external_id: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_id from user_identities where provider=%s and external_id_hash=%s",
                (provider, hash_pii_optional(external_id, kind="external_id")),
            )
            row = cur.fetchone()
            if row:
                return int(row["user_id"])

            base = f"{provider}:{external_id}".encode("utf-8")
            for i in range(20):
                digest = hashlib.sha256(base + f":{i}".encode("utf-8")).digest()
                new_id = int.from_bytes(digest[:8], "big") % 2_000_000_000 + 1

                ensure_user_tx(cur, new_id)

                try:
                    cur.execute(
                        """
                        insert into user_identities(provider, external_id, user_id,
                                                    external_id_hash, external_id_enc)
                        values (%s, %s, %s, %s, %s)
                        """,
                        (provider, external_id, new_id,
                         hash_pii_optional(external_id, kind="external_id"),
                         encrypt_pii_optional(external_id)),
                    )
                    conn.commit()
                    return new_id
                except Exception:
                    conn.rollback()
                    with get_conn() as conn2:
                        with conn2.cursor() as cur2:
                            cur2.execute(
                                "select user_id from user_identities where provider=%s and external_id_hash=%s",
                                (provider, hash_pii_optional(external_id, kind="external_id")),
                            )
                            r2 = cur2.fetchone()
                            if r2:
                                return int(r2["user_id"])
                    continue

            raise RuntimeError("Falha ao criar user_id canônico (colisão repetida)")


# ──────────────────────────────────────────────────────────────────────────────
# Link codes (vinculação entre plataformas)
# ──────────────────────────────────────────────────────────────────────────────

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
                set provider=excluded.provider, user_id=excluded.user_id,
                    expires_at=excluded.expires_at, consumed_at=null
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
                where token = %s and provider = %s and expires_at > %s and consumed_at is null
                returning user_id
                """,
                (token, provider, now),
            )
            row = cur.fetchone()
        conn.commit()
    return int(row["user_id"]) if row else None


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


def bind_identity(provider: str, external_id: str, user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into user_identities(provider, external_id, user_id,
                                            external_id_hash, external_id_enc)
                values (%s, %s, %s, %s, %s)
                on conflict (provider, external_id) do update
                set user_id = excluded.user_id,
                    external_id_hash = coalesce(excluded.external_id_hash, user_identities.external_id_hash),
                    external_id_enc = coalesce(excluded.external_id_enc, user_identities.external_id_enc)
                """,
                (provider, external_id, user_id,
                 hash_pii_optional(external_id, kind="external_id"),
                 encrypt_pii_optional(external_id)),
            )
        conn.commit()


def link_platform_identity(provider: str, external_id: str, target_user_id: int) -> int:
    """
    Liga (provider, external_id) ao target_user_id.
    O target_user_id é SEMPRE o primário.
    """
    current_user_id = get_or_create_canonical_user(provider, external_id)
    if current_user_id == target_user_id:
        return target_user_id

    merge_users(current_user_id, target_user_id)
    bind_identity(provider, external_id, target_user_id)
    return target_user_id


# ──────────────────────────────────────────────────────────────────────────────
# Senha (helpers internos — usados por reports.py)
# ──────────────────────────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False
