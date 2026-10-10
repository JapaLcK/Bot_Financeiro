"""Conta paga sem senha e sem Google/Apple: bloqueada até criar a senha.

PR 4 do funil v3. A tabela rota a rota mora em `test_rotas_senha_obrigatoria.py`;
aqui ficam a fonte única (`db.conta_sem_credencial`), o `/auth/me`, o bloqueio
por chamada direta nos pontos fora do gate central, o /conta, o WS, o MFA, o
`gate_onboarding` e a conversa ponta a ponta (quiz → webhook → senha → login).
Tudo pelo TestClient e Postgres reais; só e-mail e Stripe são falsos.

Controles (medidos no PR, ver o relato): `conta_sem_credencial` sem o `not
exists` em auth_identities deixa vermelho o "só-Google"; sem a perna no gate o
/data volta 200; sem o `exige_credencial=False` o /contact vira 403.
"""
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import core.services.email_service as email_service
import db
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from db.connection import get_conn
from test_quiz_conta import CSRF, _conta_quiz, _email, _navegador, env  # noqa: F401 (env é autouse)

H = {dashboard.CSRF_HEADER_NAME: CSRF}


def conta_paga_sem_credencial(plan: str = "pro_max"):
    """Conta nascida na /assinar (sem senha, logada) e paga pelas escritas
    oficiais. Devolve (uid, email, cliente com a sessão da conta)."""
    email, client = _email(), _navegador()
    r = _conta_quiz(client, email)
    assert r.status_code == 200, r.text
    uid = int(r.json()["user_id"])
    db.mark_plan_selected(uid)
    db.update_user_plan(uid, plan, datetime.now(timezone.utc) + timedelta(days=30))
    db_support.invalidate_auth_user_cache(uid)
    assert db.conta_sem_credencial(uid), "a conta do quiz devia nascer sem credencial"
    return uid, email, client


def _com_senha(uid: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set password_hash = 'x' where user_id = %s", (uid,))
        conn.commit()
    db_support.invalidate_auth_user_cache(uid)


def _conta_crua(uid: int, senha: str | None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into auth_accounts (user_id, email, password_hash) values (%s, %s, %s)",
                    (uid, f"cs-{uid}@t.local", senha))
        conn.commit()


# ── 1. a fonte única ─────────────────────────────────────────────────────────

def test_conta_sem_credencial_os_seis_casos(user_id):
    assert db.conta_sem_credencial(user_id) is False, "sem auth_accounts não é 'sem credencial'"
    _conta_crua(user_id, None)
    assert db.conta_sem_credencial(user_id) is True
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set phone_status = 'confirmed' where user_id = %s", (user_id,))
        conn.commit()
    assert db.conta_sem_credencial(user_id) is True, "telefone confirmado não é credencial"
    db.link_google_identity(user_id, f"g-{user_id}", f"cs-{user_id}@t.local")
    assert db.conta_sem_credencial(user_id) is False, "só-Google tem credencial"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from auth_identities where user_id = %s", (user_id,))
        conn.commit()
    db.link_google_identity(user_id, f"a-{user_id}", None, provider=db.PROVIDER_APPLE)
    assert db.conta_sem_credencial(user_id) is False, "só-Apple tem credencial"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from auth_identities where user_id = %s", (user_id,))
        cur.execute("update auth_accounts set password_hash = '' where user_id = %s", (user_id,))
        conn.commit()
    assert db.conta_sem_credencial(user_id) is True, "senha vazia não é credencial"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set password_hash = 'x' where user_id = %s", (user_id,))
        conn.commit()
    assert db.conta_sem_credencial(user_id) is False, "com senha tem credencial"


# ── 2. /auth/me ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("credencial,esperado", [
    (None, True), ("senha", False), (db.PROVIDER_GOOGLE, False), (db.PROVIDER_APPLE, False),
])
def test_auth_me_diz_se_precisa_criar_senha(credencial, esperado):
    uid, email, client = conta_paga_sem_credencial()
    if credencial == "senha":
        _com_senha(uid)
    elif credencial:
        db.link_google_identity(uid, f"{credencial}-{uid}", email, provider=credencial)
    me = client.get("/auth/me").json()
    assert me["precisa_criar_senha"] is esperado and me["app_access"] is True, me


# ── 4. bloqueio por chamada direta ──────────────────────────────────────────

_BLOQUEADAS = [
    ("GET", "/data/{uid}", None), ("GET", "/ai/messages", None),
    ("POST", "/ai/chat", {"message": "oi"}), ("DELETE", "/ai/pending", None), ("POST", "/auth/link-code", None),
    ("POST", "/billing/portal", None), ("GET", "/billing/subscription", None),
    ("POST", "/billing/change-plan", {"plan": "pro"}), ("POST", "/billing/cancel-change", None),
    ("POST", "/api/affiliate/payout", {"pix_key": "x"}), ("POST", "/api/push/register", {"token": "t"}),
]


def _chama(client, metodo, path, corpo, uid):
    return client.request(metodo, path.format(uid=uid), json=corpo, headers=H)


@pytest.fixture
def portal_espiao(monkeypatch):
    chamadas = []

    async def _portal(*a, **k):
        chamadas.append(a)
        return SimpleNamespace(url="https://billing.stripe.test/sessao")

    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(dashboard, "_create_billing_portal", _portal)
    return chamadas


