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
    if not token:
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

    A linha de `accounts` é PRESERVADA com `balance = 0` (não é apagada) — ver
    o comentário no início da transação: é ela que serializa o reset contra um
    lançamento concorrente (issue #246).

    Ficam intactos: users (a linha), auth_accounts (login, plano, Stripe,
    opt-outs, contadores de IA, deletion_*), auth_identities, user_identities
    (vínculo WhatsApp/Discord — decisão do dono), MFA e sessões, tokens,
    plan_trials, push_tokens, open_finance_item_registry, audit_events,
    pii_access_log, system_event_logs, affiliate*, checkout_funnel_events.

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
                # estrutura. Dois tetos: nas 3 portas do OF o DeadlockDetected
                # cai em `except Exception: pass` e some (lista em
                # db/accounts.py:1500-1512); e o reset morto sobe 500, não o 503
                # recuperável que frontend/routes/open_finance.py:393-401 já dá
                # para a MESMA exceção — com o `remote_cleanup` já executado, o
                # que é gatilho novo para a janela residual documentada acima.
                #
                # Quem chega DEPOIS do lock não deadlocka, só ESPERA — no
                # `ensure_user` do writer, que pede accounts em transação
                # PRÓPRIA antes de qualquer caixinha. Invariante frágil: vale
                # enquanto todo escritor de accounts chamar `ensure_user` — hoje
                # os 9 chamam (`grep -rn 'update accounts set' --include='*.py'
                # db/`, menos o `merge_users`, que é a exceção conhecida). E a
                # espera não é só do dono da linha — a fila do
                # WhatsApp tem consumidor único (`_worker_loop`,
                # adapters/whatsapp/wa_app.py:324), então um writer preso trava
                # as mensagens de TODOS os usuários durante a janela.
                #
                # Sem `lock_timeout` de propósito: o do repo vive nas conexões
                # DEDICADAS do `pluggy_item_lock` (db/open_finance_state.py:595,
                # :611, :676), feitas para ter teto próprio. No pool ele valeria
                # para TODO write do produto, e espera correta viraria erro. Se
                # incomodar, a saída é ordem única de lock nos writers.
                #
                # Janela MEDIDA 2026-09-03 (Postgres 15.15 local, 3 execuções,
                # writer disparado no 1º `_table_exists` — gatilho de
                # tests/test_account_reset.py::_reset_com_lancamento_concorrente).
                # REMEÇA antes de reusar: conta vazia 0,05 s; MAIOR CONTA REAL de
                # produção 0,08 s (1.858 linhas; 342 contas, p99 101, nenhuma
                # acima de 10 mil). Sintético 27× maior: 50k launches sozinhos
                # 0,58 s, com 20k open_finance_transactions 16,9 s — explode o
                # PRODUTO das duas, porque `imported_launch_id` referencia
                # launches com `on delete set null` sem índice
                # (db/schema.py:416). Conta com dezenas de milhares de
                # lançamentos E Open Finance muda a decisão; o conserto é o
                # índice.
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
                # passe (Codex PR #217, 11º). PAUSED fica fora do capture: o
                # item já foi deletado na Pluggy (mesma regra do
                # list_pluggy_item_ids).
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
                    rows = cur.fetchall()
                    counts["open_finance_connections"] = len(rows)
                    pluggy_items_swept = sorted({
                        r["provider_item_id"] for r in rows
                        if r["provider"] == "pluggy" and r["provider_item_id"]
                        and str(r["status"] or "").upper() != "PAUSED"
                    })

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


def delete_user_data(user_id: int) -> dict:
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

            if _table_exists(cur, "auth_rate_limits") and emails:
                identifiers = [f"email:{email.strip().lower()}" for email in emails]
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

            for table in (
                "open_finance_connections",
                "credit_transactions",
            ):
                if _table_exists(cur, table):
                    cur.execute(f"delete from {table} where user_id = %s", (user_id,))

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

            for table in user_owned_tables:
                if _table_exists(cur, table) and _column_exists(cur, table, "user_id"):
                    cur.execute(f"delete from {table} where user_id = %s", (user_id,))

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
    return {"user_id": user_id, "deleted": bool(deleted), "email": primary_email}


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
        try:
            results.append(delete_user_data(user_id))
        except Exception as exc:
            _restore_account_deletion_schedule(user_id)
            results.append({"user_id": user_id, "deleted": False, "error": str(exc)})
    return results
