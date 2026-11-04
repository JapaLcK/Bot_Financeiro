"""Contrato: as credenciais de sessão de um navegador concordam sobre a conta.

Dois cookies de sessão válidos (`auth_token` e `dashboard_token`) de contas
diferentes não são sessão nenhuma: as rotas autenticadas respondem 401 renovável,
sem dado, e não escrevem nada; as páginas mandam ao login; o WebSocket fecha.
Positivos: jarro coerente (mesmo de duas sessões), um cookie só, token vencido,
o Bearer do app, a saída e a entrada seguem funcionando. Banco e monólito reais.
"""
import time
import uuid
from types import SimpleNamespace

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, limpa_rate_limits, sessao_de
from conftest import promote_to_pro
from core.sessions import get_active_session, revoke_session
from db.connection import get_conn
from db.recurring import list_recurring_expenses
from frontend.routes.shared import AUTH_COOKIE_NAME, DASHBOARD_COOKIE_NAME, JWT_SECRET

SENHA = "senha-forte-123"
DOMINIO = "testserver.local"  # o domínio que o jar do TestClient dá ao cookie do servidor
ESCRITA = "/api/v2/gastos-fixos"
GASTO = {"nome": "Aluguel", "valor": "10.00", "dia": 5, "categoria": "moradia", "forma": "conta"}
LEITURAS = ["/auth/me", "/auth/mfa/status", "/auth/dashboard-profile", "/auth/validate",
            "/api/v2/me", "/billing/subscription"]


def _conta():
    email = f"div-{uuid.uuid4().hex[:10]}@example.com"
    uid = int(db.register_auth_user(email, SENHA)["user_id"])
    promote_to_pro(uid)
    db.mark_onboarding_completed(uid)
    return SimpleNamespace(uid=uid, email=email, s=sessao_de(uid))


@pytest.fixture
def ab(monkeypatch):
    """Duas contas com senha, plano e o /painel liberado."""
    a, b = _conta(), _conta()
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a.uid},{b.uid}")
    return a, b


def jarro(*, auth=None, refresh=None, dash=None):
    """Cliente com os cookies no domínio e no caminho em que o servidor os grava:
    a resposta que os troca ou os expira age sobre eles, como num navegador."""
    c = TestClient(dashboard.app)
    for nome, valor, caminho in ((AUTH_COOKIE_NAME, auth, "/"),
                                 (dashboard.REFRESH_COOKIE_NAME, refresh, dashboard.REFRESH_COOKIE_PATH),
                                 (DASHBOARD_COOKIE_NAME, dash, "/")):
        if valor:
            c.cookies.delete(nome)
            c.cookies.set(nome, valor, domain=DOMINIO, path=caminho)
    return c


def misto(a, b):
    """auth e refresh da conta A, dashboard da conta B."""
    return jarro(auth=a.s["access"], refresh=a.s["refresh"], dash=b.s["dashboard"])


def escreve(c, **headers):
    return c.post(ESCRITA, json=GASTO, headers={**csrf(c), **headers})


def gravados(*contas):
    return [len(list_recurring_expenses(x.uid)) for x in contas]


def um(sql, *args):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchone().values())[0]


def sem_dado(r, *contas):
    return not any(t in r.text for x in contas for t in (str(x.uid), x.email))


# ── Negativos ────────────────────────────────────────────────────────────────

def test_n1_rotas_autenticadas_respondem_401_renovavel_sem_dado(ab):
    a, b = ab
    c = misto(a, b)
    for rota in LEITURAS + [f"/settings/{b.uid}/security"]:
        r = c.get(rota)
        assert r.status_code == 401, (rota, r.status_code, r.text[:200])
        assert r.headers.get("www-authenticate"), rota
        assert "x-pb-conta" not in r.headers, rota
        assert sem_dado(r, a, b), (rota, r.text[:200])


def test_n2_mfa_setup_nao_toca_conta_nenhuma(ab):
    a, b = ab
    c = misto(a, b)
    r = c.post("/auth/mfa/setup", json={"password": SENHA}, headers=csrf(c))
    assert r.status_code == 401, r.text
    assert um("select count(*) from user_mfa where user_id = any(%s)", [a.uid, b.uid]) == 0