def test_sem_credencial_bloqueia_e_as_saidas_abrem_e_com_senha_libera(monkeypatch, portal_espiao):
    enviados = []
    monkeypatch.setattr(email_service, "send_password_reset_email",
                        lambda to, url, has_pw: enviados.append((to, url, has_pw)) or True)
    monkeypatch.setattr("core.services.plan_service.dashboard_v2_enabled", lambda *a, **k: True)
    uid, email, client = conta_paga_sem_credencial()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set stripe_customer_id = %s where user_id = %s", (f"cus_{uid}", uid))
        conn.commit()
    db_support.invalidate_auth_user_cache(uid)

    for metodo, path, corpo in _BLOQUEADAS:
        r = _chama(client, metodo, path, corpo, uid)
        assert (r.status_code, r.json().get("detail")) == (403, {"error": "password_required"}), (path, r.text)
    r = client.get("/api/v2/me")
    assert r.status_code == 403 and r.json()["error"]["code"] == "password_required", r.text

    r = client.get("/conta", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("/home"), r.headers
    assert portal_espiao == [], "o portal do Stripe foi criado para a conta sem senha"

    with pytest.raises(WebSocketDisconnect) as fechou:
        with client.websocket_connect(f"/ws/{uid}") as ws:
            ws.receive_json()
    assert fechou.value.code == 4403

    # As saídas continuam abertas para a MESMA conta.
    assert client.get("/auth/me").status_code == 200
    r = client.patch(f"/settings/{uid}/security/contact", json={"display_name": "Fulana"}, headers=H)
    assert r.status_code == 200, r.text
    r = client.post(f"/settings/{uid}/password-reset", headers=H)
    assert r.status_code == 200 and enviados and enviados[0][0] == email and enviados[0][2] is False, r.text

    # Controle positivo: com senha, as mesmas rotas deixam de dar password_required.
    _com_senha(uid)
    assert client.get(f"/data/{uid}").status_code == 200
    assert client.get("/ai/messages").status_code == 200
    for metodo, path, corpo in _BLOQUEADAS:
        r = _chama(client, metodo, path, corpo, uid)
        assert "password_required" not in r.text, (path, r.status_code, r.text[:200])
    r = client.get("/conta", follow_redirects=False)
    assert r.headers["location"] == "https://billing.stripe.test/sessao"
    assert portal_espiao[-1][2:] == ("/settings", "/conta")
    with client.websocket_connect(f"/ws/{uid}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
    assert client.post("/auth/logout", headers=H).status_code == 200


# ── 5. a conversa inteira ───────────────────────────────────────────────────

def test_quiz_webhook_senha_login(monkeypatch):
    from test_billing_webhook_lifecycle import _FakeStripe, _fake_sub, _post

    tokens = []
    monkeypatch.setattr(email_service, "send_password_reset_email",
                        lambda to, url, has_pw: tokens.append(url.split("#token=", 1)[1]) or True)
    email, client = _email(), _navegador()
    r = _conta_quiz(client, email)
    assert r.status_code == 200, r.text
    uid = int(r.json()["user_id"])

    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    fake = _FakeStripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    evento = {"type": "checkout.session.completed", "id": f"evt_cs_{uid}", "created": 1_800_000_000,
              "data": {"object": {"metadata": {"finbot_user_id": str(uid)}, "subscription": f"sub_{uid}",
                                  "id": f"cs_{uid}"}}}
    r = _post(TestClient(dashboard.app), fake, evento, subs={f"sub_{uid}": _fake_sub("active")})
    assert r.status_code == 200, r.text

    me = client.get("/auth/me").json()
    assert (me["app_access"], me["needs_plan_selection"], me["precisa_criar_senha"]) == (True, False, True), me
    assert client.get(f"/data/{uid}").json()["detail"] == {"error": "password_required"}

    assert client.post(f"/settings/{uid}/password-reset", headers=H).status_code == 200
    r = client.post("/auth/reset-password", json={"token": tokens[-1], "new_password": "senha-nova-123"}, headers=H)
    assert r.status_code == 200, r.text
    assert client.get("/auth/me").status_code == 401, "o reset devia derrubar a sessão antiga"
    assert client.get(f"/data/{uid}").status_code == 401, "o dashboard_token antigo sobreviveu ao reset"

    novo = _navegador()
    r = novo.post("/auth/login", json={"email": email, "password": "senha-nova-123"}, headers=H)
    assert r.status_code == 200, r.text
    assert novo.get("/auth/me").json()["precisa_criar_senha"] is False
    assert novo.get(f"/data/{uid}").status_code == 200


# ── 7. MFA e 8. gate_onboarding ─────────────────────────────────────────────

def test_convite_do_mfa_fica_calado_sem_credencial_e_volta_com_google():
    uid, email, client = conta_paga_sem_credencial()
    assert client.get("/auth/validate").json()["show_mfa_onboarding"] is False
    assert client.get("/auth/me").json()["show_mfa_onboarding"] is False
    db.link_google_identity(uid, f"g-{uid}", email)
    assert client.get("/auth/validate").json()["show_mfa_onboarding"] is True, "o convite se perdeu"


@pytest.mark.parametrize("rota", ["/home", "/app"])
def test_gate_onboarding_nao_manda_a_conta_sem_senha_ao_wizard(rota):
    uid, _, client = conta_paga_sem_credencial()
    assert db.needs_onboarding(uid)
    r = client.get(rota, follow_redirects=False)
    assert r.status_code == 200 and "criar-senha.js" in r.text, (r.status_code, r.headers.get("location"))
    _com_senha(uid)
    r = client.get(rota, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/onboarding"
