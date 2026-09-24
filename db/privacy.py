"""
db/privacy.py — Exportação e exclusão segura de dados do usuário.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from core.crypto import PiiAccessContext, decrypt_pii_optional, encrypt_pii_optional
from core.pg_text import tem_veneno

from .connection import get_conn
from .users import _check_password, ensure_user_tx


# Conta criada só via Google não tem password_hash: NENHUMA senha funciona, e
# devolver "Senha incorreta." mandava o usuário tentar de novo pra sempre. A
# mensagem aqui é EXPLÍCITA de propósito — ao contrário da vagueza de
# /auth/login ("E-mail ou senha incorretos.",
# frontend/finance_bot_websocket_custom.py:2646-2659), que existe contra
# enumeração de e-mails por chamador ANÔNIMO. Nos endpoints que re-autenticam,
# o usuário já está logado como ele mesmo: não há e-mail alheio a enumerar.
PASSWORD_NOT_SET_MSG = (
    "Sua conta foi criada com o Google e ainda não tem senha. "
    "Defina uma senha para continuar."
)


class PasswordNotSetError(PermissionError):
    """Conta sem password_hash (login só via OAuth) — não é senha errada.

    Herda de PermissionError de propósito: as rotas que já capturam
    PermissionError continuam recusando a operação mesmo sem tratar este caso.
    Quem quiser o 409 captura ANTES do except PermissionError.
    """


def verify_user_password(user_id: int, password: str) -> bool:
    """Confirma que `password` corresponde ao hash atual da conta do usuário.

    Levanta PasswordNotSetError quando a conta não tem senha nenhuma. Senha
    ERRADA continua devolvendo False (contrato preservado pelos chamadores).
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select password_hash from auth_accounts where user_id = %s limit 1",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return False
    if not row.get("password_hash"):
        raise PasswordNotSetError(PASSWORD_NOT_SET_MSG)
    # DEPOIS do estado da conta, de propósito: o que o cliente mandou não muda o
    # fato de a conta não ter senha. Enquanto esta linha era a primeira do corpo,
    # senha vazia numa conta só-Google voltava False e virava o 401 "Senha
    # incorreta." que esta função existe pra parar de mentir.
    if not password:
        return False
    return _check_password(password, row["password_hash"])


def get_user_email(user_id: int) -> str | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select email, email_enc from auth_accounts where user_id = %s limit 1",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    if row.get("email_enc"):
        return decrypt_pii_optional(
            row["email_enc"],
            ctx=PiiAccessContext(
                purpose="get_user_email",
                actor="system",
                subject_user_id=user_id,
                field="email",
            ),
        )
    return row.get("email")


def create_data_export_token(
    user_id: int,
    *,
    minutes_valid: int = 15,
    request_ip: str | None = None,
    request_user_agent: str | None = None,
    delivered_to_email: str | None = None,
) -> tuple[str, datetime]:
    """Cria um token de uso único para baixar a exportação completa.

    Retorna (token, expires_at). O token é opaco (urlsafe, ~43 chars).
    """
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes_valid)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into data_export_tokens
              (token, user_id, expires_at, request_ip, request_user_agent,
               delivered_to_email_enc)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (token, user_id, expires_at, request_ip, request_user_agent,
             encrypt_pii_optional(delivered_to_email)),
        )
        conn.commit()

    return token, expires_at


def consume_data_export_token(token: str) -> int | None:
    """Valida e marca o token como usado em uma única transação atômica.

    Retorna o `user_id` associado se o token era válido (existe, não expirou
    e não foi usado). Retorna `None` em qualquer outro caso.
    """
    # NUL/surrogate morre aqui e não no `cur.execute`: o token vem do path de
    # `/auth/account/export/download/{token}`, que é ANÔNIMA, e o psycopg
    # estoura antes de comparar — 500 em vez do 410 que esta rota já dá para
    # token inválido (#321). "Token inválido" é o que ele é.
    if not token or tem_veneno(token):
        return None
    now = datetime.now(timezone.utc)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            update data_export_tokens
            set used_at = %s
            where token = %s
              and used_at is null
              and expires_at > %s
            returning user_id
            """,
            (now, token, now),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        return None
    return int(row["user_id"])


def has_recent_export_request(user_id: int, within_minutes: int = 60) -> bool:
    """True se o usuário já solicitou um export nos últimos N minutos.

    Usado como cooldown adicional ao rate-limit por IP, pra evitar que o
    mesmo usuário gere múltiplos links válidos simultaneamente.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select 1
            from data_export_tokens
            where user_id = %s
              and created_at >= %s
              and used_at is null
              and expires_at > now()
            limit 1
            """,
            (user_id, cutoff),
        )
        row = cur.fetchone()
    return bool(row)


class PrivacyJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return bytes(obj).hex()
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return super().default(obj)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, cls=PrivacyJSONEncoder, ensure_ascii=False))


def _table_exists(cur, table: str) -> bool:
    cur.execute("select to_regclass(%s) is not null as exists", (table,))
    row = cur.fetchone()
    return bool(row and row["exists"])


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        select exists (
          select 1
          from information_schema.columns
          where table_schema = 'public'
            and table_name = %s
            and column_name = %s
        ) as exists
        """,
        (table, column),
    )
    row = cur.fetchone()
    return bool(row and row["exists"])


def ensure_account_deletion_columns() -> None:
    statements = [
        "alter table auth_accounts add column if not exists deletion_requested_at timestamptz",
        "alter table auth_accounts add column if not exists deletion_scheduled_for timestamptz",
        "alter table auth_accounts add column if not exists deletion_status text",
        "alter table auth_accounts add column if not exists deletion_processing_started_at timestamptz",
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
    ]
    with get_conn() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()


