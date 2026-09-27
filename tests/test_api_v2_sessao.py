"""`usuario_atual`: quem entra na /api/v2 e quem não, pelo monólito real.

Sessão real (`_issue_session_token`, a mesma do login), banco real, e o
`TestClient` no app do monólito, para a requisição atravessar o mount e os
middlewares do pai. Negativos: cada guarda da dependência recusa no envelope.
Positivos: os dois canais de credencial, a carência e as duas formas de
liberação passam — sem eles o grupo ficaria verde num `usuario_atual` que
recusa todo mundo.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import db
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import req
from conftest import em_carencia, promote_to_pro
from core.sessions import revoke_session
from frontend.routes.shared import WWW_AUTHENTICATE_401

ME = "/api/v2/me"
UA_APP = "Mozilla/5.0 PigBankApp/1.0"


@pytest.fixture(autouse=True)
def _ninguem_liberado(monkeypatch):
    """Lista vazia por padrão: cada teste libera quem precisa, e nenhum herda
    os e-mails de teste do default."""
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")


def libera(monkeypatch, uid):
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(uid))


def sessao(uid):
    email = db.get_auth_user(uid)["email"]
    access, jti, refresh = dashboard._issue_session_token(uid, email, req("/auth/login"))
    dash = dashboard.make_dashboard_token(uid, hours=dashboard.DASHBOARD_SESSION_HOURS, jti=jti)
    return {"access": access, "jti": jti, "refresh": refresh, "dashboard": dash}


def get_com_cookie(token, **headers):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, token)
    return client.get(ME, headers=headers)


def get_com_bearer(token, **headers):
    return TestClient(dashboard.app).get(ME, headers={"Authorization": f"Bearer {token}", **headers})


def sql(query, *args):
    with db.get_conn() as conn:
        conn.execute(query, args)
        conn.commit()
    db_support.invalidate_auth_user_cache()


def assert_erro(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.text


# ── Negativos ────────────────────────────────────────────────────────────────

def test_sem_credencial_401_com_www_authenticate():
    r = TestClient(dashboard.app).get(ME)
    assert_erro(r, 401, "unauthenticated")
    # Sem este header o auth-refresh.js não renova e o usuário cai no login.
    assert r.headers.get("www-authenticate") == WWW_AUTHENTICATE_401["WWW-Authenticate"]


def test_sessao_revogada_401(monkeypatch, user_id):
    promote_to_pro(user_id)
    libera(monkeypatch, user_id)
    s = sessao(user_id)
    assert revoke_session(user_id, s["jti"])
    assert_erro(get_com_cookie(s["dashboard"]), 401, "unauthenticated")
    assert_erro(get_com_bearer(s["access"]), 401, "unauthenticated")


def test_refresh_token_no_bearer_401(monkeypatch, user_id):
    promote_to_pro(user_id)
    libera(monkeypatch, user_id)
    s = sessao(user_id)
    assert s["refresh"].startswith("rt_")
    assert_erro(get_com_bearer(s["refresh"]), 401, "unauthenticated")


def test_free_sem_carencia_402_subscription_required(monkeypatch, user_id):
    promote_to_pro(user_id)
    sql("update auth_accounts set plan='free' where user_id=%s", user_id)
    db.mark_plan_selected(user_id)
    db_support.invalidate_auth_user_cache(user_id)
    libera(monkeypatch, user_id)
    assert_erro(get_com_cookie(sessao(user_id)["dashboard"]), 402, "subscription_required")


def test_sem_escolha_de_plano_402_plan_selection_required(monkeypatch, user_id):
    promote_to_pro(user_id)
    sql("update auth_accounts set plan='free', plan_selected_at=null where user_id=%s", user_id)
    libera(monkeypatch, user_id)
    assert_erro(get_com_cookie(sessao(user_id)["dashboard"]), 402, "plan_selection_required")


def test_conta_agendada_para_exclusao_403(monkeypatch, user_id):
    promote_to_pro(user_id)
    sql("update auth_accounts set deletion_status='scheduled', deletion_scheduled_for=%s"
        " where user_id=%s", datetime.now(timezone.utc) + timedelta(days=7), user_id)
    libera(monkeypatch, user_id)
    assert_erro(get_com_cookie(sessao(user_id)["dashboard"]), 403, "forbidden")


def test_fora_da_lista_404_dashboard_v2_disabled(user_id):
    promote_to_pro(user_id)
    assert_erro(get_com_cookie(sessao(user_id)["dashboard"]), 404, "dashboard_v2_disabled")


def test_user_agent_do_app_sem_sessao_401():
    """O UA só escolhe tela: não abre a API para ninguém."""
    assert_erro(TestClient(dashboard.app).get(ME, headers={"User-Agent": UA_APP}),
                401, "unauthenticated")


# ── Positivos ────────────────────────────────────────────────────────────────

def test_cookie_dashboard_token_200(monkeypatch, user_id):
    promote_to_pro(user_id)
    libera(monkeypatch, user_id)
    r = get_com_cookie(sessao(user_id)["dashboard"])
    assert r.status_code == 200, r.text
    assert r.json() == {"plan_tier": "plus"}


def test_bearer_access_200(monkeypatch, user_id):
    promote_to_pro(user_id, plan="pro_max")
    libera(monkeypatch, user_id)
    r = get_com_bearer(sessao(user_id)["access"])
    assert r.status_code == 200, r.text
    assert r.json() == {"plan_tier": "pro"}


def test_carencia_200_com_tier_free(monkeypatch, user_id):
    em_carencia(user_id)
    libera(monkeypatch, user_id)
    r = get_com_cookie(sessao(user_id)["dashboard"])
    assert r.status_code == 200, r.text
    assert r.json() == {"plan_tier": "free"}


def test_liberado_por_email_em_caixa_diferente_200(monkeypatch, user_id):
    promote_to_pro(user_id)
    email = db.get_auth_user(user_id)["email"]
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", f"outro@x.com, {email.upper()}")
    r = get_com_cookie(sessao(user_id)["dashboard"])
    assert r.status_code == 200, r.text


def test_user_agent_do_app_com_sessao_200(monkeypatch, user_id):
    """O UA também não fecha a API: o app novo (Expo) passa pela mesma chave."""
    promote_to_pro(user_id)
    libera(monkeypatch, user_id)
    r = get_com_cookie(sessao(user_id)["dashboard"], **{"User-Agent": UA_APP})
    assert r.status_code == 200, r.text


# ── Unidade: a chave `dashboard_v2_enabled` ──────────────────────────────────

def test_chave_sem_env_libera_so_os_emails_de_teste(monkeypatch):
    from core.services.plan_service import _AGENTS_BETA_EMAILS_DEFAULT, dashboard_v2_enabled

    monkeypatch.delenv("DASHBOARD_V2_BETA_EMAILS")
    monkeypatch.delenv("DASHBOARD_V2_BETA_USER_IDS")
    teste = sorted(_AGENTS_BETA_EMAILS_DEFAULT)[0]
    assert dashboard_v2_enabled(1, teste.upper())
    assert not dashboard_v2_enabled(1, "qualquer@x.com")


def test_chave_com_env_vazia_nao_libera_ninguem(monkeypatch):
    from core.services.plan_service import _AGENTS_BETA_EMAILS_DEFAULT, dashboard_v2_enabled

    for email in _AGENTS_BETA_EMAILS_DEFAULT:
        assert not dashboard_v2_enabled(1, email)


def test_chave_por_id_e_por_email_em_caixa_diferente(monkeypatch):
    from core.services.plan_service import dashboard_v2_enabled

    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", " 7 , 8 ")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "Fulana@Exemplo.com")
    assert dashboard_v2_enabled(8, "x@y.com")
    assert not dashboard_v2_enabled(9, "x@y.com")
    assert dashboard_v2_enabled(9, " fulana@EXEMPLO.com ")