def test_n3_link_code_nao_cria_codigo(ab):
    a, b = ab
    antes = um("select count(*) from link_codes where user_id = any(%s)", [a.uid, b.uid])
    c = misto(a, b)
    r = c.post("/auth/link-code", headers=csrf(c))
    assert r.status_code == 401, r.text
    assert um("select count(*) from link_codes where user_id = any(%s)", [a.uid, b.uid]) == antes


def test_n4_onboarding_seen_nao_grava(ab):
    a, b = ab
    c = misto(a, b)
    r = c.post("/auth/mfa/onboarding-seen", headers=csrf(c))
    assert r.status_code == 401, r.text
    assert um("select count(*) from auth_accounts where user_id = any(%s)"
              " and mfa_onboarding_shown_at is not null", [a.uid, b.uid]) == 0


def test_n5_dashboard_token_nao_grava_cookie(ab):
    a, b = ab
    c = misto(a, b)
    r = c.post("/auth/dashboard-token", headers=csrf(c))
    assert r.status_code == 401, r.text
    assert not any(DASHBOARD_COOKIE_NAME in m for m in r.headers.get_list("set-cookie"))


def test_n6_paginas_mandam_ao_login(ab):
    a, b = ab
    for rota in ("/painel", "/conta"):
        r = misto(a, b).get(rota, follow_redirects=False)
        assert r.status_code == 302 and "/login?next=" in r.headers["location"], (rota, r.status_code)


def test_n7_websocket_fecha_1008_e_o_coerente_abre(ab):
    a, b = ab
    with pytest.raises(WebSocketDisconnect) as fechou:
        with misto(a, b).websocket_connect(f"/ws/{b.uid}") as ws:
            ws.receive_json()
    assert fechou.value.code == 1008
    with jarro(dash=b.s["dashboard"]).websocket_connect(f"/ws/{b.uid}") as ws:  # controle
        assert ws.receive_json()["type"] == "snapshot"


# ── Positivos ────────────────────────────────────────────────────────────────

def test_p1_mesma_conta_nos_dois_cookies_de_sessoes_diferentes(ab):
    a, _ = ab
    outra = sessao_de(a.uid)  # outro login da mesma conta: outro jti
    c = jarro(auth=a.s["access"], dash=outra["dashboard"])
    assert c.get("/auth/me").json()["user_id"] == a.uid
    assert c.get("/api/v2/me").status_code == 200
    r = escreve(c)
    assert r.status_code == 200, r.text
    assert r.headers["x-pb-conta"] == str(a.uid)
    assert gravados(a) == [1]


def test_p2_so_o_auth(ab):
    a, _ = ab
    assert jarro(auth=a.s["access"]).get("/auth/me").json()["user_id"] == a.uid


def test_p3_so_o_dashboard(ab):
    a, _ = ab
    c = jarro(dash=a.s["dashboard"])
    assert c.get("/api/v2/me").status_code == 200
    assert c.get("/auth/dashboard-profile").json()["user_id"] == a.uid


def test_p5_auth_vencido_de_outra_conta_nao_conta(ab):
    a, b = ab
    vencido = pyjwt.encode({"sub": str(a.uid), "email": a.email, "type": "auth",
                            "jti": a.s["jti"], "exp": int(time.time()) - 60}, JWT_SECRET, algorithm="HS256")
    c = jarro(auth=vencido, dash=b.s["dashboard"])
    assert c.get("/auth/dashboard-profile").json()["user_id"] == b.uid
    assert c.get("/api/v2/me").status_code == 200
    assert escreve(c).status_code == 200 and gravados(a, b) == [0, 1]


def test_p6_sair_encerra_as_duas_sessoes(ab):
    a, b = ab
    c = misto(a, b)
    assert c.post("/auth/logout", headers=csrf(c)).status_code == 200
    assert get_active_session(a.s["jti"]) is None
    assert get_active_session(b.s["jti"]) is None