def is_account_scheduled_for_deletion(user_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select deletion_requested_at, deletion_scheduled_for, deletion_status
            from auth_accounts
            where user_id = %s
              and deletion_status in ('scheduled', 'processing')
              and deletion_scheduled_for is not null
            limit 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def schedule_account_deletion(user_id: int, password: str, grace_days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    scheduled_for = now + timedelta(days=grace_days)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, email, password_hash, deletion_status, deletion_scheduled_for
                from auth_accounts
                where user_id = %s
                limit 1
                """,
                (user_id,),
            )
            account = cur.fetchone()
            if not account:
                raise LookupError("Conta de login não encontrada.")
            # Este caminho NÃO passa por verify_user_password (select próprio):
            # sem esta guarda, _check_password(password, None) estoura
            # AttributeError, é engolido em db/users.py:350-354 e vira
            # "Senha incorreta." — mesma raiz, segundo caminho de código.
            if not account["password_hash"]:
                raise PasswordNotSetError(PASSWORD_NOT_SET_MSG)
            # O "informe a senha" vem DEPOIS do estado da conta (era a primeira
            # linha da função): senha vazia numa conta só-Google devolvia 400
            # "Informe sua senha", terceiro status para o mesmo estado.
            if not password:
                raise ValueError("Informe sua senha para confirmar a exclusão.")
            if not _check_password(password, account["password_hash"]):
                raise PermissionError("Senha incorreta.")

            if account.get("deletion_status") == "scheduled" and account.get("deletion_scheduled_for"):
                scheduled_for = account["deletion_scheduled_for"]
            else:
                cur.execute(
                    """
                    update auth_accounts
                    set deletion_status = 'scheduled',
                        deletion_requested_at = %s,
                        deletion_scheduled_for = %s,
                        deletion_processing_started_at = null
                    where user_id = %s
                    """,
                    (now, scheduled_for, user_id),
                )

            # Reduz a janela de uso de tokens de uso único. Cookies JWT antigos
            # também são bloqueados pelos guards do backend.
            for table in ("dashboard_sessions", "link_codes", "platform_onboarding_tokens", "password_reset_tokens"):
                if _table_exists(cur, table):
                    cur.execute(f"delete from {table} where user_id = %s", (user_id,))

        conn.commit()

    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)

    return {
        "user_id": user_id,
        "status": "scheduled",
        "deletion_scheduled_for": scheduled_for,
        "grace_days": grace_days,
    }


def _fetch_rows(cur, name: str, sql: str, params: tuple) -> tuple[str, list[dict]]:
    cur.execute(sql, params)
    return name, [dict(row) for row in cur.fetchall()]


def build_user_export_zip(user_id: int) -> bytes:
    datasets: dict[str, list[dict]] = {}

    with get_conn() as conn, conn.cursor() as cur:
        queries = [
            ("usuario", "select * from users where id = %s", (user_id,)),
            (
                "conta_login",
                """
                select id, user_id, email, phone_e164, phone_status, phone_confirmed_at,
                       whatsapp_verified_at, plan, plan_expires_at, created_at,
                       stripe_customer_id, engagement_opt_out, last_activity_at,
                       last_tip_sent_at, tip_email_opt_out, last_insight_sent_at,
                       insight_email_opt_out, whatsapp_updates_opt_out,
                       last_reengagement_sent_at, deletion_requested_at,
                       deletion_scheduled_for, deletion_status,
                       deletion_processing_started_at
                from auth_accounts
                where user_id = %s
                """,
                (user_id,),
            ),
            ("identidades", "select * from user_identities where user_id = %s", (user_id,)),
            ("contas", "select * from accounts where user_id = %s", (user_id,)),
            ("lancamentos", "select * from launches where user_id = %s", (user_id,)),
            ("declaracoes_bancarias", "select * from bank_movement_declarations where user_id = %s", (user_id,)),
            ("orcamentos", "select * from category_budgets where user_id = %s", (user_id,)),
            ("regras_categorias", "select * from user_category_rules where user_id = %s", (user_id,)),
            ("gatilhos_categorias", "select * from user_category_triggers where user_id = %s", (user_id,)),
            ("candidatos_gatilhos_categorias", "select * from user_trigger_candidates where user_id = %s", (user_id,)),
            ("feedback_categorias", "select * from user_category_feedback where user_id = %s", (user_id,)),
            ("acoes_pendentes", "select * from pending_actions where user_id = %s", (user_id,)),
            ("caixinhas", "select * from pockets where user_id = %s", (user_id,)),
            ("investimentos", "select * from investments where user_id = %s", (user_id,)),
            ("lotes_investimentos", "select * from investment_lots where user_id = %s", (user_id,)),
            ("cartoes", "select * from credit_cards where user_id = %s", (user_id,)),
            ("faturas_cartao", "select * from credit_bills where user_id = %s", (user_id,)),
            ("transacoes_cartao", "select * from credit_transactions where user_id = %s", (user_id,)),
            ("preferencias_resumo_diario", "select * from daily_report_prefs where user_id = %s", (user_id,)),
            ("importacoes_ofx", "select * from ofx_imports where user_id = %s", (user_id,)),
            ("sessoes_dashboard", "select code, user_id, expires_at, created_at from dashboard_sessions where user_id = %s", (user_id,)),
            (
                "conexoes_open_finance",
                "select * from open_finance_connections where user_id = %s",
                (user_id,),
            ),
            (
                "contas_open_finance",
                """
                select a.*
                from open_finance_accounts a
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
            (
                "transacoes_open_finance",
                """
                select t.*
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                """,
                (user_id,),
            ),
        ]

        for name, sql, params in queries:
            table_name = sql.split(" from ", 1)[-1].split()[0].strip()
            if table_name and table_name.isidentifier() and not _table_exists(cur, table_name):
                datasets[name] = []
                continue
            datasets[name] = _fetch_rows(cur, name, sql, params)[1]

        optional_queries = [
            (
                "eventos_login",
                "select id, user_id, email, success, failure_reason, ip_address, user_agent, created_at from auth_login_events where user_id = %s",
                (user_id,),
            ),
            (
                "eventos_sistema",
                "select id, level, event_type, message, source, user_id, details, created_at from system_event_logs where user_id = %s",
                (user_id,),
            ),
            # Cobranças Pix (§3.2). Colunas NOMEADAS e não `select *`: o
            # `qr_payload_enc` fica de fora porque é o "copia e cola" que MOVE
            # dinheiro (§13.6) — cifrado no banco, ele sairia daqui em texto
            # inútil para a pessoa e útil para quem interceptasse o ZIP. O
            # rastreio (`ga_client_id`, `fbp`, `fbc`) entra: é dado sobre a
            # pessoa, e é justamente o que ela tem direito de ver.
            (
                "cobrancas_pix",
                """
                select id, user_id, external_reference, asaas_payment_id,
                       asaas_customer_id, plan, plan_stored, price_cents,
                       credit_cents, amount_cents, currency, duration_days,
                       stripe_subscription_id, public_token, status,
                       due_date, qr_expires_at, access_starts_at,
                       access_expires_at, ga_client_id, fbp, fbc,
                       created_at, paid_at, canceled_at, refunded_at, purged_at
                from pix_charges
                where user_id = %s
                """,
                (user_id,),
            ),
        ]
        for name, sql, params in optional_queries:
            table_name = sql.split(" from ", 1)[-1].split()[0].strip()
            if _table_exists(cur, table_name):
                datasets[name] = _fetch_rows(cur, name, sql, params)[1]

    manifest = {
        "generated_at": datetime.now(timezone.utc),
        "user_id": user_id,
        "format": "json+csv",
        "datasets": {name: len(rows) for name, rows in datasets.items()},
        "notes": [
            "Hashes de senha não são exportados.",
            "Arquivos CSV são cópias tabulares; dados aninhados também aparecem no JSON completo.",
        ],
    }
    payload = {"manifesto": manifest, "dados": datasets}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "dados.json",
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "manifesto.json",
            json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2),
        )
        for name, rows in datasets.items():
            csv_buffer = io.StringIO()
            fieldnames = sorted({key for row in rows for key in row.keys()})
            writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames or ["sem_dados"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    key: json.dumps(_json_safe(value), ensure_ascii=False) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                })
            zf.writestr(f"csv/{name}.csv", csv_buffer.getvalue())

    return buffer.getvalue()


