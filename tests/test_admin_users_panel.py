"""Testes do painel de usuários do admin (/admin/api/users e /{user_id}).

Cobrem: exigência de auth, classificação de assinatura por conta
(_derive_account_status), agregados, filtros por status/plano, busca por
e-mail, paginação e o drill-down individual. O resumo Stripe roda com a
chave vazia (billing.available == False) — a API externa nunca é chamada.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard
from db import ensure_user, get_conn

NOW = datetime.now(timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def _admin_tables():
    """system_event_logs não vem do init_db — só do ensure_admin_tables()
    (startup do app). No CI o banco é fresco e o drill-down consulta essa
    tabela; mesmo precedente de test_security_alerts."""
    import asyncio

    asyncio.run(admin_dashboard.ensure_admin_tables())


@pytest.fixture(autouse=True)
def configured_admin(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop_log)
    # Stripe fora do ar nos testes: chave vazia + cache limpo entre testes.
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "")
    monkeypatch.setattr(
        admin_dashboard, "_billing_summary_cache", {"fetched_at": None, "data": None}
    )
    # /admin/auth/login tem rate limit de 10/min (slowapi, storage em memória
    # compartilhado entre testes). Cada _admin_client() loga; num arquivo com
    # muitos testes isso estoura o teto e derruba o último com 429. Zera o
    # storage por teste — não afrouxa o limite em produção.
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass


def _admin_client() -> TestClient:
    client = TestClient(dashboard.app, base_url="https://testserver")
    csrf = "test-admin-csrf"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, csrf)
    login = client.post(
        "/admin/auth/login",
        headers={dashboard.CSRF_HEADER_NAME: csrf},
        json={"username": "admin", "password": "secret-admin"},
    )
    assert login.status_code == 200
    return client


def _mk_account(email: str, *, plan: str = "free", pay: str = "inactive",
                stripe_customer: str | None = None, expires=None,
                source: str | None = None) -> int:
    uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts
                    (user_id, email, password_hash, plan, last_payment_status,
                     stripe_customer_id, plan_expires_at, signup_source)
                values (%s, %s, 'x', %s, %s, %s, %s, %s)
                """,
                (uid, email, plan, pay, stripe_customer, expires, source),
            )
        conn.commit()
    return uid


@pytest.fixture()
def panel_accounts():
    """Uma conta de cada categoria; e-mails com prefixo único pra busca."""
    tag = uuid.uuid4().hex[:8]
    uids = {
        "paying": _mk_account(f"panel-{tag}-paying@test.local", plan="pro",
                              pay="active", stripe_customer="cus_test1"),
        "trial": _mk_account(f"panel-{tag}-trial@test.local", plan="pro",
                             pay="trialing", stripe_customer="cus_test2"),
        "past_due": _mk_account(f"panel-{tag}-pastdue@test.local", plan="pro",
                                pay="past_due", stripe_customer="cus_test3"),
        # Estado real pós-webhook customer.subscription.deleted:
        # plan volta pra free E last_payment_status vira canceled
        "canceled": _mk_account(f"panel-{tag}-canceled@test.local", plan="free",
                                pay="canceled", stripe_customer="cus_test4"),
        "granted": _mk_account(f"panel-{tag}-granted@test.local", plan="pro"),
        # Cortesia vencida: sem Stripe, plan_expires_at no passado → canceled
        "granted_expired": _mk_account(f"panel-{tag}-grantexp@test.local", plan="pro",
                                       expires=NOW - timedelta(days=2)),
        "free": _mk_account(f"panel-{tag}-free@test.local"),
    }
    yield tag, uids
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from auth_accounts where user_id = any(%s)",
                (list(uids.values()),),
            )
        conn.commit()


# ── Classificação ──────────────────────────────────────────────────────────

class TestDeriveAccountStatus:
    def _st(self, **row):
        return admin_dashboard._derive_account_status(row, NOW)

    def test_free(self):
        assert self._st(plan="free") == "free"
        assert self._st(plan=None) == "free"

    def test_free_com_pagamento_cancelado_e_ex_assinante(self):
        # customer.subscription.deleted volta plan pra 'free' e grava
        # last_payment_status='canceled' — é ex-assinante, não free comum
        assert self._st(plan="free", last_payment_status="canceled") == "canceled"

    def test_cortesia_vencida_vira_canceled(self):
        assert self._st(plan="pro", last_payment_status="inactive",
                        stripe_customer_id=None,
                        plan_expires_at=NOW - timedelta(days=1)) == "canceled"

    def test_expiracao_vence_status_de_pagamento(self):
        # Webhook de renovação perdido: status diz active/trialing mas o plano
        # venceu — plan_service._paid_plan_active nega o entitlement, e o
        # painel tem de concordar
        for pay in ("active", "trialing", "past_due"):
            assert self._st(plan="pro", last_payment_status=pay,
                            stripe_customer_id="cus_x",
                            plan_expires_at=NOW - timedelta(hours=1)) == "canceled"
        # Vigente continua classificando pelo status normalmente
        assert self._st(plan="pro", last_payment_status="active",
                        stripe_customer_id="cus_x",
                        plan_expires_at=NOW + timedelta(days=10)) == "paying"

    def test_paying(self):
        assert self._st(plan="pro", last_payment_status="active",
                        stripe_customer_id="cus_x") == "paying"

    def test_trial(self):
        assert self._st(plan="pro", last_payment_status="trialing",
                        stripe_customer_id="cus_x") == "trial"

    def test_past_due_variants(self):
        for pay in ("past_due", "unpaid", "incomplete"):
            assert self._st(plan="pro", last_payment_status=pay,
                            stripe_customer_id="cus_x") == "past_due"

    def test_canceled(self):
        assert self._st(plan="pro", last_payment_status="canceled",
                        stripe_customer_id="cus_x") == "canceled"

    def test_granted_sem_stripe(self):
        # 'inactive' é o default NOT NULL da coluna — o estado real de um grant
        assert self._st(plan="pro", last_payment_status="inactive",
                        stripe_customer_id=None) == "granted"

    def test_legado_com_stripe_expirado_vira_canceled(self):
        assert self._st(plan="pro", last_payment_status="inactive",
                        stripe_customer_id="cus_x",
                        plan_expires_at=NOW - timedelta(days=1)) == "canceled"


