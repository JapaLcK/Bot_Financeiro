"""Testes do painel de usuários do admin (/admin/api/users e /{user_id}).

Cobrem: exigência de auth, classificação de assinatura por conta
(_derive_account_status), agregados, filtros por status/plano, busca por
e-mail, paginação e o drill-down individual. O resumo Stripe roda com a
chave vazia (billing.available == False) — a API externa nunca é chamada.
"""
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
                stripe_customer: str | None = None, expires=None) -> int:
    uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into auth_accounts
                    (user_id, email, password_hash, plan, last_payment_status,
                     stripe_customer_id, plan_expires_at)
                values (%s, %s, 'x', %s, %s, %s, %s)
                """,
                (uid, email, plan, pay, stripe_customer, expires),
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


def _log_event(uid, event_type, *, days_ago=0):
    from datetime import datetime, timedelta, timezone
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into system_event_logs (level, event_type, message, source, user_id, details, created_at) "
                "values ('info', %s, 'x', 'billing', %s, '{}'::jsonb, %s)",
                (event_type, uid, ts),
            )
        conn.commit()


def test_checkout_funnel_conta_pessoas_distintas(panel_accounts):
    """checkout_funnel conta USUÁRIOS distintos: abrir 2x não vira 2 no
    started; completed = quem abriu E concluiu na janela."""
    _tag, uids = panel_accounts
    client = _admin_client()

    base = client.get("/admin/api/users").json()["checkout_funnel"]
    b_started, b_completed = base["started_30d"], base["completed_30d"]

    # 2 pessoas abrem o checkout; uma delas abre 2x (não deve inflar); 1 conclui
    _log_event(uids["paying"], "billing_checkout_started")
    _log_event(uids["paying"], "billing_checkout_started")  # 2ª abertura, mesmo user
    _log_event(uids["trial"], "billing_checkout_started")
    _log_event(uids["paying"], "billing_checkout_completed")

    funnel = client.get("/admin/api/users").json()["checkout_funnel"]
    assert funnel["started_30d"] == b_started + 2      # 2 pessoas, não 3 eventos
    assert funnel["completed_30d"] == b_completed + 1  # só paying (abriu E fechou)


def test_checkout_funnel_conversao_nunca_passa_de_100pct(panel_accounts):
    """Guard do P1: conclusão SEM abertura na coorte (histórico anterior ao
    recurso, ou início fora da janela) não entra no numerador — a conversão
    fica sempre <= 100%."""
    _tag, uids = panel_accounts
    client = _admin_client()

    base = client.get("/admin/api/users").json()["checkout_funnel"]

    # Conclusão histórica sem started correspondente na janela (caso do deploy)
    _log_event(uids["canceled"], "billing_checkout_completed")
    # Abertura antiga (fora da janela de 30d) + conclusão recente: não é coorte
    _log_event(uids["granted"], "billing_checkout_started", days_ago=40)
    _log_event(uids["granted"], "billing_checkout_completed")
    # Uma coorte legítima na janela
    _log_event(uids["paying"], "billing_checkout_started")
    _log_event(uids["paying"], "billing_checkout_completed")

    funnel = client.get("/admin/api/users").json()["checkout_funnel"]
    # started só ganhou o paying (o granted abriu fora da janela)
    assert funnel["started_30d"] == base["started_30d"] + 1
    # completed só ganhou o paying — as duas conclusões sem coorte não contam
    assert funnel["completed_30d"] == base["completed_30d"] + 1
    # E a razão respeita o teto
    assert funnel["completed_30d"] <= funnel["started_30d"]


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