class ResetLockUnavailableError(RuntimeError):
    """Lock de um item Pluggy ocupado no reset — o reset inteiro aborta.

    Mesmo contrato do sync (`_grava_reconexao`): quem não adquiriu dentro do
    teto não escreve; o chamador devolve "tente de novo" (503). A janela do
    lock é só a fase de escrita de um sync, então a retentativa quase sempre
    entra."""


# Tabelas apagadas pelo reset "Recomeçar do zero", na ordem (child-first).
# As FKs reais são cascade/set null — a ordem é cinto-e-suspensório contra um
# banco antigo sem elas. Fora desta lista ficam as três tabelas OF que exigem
# join (deletadas à parte em reset_user_data) e a credit_bills (via card +
# coluna user_id, padrão de delete_user_data).
_RESET_TABLES = (
    # Recorrentes
    "recurring_income_credits",
    "bill_instances",
    "recurring_charges",
    "recurring_expenses",
    "recurring_incomes",
    "recurring_suggestion_dismissed",
    # Investimentos / caixinhas / orçamentos
    "investment_lots",
    "investments",
    "pocket_lots",
    "pockets",
    "budget_alert_sent",
    "category_budgets",
    # Categorias (regra ≠ categoria: tabelas diferentes, as duas somem)
    "user_category_rules",
    "user_category_triggers",
    "user_trigger_candidates",
    "user_category_feedback",
    "user_categories",
    # Agentes
    "agent_events",
    "agents",
    # IA / conversa. O delete CRU de pending_actions é EXCEÇÃO justificada às
    # escritas condicionais de docs/armadilhas.md (§ pending_actions): no reset
    # TODA pendência do usuário é obsoleta por definição — não há pendência a
    # preservar, então não há leitura anterior a respeitar.
    "ai_messages",
    "ai_pending_actions",
    "ai_fallback_log",
    "ai_proactive_cache",
    "pending_actions",
    # Uso / lançamentos. launches ANTES de financial_spaces: deletar o filho
    # primeiro evita o set null inútil da FK composta (user_id, space_id).
    "ofx_imports",
    "daily_report_prefs",
    "launches",
    "financial_spaces",
    # `accounts` NÃO entra aqui: a linha é preservada e o saldo é zerado no
    # INÍCIO da transação (ver reset_user_data) — apagar no fim recriava o
    # bug do #246.
)