# O CASE em SQL não importa constante Python: a regra vive nos dois lados e a
# única defesa é um teste que os compare (CLAUDE.md §0.7, mesmo precedente de
# tests/test_phosphor_subset.py). O regex casa os dois formatos usados no CASE,
# `= 'x'` e `IN ('x', 'y')`, e devolve a categoria do THEN.
_WHEN_STATUS_RX = re.compile(
    r"a\.last_payment_status.*?(?:=\s*'([a-z_]+)'|IN\s*\(([^)]*)\))\s*THEN\s*'([a-z_]+)'",
    re.S,
)


def _status_do_case_sql() -> dict:
    """Mapa {status cru -> categoria} lido do _ACCOUNT_STATUS_SQL."""
    sql = admin_dashboard._ACCOUNT_STATUS_SQL
    ramos = list(_WHEN_STATUS_RX.finditer(sql))
    # Guarda contra parse cego: cada ramo cita a coluna exatamente uma vez, então
    # ramo novo numa forma que o regex não casa faz ESTA linha ficar vermelha em
    # vez de o ramo sumir da comparação em silêncio.
    assert len(ramos) == sql.count("last_payment_status"), ramos
    mapa = {}
    for m in ramos:
        unico, lista, categoria = m.groups()
        for st in ([unico] if unico else re.findall(r"'([a-z_]+)'", lista)):
            assert mapa.setdefault(st, categoria) == categoria, st
    return mapa


def test_status_vivos_do_sql_batem_com_a_constante_python():
    """Divergir aqui é o bug real que aconteceu: o CASE tratava 'unpaid' e
    'incomplete' como Past due (assinatura viva) e o gate do /trial-reset não —
    passavam e a trava era apagada."""
    mapa = _status_do_case_sql()
    vivos_sql = {st for st, cat in mapa.items() if cat in ("trial", "paying", "past_due")}
    assert vivos_sql == set(admin_dashboard._LIVE_PAYMENT_STATUSES)
    assert {st for st, cat in mapa.items() if cat == "past_due"} == set(
        admin_dashboard._PAST_DUE_PAYMENT_STATUSES
    )
    # Terceiro lugar da mesma regra: a função Python que o SQL espelha.
    for st, cat in mapa.items():
        assert admin_dashboard._derive_account_status(
            {"plan": "pro", "last_payment_status": st}, NOW
        ) == cat, st


# ── Resumo Stripe (MRR / ticket médio) ─────────────────────────────────────

class _FakePrice:
    def __init__(self, unit_amount, interval, interval_count=1):
        self.unit_amount = unit_amount
        self.recurring = type("R", (), {"interval": interval,
                                        "interval_count": interval_count})()


class _FakeSub:
    def __init__(self, status, unit_amount, interval="month"):
        self.status = status
        item = type("I", (), {"price": _FakePrice(unit_amount, interval)})()
        self.items = type("Items", (), {"data": [item]})()


def test_stripe_billing_summary_normaliza_mrr(monkeypatch):
    import sys
    import types

    subs = [
        _FakeSub("active", 1990),            # R$ 19,90/mês
        _FakeSub("active", 1990),
        _FakeSub("active", 19900, "year"),   # R$ 199,00/ano → R$ 16,58/mês
        _FakeSub("trialing", 1990),
        _FakeSub("past_due", 1990),
        _FakeSub("canceled", 1990),
    ]

    class _FakeList:
        def auto_paging_iter(self):
            return iter(subs)

    fake_stripe = types.SimpleNamespace(
        api_key=None,
        Subscription=types.SimpleNamespace(list=lambda **kw: _FakeList()),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "sk_test_fake")

    data = admin_dashboard._fetch_stripe_billing_sync()
    assert data["available"] is True
    assert data["subscriptions"] == {
        "active": 3, "trialing": 1, "past_due": 1, "canceled": 1, "other": 0,
    }
    # 19,90 + 19,90 + 199/12 = 56,38
    assert data["mrr"] == pytest.approx(56.38, abs=0.01)
    assert data["ticket_medio"] == pytest.approx(18.79, abs=0.01)
    assert data["trial_mrr_potencial"] == pytest.approx(19.90, abs=0.01)


