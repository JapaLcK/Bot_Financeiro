"""Recomeçar do zero — reset_user_data (db/privacy.py) + POST /settings/reset.

O reset apaga dados financeiros e de uso e PRESERVA a conta (login, plano,
segurança, vínculos WhatsApp/Discord, opt-outs — decisões do dono). Onboarding
reabre. Tudo numa transação só.

CONTROLE NEGATIVO do grupo (§3 do CLAUDE.md): com o corpo de reset_user_data
trocado por no-op (deletes removidos, mantendo a verificação de senha),
`test_reset_apaga_tudo_do_usuario_e_isola_o_vizinho` fica vermelho — injetado
num caso verde, verificado à mão nesta sessão (ver relato do PR).
POSITIVO: os testes de preservação/isolamento provam que o caminho legítimo
(dados do vizinho, conta e login) continua de pé — um reset que apagasse tudo
de todos também seria pego.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import db.privacy as privacy
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from db.connection import get_conn
from db.privacy import reset_user_data

SENHA = "senha-certa-123"
SEGREDO_WEBHOOK = "test-webhook-secret"


def _item_de(uid: int) -> str:
    return f"item-reset-{uid}"


def _semeia(uid: int) -> None:
    """Uma linha em cada tabela que o reset apaga + as que ele preserva.

    plan_trials fica de fora (FK set null: a linha sobreviveria ao cleanup do
    teste) — o teste de preservação semeia e limpa por conta própria.
    """
    from db.users import _hash_password

    hoje = date.today()
    email = f"reset-{uid}@t.local"
    item = _item_de(uid)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # ── conta e segurança (preservadas) ─────────────────────────────
            cur.execute(
                """
                insert into auth_accounts
                  (user_id, email, password_hash, plan, stripe_customer_id,
                   ai_messages_this_month, tip_email_opt_out, insight_email_opt_out,
                   engagement_opt_out, whatsapp_updates_opt_out,
                   onboarding_step, onboarding_completed_at)
                values (%s, %s, %s, 'pro', %s, 7, true, true, true, true, 5, now())
                """,
                (uid, email, _hash_password(SENHA), f"cus_test_{uid}"),
            )
            cur.execute(
                "insert into user_identities (provider, external_id, user_id) values ('whatsapp', %s, %s)",
                (f"ext-{uid}", uid),
            )
            cur.execute(
                "insert into user_mfa (user_id, secret_encrypted, enabled) values (%s, 'enc', true)",
                (uid,),
            )
            cur.execute(
                "insert into user_mfa_backup_codes (user_id, code_hash) values (%s, 'hash')",
                (uid,),
            )
            cur.execute(
                "insert into auth_sessions (jti, user_id) values (%s, %s)",
                (f"jti-{uid}", uid),
            )
            cur.execute(
                "insert into push_tokens (user_id, token) values (%s, %s)",
                (uid, f"tok-{uid}"),
            )
            cur.execute(
                "insert into open_finance_item_registry (user_id, provider_item_id, origin) "
                "values (%s, %s, 'connect_token')",
                (uid, item),
            )
            cur.execute(
                "insert into audit_events (user_id, event) values (%s, 'open_finance_connected')",
                (uid,),
            )
            cur.execute(
                "update users set reminders_enabled = true, reminders_days_before = 5 where id = %s",
                (uid,),
            )

            # ── Open Finance (apagado) ──────────────────────────────────────
            cur.execute(
                """
                insert into open_finance_connections
                  (user_id, provider, provider_item_id, status, institution_id, institution_name)
                values (%s, 'pluggy', %s, 'UPDATED', '612', 'Nubank') returning id
                """,
                (uid, item),
            )
            con = cur.fetchone()["id"]
            cur.execute(
                "insert into open_finance_accounts (connection_id, provider_account_id, name, type) "
                "values (%s, 'acc-1', 'Conta', 'BANK') returning id",
                (con,),
            )
            acc = cur.fetchone()["id"]
            cur.execute(
                "insert into open_finance_transactions "
                "(account_id, provider_transaction_id, description, amount, transaction_date) "
                "values (%s, 'tx-1', 'compra', 10, %s)",
                (acc, hoje),
            )
            cur.execute(
                "insert into open_finance_investments (connection_id, provider_investment_id, name) "
                "values (%s, 'inv-1', 'Caixinha')",
                (con,),
            )

            # ── crédito (apagado) ───────────────────────────────────────────
            cur.execute(
                "insert into credit_cards (user_id, name, closing_day, due_day) "
                "values (%s, 'Cartão', 1, 10) returning id",
                (uid,),
            )
            card = cur.fetchone()["id"]
            cur.execute("update users set default_card_id = %s where id = %s", (card, uid))
            cur.execute(
                "insert into credit_bills (user_id, card_id, period_start, period_end) "
                "values (%s, %s, %s, %s) returning id",
                (uid, card, hoje, hoje),
            )
            bill = cur.fetchone()["id"]
            cur.execute(
                "insert into credit_transactions (bill_id, user_id, card_id, valor, purchased_at) "
                "values (%s, %s, %s, 25, %s)",
                (bill, uid, card, hoje),
            )

            # ── recorrentes (apagados) ──────────────────────────────────────
            cur.execute(
                "insert into recurring_incomes (user_id, name, amount, category, pay_day) "
                "values (%s, 'Salário', 10, 'renda', 5) returning id",
                (uid,),
            )
            inc = cur.fetchone()["id"]
            cur.execute(
                "insert into recurring_income_credits (income_id, user_id, amount, ym) "
                "values (%s, %s, 10, '2026-08')",
                (inc, uid),
            )
            cur.execute(
                "insert into recurring_expenses (user_id, name, amount, category, due_day, payment_type) "
                "values (%s, 'Luz', 10, 'contas', 5, 'account') returning id",
                (uid,),
            )
            exp = cur.fetchone()["id"]
            cur.execute(
                "insert into recurring_charges (recurring_id, user_id, amount, ym) "
                "values (%s, %s, 10, '2026-08')",
                (exp, uid),
            )
            cur.execute(
                "insert into bill_instances (recurring_id, user_id, due_date, amount) "
                "values (%s, %s, %s, 10)",
                (exp, uid, hoje),
            )
            cur.execute(
                "insert into recurring_suggestion_dismissed (user_id, merchant_key, amount) "
                "values (%s, 'merc', 10)",
                (uid,),
            )

            # ── investimentos / caixinhas / orçamentos (apagados) ───────────
            cur.execute(
                "insert into investments (user_id, name, rate, period, last_date) "
                "values (%s, 'CDB', 0.1, 'monthly', %s) returning id",
                (uid, hoje),
            )
            inv = cur.fetchone()["id"]
            cur.execute(
                "insert into investment_lots "
                "(user_id, investment_id, principal_initial, principal_remaining, balance, opened_at, last_date) "
                "values (%s, %s, 10, 10, 10, %s, %s)",
                (uid, inv, hoje, hoje),
            )
            cur.execute(
                "insert into pockets (user_id, name) values (%s, 'Meta') returning id",
                (uid,),
            )
            poc = cur.fetchone()["id"]
            cur.execute(
                "insert into pocket_lots "
                "(user_id, pocket_id, principal_initial, principal_remaining, balance, opened_at, last_date) "
                "values (%s, %s, 5, 5, 5, %s, %s)",
                (uid, poc, hoje, hoje),
            )
            cur.execute(
                "insert into category_budgets (user_id, categoria, budget) values (%s, 'mercado', 100)",
                (uid,),
            )
            cur.execute(
                "insert into budget_alert_sent (user_id, categoria, ym, threshold) "
                "values (%s, 'mercado', '2026-08', 80)",
                (uid,),
            )

            # ── categorias e agentes (apagados) ─────────────────────────────
            cur.execute("insert into user_categories (user_id, name) values (%s, 'Pets')", (uid,))
            cur.execute(
                "insert into user_category_rules (user_id, keyword, category) values (%s, 'racao', 'Pets')",
                (uid,),
            )
            cur.execute(
                "insert into agents (user_id, kind) values (%s, 'guardiao') returning id", (uid,)
            )
            ag = cur.fetchone()["id"]
            cur.execute(
                "insert into agent_events (agent_id, user_id, kind, dedupe_key) "
                "values (%s, %s, 'guardiao', %s)",
                (ag, uid, f"dk-{uid}"),
            )

            # ── IA / conversa (apagados) ────────────────────────────────────
            cur.execute(
                "insert into ai_messages (user_id, role, content) values (%s, 'user', 'oi')", (uid,)
            )
            cur.execute(
                "insert into ai_pending_actions (user_id, tool_name, tool_args, summary) "
                "values (%s, 't', %s, 's')",
                (uid, Jsonb({})),
            )
            cur.execute(
                "insert into ai_fallback_log (user_id, question) values (%s, 'q')", (uid,)
            )
            cur.execute(
                "insert into ai_proactive_cache (user_id, kind, payload) values (%s, 'k', %s)",
                (uid, Jsonb({})),
            )
            cur.execute(
                "insert into pending_actions (user_id, action_type, payload, expires_at) "
                "values (%s, 'confirm', %s, now() + interval '1 hour')",
                (uid, Jsonb({})),
            )

            # ── uso e lançamentos (apagados) ────────────────────────────────
            cur.execute(
                "insert into financial_spaces (user_id, name) values (%s, 'Casa') returning id",
                (uid,),
            )
            space = cur.fetchone()["id"]
            cur.execute(
                "insert into launches (user_id, tipo, valor, space_id) values (%s, 'gasto', 50, %s)",
                (uid, space),
            )
            # ensure_user (fixture) já criou a linha de accounts — só o saldo
            cur.execute("update accounts set balance = 100 where user_id = %s", (uid,))
            cur.execute(
                "insert into ofx_imports (user_id, file_hash, total_transactions) values (%s, %s, 1)",
                (uid, uuid.uuid4().hex),
            )
            cur.execute("insert into daily_report_prefs (user_id) values (%s)", (uid,))
        conn.commit()


# Contagens por tabela do que o reset APAGA. As três tabelas de gatilho de
# categoria (user_category_triggers etc.) não existem no schema — o reset as
# guarda com _table_exists, e aqui elas ficam de fora pelo mesmo motivo.
_OF_JOINS = {
    "open_finance_accounts": (
        "select count(*) as n from open_finance_accounts a "
        "join open_finance_connections c on c.id = a.connection_id where c.user_id = %s"
    ),
    "open_finance_transactions": (
        "select count(*) as n from open_finance_transactions t "
        "join open_finance_accounts a on a.id = t.account_id "
        "join open_finance_connections c on c.id = a.connection_id where c.user_id = %s"
    ),
    "open_finance_investments": (
        "select count(*) as n from open_finance_investments i "
        "join open_finance_connections c on c.id = i.connection_id where c.user_id = %s"
    ),
}
_TABELAS_SIMPLES = (
    "open_finance_connections", "credit_transactions", "credit_bills", "credit_cards",
    "recurring_income_credits", "bill_instances", "recurring_charges",
    "recurring_expenses", "recurring_incomes", "recurring_suggestion_dismissed",
    "investment_lots", "investments", "pocket_lots", "pockets",
    "budget_alert_sent", "category_budgets",
    "user_category_rules", "user_categories", "agent_events", "agents",
    "ai_messages", "ai_pending_actions", "ai_fallback_log", "ai_proactive_cache",
    "pending_actions",
    "ofx_imports", "daily_report_prefs", "launches", "financial_spaces",
    # `accounts` NÃO entra: desde o conserto do #246 a linha SOBREVIVE ao reset
    # com balance = 0 (é o lock dela que serializa o reset contra um lançamento
    # concorrente). A garantia real virou `balance == 0`, medida em _saldo().
)


def _saldo(uid: int):
    """Saldo da conta, ou None se a linha não existir."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id = %s", (uid,))
            row = cur.fetchone()
        conn.commit()
    return None if row is None else Decimal(str(row["balance"]))