def reset_user_data(
    user_id: int,
    password: str,
    remote_cleanup: "Callable[[], None] | None" = None,
) -> dict:
    """Recomeçar do zero: apaga dados financeiros e de uso, PRESERVA a conta.

    `remote_cleanup` (opcional) roda DEPOIS da senha e dos locks e ANTES de
    qualquer delete local — é onde a rota deleta os items na Pluggy. O
    invariante: se o reset local não vai acontecer (lock ocupado → aborto),
    a Pluggy não foi tocada. O hook é responsável pelo próprio best-effort
    (exceção dele aborta o reset com nada apagado localmente).

    CONTRASTE com o parâmetro de MESMO NOME em `delete_user_data`: lá o hook é
    chamado sob `try/except` e lock ocupado só loga, porque a exclusão não pode
    ser bloqueada pela Pluggy (decisão do dono, D4). Aqui o aborto é o correto:
    a conta sobrevive ao reset e a retentativa é do usuário.

    A linha de `accounts` é PRESERVADA (não é apagada) e o saldo é zerado na
    PRIMEIRA escrita da transação — ver o comentário no início dela: é esse
    `update` que serializa o reset contra um lançamento concorrente (#246).

    O saldo final NÃO é necessariamente 0: um lançamento que chegue durante a
    transação escreve depois, sobre o zero, e o saldo pode ficar NEGATIVO. É a
    aritmética da decisão do dono registrada abaixo — o lançamento sobrevive
    com o dinheiro dele —, e é o desfecho correto: a alternativa (saldo 0 com o
    lançamento vivo) é exatamente o bug do #246.

    Ficam intactos: users (a linha), auth_accounts (login, plano, Stripe,
    opt-outs, contadores de IA, deletion_*), auth_identities, user_identities
    (vínculo WhatsApp/Discord — decisão do dono), MFA e sessões, tokens,
    plan_trials, push_tokens, open_finance_item_registry, audit_events,
    pii_access_log, system_event_logs, affiliate*, checkout_funnel_events.

    O `open_finance_item_registry` é preservado E GANHA uma linha por conexão
    apagada (`origin='removed'`, `last_event='reset'`, na mesma transação do
    delete): é ela que impede uma reentrega de `item/created` de recriar pelo
    webhook o banco que o reset acabou de remover.

    Uma transação só: falha no meio → nada mudou (sem carência, sem meio-termo).

    SEM a re-varredura pós-commit que delete_user_data faz: por decisão do
    dono, dado criado DURANTE a janela do reset por outro fluxo (ex.: um
    lançamento chegando pelo WhatsApp) sobrevive — o usuário continua existindo
    e escrita nova dele é dado novo, não resíduo do passado.
    """
    if not verify_user_password(user_id, password):
        raise PermissionError("Senha incorreta.")

    # Import na função: db/privacy não importa módulos irmãos no topo (ciclo).
    from .open_finance import list_pluggy_item_ids
    from .open_finance_state import pluggy_items_lock

    counts: dict[str, int] = {}

    def _delete(cur, table: str, sql: str | None = None) -> None:
        if not _table_exists(cur, table):
            return
        if sql is None:
            if not _column_exists(cur, table, "user_id"):
                return
            sql = f"delete from {table} where user_id = %s"
        cur.execute(sql, (user_id,))
        counts[table] = counts.get(table, 0) + cur.rowcount

    # Locks dos items Pluggy ANTES de qualquer delete: um sync na fase de
    # escrita re-inseriria contas/transações no meio da limpeza. Todos os
    # locks entram numa ÚNICA sessão dedicada (`pluggy_items_lock`) — N
    # `pluggy_item_lock` aninhados retinham N slots do semáforo e, com mais
    # itens que OF_SYNC_LOCK_MAX_CONN, o reset esperava um slot que ele mesmo
    # segurava (503 pra sempre; Codex, PR #217). A saída do `with` libera
    # todos, inclusive em exceção.
    with pluggy_items_lock(list_pluggy_item_ids(user_id)) as locked:
        if not locked:
            raise ResetLockUnavailableError(
                "Sincronização bancária em andamento. "
                "Tente de novo em alguns segundos."
            )

        # Limpeza remota SOB os locks e ANTES de qualquer delete local. Rede
        # dentro do lock viola de propósito a disciplina de janela curta do
        # `pluggy_item_lock` (ver docstring dele): aqui a operação é rara,
        # disparada pelo usuário, e segurar o lock é exatamente o que impede
        # um sync de escrever durante a deleção remota + limpeza local — o
        # pior caso pra um sync concorrente é esperar o teto e reportar
        # sync_in_progress, o mesmo desfecho de perder o lock pra outro sync.
        #
        # Janela residual (trade-off documentado): exceção DEPOIS daqui (ex.:
        # erro de DB no meio dos deletes) deixa item já removido na Pluggy com
        # a conexão local viva. Não é silencioso nem terminal: o próximo
        # sync/job de saúde consulta GET /items/{id}, leva 404 (item_missing)
        # e marca a conexão ERROR/item_missing com CTA "Refaça a conexão com o
        # banco" (core/services/pluggy_sync.py, pluggy_health.py) — e o
        # próprio reset, retentado, termina a limpeza local.
        if remote_cleanup is not None:
            remote_cleanup()

        with get_conn() as conn:
            with conn.cursor() as cur:
                # PRIMEIRAS escritas da transação, e são elas que fazem o reset
                # ser correto sob concorrência (#246): o `update` segura o lock
                # de accounts até o commit, então um lançamento concorrente só
                # escreve depois, sobre saldo 0. Saldo NEGATIVO após o reset é o
                # certo — o lançamento sobreviveu (decisão do dono) com o
                # dinheiro dele. `ensure_user_tx` ANTES porque sem a linha o
                # update casa 0 e não trava nada, e o estado é alcançável
                # (`merge_users` apaga accounts da origem, db/users.py:88); o
                # `on conflict do nothing` (:19) é inócuo no caso normal.
                #
                # ponytail: o lock inverte a ordem accounts×pockets/investments
                # de 4 fluxos (db/pockets.py:336→466, db/investments.py:1041→1096
                # e :1554→1680, db/accounts.py:1647/1696→1718). Deadlock é REAL e
                # é novo, e o reset NÃO é imune: ele fecha o ciclo (pede pockets
                # no `_delete` do laço segurando accounts desde o update), e
                # morre quem o Postgres detecta primeiro — ordem de chegada, não
                # estrutura. Teto: nas 3 portas do OF o DeadlockDetected cai em
                # `except Exception: pass` e some (db/accounts.py:1500-1512). No
                # reset ele vira 503 (frontend/routes/settings.py:188) com o
                # `remote_cleanup` já feito — gatilho novo para a janela residual.
                #
                # Quem chega DEPOIS do lock não deadlocka, só ESPERA — no
                # `ensure_user` do writer, que pede accounts em transação
                # PRÓPRIA antes de qualquer caixinha. Invariante frágil: vale
                # enquanto todo escritor de accounts chamar `ensure_user` — hoje
                # os 10 chamam, menos o `merge_users`, que é a exceção conhecida.
                # A receita, e ela precisa ser case-INSENSITIVE: uma versão
                # anterior deste comentário usava `grep -rn 'update accounts
                # set'` e por isso dizia 9 — o `set_balance` (db/accounts.py:41)
                # escreve em MAIÚSCULAS e ficava invisível.
                #     grep -rniE 'update[[:space:]]+accounts[[:space:]]+set' \
                #          --include='*.py' db/
                # (medido 2026-09-04: 12 linhas = 10 escritores + este
                # comentário + o `update` do próprio reset. REMEÇA antes de
                # reusar.) E a
                # espera não é só do dono da linha — a fila do WhatsApp tem
                # consumidor único (`_worker_loop`, adapters/whatsapp/wa_app.py:324),
                # então um writer preso trava as mensagens de TODOS na janela.
                #
                # Sem `lock_timeout` de propósito: o do repo vive nas conexões
                # DEDICADAS do `pluggy_item_lock` (os `set_config('lock_timeout',
                # …)` de `pluggy_item_lock` e `pluggy_items_lock`, em
                # db/open_finance_state.py — sem número: os três que estavam
                # aqui apontavam para linha em branco muito antes desta leitura,
                # CLAUDE.md §2), feitas para ter teto próprio. No pool ele valeria
                # para TODO write do produto, e espera correta viraria erro. Se
                # incomodar, a saída é ordem única de lock nos writers.
                #
                # Janela MEDIDA 2026-09-03 (Postgres 15.15 local, 3 execuções,
                # writer disparado no 1º `_table_exists` — gatilho de
                # tests/test_account_reset.py::_reset_com_lancamento_concorrente).
                # REMEÇA antes de reusar: conta vazia 0,05 s; MAIOR CONTA REAL de
                # produção 0,08 s (1.858 linhas; 342 contas, p99 101, nenhuma acima de
                # 10 mil). Sintético: 50 mil launches sozinhos 0,58 s; os mesmos com
                # 20 mil open_finance_transactions, 16,9 s. Custo ≈ linhas apagadas ×
                # tamanho GLOBAL de cada filha que as referencia (open_finance_
                # transactions conta DUAS vezes p/ launches) — nenhuma das FKs
                # `on delete set null` p/ launches/credit_transactions é indexada,
                # então conta pequena também dói se a filha for grande; o conserto é
                # o índice. Os 0,08 s são só o 1º fator (linhas-pai) e em banco
                # LOCAL: o 2º — tamanho das filhas EM PRODUÇÃO — nunca foi medido,
                # então eles NÃO sustentam "janela pequena em produção". Ele, as
                # FKs e as queries: #253.
                ensure_user_tx(cur, user_id)
                cur.execute("update accounts set balance = 0 where user_id = %s", (user_id,))
                # sem `counts["accounts"]`: o retorno é {"deleted": ...} e a
                # linha NÃO é apagada — contar aqui seria mentira no contrato.

                # Open Finance, child-first (accounts/transactions/investments
                # não têm user_id — o isolamento entra pelo join na connection).
                _delete(cur, "open_finance_transactions", """
                    delete from open_finance_transactions t
                    using open_finance_accounts a, open_finance_connections c
                    where t.account_id = a.id
                      and a.connection_id = c.id
                      and c.user_id = %s
                    """)
                _delete(cur, "open_finance_investments", """
                    delete from open_finance_investments i
                    using open_finance_connections c
                    where i.connection_id = c.id
                      and c.user_id = %s
                    """)
                _delete(cur, "open_finance_accounts", """
                    delete from open_finance_accounts a
                    using open_finance_connections c
                    where a.connection_id = c.id
                      and c.user_id = %s
                    """)
                # RETURNING: o que ESTE delete varreu. Item salvo entre a
                # enumeração do remote_cleanup e este delete não foi deletado
                # na Pluggy — o chamador compara os dois conjuntos e faz um 2º
                # passe (Codex PR #217, 11º). Quem decide o que entra é
                # `pluggy_items_a_deletar`, fonte única do filtro (§0.7).
                pluggy_items_swept: list[str] = []
                if _table_exists(cur, "open_finance_connections"):
                    from .open_finance_state import pluggy_items_a_deletar

                    cur.execute(
                        """
                        delete from open_finance_connections
                        where user_id = %s
                        returning provider, provider_item_id, status
                        """,
                        (user_id,),
                    )
                    rows = cur.fetchall()
                    counts["open_finance_connections"] = len(rows)
                    pluggy_items_swept = pluggy_items_a_deletar(rows)
                    # Marca da remoção deliberada, na MESMA transação do delete
                    # (o registry é preservado pelo reset, então ela sobrevive):
                    # sem ela uma reentrega de `item/created` recria pelo webhook
                    # a conexão que o reset apagou. Mesma regra e mesma função do
                    # disconnect — CLAUDE.md §0.7.
                    if _table_exists(cur, "open_finance_item_registry"):
                        from .open_finance_state import mark_items_removed

                        mark_items_removed(cur, user_id, rows, last_event="reset")

                # Crédito: transações → faturas (via card E via coluna user_id,
                # padrão de delete_user_data) → cartões.
                _delete(cur, "credit_transactions")
                if _table_exists(cur, "credit_bills"):
                    if _table_exists(cur, "credit_cards"):
                        cur.execute(
                            """
                            delete from credit_bills b
                            using credit_cards c
                            where b.card_id = c.id
                              and c.user_id = %s
                            """,
                            (user_id,),
                        )
                        counts["credit_bills"] = counts.get("credit_bills", 0) + cur.rowcount
                    if _column_exists(cur, "credit_bills", "user_id"):
                        cur.execute("delete from credit_bills where user_id = %s", (user_id,))
                        counts["credit_bills"] = counts.get("credit_bills", 0) + cur.rowcount
                _delete(cur, "credit_cards")

                for table in _RESET_TABLES:
                    _delete(cur, table)

                # Mesma transação: zera as preferências que apontavam para o que
                # sumiu e reabre o onboarding (needs_onboarding volta a True).
                cur.execute(
                    """
                    update users
                    set default_card_id = null,
                        reminders_enabled = false,
                        reminders_days_before = 3
                    where id = %s
                    """,
                    (user_id,),
                )
                cur.execute(
                    """
                    update auth_accounts
                    set onboarding_step = 0,
                        onboarding_completed_at = null
                    where user_id = %s
                    """,
                    (user_id,),
                )

            conn.commit()

    return {"user_id": user_id, "deleted": counts,
            "pluggy_items_swept": pluggy_items_swept}