def test_stripe_billing_cache_faz_backoff_apos_falha(monkeypatch):
    """Queda do Stripe não pode virar retry a cada request: o carimbo do cache
    avança mesmo na falha (TTL = backoff) e o payload bom fica com stale=True."""
    import asyncio

    calls = {"n": 0}
    ok_payload = {"available": True, "mrr": 39.8, "fetched_at": "2026-01-01T00:00:00+00:00"}

    def _fake_sync():
        calls["n"] += 1
        if calls["n"] == 1:
            return dict(ok_payload)
        return {"available": False, "reason": "boom"}

    monkeypatch.setattr(admin_dashboard, "_fetch_stripe_billing_sync", _fake_sync)

    # 1ª chamada: sucesso, entra no cache
    data = asyncio.run(admin_dashboard.fetch_billing_summary())
    assert data["available"] is True and "stale" not in data and calls["n"] == 1

    # TTL vencido + Stripe fora: preserva números, marca stale, avança carimbo
    admin_dashboard._billing_summary_cache["fetched_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=400)
    )
    data = asyncio.run(admin_dashboard.fetch_billing_summary())
    assert data["available"] is True and data["stale"] is True
    assert data["mrr"] == ok_payload["mrr"] and calls["n"] == 2

    # Dentro do TTL pós-falha: NÃO tenta o Stripe de novo
    data = asyncio.run(admin_dashboard.fetch_billing_summary())
    assert data["stale"] is True and calls["n"] == 2


# ── Endpoint de lista ──────────────────────────────────────────────────────

def test_users_endpoint_requires_admin_auth():
    response = TestClient(dashboard.app).get("/admin/api/users")
    assert response.status_code == 401


def _funnel_event(uid, kind, session_id, *, days_ago=0):
    """Insere um evento na tabela dedicada checkout_funnel_events."""
    from datetime import datetime, timedelta, timezone
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into checkout_funnel_events (user_id, session_id, kind, created_at) "
                "values (%s, %s, %s, %s)",
                (uid, session_id, kind, ts),
            )
        conn.commit()


def test_checkout_funnel_conta_pessoas_e_sessoes(panel_accounts):
    """people conta usuários distintos; sessions_started conta sessões
    distintas; sessions_completed liga início e fim pelo MESMO session_id."""
    tag, uids = panel_accounts
    client = _admin_client()

    base = client.get("/admin/api/users").json()["checkout_funnel"]

    # paying abre 2 sessões (A e B); só A conclui. trial abre 1 sessão (C) sem concluir.
    _funnel_event(uids["paying"], "started", f"sess_A_{tag}")
    _funnel_event(uids["paying"], "completed", f"sess_A_{tag}")
    _funnel_event(uids["paying"], "started", f"sess_B_{tag}")   # abandonada
    _funnel_event(uids["trial"], "started", f"sess_C_{tag}")    # abandonada

    f = client.get("/admin/api/users").json()["checkout_funnel"]
    assert f["people_30d"] == base["people_30d"] + 2            # paying + trial
    assert f["sessions_started_30d"] == base["sessions_started_30d"] + 3  # A, B, C
    assert f["sessions_completed_30d"] == base["sessions_completed_30d"] + 1  # só A


def test_checkout_funnel_conversao_por_sessao_resolve_recompra(panel_accounts):
    """P2 (388): comprou numa sessão, cancelou, reabre outra e abandona. A
    correlação por session_id conta a 2ª sessão como NÃO convertida — a
    conversão por sessão nunca fica inflada pela compra velha."""
    tag, uids = panel_accounts
    client = _admin_client()

    base = client.get("/admin/api/users").json()["checkout_funnel"]

    # sessão A: comprou há 20d (started+completed, mesmo id). sessão B: reabre
    # há 2d e abandona (started sem completed).
    _funnel_event(uids["canceled"], "started", f"sess_old_{tag}", days_ago=20)
    _funnel_event(uids["canceled"], "completed", f"sess_old_{tag}", days_ago=20)
    _funnel_event(uids["canceled"], "started", f"sess_new_{tag}", days_ago=2)

    f = client.get("/admin/api/users").json()["checkout_funnel"]
    assert f["sessions_started_30d"] == base["sessions_started_30d"] + 2   # old + new
    assert f["sessions_completed_30d"] == base["sessions_completed_30d"] + 1  # só a old
    # a razão respeita o teto e a sessão nova (abandonada) não é convertida
    assert f["sessions_completed_30d"] <= f["sessions_started_30d"]


def test_checkout_funnel_conclusao_sem_abertura_nao_conta(panel_accounts):
    """P1: conclusão cujo session_id nunca teve um 'started' (histórico órfão,
    ou completed sem session_id) não infla o completed — só sessões abertas
    entram no started, e completed é subconjunto delas."""
    tag, uids = panel_accounts
    client = _admin_client()

    base = client.get("/admin/api/users").json()["checkout_funnel"]

    # completed órfão (nenhum started com esse session_id)
    _funnel_event(uids["free"], "completed", f"sess_orphan_{tag}")
    # completed sem session_id
    _funnel_event(uids["free"], "completed", None)

    f = client.get("/admin/api/users").json()["checkout_funnel"]
    # nenhuma sessão ABERTA nova → started e completed inalterados
    assert f["sessions_started_30d"] == base["sessions_started_30d"]
    assert f["sessions_completed_30d"] == base["sessions_completed_30d"]