def _linhas_accounts(uid: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from accounts where user_id = %s", (uid,))
            n = cur.fetchone()["n"]
        conn.commit()
    return n


def _soma_delta_conta(uid: int) -> Decimal:
    """Soma de efeitos->>'delta_conta' dos lançamentos SOBREVIVENTES."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select coalesce(sum((efeitos->>'delta_conta')::numeric), 0) as s "
                "from launches where user_id = %s and efeitos ? 'delta_conta'",
                (uid,),
            )
            s = cur.fetchone()["s"]
        conn.commit()
    return Decimal(str(s))


def _contagens(uid: int) -> dict[str, int]:
    out: dict[str, int] = {}
    with get_conn() as conn:
        with conn.cursor() as cur:
            for tabela, sql in _OF_JOINS.items():
                cur.execute(sql, (uid,))
                out[tabela] = cur.fetchone()["n"]
            for tabela in _TABELAS_SIMPLES:
                cur.execute(f"select count(*) as n from {tabela} where user_id = %s", (uid,))
                out[tabela] = cur.fetchone()["n"]
        conn.commit()
    return out


@pytest.fixture(autouse=True)
def _zera_rate_limit():
    """O arquivo soma 6+ POSTs em /settings/reset (limite 5/minute, mesmo IP
    do TestClient) — sem zerar o storage do limiter entre testes, o 6º levaria
    429 no lugar do status que o teste mede."""
    from frontend.routes import shared as routes_shared

    routes_shared.limiter.reset()
    yield


def _auth(client: TestClient, uid: int, email: str = "reset@t.com") -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token, "Content-Type": "application/json"}


# ── 1. apaga e isola ─────────────────────────────────────────────────────────

def test_reset_apaga_tudo_do_usuario_e_isola_o_vizinho(user_id):
    vizinho = user_id + 1
    db.ensure_user(vizinho)
    _semeia(user_id)
    _semeia(vizinho)

    antes_a = _contagens(user_id)
    antes_b = _contagens(vizinho)
    assert all(n >= 1 for n in antes_a.values()), f"semeadura incompleta: {antes_a}"

    reset_user_data(user_id, SENHA)

    depois_a = _contagens(user_id)
    assert all(n == 0 for n in depois_a.values()), \
        f"sobrou linha após o reset: { {t: n for t, n in depois_a.items() if n} }"
    # accounts sobrevive — mas ZERADA (a garantia real; ver _TABELAS_SIMPLES).
    assert _linhas_accounts(user_id) == 1, "a linha de accounts tinha que sobreviver"
    assert _saldo(user_id) == 0, "a linha sobreviveu, mas o saldo não zerou"
    assert _contagens(vizinho) == antes_b, "o reset de A tocou dados de B"
    assert _saldo(vizinho) == 100, "o reset de A zerou o saldo de B"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select default_card_id, reminders_enabled, reminders_days_before "
                "from users where id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        conn.commit()
    assert row["default_card_id"] is None
    assert row["reminders_enabled"] is False
    assert row["reminders_days_before"] == 3


# ── 2. preserva conta, segurança e vínculos ──────────────────────────────────

def test_reset_preserva_conta_login_seguranca_e_vinculos(user_id):
    _semeia(user_id)
    phone_hash = f"ph-reset-{uuid.uuid4().hex}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into plan_trials (phone_hash, user_id, started_at, model_version) "
                "values (%s, %s, now(), 2)",
                (phone_hash, user_id),
            )
        conn.commit()

    preservadas = {
        "user_identities": "select count(*) as n from user_identities where user_id = %s",
        "user_mfa": "select count(*) as n from user_mfa where user_id = %s",
        "user_mfa_backup_codes": "select count(*) as n from user_mfa_backup_codes where user_id = %s",
        "auth_sessions": "select count(*) as n from auth_sessions where user_id = %s",
        "push_tokens": "select count(*) as n from push_tokens where user_id = %s",
        "open_finance_item_registry": "select count(*) as n from open_finance_item_registry where user_id = %s",
        "audit_events": "select count(*) as n from audit_events where user_id = %s",
        "plan_trials": "select count(*) as n from plan_trials where user_id = %s",
    }
    conta_sql = (
        "select email, password_hash, plan, stripe_customer_id, ai_messages_this_month, "
        "tip_email_opt_out, insight_email_opt_out, engagement_opt_out, whatsapp_updates_opt_out "
        "from auth_accounts where user_id = %s"
    )

    def _estado():
        with get_conn() as conn:
            with conn.cursor() as cur:
                contagens = {}
                for nome, sql in preservadas.items():
                    cur.execute(sql, (user_id,))
                    contagens[nome] = cur.fetchone()["n"]
                cur.execute(conta_sql, (user_id,))
                conta = dict(cur.fetchone())
            conn.commit()
        return contagens, conta

    try:
        contagens_antes, conta_antes = _estado()
        assert all(n >= 1 for n in contagens_antes.values()), contagens_antes

        reset_user_data(user_id, SENHA)

        contagens_depois, conta_depois = _estado()
        assert contagens_depois == contagens_antes, "o reset apagou algo que devia preservar"
        assert conta_depois == conta_antes, "o reset mexeu em coluna da conta que devia preservar"
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from plan_trials where phone_hash = %s", (phone_hash,))
            conn.commit()


# ── 3. onboarding reabre ─────────────────────────────────────────────────────

def test_reset_reabre_o_onboarding(user_id):
    _semeia(user_id)
    assert db.needs_onboarding(user_id) is False

    reset_user_data(user_id, SENHA)

    assert db.needs_onboarding(user_id) is True
    assert db.get_onboarding_state(user_id) == {"step": 0, "completed": False}


# ── 4. tudo-ou-nada ──────────────────────────────────────────────────────────

def test_falha_no_meio_da_transacao_nao_muda_nada(user_id, monkeypatch):
    _semeia(user_id)
    antes = _contagens(user_id)

    original = privacy._table_exists

    # `accounts` saiu de _RESET_TABLES (#246); a última do laço vem da própria
    # lista (§0.7) — hardcodar aqui daria DID NOT RAISE em silêncio no dia em
    # que alguém acrescentasse uma tabela no fim.
    ultima = privacy._RESET_TABLES[-1]

    def _explode_no_fim(cur, table):
        if table == ultima:  # última do laço — meio da transação
            raise RuntimeError("falha injetada")
        return original(cur, table)

    monkeypatch.setattr(privacy, "_table_exists", _explode_no_fim)
    with pytest.raises(RuntimeError, match="falha injetada"):
        reset_user_data(user_id, SENHA)
    monkeypatch.setattr(privacy, "_table_exists", original)

    assert _contagens(user_id) == antes, "transação abortada não podia ter apagado nada"
    assert db.needs_onboarding(user_id) is False, "onboarding não podia ter sido reaberto"


# ── 4b. corrida: lançamento chegando DURANTE o reset (issue #246) ────────────
#
# Sem mock de `db`: duas conexões de verdade. A thread abre a própria e chama
# `add_launch_and_update_balance`, exatamente como o WhatsApp faria.

def _reset_com_lancamento_concorrente(uid: int, monkeypatch, gatilho: str | None = None) -> None:
    """`reset_user_data` com um lançamento real de R$250 entrando no meio.

    O gatilho é a ÚLTIMA tabela do laço (`_RESET_TABLES[-1]`), portanto DEPOIS
    do `delete from launches`: é onde o vermelho sem o conserto é o do DINHEIRO
    (R$250 criados ao apagar o sobrevivente). Disparada no começo do laço a
    corrida também fica vermelha, mas na premissa ("tinha que deixar 1
    lançamento: []") — o reset apaga o recém-chegado e o teste não mede a
    invariante que interessa.

    VIZINHO (isolamento, CLAUDE.md §0): um segundo usuário, semeado igual,
    lança os MESMOS R$250 na MESMA janela. O lock é da linha de A, então B não
    espera por ele e fecha em 100-250 = -150, com o resto dos dados intacto.
    Sem o `and user_id = %s` do `update accounts` o reset trava e zera TODAS as
    linhas: B lê 0 e fecha em -250. É vermelho que o caso sereno
    (`test_reset_apaga_tudo_do_usuario_e_isola_o_vizinho`) não produz — lá B
    não escreve durante a janela.
    """
    original = privacy._table_exists
    gatilho = gatilho or privacy._RESET_TABLES[-1]
    erro: list[BaseException] = []
    disparou: list[bool] = []

    vizinho = uid + 1
    db.ensure_user(vizinho)
    _semeia(vizinho)
    antes_vizinho = _contagens(vizinho)

    def _lanca(quem: int):
        try:
            db.add_launch_and_update_balance(quem, "despesa", 250.0, None, "corrida", "mercado")
        except BaseException as e:  # noqa: BLE001 - a thread não pode engolir
            erro.append(e)

    t = threading.Thread(target=_lanca, args=(uid,))
    tv = threading.Thread(target=_lanca, args=(vizinho,))

    def _dispara(cur, table):
        if table == gatilho and not disparou:
            disparou.append(True)
            t.start()
            tv.start()
            # COM o conserto a thread bloqueia no `update accounts` (o reset
            # segura o lock da linha) e o join estoura o teto — é o esperado.
            # SEM o conserto ela termina em milissegundos.
            t.join(timeout=2.0)
        return original(cur, table)

    monkeypatch.setattr(privacy, "_table_exists", _dispara)
    reset_user_data(uid, SENHA)
    monkeypatch.setattr(privacy, "_table_exists", original)

    t.join(timeout=10.0)
    tv.join(timeout=10.0)
    assert disparou, "o gatilho da corrida não disparou"
    assert not t.is_alive(), "o lançamento concorrente não destravou após o commit"
    assert not tv.is_alive(), "o lançamento do vizinho não destravou após o commit"
    assert not erro, f"o lançamento concorrente falhou: {erro}"
    assert _contagens(vizinho) == dict(antes_vizinho, launches=antes_vizinho["launches"] + 1), \
        "o reset de A mexeu nas linhas de B durante a corrida"
    assert _saldo(vizinho) == Decimal("-150"), \
        f"o vizinho fechou em {_saldo(vizinho)}, não em 100-250 — leu saldo zerado por A"


def _ids_de_launches(uid: int) -> list[int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from launches where user_id = %s order by id", (uid,))
            ids = [r["id"] for r in cur.fetchall()]
        conn.commit()
    return ids


def test_lancamento_da_janela_nao_vira_dinheiro_ao_ser_apagado(user_id, monkeypatch):
    """NEGATIVO do grupo: com o reset da `main` isto fecha em R$250 do nada."""
    _semeia(user_id)
    _reset_com_lancamento_concorrente(user_id, monkeypatch)

    db.ensure_user(user_id)  # o que qualquer porta faz antes de mexer no saldo
    sobreviventes = _ids_de_launches(user_id)
    assert len(sobreviventes) == 1, f"a corrida tinha que deixar 1 lançamento: {sobreviventes}"

    db.delete_launch_and_rollback(user_id, sobreviventes[0])
    assert _saldo(user_id) == 0, "apagar o lançamento da janela criou dinheiro"


def test_reset_deixa_saldo_em_sincronia_com_os_lancamentos_sobreviventes(user_id, monkeypatch):
    """A CAUSA, não o sintoma: saldo == soma dos delta_conta que sobraram.

    Decisão do dono: o lançamento da janela sobrevive COM o dinheiro dele, e o
    saldo fica negativo logo após o reset. É o correto.
    """
    _semeia(user_id)
    _reset_com_lancamento_concorrente(user_id, monkeypatch)

    assert _linhas_accounts(user_id) == 1, "a linha de accounts tinha que sobreviver ao reset"
    assert _soma_delta_conta(user_id) == Decimal("-250"), "o lançamento da janela tinha que sobreviver"
    assert _saldo(user_id) == _soma_delta_conta(user_id), \
        f"saldo {_saldo(user_id)} != soma dos sobreviventes {_soma_delta_conta(user_id)}"


def test_reset_sem_corrida_zera_a_conta_e_o_caminho_normal_continua(user_id):
    """POSITIVO do grupo: sem ele, um reset que só travasse passaria nos dois
    testes de corrida acima."""
    _semeia(user_id)

    reset_user_data(user_id, SENHA)

    assert _linhas_accounts(user_id) == 1
    assert _saldo(user_id) == 0
    assert _ids_de_launches(user_id) == []

    lid, _seq, bal = db.add_launch_and_update_balance(
        user_id, "despesa", 40.0, None, "pos-reset", "mercado")
    assert bal == -40
    db.delete_launch_and_rollback(user_id, lid)
    assert _saldo(user_id) == 0
    assert _ids_de_launches(user_id) == []


def test_reset_de_conta_sem_linha_de_accounts_nao_deixa_divida_fantasma(user_id, monkeypatch):
    """NEGATIVO do `ensure_user_tx`: sem ele o `update` casa 0 e não trava NADA.

    Estado real e alcançável: `merge_users` apaga accounts do usuário de origem
    (db/users.py:88) e migra auth_accounts sem recriar a linha (:155-162) — a
    conta fica com login válido e ZERO linhas em accounts. Todo reset rodado na
    versão anterior deixava a conta assim também.

    O gatilho aqui é a PRIMEIRA tabela do laço, não a última: é a posição
    discriminante deste caso. Sem `ensure_user_tx` o lançamento entra livre (não
    há linha para travar) e o `delete from launches`, que vem DEPOIS, o apaga —
    sobra saldo -250 com 0 lançamentos, dívida que não existe. Só o gatilho na
    ÚLTIMA posição escapa (o lançamento chega depois do `delete from launches` e
    sobrevive) — e é justamente o que os outros dois testes de corrida usam, por
    isso eles não pegam este bug.
    """
    _semeia(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from accounts where user_id = %s", (user_id,))
        conn.commit()
    assert _linhas_accounts(user_id) == 0, "a pré-condição do teste não montou"

    _reset_com_lancamento_concorrente(user_id, monkeypatch, gatilho=privacy._RESET_TABLES[0])

    assert _linhas_accounts(user_id) == 1, "o reset tinha que ter recriado a linha"
    assert _soma_delta_conta(user_id) == Decimal("-250"), \
        f"o lançamento da janela sumiu: {_ids_de_launches(user_id)}"
    assert _saldo(user_id) == _soma_delta_conta(user_id), \
        f"saldo {_saldo(user_id)} != soma dos sobreviventes {_soma_delta_conta(user_id)}"


# ── 5. senha ─────────────────────────────────────────────────────────────────

def test_senha_errada_recusa_e_nada_muda(user_id):
    _semeia(user_id)
    antes = _contagens(user_id)

    with pytest.raises(PermissionError):
        reset_user_data(user_id, "senha-errada")
    assert _contagens(user_id) == antes
    assert db.needs_onboarding(user_id) is False

    reset_user_data(user_id, SENHA)  # a certa reseta
    assert all(n == 0 for n in _contagens(user_id).values())


# ── 6. conta agendada para exclusão ──────────────────────────────────────────

def test_rota_recusa_conta_agendada_para_exclusao(user_id):
    _semeia(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set deletion_status = 'scheduled', "
                "deletion_requested_at = now(), "
                "deletion_scheduled_for = now() + interval '7 days' "
                "where user_id = %s",
                (user_id,),
            )
        conn.commit()
    antes = _contagens(user_id)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.post("/settings/reset", json={"password": SENHA}, headers=headers)

    assert resp.status_code == 403, resp.text
    assert _contagens(user_id) == antes


# ── 7. limpeza remota na Pluggy ──────────────────────────────────────────────

def test_rota_deleta_os_items_do_usuario_na_pluggy(user_id, monkeypatch):
    from frontend.routes import shared as routes_shared

    _semeia(user_id)
    deletados: list[str] = []
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item",
        lambda item_id, api_key=None: deletados.append(item_id),
    )
    # Cache do "mês corrente" (TTL 45s) com dado pré-reset: o reset tem de
    # derrubá-lo, senão outra aba segue vendo pockets/cartões/OF apagados.
    routes_shared.dashboard_current_cache[user_id] = (0.0, {"pre": "reset"}, None, None)

    # Dashboards noutro dispositivo só sabem do reset pelo WS: a rota tem de
    # emitir o broadcast por usuário (reuso do open_finance_synced).
    avisados: list[int] = []

    async def _broadcast(uid, payload):
        avisados.append(int(uid))
        return 0

    monkeypatch.setattr(dashboard.manager, "broadcast_to_user", _broadcast)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.post("/settings/reset", json={"password": SENHA}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert deletados == [_item_de(user_id)], "a limpeza remota não recebeu os items do usuário"
    assert all(n == 0 for n in _contagens(user_id).values())
    assert user_id not in routes_shared.dashboard_current_cache, \
        "o snapshot cacheado do dashboard sobreviveu ao reset (Codex PR #217, rodada 2)"
    assert avisados == [user_id], \
        "o reset tinha que avisar os dashboards conectados via broadcast_to_user"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from audit_events where user_id = %s and event = 'account_reset'",
                (user_id,),
            )
            n = cur.fetchone()["n"]
        conn.commit()
    assert n == 1, "sucesso do reset tinha que registrar o audit event account_reset"


def test_falha_da_pluggy_nao_impede_o_reset_local(user_id, monkeypatch):
    _semeia(user_id)

    def _pluggy_fora(uid):
        raise RuntimeError("pluggy fora do ar")

    monkeypatch.setattr(of_routes, "delete_pluggy_items_best_effort", _pluggy_fora)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.post("/settings/reset", json={"password": SENHA}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert all(n == 0 for n in _contagens(user_id).values()), \
        "falha remota (best-effort) não podia impedir o reset local"


# ── 7b. contrato do lock: ocupado → nada local E nada remoto ────────────────
# CONTROLE NEGATIVO destes dois: com o aborto por lock trocado por no-op
# (`raise ResetLockUnavailableError` → pular o item), os dois ficam vermelhos —
# verificado por mutação nesta sessão (ver relato do PR).

def test_lock_ocupado_aborta_sem_tocar_a_pluggy(user_id, monkeypatch):
    from db.open_finance_state import pluggy_item_lock

    _semeia(user_id)
    antes = _contagens(user_id)
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "100")  # lido a cada chamada

    tocada: list[str] = []
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item",
        lambda item_id, api_key=None: tocada.append(item_id),
    )

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    with pluggy_item_lock(_item_de(user_id)) as segurei:
        assert segurei, "pré-condição: o teste precisa estar segurando o lock"
        resp = client.post("/settings/reset", json={"password": SENHA}, headers=headers)

    assert resp.status_code == 503, resp.text
    assert tocada == [], "com o lock ocupado a Pluggy NÃO podia ter sido tocada"
    assert _contagens(user_id) == antes, "com o lock ocupado nada local podia sumir"


def test_aborto_por_lock_libera_os_locks_ja_adquiridos(user_id, monkeypatch):
    from db.open_finance_state import pluggy_item_lock
    from db.privacy import ResetLockUnavailableError

    _semeia(user_id)
    # Segundo item do mesmo usuário: o reset precisa adquirir os DOIS locks.
    item2 = f"{_item_de(user_id)}-b"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into open_finance_connections "
                "(user_id, provider, provider_item_id, status, institution_id, institution_name) "
                "values (%s, 'pluggy', %s, 'UPDATED', '613', 'Inter')",
                (user_id, item2),
            )
        conn.commit()
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "100")

    with pluggy_item_lock(item2) as segurei:
        assert segurei
        with pytest.raises(ResetLockUnavailableError):
            reset_user_data(user_id, SENHA)

    # Lock solto: a retentativa entra e completa. Se o aborto tivesse vazado o
    # lock do primeiro item (ExitStack não fechado), esta chamada estouraria o
    # teto de 100ms nele e levantaria de novo.
    reset_user_data(user_id, SENHA)
    assert all(n == 0 for n in _contagens(user_id).values())


def test_reset_com_mais_items_que_o_teto_de_slots(user_id, monkeypatch):
    """Codex PR #217 (P2): usuário com mais itens Pluggy que OF_SYNC_LOCK_MAX_CONN.

    Com um `pluggy_item_lock` por item, o reset retinha um slot do semáforo por
    lock e o (teto+1)-ésimo esperava um slot que o PRÓPRIO reset segurava —
    False sempre, 503 em toda retentativa, conta impossível de resetar.
    Com os N locks numa única sessão (`pluggy_items_lock`), 1 slot basta.
    CONTROLE NEGATIVO: no código antigo (ExitStack de locks singulares) este
    teste fica vermelho — verificado por mutação nesta sessão.
    """
    from db import open_finance_state

    _semeia(user_id)  # 1º item
    with get_conn() as conn:
        with conn.cursor() as cur:
            for n in (2, 3):
                cur.execute(
                    "insert into open_finance_connections "
                    "(user_id, provider, provider_item_id, status, institution_id, institution_name) "
                    "values (%s, 'pluggy', %s, 'UPDATED', %s, 'Banco N')",
                    (user_id, f"{_item_de(user_id)}-{n}", str(600 + n)),
                )
        conn.commit()

    monkeypatch.setenv("OF_SYNC_LOCK_MAX_CONN", "2")   # teto < nº de itens (3)
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "100")  # o auto-bloqueio viraria 100ms, não 15s
    open_finance_state._lock_slots.cache_clear()       # o teto é lru_cache — relê o env
    try:
        reset_user_data(user_id, SENHA)
    finally:
        open_finance_state._lock_slots.cache_clear()   # próximos testes voltam ao default

    assert all(n == 0 for n in _contagens(user_id).values())


# ── 7c. reconexão que esperava o lock do reset não ressuscita a conexão ─────

def test_reconexao_esperando_o_lock_do_reset_nao_recria_a_conexao(user_id, monkeypatch):
    """Codex PR #217 (P2, 4º): /pluggy-item de um item JÁ conectado espera o
    lock que o reset segura e escreve DEPOIS do commit — recriando a conexão
    que o reset acabou de apagar (e agendando sync que repopula). A correção:
    sob o lock, se a conexão própria que existia na validação sumiu, um
    reset/disconnect interveio → 409, nada gravado, nada agendado."""
    from contextlib import contextmanager

    from db.open_finance_state import pluggy_item_lock as lock_real

    _semeia(user_id)
    item = _item_de(user_id)

    monkeypatch.setattr(
        of_routes, "get_pluggy_item",
        lambda item_id, api_key=None: {
            "id": item_id, "status": "UPDATED", "clientUserId": str(user_id),
            "connector": {"id": 612, "name": "Nubank"},
        },
    )
    agendados: list[str] = []
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda i: agendados.append(i))

    # Interleaving determinístico do cenário: a reconexão "espera" o lock do
    # reset = o reset completa inteiro antes de o lock ser adquirido.
    estado = {"resetou": False}

    @contextmanager
    def lock_apos_o_reset(item_id, *, budget_ms=None):
        if not estado["resetou"]:
            estado["resetou"] = True
            reset_user_data(user_id, SENHA)
        with lock_real(item_id, budget_ms=budget_ms) as got:
            yield got

    monkeypatch.setattr(of_routes, "pluggy_item_lock", lock_apos_o_reset)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.post(f"/open-finance/{user_id}/pluggy-item",
                       json={"item": {"id": item}}, headers=headers)

    assert resp.status_code == 409, resp.text
    assert db.get_connections_by_item_id(item) == [], \
        "a reconexão atrasada ressuscitou a conexão que o reset apagou"
    assert agendados == [], "sync agendado repopularia os dados apagados"


def test_sync_de_item_varrido_pelo_reset_nao_recria_nada(user_id):
    """Instância irmã (conexão de item NOVO na janela do reset): linha inserida
    ANTES do delete do reset é varrida pelo `where user_id`, e o sync inicial
    agendado aborta em connection_not_found ANTES de qualquer chamada à Pluggy
    (core/services/pluggy_sync.py) — nada é recriado."""
    from core.services.pluggy_sync import sync_pluggy_item

    _semeia(user_id)
    item = _item_de(user_id)
    reset_user_data(user_id, SENHA)

    resultado = sync_pluggy_item(item)

    assert resultado == {"ok": False, "reason": "connection_not_found", "item_id": item}
    assert _contagens(user_id)["open_finance_connections"] == 0


# ── 7c-bis. item salvo entre a enumeração remota e o DELETE local ───────────

def test_item_salvo_durante_a_janela_do_reset_e_deletado_na_pluggy(user_id, monkeypatch):
    """Codex PR #217 (P2, 11º): item Pluggy salvo DEPOIS de a limpeza remota
    enumerar os items (T1) e ANTES do DELETE local (T2) era varrido do banco
    sem nunca ser deletado na Pluggy — órfão que bloqueia reconexão ("já
    possui conexão com este acesso", frontend/routes/open_finance.py:919).
    O segundo passe compara o RETURNING do DELETE com a enumeração e deleta
    o que ela não viu. CONTROLE NEGATIVO: sem o segundo passe (código
    anterior), este teste fica vermelho (item novo nunca deletado)."""
    _semeia(user_id)
    item_velho = _item_de(user_id)
    item_novo = f"{item_velho}-tardio"

    deletados: list[str] = []
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item",
        lambda item_id, api_key=None: deletados.append(item_id),
    )

    # Interleaving determinístico: o save do item novo acontece LOGO APÓS a
    # enumeração da limpeza remota (T1) — dentro da janela até o DELETE (T2).
    real_list = of_routes.list_pluggy_item_ids
    injetado = {"feito": False}

    def _lista_e_injeta(uid):
        itens = real_list(uid)
        if not injetado["feito"]:
            injetado["feito"] = True
            db.save_pluggy_open_finance_item(
                uid, {"id": item_novo, "status": "UPDATED",
                      "connector": {"id": 613, "name": "Inter"}})
        return itens

    monkeypatch.setattr(of_routes, "list_pluggy_item_ids", _lista_e_injeta)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.post("/settings/reset", json={"password": SENHA}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert all(n == 0 for n in _contagens(user_id).values()), "o DELETE local tinha que varrer o item novo"
    assert item_velho in deletados, "o item enumerado tinha que ser deletado no 1º passe"
    assert item_novo in deletados, \
        "item salvo na janela T1→T2 ficou órfão na Pluggy (bloqueia reconexão)"


# ── 7d. fill do cache em voo não republica dado pré-reset ───────────────────

def test_fill_em_voo_nao_republica_cache_pre_reset(user_id, monkeypatch):
    """Codex PR #217 (P2): um `_get_dashboard_current_state` que já perdeu o
    cache e está aguardando o banco quando a invalidação roda acordava e
    gravava o resultado pré-reset de volta (até 45s de TTL servindo dado
    apagado). Com a época: o fill captura antes dos gathers e só publica se
    ela não mudou. CONTROLE NEGATIVO: com a publicação incondicional (código
    anterior), este teste fica vermelho — verificado por mutação na sessão."""
    import asyncio
    import threading

    from frontend.routes import shared as routes_shared

    liberar = threading.Event()
    comecou = threading.Event()
    original = dashboard.accrue_all_pockets

    def _trava(uid):
        comecou.set()
        assert liberar.wait(timeout=10), "orquestração: ninguém liberou o fill"
        return original(uid)

    monkeypatch.setattr(dashboard, "accrue_all_pockets", _trava)
    routes_shared.invalidate_dashboard_current_cache(user_id)  # estado limpo

    async def _cenario():
        tarefa = asyncio.create_task(dashboard._get_dashboard_current_state(user_id))
        await asyncio.to_thread(comecou.wait, 5)
        # o reset completa enquanto o fill espera o banco
        routes_shared.invalidate_dashboard_current_cache(user_id)
        liberar.set()
        await tarefa

    asyncio.run(_cenario())
    assert user_id not in routes_shared.dashboard_current_cache, \
        "fill iniciado antes da invalidação republicou o resultado pré-reset"

    # Positivo: sem invalidação no meio, o fill publica normal (a época não
    # pode virar um cache que nunca enche).
    liberar.set()
    asyncio.run(dashboard._get_dashboard_current_state(user_id))
    assert user_id in routes_shared.dashboard_current_cache
    routes_shared.invalidate_dashboard_current_cache(user_id)


# ── 8. webhook pós-reset não ressuscita nada ─────────────────────────────────

def test_webhook_pos_reset_nao_recria_conexao(user_id, monkeypatch):
    _semeia(user_id)
    item = _item_de(user_id)
    reset_user_data(user_id, SENHA)

    eventos: list[str] = []

    async def _log(level, event_type, message, **kw):
        eventos.append(event_type)

    agendados: list[str] = []
    monkeypatch.setattr(of_routes, "log_system_event", _log)
    monkeypatch.setattr(of_routes, "_schedule_pluggy_sync", lambda i: agendados.append(i))
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SEGREDO_WEBHOOK)

    client = TestClient(dashboard.app)
    resp = client.post(
        f"/open-finance/pluggy/webhook?token={SEGREDO_WEBHOOK}",
        content=json.dumps({"event": "item/updated", "itemId": item}).encode(),
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 200, resp.text
    assert agendados == [], "item resetado não podia disparar sync"
    assert "of_webhook_item_unknown" in eventos, eventos
    contagens = _contagens(user_id)
    for tabela in ("open_finance_connections", "open_finance_accounts",
                   "open_finance_transactions", "open_finance_investments"):
        assert contagens[tabela] == 0, f"webhook recriou linha em {tabela}"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from open_finance_item_registry "
                "where provider_item_id = %s and origin = 'webhook'",
                (item,),
            )
            n = cur.fetchone()["n"]
            # linha de registry sem dono não cascateia com o cleanup do user
            cur.execute(
                "delete from open_finance_item_registry "
                "where provider_item_id = %s and origin = 'webhook'",
                (item,),
            )
        conn.commit()
    assert n == 1, "item de webhook desconhecido tinha que ir pro registry"


def test_deadlock_no_reset_vira_503_e_nao_apaga_nada(user_id, monkeypatch):
    """Deadlock DENTRO da transação do reset: 503 recuperável, não 500.

    O reset toma accounts na primeira escrita e pockets/investments no laço; um
    saque de caixinha faz o contrário, e o Postgres mata quem detectar primeiro
    — o reset NÃO é imune (ver comentário do `update accounts` em db/privacy.py).
    A exceção é levantada no 1º `_table_exists`, que roda DEPOIS do
    `update accounts`: o caminho real da rota e da transação é exercitado.

    NEGATIVO: sem o `except psycopg.OperationalError` da rota
    (frontend/routes/settings.py) a exceção sobe e o teste fica vermelho.
    Este caso sozinho NÃO mede a categoria — ele passaria também com o `except`
    capturando só a folha `DeadlockDetected`. Quem mede é o irmão abaixo.
    """
    _semeia(user_id)
    antes = _contagens(user_id)
    vizinho = user_id + 1
    db.ensure_user(vizinho)
    _semeia(vizinho)
    antes_vizinho = _contagens(vizinho)

    tocada: list[str] = []
    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item",
        lambda item_id, api_key=None: tocada.append(item_id),
    )

    def _deadlock(cur, table):
        raise psycopg.errors.DeadlockDetected("deadlock detected")

    monkeypatch.setattr(privacy, "_table_exists", _deadlock)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.post("/settings/reset", json={"password": SENHA}, headers=headers)

    assert resp.status_code == 503, resp.text
    assert "Tente de novo" in resp.json()["detail"], resp.text
    # Deadlock aborta a transação inteira: nada local sumiu.
    assert _contagens(user_id) == antes
    # TETO conhecido, e é por isso que ele está num assert: o `remote_cleanup`
    # roda ANTES dos deletes locais, então os items JÁ foram deletados na Pluggy
    # com o banco local intacto — a janela residual documentada em
    # db/privacy.reset_user_data, agora com um gatilho a mais.
    assert tocada == [_item_de(user_id)]
    # Isolamento sob abort (CLAUDE.md §0): a transação morta não pode ter
    # tocado B. É POSITIVO, não discrimina a mutação do `and user_id = %s` —
    # o rollback desfaz o zero global de qualquer jeito; pega conserto futuro
    # que escreva em accounts fora da transação.
    assert _contagens(vizinho) == antes_vizinho, "o reset abortado de A tocou dados de B"
    assert _saldo(vizinho) == 100, "o reset abortado de A mexeu no saldo de B"




def test_pool_timeout_no_reset_tambem_vira_503(user_id, monkeypatch):
    """A CATEGORIA, não a folha — é este caso que mede a ampliação do `except`.

    O irmão acima injeta `DeadlockDetected` e passaria mesmo com a rota
    capturando só aquela folha. Aqui a exceção é `psycopg_pool.PoolTimeout`,
    que NÃO é deadlock e é a que o próprio conserto deste PR tornou mais
    provável: durante a transação, todo `ensure_user` daquele usuário bloqueia
    SEGURANDO um dos 8 slots do pool sync (db/connection.py, DB_POOL_MAX_SYNC).

    As quatro folhas relevantes descendem de `psycopg.OperationalError`
    (medido): DeadlockDetected, PoolTimeout, QueryCanceled, LockNotAvailable.
    Todas abortam a transação inteira e todas são temporárias, então o desfecho
    certo é o mesmo 503 recuperável — e cair em 500 aqui deixaria os items da
    Pluggy já deletados remotamente com o banco local intacto.

    NEGATIVO: voltando a rota para `except psycopg.errors.DeadlockDetected`,
    este teste fica vermelho com 500, e o irmão acima continua VERDE — que é
    exatamente a demonstração de que a folha não bastava.
    """
    import psycopg_pool

    _semeia(user_id)
    antes = _contagens(user_id)

    monkeypatch.setattr(of_routes, "create_pluggy_api_key", lambda: "api-key")
    monkeypatch.setattr(
        of_routes, "delete_pluggy_item", lambda item_id, api_key=None: None,
    )

    def _pool_timeout(cur, table):
        raise psycopg_pool.PoolTimeout("couldn't get a connection after 30 sec")

    monkeypatch.setattr(privacy, "_table_exists", _pool_timeout)

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.post("/settings/reset", json={"password": SENHA}, headers=headers)

    assert resp.status_code == 503, resp.text
    assert "Tente de novo" in resp.json()["detail"], resp.text
    assert _contagens(user_id) == antes, "a transação abortada apagou dado"

# ── 9. sem sessão ────────────────────────────────────────────────────────────

def test_rota_sem_sessao_devolve_401_e_nada_muda(user_id):
    _semeia(user_id)
    antes = _contagens(user_id)

    client = TestClient(dashboard.app)
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    resp = client.post(
        "/settings/reset",
        json={"password": SENHA},
        headers={dashboard.CSRF_HEADER_NAME: token, "Content-Type": "application/json"},
    )

    assert resp.status_code == 401, resp.text
    assert _contagens(user_id) == antes