def delete_user_data(
    user_id: int,
    remote_cleanup: "Callable[[], None] | None" = None,
) -> dict:
    """Exclusão definitiva: apaga a conta e tudo que pertence a ela.

    `remote_cleanup` (opcional) roda DEPOIS dos locks dos items Pluggy e ANTES
    de qualquer delete local — é onde o chamador deleta os items na Pluggy.

    DIVERGE do hook de `reset_user_data` de propósito (decisão do dono, D4):
    aqui a exclusão NUNCA é bloqueada pela Pluggy. Exceção do hook é engolida e
    logada por ESTA função (no reset ela aborta o reset com nada apagado
    localmente), e lock de item ocupado loga e SEGUE (no reset ele levanta
    `ResetLockUnavailableError` e a Pluggy não é tocada). O prazo da LGPD ganha
    do lock — a mesma palavra `remote_cleanup` tem, de propósito, duas
    semânticas nas duas funções.

    Os logs DESTA função vão com a COLUNA `user_id = None`, sempre: esta mesma
    transação faz `delete from system_event_logs where user_id = %s` e a FK é
    `on delete cascade` — log com dono é log que se apaga sozinho (ou cujo
    INSERT viola a FK, se escrito depois do commit). A lista `items` em `details`
    é a chave operacional: com ela o operador acha o item na Pluggy e no log,
    sem precisar do dono. Não há ferramenta no repositório que consuma esses ids
    — o one-shot que fazia isso saiu em `924aee3f` e volta do histórico com
    `git checkout bda3ee7 -- scripts/adotar_items_of_orfaos.py scripts/adotar_items_lista.py`
    (os DOIS arquivos, ver `frontend/routes/open_finance.py`,
    `_adota_item_orfao`, para por que ele saiu). O `user_id` não vai nem para
    `details`, porque a exclusão existe justamente para remover identificadores
    da conta (mesma regra de `plan_trials`).

    JANELA RESIDUAL, a mesma do reset (o comentário em :628-640) e sem log
    próprio: exceção no delete LOCAL depois de o `remote_cleanup` ter dado certo
    deixa o item já apagado na Pluggy, a conexão local viva apontando para ele, a
    conta reagendada — e nada registrado (não há log aqui nesse caminho). Não é
    terminal: a rodada seguinte re-enumera o mesmo item e o 404-como-sucesso de
    `delete_pluggy_item` (core/services/pluggy.py) torna a retentativa idempotente.

    Devolve, além de `{"user_id", "deleted", "email"}`, o `pluggy_items_swept`:
    os items que o DELETE local varreu, para o chamador comparar com o que a
    limpeza remota enumerou e fazer o 2º passe.
    """
    primary_email = None
    user_owned_tables = (
        # Tabelas com coluna user_id e ON DELETE CASCADE (verificado em prod).
        # Dependiam só do cascade; incluídas no sweep explícito + na verificação
        # de sobra como cinto-e-suspensório, caso um DB antigo perca a FK.
        # pocket_lots antes de pockets (child-first) por segurança de ordem.
        "ai_messages",
        "ai_pending_actions",
        "recurring_charges",
        "recurring_incomes",
        "recurring_expenses",
        "bill_instances",
        "user_mfa_backup_codes",
        "user_mfa",
        "data_export_tokens",
        "auth_refresh_tokens",
        "auth_sessions",
        "user_categories",
        "pocket_lots",
        "affiliates",
        "credit_cards",
        "investment_lots",
        "investments",
        "category_budgets",
        "pending_actions",
        "user_category_rules",
        "user_category_triggers",
        "user_trigger_candidates",
        "user_category_feedback",
        "daily_report_prefs",
        "ofx_imports",
        "dashboard_sessions",
        "link_codes",
        "platform_onboarding_tokens",
        "password_reset_tokens",
        "accounts",
        "launches",
        "pockets",
        "user_identities",
        "auth_accounts",
    )
    # Import na função: db/privacy não importa módulos irmãos no topo (ciclo).
    from core.observability import log_system_event_sync

    from .open_finance import list_pluggy_item_ids
    from .open_finance_state import pluggy_items_a_deletar, pluggy_items_lock

    # Enumeração SÓ por `list_pluggy_item_ids` (que filtra `user_id` E provider).
    # NUNCA pelo `open_finance_item_registry`: ele tem linhas com `user_id NULL` e
    # linhas de outros donos do MESMO item — enumerar por ele deletaria o banco de
    # um vizinho. A `uq_of_conn_provider_item` nasce num bloco que só emite warning
    # (db/schema.py:521-528) e pode não existir num DB antigo: a garantia real é o
    # filtro por user_id, não o índice.
    # ponytail: PAUSED fica de fora (regra do `list_pluggy_item_ids`). Hoje a pausa
    # só acontece DEPOIS de o delete remoto ter dado certo (trial expiry). Se um dia
    # pausar sem deletar, item pausado de conta excluída fica órfão lá para sempre.
    itens_pluggy = list_pluggy_item_ids(user_id)

    # A enumeração acima e o `pluggy_items_lock` abaixo ficam FORA do best-effort
    # de propósito: exceção neles é falha de BANCO, não da Pluggy, e engoli-la
    # seria excluir a conta com a limpeza remota silenciosamente pulada (item
    # órfão e pago). Solta, ela sobe para o `except` de
    # `process_due_account_deletions`, que restaura o agendamento e devolve
    # `{"error": ...}` — o cron loga `account_deletion_job` em nível error e sai 1.
    #
    # Locks dos items ANTES de qualquer delete, mesma ordem do reset e do
    # disconnect: sem eles um sync na fase de escrita re-insere contas/transações
    # no meio da limpeza, e a deleção remota acontece com o item sendo lido.
    with pluggy_items_lock(itens_pluggy) as locked:
        if not locked:
            # NÃO aborta (decisão do dono, D4): o prazo da LGPD ganha do lock —
            # é o INVERSO do reset, que levanta `ResetLockUnavailableError`. O que
            # um sync concorrente escrever no meio não fica órfão, e o mecanismo
            # NÃO é a re-varredura pós-commit: `open_finance_connections` não está
            # em `user_owned_tables`, e tanto a re-varredura quanto a `RuntimeError`
            # de sobras só percorrem essa tupla. Quem protege é (a) DENTRO da
            # transação, o `RETURNING` do delete + o 2º passe remoto do chamador,
            # que pega item salvo antes do commit; e (b) DEPOIS do commit, a FK
            # `open_finance_connections_user_id_fkey`: sem a linha de `users`, a
            # re-inserção estoura `ForeignKeyViolation` (medido) — o sync perde a
            # escrita em vez de deixar conexão de conta excluída no banco.
            log_system_event_sync(
                "warning", "account_deletion_of_lock_busy",
                f"Exclusão de conta seguiu SEM os locks dos items Pluggy: {itens_pluggy}",
                source="db.privacy", user_id=None,
                details={"items": itens_pluggy},
            )

        if remote_cleanup is not None:
            # O `try` é ESTRUTURAL, não cortesia com o chamador: é ele que faz
            # "falha remota não bloqueia a exclusão" valer sem depender de o
            # chamador lembrar (`reset_user_data` chama o hook CRU de propósito).
            # Cobre também o `ImportError` do import db/ -> frontend/ do hook.
            try:
                remote_cleanup()
            except Exception as exc:  # noqa: BLE001 — best-effort; a exclusão segue
                log_system_event_sync(
                    "warning", "account_deletion_pluggy_cleanup_failed",
                    f"Limpeza remota na Pluggy falhou na exclusão de conta: {exc}",
                    source="db.privacy", user_id=None,
                    details={"items": itens_pluggy, "error": str(exc)[:200]},
                )

        deleted = 0

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select email, email_enc from auth_accounts where user_id = %s",
                    (user_id,),
                )
                ctx_del = PiiAccessContext(
                    purpose="account_deletion_lookup",
                    actor="system:account_deletion_job",
                    subject_user_id=user_id,
                    field="email",
                )
                emails: list[str] = []
                for row in cur.fetchall():
                    enc = row.get("email_enc")
                    if enc:
                        val = decrypt_pii_optional(enc, ctx=ctx_del)
                    else:
                        val = row.get("email")
                    if val:
                        emails.append(val)
                primary_email = emails[0] if emails else None

                if _table_exists(cur, "auth_login_events"):
                    cur.execute("delete from auth_login_events where user_id = %s", (user_id,))
                    if emails:
                        cur.execute("delete from auth_login_events where email = any(%s)", (emails,))

                if _table_exists(cur, "system_event_logs"):
                    cur.execute("delete from system_event_logs where user_id = %s", (user_id,))

                if _table_exists(cur, "email_verification_codes") and emails:
                    cur.execute("delete from email_verification_codes where email = any(%s)", (emails,))

                if _table_exists(cur, "auth_rate_limits"):
                    identifiers = [f"email:{email.strip().lower()}" for email in emails]
                    identifiers.append(f"user:{user_id}")  # teto por conta (mfa-verify)
                    cur.execute("delete from auth_rate_limits where identifier = any(%s)", (identifiers,))

                if _table_exists(cur, "open_finance_transactions"):
                    cur.execute(
                        """
                        delete from open_finance_transactions t
                        using open_finance_accounts a, open_finance_connections c
                        where t.account_id = a.id
                          and a.connection_id = c.id
                          and c.user_id = %s
                        """,
                        (user_id,),
                    )
                if _table_exists(cur, "open_finance_accounts"):
                    cur.execute(
                        """
                        delete from open_finance_accounts a
                        using open_finance_connections c
                        where a.connection_id = c.id
                          and c.user_id = %s
                        """,
                        (user_id,),
                    )

                # RETURNING: o que ESTE delete varreu. Item salvo ENTRE a
                # enumeração do `remote_cleanup` e este delete não foi deletado
                # na Pluggy — o chamador compara os dois conjuntos e faz um 2º
                # passe. O filtro é o do reset e o do disconnect, e agora é UMA
                # função (`pluggy_items_a_deletar`, CLAUDE.md §0.7): PAUSED fica
                # fora porque o item já foi deletado lá.
                pluggy_items_swept: list[str] = []
                if _table_exists(cur, "open_finance_connections"):
                    cur.execute(
                        """
                        delete from open_finance_connections
                        where user_id = %s
                        returning provider, provider_item_id, status
                        """,
                        (user_id,),
                    )
                    pluggy_items_swept = pluggy_items_a_deletar(cur.fetchall())
                if _table_exists(cur, "credit_transactions"):
                    cur.execute("delete from credit_transactions where user_id = %s", (user_id,))

                if _table_exists(cur, "credit_bills"):
                    if _table_exists(cur, "credit_cards"):
                        cur.execute(
                            """
                            delete from credit_bills b
                            using credit_cards c
                            where b.card_id = c.id
                              and c.user_id = %s
                            """,
                            (user_id,),
                        )
                    if _column_exists(cur, "credit_bills", "user_id"):
                        cur.execute("delete from credit_bills where user_id = %s", (user_id,))

                # `plan_trials` não aparece em `user_owned_tables` de propósito: a
                # linha é keyed por phone_hash e segura a trava de 15 dias de teste
                # por telefone, na vida — apagar devolveria um trial novo a cada
                # conta recriada com o mesmo número. O `user_id` dela é desvinculado
                # pela FK `on delete set null` (db/schema_repairs.py), e não por um
                # UPDATE aqui: um UPDATE perde a corrida com um
                # `claim_trial_for_user` que commite depois dele, e a varredura
                # pós-commit nunca revisita esta tabela.

                # `pix_charges` NÃO entra em `user_owned_tables` pelo mesmo motivo
                # da `plan_trials`: a linha SOBREVIVE à exclusão — é o registro do
                # dinheiro que entrou, e reconciliar pagamento é obrigação fiscal.
                # Quem desfaz o vínculo é a FK `on delete set null` (§13.2), não um
                # UPDATE aqui, porque UPDATE perde a corrida com um webhook que
                # commite depois e a varredura pós-commit não revisita a tabela.
                #
                # O que este UPDATE faz é o outro lado: apagar o que NÃO é registro
                # financeiro. `ga_client_id`, `fbp` e `fbc` são os identificadores
                # com que GA e Meta reidentificam a pessoa e não reconciliam
                # centavo nenhum; `qr_payload_enc` é instrumento de pagamento ao
                # portador (§13.6); `asaas_customer_id` liga a linha ao cadastro da
                # pessoa no provedor. Valores, ids e datas ficam. Sem `where
                # purged_at is null` de propósito: aqui a conta está sendo excluída
                # AGORA e reescrever o carimbo de uma linha já purgada não tem
                # custo, enquanto pular uma linha teria — a varredura diária do
                # §13.2 é que precisa do filtro, para não reescrever todo dia.
                if _table_exists(cur, "pix_charges"):
                    cur.execute(
                        """
                        update pix_charges
                           set ga_client_id = null, fbp = null, fbc = null,
                               qr_payload_enc = null, asaas_customer_id = null,
                               purged_at = now()
                         where user_id = %s
                        """,
                        (user_id,),
                    )

                for table in user_owned_tables:
                    if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                        cur.execute(f"delete from {table} where user_id = %s", (user_id,))

                # CINTO (P2 do Codex na PR #539, reproduzido pelo Tester). O
                # `RETURNING` acima é a foto do instante do DELETE. Entre ele e o
                # `delete from users` logo abaixo, outra sessão AINDA consegue
                # commitar uma conexão nova: a linha de `users` existe até aqui, e
                # a porta de produção é o webhook `item/created`
                # (`_adota_item_orfao`). O `delete from users` leva essa conexão
                # pela CASCATA — sem `RETURNING`, então o item ficava vivo (e pago)
                # na Pluggy depois da exclusão LGPD. A reconsulta enxerga a linha
                # (READ COMMITTED: o commit da outra sessão é visível ao statement
                # seguinte) e soma ao 2º passe, que roda depois do commit e funciona
                # com a conta já apagada (`item_ids` explícito).
                #
                # É CINTO, não a porta: quem fecha a porta é a guarda de exclusão
                # agendada na adoção. Travar `users` no começo da transação — a
                # correção que o apontamento sugeria — foi MEDIDA e dá
                # `DeadlockDetected ... while locking tuple in relation "users"`:
                # todo escritor do repositório trava `accounts` antes de `users`
                # (`db/bank_movements.py:_lock_user`) e a exclusão inverteria a ordem.
                #
                # SOBRA a janela entre esta reconsulta e o `delete from users`, e o
                # que a fecha NÃO é ela ser curta — foi MEDIDO pelo Tester (PR-C
                # #539) e são coisas diferentes. Com INSERT CRU em
                # `open_finance_connections` a janela está fisicamente ABERTA e o
                # item VAZA (a conexão commita, sai pela cascata e fica viva na
                # Pluggy). Quem a fecha para a escrita REAL é a ORDEM desta função:
                # todo escritor passa por `_lock_user` (`db/bank_movements.py:58`,
                # `select ... from accounts ... for update`) e o laço de
                # `user_owned_tables` acima já apagou a linha de `accounts` deste
                # usuário — a escrita concorrente morre em `ForeignKeyViolation`
                # antes de chegar à conexão (`test_p3a`, sonda do Tester).
                # CONSEQUÊNCIA: mover o `delete from accounts` para DEPOIS desta
                # reconsulta reabre o vazamento. A ordem `accounts → users` é a
                # proteção; mantenha-a.
                if _table_exists(cur, "open_finance_connections"):
                    cur.execute(
                        """
                        select provider, provider_item_id, status
                        from open_finance_connections
                        where user_id = %s
                        """,
                        (user_id,),
                    )
                    pluggy_items_swept = sorted(
                        set(pluggy_items_swept) | set(pluggy_items_a_deletar(cur.fetchall()))
                    )

                cur.execute("delete from users where id = %s", (user_id,))
                deleted += cur.rowcount

                # Bancos antigos podem não ter todas as FKs/cascades esperadas.
                # A segunda passada remove qualquer resíduo órfão que tenha ficado.
                for table in user_owned_tables:
                    if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                        cur.execute(f"delete from {table} where user_id = %s", (user_id,))

                cur.execute("delete from users where id = %s", (user_id,))
                deleted += cur.rowcount

                cur.execute("select 1 from users where id = %s", (user_id,))
                if cur.fetchone():
                    raise RuntimeError(f"Falha ao remover usuário {user_id}: registro ainda existe após a limpeza final.")

            conn.commit()

        # Verificação pós-commit: garante que outra conexão também enxerga a conta
        # como removida antes de o job considerar a exclusão concluída.
        with get_conn() as conn:
            with conn.cursor() as cur:
                for table in user_owned_tables:
                    if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                        cur.execute(f"delete from {table} where user_id = %s", (user_id,))

                cur.execute("delete from users where id = %s", (user_id,))
                deleted += cur.rowcount

                cur.execute("select 1 from users where id = %s", (user_id,))
                user_still_exists = cur.fetchone() is not None

                leftovers: dict[str, int] = {}
                for table in user_owned_tables:
                    if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                        cur.execute(f"select count(*) as total from {table} where user_id = %s", (user_id,))
                        total = int(cur.fetchone()["total"])
                        if total:
                            leftovers[table] = total

            conn.commit()

        if user_still_exists or leftovers:
            raise RuntimeError(
                f"Falha ao confirmar exclusão do usuário {user_id}: "
                f"user_exists={user_still_exists}; leftovers={leftovers}"
            )

        from db_support import invalidate_auth_user_cache
        invalidate_auth_user_cache(user_id)  # a conta saiu do banco; sai do cache junto
        return {"user_id": user_id, "deleted": bool(deleted), "email": primary_email,
                "pluggy_items_swept": pluggy_items_swept}