def test_checkout_funnel_imune_ao_purge_do_feed(panel_accounts):
    """O funil vive em tabela própria (checkout_funnel_events), NÃO em
    system_event_logs — então o 'Limpar' do feed não pode zerá-lo. Aqui isso
    é estrutural (tabelas diferentes), mas o teste trava a garantia."""
    tag, uids = panel_accounts
    client = _admin_client()

    _funnel_event(uids["paying"], "started", f"sess_P_{tag}")
    _funnel_event(uids["paying"], "completed", f"sess_P_{tag}")
    base = client.get("/admin/api/users").json()["checkout_funnel"]

    # Apaga TUDO de system_event_logs
    resp = client.request(
        "DELETE", "/admin/api/events",
        headers={dashboard.CSRF_HEADER_NAME: "test-admin-csrf"},
    )
    assert resp.status_code == 200

    after = client.get("/admin/api/users").json()["checkout_funnel"]
    assert after["sessions_started_30d"] == base["sessions_started_30d"]
    assert after["sessions_completed_30d"] == base["sessions_completed_30d"]


def test_users_list_expoe_signup_source_e_agregado(panel_accounts):
    tag, _uids = panel_accounts
    client = _admin_client()

    # Contas com origem explícita, buscáveis pelo prefixo único
    web = _mk_account(f"srcpanel-{tag}-web@test.local", source="web")
    app = _mk_account(f"srcpanel-{tag}-app@test.local", source="app")
    goog = _mk_account(f"srcpanel-{tag}-goog@test.local", source="google")
    unknown = _mk_account(f"srcpanel-{tag}-unk@test.local")  # signup_source NULL
    try:
        data = client.get(f"/admin/api/users?q=srcpanel-{tag}").json()
        src_by_uid = {u["user_id"]: u["signup_source"] for u in data["users"]}
        assert src_by_uid == {web: "web", app: "app", goog: "google", unknown: None}

        # Agregado por origem é da base inteira e conta NULL como 'desconhecido'
        by_source = data["by_source"]
        assert by_source.get("web", 0) >= 1
        assert by_source.get("app", 0) >= 1
        assert by_source.get("google", 0) >= 1
        assert by_source.get("desconhecido", 0) >= 1
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from auth_accounts where user_id = any(%s)",
                            ([web, app, goog, unknown],))
            conn.commit()


def test_users_list_aggregates_and_statuses(panel_accounts):
    tag, uids = panel_accounts
    client = _admin_client()

    data = client.get(f"/admin/api/users?q=panel-{tag}").json()
    assert data["total"] == 7
    assert data["truncated"] is False
    status_by_uid = {u["user_id"]: u["account_status"] for u in data["users"]}
    assert status_by_uid == {
        uids["paying"]: "paying",
        uids["trial"]: "trial",
        uids["past_due"]: "past_due",
        uids["canceled"]: "canceled",
        uids["granted"]: "granted",
        uids["granted_expired"]: "canceled",  # cortesia vencida
        uids["free"]: "free",
    }
    email_by_uid = {u["user_id"]: u["email"] for u in data["users"]}
    assert email_by_uid[uids["paying"]] == f"panel-{tag}-paying@test.local"
    # Colunas cifradas nunca vazam no JSON
    assert all("email_enc" not in u for u in data["users"])

    # Paridade SQL ↔ Python: o account_status calculado pelo CASE no banco
    # tem de bater com _derive_account_status sobre os mesmos campos crus
    for u in data["users"]:
        raw = dict(u)
        if raw.get("plan_expires_at"):
            raw["plan_expires_at"] = datetime.fromisoformat(raw["plan_expires_at"])
        assert admin_dashboard._derive_account_status(raw, NOW) == u["account_status"], u

    # Agregados são da base inteira (>= os do fixture)
    agg = data["aggregates"]
    for status in ("paying", "trial", "past_due", "granted", "free"):
        assert agg[status] >= 1
    assert agg["canceled"] >= 2
    assert agg["total"] >= 7

    # Stripe indisponível nos testes → available False, sem quebrar a resposta
    assert data["billing"]["available"] is False


def test_users_list_filters_by_status_and_plan(panel_accounts):
    tag, uids = panel_accounts
    client = _admin_client()

    data = client.get(f"/admin/api/users?q=panel-{tag}&status=trial").json()
    assert data["total"] == 1
    assert data["users"][0]["user_id"] == uids["trial"]

    # plan=free traz o free comum E o ex-assinante (plan voltou pra free)
    data = client.get(f"/admin/api/users?q=panel-{tag}&plan=free").json()
    assert data["total"] == 2
    assert {u["user_id"] for u in data["users"]} == {uids["free"], uids["canceled"]}

    # status=canceled junta ex-assinante e cortesia vencida
    data = client.get(f"/admin/api/users?q=panel-{tag}&status=canceled").json()
    assert {u["user_id"] for u in data["users"]} == {uids["canceled"], uids["granted_expired"]}

    # status inválido é ignorado (não explode, não filtra)
    data = client.get(f"/admin/api/users?q=panel-{tag}&status=xpto").json()
    assert data["total"] == 7