def test_p7_entrar_a_partir_de_um_jarro_misto(ab):
    a, b = ab
    limpa_rate_limits("login", a.email)
    c = misto(a, b)
    r = c.post("/auth/login", json={"email": a.email, "password": SENHA}, headers=csrf(c))
    assert r.status_code == 200, r.text
    for rota in ("/auth/me", "/auth/dashboard-profile", "/auth/validate"):
        assert c.get(rota).json()["user_id"] == a.uid, rota
    assert escreve(c).status_code == 200 and gravados(a, b) == [1, 0]


def test_p8_bearer_vale_com_e_sem_cookies(ab):
    a, b = ab
    bearer = {"Authorization": f"Bearer {a.s['access']}"}
    for c in (TestClient(dashboard.app), misto(a, b)):
        assert c.get("/auth/me", headers=bearer).json()["user_id"] == a.uid
        assert c.get("/api/v2/me", headers=bearer).status_code == 200


def test_p9_link_do_bot_de_conta_sem_email_substitui_a_sessao(ab):
    a, _ = ab
    sem_email = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    db.ensure_user(sem_email)
    c = jarro(auth=a.s["access"], refresh=a.s["refresh"], dash=a.s["dashboard"])
    r = c.get(f"/d/{db.create_dashboard_session(sem_email)}", follow_redirects=False)
    assert r.status_code == 302, r.text
    nomes = {k.name for k in c.cookies.jar}
    assert AUTH_COOKIE_NAME not in nomes and dashboard.REFRESH_COOKIE_NAME not in nomes, nomes
    assert c.get("/auth/me").status_code == 401
    assert get_active_session(a.s["jti"]) is not None  # a sessão anterior não é revogada
    revoke_session(a.uid, a.s["jti"])


def test_p9b_link_do_bot_sem_refresh_nao_entrega_access(ab, monkeypatch):
    """Sem o refresh, o link não entrega o access: só o dashboard da conta nova."""
    import core.refresh_tokens

    def falha(*a, **k):
        raise RuntimeError("banco fora")

    a, _ = ab
    monkeypatch.setattr(core.refresh_tokens, "create_refresh_token", falha)
    c = jarro(auth=a.s["access"], refresh=a.s["refresh"])
    r = c.get(f"/d/{db.create_dashboard_session(a.uid)}", follow_redirects=False)
    assert r.status_code == 302, r.text
    auth = [m for m in r.headers.get_list("set-cookie") if m.startswith(f"{AUTH_COOKIE_NAME}=")]
    assert auth and all('=""' in m or "Max-Age=0" in m for m in auth), auth
    assert c.get("/auth/me").status_code == 401


def test_divergencia_nao_vira_warning(ab, caplog):
    """WARNING é espelhado no banco por request: a divergência registra abaixo disso."""
    a, b = ab
    with caplog.at_level("INFO"):
        assert misto(a, b).get("/api/v2/me").status_code == 401
    registros = [x for x in caplog.records if "sessao_divergente" in x.getMessage()]
    assert registros and all(x.levelname == "INFO" for x in registros), registros


def test_p9c_link_do_bot_de_conta_com_email_entrega_a_sessao_inteira(ab):
    """Controle do p9/p9b: com e-mail e refresh, o link entrega os três cookies da conta nova."""
    a, b = ab
    c = misto(b, b)
    r = c.get(f"/d/{db.create_dashboard_session(a.uid)}", follow_redirects=False)
    assert r.status_code == 302, r.text
    # Pelos cabeçalhos, não pelo jar: o jar do httpx não aplica os Set-Cookie na ordem.
    for nome in (AUTH_COOKIE_NAME, dashboard.REFRESH_COOKIE_NAME):
        enviados = [m for m in r.headers.get_list("set-cookie") if m.startswith(f"{nome}=")]
        assert enviados and not any('=""' in m or "Max-Age=0" in m for m in enviados), (nome, enviados)
    for rota in ("/auth/me", "/auth/dashboard-profile"):
        assert c.get(rota).json()["user_id"] == a.uid, rota