def _claim_due_account_deletions(limit: int, stale_after_minutes: int) -> list[int]:
    ensure_account_deletion_columns()

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select user_id
            from auth_accounts
            where deletion_scheduled_for <= now()
              and (
                deletion_status = 'scheduled'
                or (
                  deletion_status = 'processing'
                  and (
                    deletion_processing_started_at is null
                    or deletion_processing_started_at <= now() - (%s * interval '1 minute')
                  )
                )
              )
            order by deletion_scheduled_for
            limit %s
            for update skip locked
            """,
            (stale_after_minutes, limit),
        )
        due_user_ids = [int(row["user_id"]) for row in cur.fetchall()]

        for user_id in due_user_ids:
            cur.execute(
                """
                update auth_accounts
                set deletion_status = 'processing',
                    deletion_processing_started_at = now()
                where user_id = %s
                """,
                (user_id,),
            )

        conn.commit()

    from db_support import invalidate_auth_user_cache
    for uid in due_user_ids:
        invalidate_auth_user_cache(uid)

    return due_user_ids


def _restore_account_deletion_schedule(user_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            update auth_accounts
            set deletion_status = 'scheduled',
                deletion_processing_started_at = null
            where user_id = %s
              and deletion_status = 'processing'
            """,
            (user_id,),
        )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)