def test_users_list_pagination(panel_accounts):
    tag, _uids = panel_accounts
    client = _admin_client()

    page0 = client.get(f"/admin/api/users?q=panel-{tag}&per_page=4&page=0").json()
    page1 = client.get(f"/admin/api/users?q=panel-{tag}&per_page=4&page=1").json()
    assert page0["total"] == page1["total"] == 7
    assert len(page0["users"]) == 4
    assert len(page1["users"]) == 3
    ids0 = {u["user_id"] for u in page0["users"]}
    ids1 = {u["user_id"] for u in page1["users"]}
    assert not (ids0 & ids1)

    # Caminho SEM busca (paginação 100% em SQL): filtra por status pra
    # não depender do resto da base, e as páginas não podem se sobrepor
    canceled_total = client.get("/admin/api/users?status=canceled").json()["total"]
    assert canceled_total >= 2
    sql0 = client.get("/admin/api/users?status=canceled&per_page=1&page=0").json()
    sql1 = client.get("/admin/api/users?status=canceled&per_page=1&page=1").json()
    assert sql0["total"] == sql1["total"] == canceled_total
    assert len(sql0["users"]) == len(sql1["users"]) == 1
    assert sql0["users"][0]["user_id"] != sql1["users"][0]["user_id"]


# ── Drill-down ─────────────────────────────────────────────────────────────

def test_user_detail_requires_admin_auth(panel_accounts):
    _tag, uids = panel_accounts
    response = TestClient(dashboard.app).get(f"/admin/api/users/{uids['paying']}")
    assert response.status_code == 401


def test_user_detail_shape(panel_accounts):
    tag, uids = panel_accounts
    client = _admin_client()

    data = client.get(f"/admin/api/users/{uids['trial']}").json()
    profile = data["profile"]
    assert profile["user_id"] == uids["trial"]
    assert profile["email"] == f"panel-{tag}-trial@test.local"
    assert profile["account_status"] == "trial"
    assert "email_enc" not in profile and "phone_enc" not in profile

    usage = data["usage"]
    assert usage["tx_total"] == 0 and usage["tx_30d"] == 0
    assert usage["pockets_count"] == 0
    assert isinstance(data["recent_events"], list)
    assert isinstance(data["recent_logins"], list)


