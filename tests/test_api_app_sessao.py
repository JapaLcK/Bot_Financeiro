"""Namespace app pelo monólito, sessão/banco/CSRF reais e coorte web preservada."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de, sql
from api.nativo.app import app, usuario_do_app
from api.v2.sessao import usuario_atual
from conftest import em_carencia, usuario_pagante
from core.sessions import revoke_session


@pytest.fixture(autouse=True)
def sem_beta(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")


def completo(uid):
    sql("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", uid)
    return uid


def cliente(uid):
    c = TestClient(dashboard.app)
    c.headers["Authorization"] = f"Bearer {sessao_de(uid)['access']}"
    return c


def test_assinante_sem_beta_le_e_escreve_perfil_sem_cookie():
    uid = completo(usuario_pagante())
    c = cliente(uid)
    assert not c.cookies
    assert c.get("/api/app/me").json() == {"plan_tier": "plus"}
    # TestClient guarda o Set-Cookie de CSRF do pai. O app usa credentials:omit
    # e não envia essa jar; não simular aqui um navegador com cookie.
    c.cookies.clear()
    assert c.put("/api/app/perfil", json={"perfil": "investir"}).json() == {"perfil": "investir"}
    assert c.get("/api/app/perfil").json() == {"perfil": "investir"}
    r = c.get("/api/v2/me")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "dashboard_v2_disabled")


@pytest.mark.parametrize("estado,code,status", [
    ("free", "subscription_required", 402),
    ("sem_escolha", "plan_selection_required", 402),
    ("expirado", "subscription_required", 402),
    ("exclusao", "forbidden", 403),
    ("sem_banco", "open_finance_onboarding_required", 403),
])
def test_guardas_servidor_nao_dependem_de_header(estado, code, status):
    uid = completo(usuario_pagante())
    agora = datetime.now(timezone.utc)
    if estado == "free":
        sql("update auth_accounts set plan='free', plan_selected_at=now() where user_id=%s", uid)
    elif estado == "sem_escolha":
        sql("update auth_accounts set plan='free', plan_selected_at=null where user_id=%s", uid)
    elif estado == "expirado":
        sql("update auth_accounts set plan_expires_at=%s, past_due_since=null,"
            " plan_selected_at=now() where user_id=%s",
            agora - timedelta(days=30), uid)
    elif estado == "exclusao":
        sql("update auth_accounts set deletion_status='scheduled', deletion_scheduled_for=%s"
            " where user_id=%s", agora + timedelta(days=7), uid)
    else:
        sql("update auth_accounts set open_finance_onboarding_completed_at=null where user_id=%s", uid)
    r = cliente(uid).get("/api/app/me", headers={"X-PigBank-Client": "app",
                                               "User-Agent": "PigBankApp/1.0"})
    assert (r.status_code, r.json()["error"]["code"]) == (status, code), r.text


def test_carencia_real_abre_e_marco_sem_banco_nao_e_revogado():
    uid = usuario_pagante()
    em_carencia(uid)
    completo(uid)
    r = cliente(uid).get("/api/app/me")
    assert (r.status_code, r.json()) == (200, {"plan_tier": "free"})


def test_banco_sincronizado_no_site_promove_marco_e_remocao_nao_revoga():
    from tests._patrimonio_helpers import conexao, conta, q

    uid = usuario_pagante()
    cid = conexao(uid, f"site-{uid}")
    conta(cid, "conta-site", "100")
    c = cliente(uid)
    assert c.get("/api/app/me").status_code == 200
    assert q("select open_finance_onboarding_completed_at as marco from auth_accounts"
             " where user_id=%s", (uid,))["marco"] is not None
    q("delete from open_finance_connections where user_id=%s and id=%s", (uid, cid))
    assert c.get("/api/app/me").status_code == 200


def test_bearer_formulario_nao_recebe_isencao_csrf_nativa():
    c = cliente(completo(usuario_pagante()))
    r = c.put("/api/app/perfil", data={"perfil": "padrao"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]


@pytest.mark.parametrize("path", ["/me", "/perfil", "/patrimonio", "/rendimento", "/mes-detalhes"])
def test_header_nativo_sem_sessao_nao_concede_acesso(path):
    r = TestClient(dashboard.app).get("/api/app" + path, headers={"X-PigBank-Client": "app"})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "unauthenticated")


def test_revogacao_refecha_namespace_app():
    uid = completo(usuario_pagante())
    s = sessao_de(uid)
    assert revoke_session(uid, s["jti"])
    r = TestClient(dashboard.app).get("/api/app/me", headers={"Authorization": f"Bearer {s['access']}"})
    assert (r.status_code, r.json()["error"]["code"]) == (401, "unauthenticated")


def test_perfil_isolado_e_cookie_continua_exigindo_csrf():
    a, b = completo(usuario_pagante()), completo(usuario_pagante())
    ca, cb = cliente(a), cliente(b)
    assert ca.put("/api/app/perfil", json={"perfil": "investir"}).status_code == 200
    assert cb.get("/api/app/perfil", params={"user_id": a, "uid": a}).json() == {"perfil": None}
    cb.cookies.clear()
    assert cb.put("/api/app/perfil", json={"perfil": "controlar"}, params={"user_id": a}).status_code == 200
    assert ca.get("/api/app/perfil").json() == {"perfil": "investir"}
    cookie = TestClient(dashboard.app)
    cookie.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(a)["dashboard"])
    r = cookie.put("/api/app/perfil", json={"perfil": "padrao"})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    assert ca.get("/api/app/perfil").json() == {"perfil": "investir"}


def test_inventario_namespace_app_tem_modelo_isolamento_e_dependencia(monkeypatch):
    import test_api_v2_rotas as portao

    monkeypatch.setattr(portao, "PREFIXO", "/api/app")
    assert portao.violacoes(dashboard.app, app) == []
    assert app.dependency_overrides[usuario_atual] is usuario_do_app
    assert "/eventos" not in app.openapi()["paths"]