def process_due_account_deletions(limit: int = 50, stale_after_minutes: int = 120) -> list[dict]:
    due_user_ids = _claim_due_account_deletions(limit, stale_after_minutes)

    results = []
    for user_id in due_user_ids:
        # O que a limpeza remota ENUMEROU — comparado adiante com o que o DELETE
        # local varreu (RETURNING), para o 2º passe pegar item salvo na janela
        # entre as duas coisas. Mesma regra de POST /settings/reset
        # (frontend/routes/settings.py) e do disconnect.
        enumerados: list[str] = []

        def _limpeza_remota() -> None:
            # Closure sobre o `user_id`/`enumerados` DESTA volta do laço: o hook
            # é chamado sincronamente dentro do `delete_user_data` logo abaixo,
            # então não há late binding a temer.
            # Import DENTRO da função: `db/` não importa `frontend/` no topo
            # (precedente: core/services/billing_access.py:243). Um ImportError
            # aqui não bloqueia a exclusão — o `try` de `delete_user_data` em
            # volta do hook cobre a categoria inteira (LGPD ganha do import).
            from frontend.routes.open_finance import delete_pluggy_items_best_effort

            # `log_user_id=False`: só a exclusão desliga o dono do log de apiKey
            # falhada — a linha tem de sobreviver à cascata sem o identificador
            # de uma conta apagada (o disconnect e o reset seguem com a coluna).
            enumerados.extend(delete_pluggy_items_best_effort(user_id, log_user_id=False))

        try:
            resultado = delete_user_data(user_id, remote_cleanup=_limpeza_remota)
        except Exception as exc:
            _restore_account_deletion_schedule(user_id)
            results.append({"user_id": user_id, "deleted": False, "error": str(exc)})
            continue

        # 2º passe remoto: item salvo ENTRE a enumeração acima e o DELETE local
        # foi varrido do banco sem ser deletado na Pluggy — órfão pago, com os
        # dados bancários do titular, DEPOIS de uma exclusão LGPD. `tardios` é
        # normalmente vazio. Best-effort como o 1º passe: o helper já loga por
        # item; este `try` cobre a falha do helper inteiro.
        tardios = sorted(set(resultado.pop("pluggy_items_swept", None) or []) - set(enumerados))
        if tardios:
            try:
                from frontend.routes.open_finance import delete_pluggy_items_best_effort

                delete_pluggy_items_best_effort(user_id, tardios, log_user_id=False)
            except Exception as exc:  # noqa: BLE001 — a conta já foi excluída
                from core.observability import log_system_event_sync

                # `user_id=None`: a conta não existe mais e a FK é on delete
                # cascade — ver o docstring de `delete_user_data`.
                log_system_event_sync(
                    "warning", "account_deletion_pluggy_cleanup_failed",
                    f"2º passe remoto da exclusão de conta falhou: {exc}",
                    source="db.privacy", user_id=None,
                    details={"items": tardios, "error": str(exc)[:200]},
                )

        results.append(resultado)
    return results