def test_user_detail_ignora_movimentos_internos(panel_accounts):
    """Movimento interno (ajuste de saldo, aporte) não pode aparecer como
    'última transação' de quem tem zero transações externas."""
    _tag, uids = panel_accounts
    uid = uids["free"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into launches (user_id, tipo, valor, nota, criado_em,
                                      is_internal_movement, source)
                values (%s, 'despesa', 5.0, 'ajuste interno', now(), true, 'manual')
                """,
                (uid,),
            )
        conn.commit()
    try:
        client = _admin_client()
        usage = client.get(f"/admin/api/users/{uid}").json()["usage"]
        assert usage["tx_total"] == 0
        assert usage["tx_30d"] == 0
        assert usage["last_tx_at"] is None
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from launches where user_id = %s", (uid,))
            conn.commit()


def test_grant_pro_em_ex_assinante_classifica_como_cortesia(panel_accounts):
    """/admin/grant-pro sobre conta com status terminal (canceled) limpa o
    last_payment_status — senão o grant vigente apareceria como 'Cancelado'
    no painel. Status em curso (active) fica intacto: webhook é o dono."""
    tag, uids = panel_accounts
    client = _admin_client()

    grant = client.get(
        f"/admin/grant-pro?email=panel-{tag}-canceled@test.local&months=2"
    ).json()
    assert grant["ok"] is True
    data = client.get(f"/admin/api/users?q=panel-{tag}-canceled").json()
    assert data["users"][0]["account_status"] == "granted"

    # Assinante ativo que ganha extensão continua Pagante
    grant = client.get(
        f"/admin/grant-pro?email=panel-{tag}-paying@test.local&months=2"
    ).json()
    assert grant["ok"] is True
    data = client.get(f"/admin/api/users?q=panel-{tag}-paying").json()
    assert data["users"][0]["account_status"] == "paying"


def test_user_detail_404_for_unknown_account():
    client = _admin_client()
    response = client.get("/admin/api/users/999999999999")
    assert response.status_code == 404


# ── Mudança de plano pelo painel (POST /admin/api/users/{id}/plan) ─────────

def _set_plan(client: TestClient, uid: int, **body):
    return client.post(
        f"/admin/api/users/{uid}/plan",
        headers={dashboard.CSRF_HEADER_NAME: "test-admin-csrf"},
        json=body,
    )


def _stored_plan(uid: int) -> tuple[str, object, str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select plan, plan_expires_at, last_payment_status "
                "from auth_accounts where user_id = %s",
                (uid,),
            )
            row = cur.fetchone()
    return row["plan"], row["plan_expires_at"], row["last_payment_status"]


def test_set_plan_requires_admin_auth(panel_accounts):
    _tag, uids = panel_accounts
    client = TestClient(dashboard.app, base_url="https://testserver")
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-admin-csrf")
    assert _set_plan(client, uids["free"], plan="pro_max").status_code == 401
    assert _stored_plan(uids["free"])[0] == "free"


def test_set_plan_sobe_e_desce(panel_accounts):
    """Upgrade põe validade no futuro; downgrade para Grátis zera a validade —
    senão a conta continuaria com data de expiração de um plano que ela não
    tem mais."""
    _tag, uids = panel_accounts
    client = _admin_client()
    uid = uids["free"]

    up = _set_plan(client, uid, plan="pro_max", months=3)
    assert up.status_code == 200, up.text
    assert up.json()["plan"] == "pro_max"
    plan, expires, _pay = _stored_plan(uid)
    assert plan == "pro_max"
    assert expires is not None
    assert timedelta(days=80) < (expires - NOW) < timedelta(days=100)
    assert client.get(f"/admin/api/users/{uid}").json()["profile"]["plan"] == "pro_max"

    down = _set_plan(client, uid, plan="free")
    assert down.status_code == 200, down.text
    plan, expires, _pay = _stored_plan(uid)
    assert plan == "free"
    assert expires is None
    assert client.get(f"/admin/api/users/{uid}").json()["profile"]["account_status"] == "free"


def test_set_plan_pago_limpa_status_terminal_mas_downgrade_nao(panel_accounts):
    """Mesma regra do /admin/grant-pro (os dois passam por set_account_plan):
    plano pago sobre ex-assinante limpa o 'canceled' — senão o painel mostraria
    'Cancelado' com plano vigente. Descer para Grátis não mexe: 'canceled' num
    ex-assinante é a descrição correta."""
    _tag, uids = panel_accounts
    client = _admin_client()
    uid = uids["canceled"]                      # plan free + last_payment_status canceled

    assert _set_plan(client, uid, plan="essencial", months=1).status_code == 200
    assert _stored_plan(uid)[2] == "inactive"
    assert client.get(f"/admin/api/users/{uid}").json()["profile"]["account_status"] == "granted"

    # Assinante em curso não perde o status: o webhook continua dono dele.
    uid_pagante = uids["paying"]
    assert _set_plan(client, uid_pagante, plan="pro_max", months=1).status_code == 200
    assert _stored_plan(uid_pagante)[2] == "active"

    # Descer para Grátis não escreve na coluna: quem estava em trial continua
    # 'trialing' (o webhook é quem fecha esse ciclo).
    uid_trial = uids["trial"]
    assert _set_plan(client, uid_trial, plan="free").status_code == 200
    assert _stored_plan(uid_trial) == ("free", None, "trialing")


@pytest.mark.parametrize("body", [
    {"plan": "plus"},          # tier certo, valor que o webhook NÃO grava
    {"plan": "pro-max"},
    {"plan": ""},
    {"plan": "pro", "months": 0},
    {"plan": "pro", "months": 999},
])
def test_set_plan_rejeita_entrada_invalida(panel_accounts, body):
    """A conta não pode ficar com um valor de plan que o resto do código não
    reconhece — 'plus' é o caso traiçoeiro: dá o mesmo tier, mas some das
    queries que casam o literal 'pro'."""
    _tag, uids = panel_accounts
    client = _admin_client()
    uid = uids["free"]
    assert _set_plan(client, uid, **body).status_code == 422
    assert _stored_plan(uid)[0] == "free"


def test_set_plan_404_para_conta_inexistente():
    client = _admin_client()
    assert _set_plan(client, 999999999999, plan="pro", months=1).status_code == 404


def test_planos_gravaveis_do_html_espelham_o_backend():
    """O <select> do painel é HTML estático — não importa a lista do Python.
    Se as duas divergirem, o admin escolhe um plano que a API recusa (ou deixa
    de ver um que existe)."""
    import pathlib
    import re

    html = (pathlib.Path(dashboard.__file__).parent / "admin-dashboard.html").read_text(
        encoding="utf-8"
    )
    match = re.search(r"const PLAN_WRITE_VALUES = \[([^\]]*)\]", html)
    assert match, "PLAN_WRITE_VALUES sumiu de frontend/admin-dashboard.html"
    do_html = tuple(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert do_html == admin_dashboard.ADMIN_PLAN_VALUES


# ── Liberar novo trial (POST /admin/api/users/{id}/trial-reset) ────────────
#
# A trava de trial é por TELEFONE (plan_trials, PK = phone_hash) e sobrevive à
# conta. _mk_account não grava phone_hash — os testes abaixo setam à mão e
# limpam plan_trials no finally (a FK é ON DELETE SET NULL, não CASCADE, então
# apagar a conta deixa a linha da trava para trás).

def _set_phone(uid: int, phone: str) -> str:
    """Vincula um telefone à conta e devolve o phone_hash gravado."""
    from core.crypto import hash_pii
    h = hash_pii(phone, kind="phone")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set phone_e164 = %s, phone_hash = %s where user_id = %s",
                (phone, h, uid),
            )
        conn.commit()
    return h


def _lock_trial(phone_hash: str, uid: int, started_at=None) -> None:
    """Queima o trial daquele telefone (o que claim_trial_for_user grava)."""
    started_at = started_at or (NOW - timedelta(days=40))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into plan_trials (phone_hash, user_id, started_at, model_version)
                values (%s, %s, %s, 2)
                on conflict (phone_hash) do update set started_at = excluded.started_at
                """,
                (phone_hash, uid, started_at),
            )
            cur.execute(
                "update auth_accounts set trial_started_at = %s where user_id = %s",
                (started_at, uid),
            )
        conn.commit()


def _trial_lock_exists(phone_hash: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select 1 from plan_trials where phone_hash = %s", (phone_hash,))
            return cur.fetchone() is not None


def _drop_locks(*hashes: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from plan_trials where phone_hash = any(%s)", (list(hashes),))
        conn.commit()


def _reset_trial(client: TestClient, uid: int):
    return client.post(
        f"/admin/api/users/{uid}/trial-reset",
        headers={dashboard.CSRF_HEADER_NAME: "test-admin-csrf"},
    )


def test_trial_reset_exige_sessao_admin(panel_accounts):
    """Sem sessão: 401 E a trava continua de pé (não basta o status)."""
    _tag, uids = panel_accounts
    uid = uids["free"]
    h = _set_phone(uid, f"+5511{uid % 100000000:08d}")
    _lock_trial(h, uid)
    try:
        anon = TestClient(dashboard.app, base_url="https://testserver")
        anon.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-admin-csrf")
        assert _reset_trial(anon, uid).status_code == 401
        assert _trial_lock_exists(h) is True
    finally:
        _drop_locks(h)


def test_trial_reset_devolve_a_elegibilidade_de_verdade(panel_accounts):
    """O caso principal, medido pelas funções REAIS de db.plans (sem mock):
    inelegível antes → 200 → linha some, âncora zerada e elegível depois."""
    from db.plans import get_trial_started_at, is_trial_eligible_for_user

    _tag, uids = panel_accounts
    uid = uids["free"]
    h = _set_phone(uid, f"+5511{uid % 100000000:08d}")
    _lock_trial(h, uid)
    try:
        assert is_trial_eligible_for_user(uid) is False
        assert get_trial_started_at(uid) is not None

        resp = _reset_trial(_admin_client(), uid)
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] == 1

        assert _trial_lock_exists(h) is False
        assert get_trial_started_at(uid) is None
        assert is_trial_eligible_for_user(uid) is True
    finally:
        _drop_locks(h)


def test_trial_reset_limpa_o_downsell_junto(panel_accounts):
    """Decisão do dono: o funil de downsell tem que poder rodar de novo."""
    _tag, uids = panel_accounts
    uid = uids["free"]
    h = _set_phone(uid, f"+5511{uid % 100000000:08d}")
    _lock_trial(h, uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set trial_downsell_sent_at = now() where user_id = %s",
                (uid,),
            )
        conn.commit()
    try:
        assert _reset_trial(_admin_client(), uid).status_code == 200
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select trial_downsell_sent_at from auth_accounts where user_id = %s",
                    (uid,),
                )
                assert cur.fetchone()["trial_downsell_sent_at"] is None
    finally:
        _drop_locks(h)


def test_trial_reset_nao_libera_o_telefone_de_outra_conta(panel_accounts):
    """Isolamento (controle positivo): resetar A não pode tocar a trava de B.
    Sem isto o grupo passaria numa implementação que apaga a plan_trials inteira
    — ou que casasse variantes de nono dígito (phone_lookup_candidates)."""
    from db.plans import is_trial_eligible_for_user

    _tag, uids = panel_accounts
    a, b = uids["free"], uids["granted"]
    ha = _set_phone(a, f"+5511{a % 100000000:08d}")
    hb = _set_phone(b, f"+5521{b % 100000000:08d}")
    _lock_trial(ha, a)
    _lock_trial(hb, b)
    try:
        assert _reset_trial(_admin_client(), a).status_code == 200
        assert _trial_lock_exists(ha) is False
        assert _trial_lock_exists(hb) is True
        assert is_trial_eligible_for_user(a) is True
        assert is_trial_eligible_for_user(b) is False
    finally:
        _drop_locks(ha, hb)


def test_trial_reset_404_para_conta_inexistente():
    assert _reset_trial(_admin_client(), 999999999999).status_code == 404


def test_trial_reset_422_sem_telefone_vinculado(panel_accounts):
    """A trava é por telefone: sem número não há o que liberar."""
    _tag, uids = panel_accounts
    resp = _reset_trial(_admin_client(), uids["free"])
    assert resp.status_code == 422
    assert "telefone" in resp.json()["detail"].lower()


def test_trial_reset_409_com_assinatura_viva_e_nao_apaga_nada(panel_accounts):
    """Decisão do dono: quem manda no trial é a Stripe. Controle positivo do
    grupo — prova que a trava continua valendo para quem não é alvo."""
    from db.plans import is_trial_eligible_for_user

    _tag, uids = panel_accounts
    uid = uids["trial"]  # last_payment_status = 'trialing'
    h = _set_phone(uid, f"+5511{uid % 100000000:08d}")
    _lock_trial(h, uid)
    try:
        resp = _reset_trial(_admin_client(), uid)
        assert resp.status_code == 409, resp.text
        assert "stripe" in resp.json()["detail"].lower()
        assert _trial_lock_exists(h) is True
        assert is_trial_eligible_for_user(uid) is False
    finally:
        _drop_locks(h)


@pytest.mark.parametrize("pay,esperado", [
    # Vivos na Stripe: o gate recusa e a trava fica de pé. 'unpaid' (dunning) e
    # 'incomplete' (3DS pendente) entram aqui porque o painel já os mostra como
    # "Past due" — assinatura viva (_LIVE_PAYMENT_STATUSES).
    ("active", 409),
    ("past_due", 409),
    ("unpaid", 409),
    ("incomplete", 409),
    # Terminais/ausentes: o botão TEM de funcionar. Controle positivo do grupo —
    # sem estes, um gate que recusa tudo passaria no teste, e é pior que o bug.
    ("canceled", 200),
    ("incomplete_expired", 200),
    ("inactive", 200),
    ("", 200),  # NULL não entra: a coluna é NOT NULL (default 'inactive')
    ("status_novo_que_a_stripe_inventar", 200),
])
def test_trial_reset_gate_por_status_de_pagamento(panel_accounts, pay, esperado):
    _tag, uids = panel_accounts
    uid = uids["free"]
    h = _set_phone(uid, f"+5511{uid % 100000000:08d}")
    _lock_trial(h, uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set last_payment_status = %s where user_id = %s",
                (pay, uid),
            )
        conn.commit()
    try:
        resp = _reset_trial(_admin_client(), uid)
        assert resp.status_code == esperado, resp.text
        assert _trial_lock_exists(h) is (esperado == 409)
    finally:
        _drop_locks(h)


def test_trial_reset_audita_sem_vazar_telefone_nem_hash(panel_accounts, monkeypatch):
    """O evento é registrado como warning (remoção de trava anti-abuso) e o
    details NÃO carrega telefone nem phone_hash — é HMAC de PII."""
    _tag, uids = panel_accounts
    uid = uids["free"]
    phone = f"+5511{uid % 100000000:08d}"
    h = _set_phone(uid, phone)
    _lock_trial(h, uid)

    eventos = []

    async def _capture(level, event_type, message, **kwargs):
        eventos.append((level, event_type, message, kwargs))

    monkeypatch.setattr(admin_dashboard, "log_system_event", _capture)
    try:
        assert _reset_trial(_admin_client(), uid).status_code == 200
        # _admin_client() loga antes e gera admin_login_success no mesmo capture.
        meus = [e for e in eventos if e[1] == "admin_trial_reset"]
        assert len(meus) == 1
        level, _tipo, message, kwargs = meus[0]
        assert level == "warning"
        assert kwargs["source"] == "admin"
        assert kwargs["user_id"] == uid
        # Lista BRANCA: procurar o telefone por substring é cego a formato
        # (um espaço no meio do número passa batido). Chave nova qualquer, com
        # qualquer conteúdo, derruba este teste e obriga a justificar.
        detalhes = kwargs["details"]
        assert set(detalhes) == {"admin", "deleted", "previous_started_at"}
        assert isinstance(detalhes["admin"], str)
        assert detalhes["deleted"] == 1
        assert isinstance(detalhes["previous_started_at"], str)
        # phone_hash é hex de HMAC, não tem variação de formato: substring serve.
        assert h not in repr(detalhes) + message
    finally:
        _drop_locks(h)


def test_drilldown_mostra_a_trava_do_telefone_de_outra_conta(panel_accounts):
    """(b) do plano: a tela dizia 'Trial iniciado —' numa conta inelegível
    porque só mostrava a âncora da CONTA. trial_lock_* é o que explica o caso.
    E phone_hash não pode vazar pro JSON — é HMAC de PII."""
    _tag, uids = panel_accounts
    dona, outra = uids["free"], uids["granted"]
    h = _set_phone(dona, f"+5511{dona % 100000000:08d}")
    # Trava gravada em nome de OUTRA conta, com a conta atual sem âncora.
    _lock_trial(h, outra)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set trial_started_at = null where user_id = %s",
                (dona,),
            )
        conn.commit()
    try:
        resp = _admin_client().get(f"/admin/api/users/{dona}")
        assert resp.status_code == 200, resp.text
        p = resp.json()["profile"]
        assert p["trial_started_at"] is None
        assert p["trial_lock_started_at"] is not None
        assert int(p["trial_lock_user_id"]) == outra
        assert "phone_hash" not in p
    finally:
        _drop_locks(h)


def test_reset_faz_o_checkout_voltar_a_mandar_trial(panel_accounts, monkeypatch):
    """A prova que importa: depois do reset, o /billing/create-checkout volta a
    pedir trial_period_days à Stripe. Roda a elegibilidade REAL — monkeypatchar
    is_trial_eligible_for_user anularia a medição."""
    from tests.test_billing_checkout import _patch_stripe
    from db.plans import claim_trial_for_user

    _tag, uids = panel_accounts
    uid = uids["free"]
    email = f"panel-{_tag}-free@test.local"
    h = _set_phone(uid, f"+5511{uid % 100000000:08d}")

    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("PLANS_TRIAL_DAYS", "15")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    fake = _patch_stripe(monkeypatch)

    user_client = TestClient(dashboard.app)
    user_client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    user_client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-admin-csrf")
    headers = {dashboard.CSRF_HEADER_NAME: "test-admin-csrf"}

    try:
        # 1. o telefone queima o trial pelo caminho real
        assert claim_trial_for_user(uid) is not None
        try:
            dashboard.limiter._storage.reset()
        except Exception:
            pass
        r1 = user_client.post("/billing/create-checkout", headers=headers)
        assert r1.status_code == 200, r1.text
        assert "trial_period_days" not in fake.last_session_kwargs["subscription_data"]

        # 2. o admin libera
        assert _reset_trial(_admin_client(), uid).status_code == 200

        # O checkout aberto do passo 1 seria REAPROVEITADO (mesmo plano/intervalo)
        # e devolveria a URL antiga sem consultar a elegibilidade de novo. Expira
        # como a Stripe faz depois de 24h, para forçar uma sessão nova.
        for session in fake.open_sessions:
            session["status"] = "expired"

        # 3. o checkout novo volta a conceder os 15 dias
        try:
            dashboard.limiter._storage.reset()
        except Exception:
            pass
        r2 = user_client.post("/billing/create-checkout", headers=headers)
        assert r2.status_code == 200, r2.text
        assert fake.last_session_kwargs["subscription_data"]["trial_period_days"] == 15
    finally:
        _drop_locks(h)
