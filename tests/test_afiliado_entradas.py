"""Toda entrada que leva conta sem plano à /precos leva o afiliado ao /afiliado, pela regra única
(`frontend/routes/shared.py::destino_sem_plano`), pelo monólito real: login por senha, Google,
link mágico `/d/{code}`, sessão viva e WebView.

Negativos: não afiliado segue na /precos, conta sem credencial não entra em laço, e `next`
válido que não seja `/afiliado` é respeitado. Controle da regra: `test_regra_e_uma_so`.
"""
import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import db
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, google_de_mentira, libera, login_google, sessao_de, sql
from db.affiliates import create_affiliate
from db.reports import register_auth_user
from frontend.routes.shared import destino_sem_plano
from test_pagina_afiliado import _ninguem_liberado, conta, get, vai_para  # noqa: F401 (o primeiro é autouse)

SENHA = "SenhaForte!2026x"
UA_APP = "Mozilla/5.0 PigBankApp/1.0"
PRECOS = "/precos?escolha=1"
SITE = dashboard.DASHBOARD_URL


def com_senha(*, afiliado=True, plano=False, direito_perdido=False):
    """Conta de verdade, com senha. `direito_perdido`: já escolheu plano e perdeu o acesso (a 2ª perna do gate)."""
    email = f"afil-{uuid.uuid4().hex[:10]}@example.com"
    uid = register_auth_user(email, SENHA)["user_id"]
    if plano:
        sql("update auth_accounts set plan='pro_max', plan_expires_at=null, plan_selected_at=now() where user_id=%s", uid)
        db.mark_onboarding_completed(uid)
    elif direito_perdido:
        sql("update auth_accounts set plan='free', plan_selected_at=now() where user_id=%s", uid)
    else:
        sql("update auth_accounts set plan='free', plan_selected_at=null where user_id=%s", uid)
    if afiliado:
        create_affiliate(uid)
    return uid, email


def login(email, ua=None):
    client = TestClient(dashboard.app)
    headers = {**csrf(client), **({"User-Agent": ua} if ua else {})}
    r = client.post("/auth/login", json={"email": email, "password": SENHA}, headers=headers)
    assert r.status_code == 200, r.text
    return client, r.json()["dashboard_url"]


# ── login por senha ─────────────────────────────────────────────────────────

def test_login_de_afiliado_sem_plano_vai_a_area_e_ela_abre():
    _, email = com_senha()
    client, destino = login(email)
    assert destino == f"{SITE}/afiliado"
    assert client.get("/afiliado", follow_redirects=False).status_code == 200


def test_login_de_afiliado_que_perdeu_o_direito_vai_a_area():
    """A 2ª perna do `_post_login_url`: já escolheu plano, o acesso acabou."""
    _, email = com_senha(direito_perdido=True)
    assert login(email)[1] == f"{SITE}/afiliado"


def test_login_de_nao_afiliado_sem_plano_segue_na_precos():
    _, email = com_senha(afiliado=False)
    assert login(email)[1] == f"{SITE}{PRECOS}"


def test_login_de_afiliado_com_plano_vai_ao_painel(monkeypatch):
    uid, email = com_senha(plano=True)
    libera(monkeypatch, uid)
    assert login(email)[1] == f"{SITE}/painel"


def test_login_no_webview_vai_a_area():
    _, email = com_senha()
    assert login(email, ua=UA_APP)[1] == f"{SITE}/afiliado"


# ── a regra ─────────────────────────────────────────────────────────────────

def test_regra_e_uma_so(user_id):
    conta(user_id)
    assert destino_sem_plano(user_id) == "/afiliado"
    assert dashboard._post_login_url(user_id) == f"{SITE}/afiliado"


def test_regra_afiliado_sem_credencial_vai_criar_a_senha(user_id):
    conta(user_id, credencial=False)
    assert destino_sem_plano(user_id) == "/recuperar-senha"
    assert dashboard._post_login_url(user_id) == f"{SITE}/recuperar-senha"


def test_regra_nao_afiliado_sem_credencial_fica_na_precos(user_id):
    conta(user_id, afiliado=False, credencial=False)
    assert destino_sem_plano(user_id) == PRECOS


def test_afiliado_sem_senha_chega_ao_saldo_pela_recuperacao():
    """O caminho inteiro: a API fica em 403 até a senha existir, e o reset é o que a cria."""
    uid, email = com_senha()
    sql("update auth_accounts set password_hash=null where user_id=%s", uid)
    s = sessao_de(uid)
    vai_para(get("/app", s), "/recuperar-senha")
    assert get("/recuperar-senha", s).status_code == 200
    r = get("/api/affiliate/me", s)
    assert r.status_code == 403 and r.json()["detail"] == {"error": "password_required"}

    assert db.consume_password_reset_token(db.create_password_reset_token(email), "OutraSenha!2026y") == uid
    db_support.invalidate_auth_user_cache()
    vai_para(get("/app", s), "/afiliado")
    assert get("/api/affiliate/me", s).status_code == 200


def test_regra_falha_cai_na_precos(monkeypatch, user_id):
    conta(user_id)

    def falha(_uid):
        raise RuntimeError("sem banco")

    monkeypatch.setattr("db.affiliates.get_affiliate_by_user", falha)
    assert destino_sem_plano(user_id) == PRECOS


# ── gate de página: todas as que o chamam ───────────────────────────────────

PAGINAS = ["/app", "/painel", "/home", "/settings", "/onboarding"]


@pytest.mark.parametrize("pagina", PAGINAS)
def test_paginas_mandam_afiliado_sem_plano_a_area(pagina, monkeypatch, user_id):
    s = conta(user_id)
    libera(monkeypatch, user_id)
    vai_para(get(pagina, s), "/afiliado")


@pytest.mark.parametrize("pagina", PAGINAS)
def test_paginas_mandam_nao_afiliado_a_precos(pagina, monkeypatch, user_id):
    s = conta(user_id, afiliado=False)
    libera(monkeypatch, user_id)
    vai_para(get(pagina, s), PRECOS)


@pytest.mark.parametrize("pagina", ["/app", "/painel", "/home", "/onboarding"])
def test_paginas_mandam_afiliado_que_perdeu_o_direito_a_area(pagina, monkeypatch, user_id):
    """A 2ª perna do gate: já escolheu plano e o acesso acabou."""
    s = conta(user_id)
    sql("update auth_accounts set plan='free', plan_selected_at=now() where user_id=%s", user_id)
    libera(monkeypatch, user_id)
    vai_para(get(pagina, s), "/afiliado")


def test_settings_segue_sendo_saida_de_quem_perdeu_o_direito(user_id):
    """`exige_direito=False`: só a perna da escolha desvia; quem perdeu o direito entra na /settings."""
    s = conta(user_id)
    sql("update auth_accounts set plan='free', plan_selected_at=now() where user_id=%s", user_id)
    assert get("/settings", s).status_code == 200


# ── Google ──────────────────────────────────────────────────────────────────

def test_google_de_afiliado_sem_plano_vai_a_area(monkeypatch):
    _, email = com_senha()
    google_de_mentira(monkeypatch, email)
    assert login_google(0)[1].headers["location"] == f"{SITE}/afiliado"


def test_google_de_nao_afiliado_sem_plano_segue_na_precos(monkeypatch):
    _, email = com_senha(afiliado=False)
    google_de_mentira(monkeypatch, email)
    assert login_google(0)[1].headers["location"] == f"{SITE}{PRECOS}"


def test_google_com_compra_em_andamento_respeita_o_next(monkeypatch):
    """`next` válido vence: quem estava comprando continua a compra, mesmo sendo afiliado."""
    _, email = com_senha()
    google_de_mentira(monkeypatch, email)
    client = TestClient(dashboard.app)
    inicio = client.get(f"/auth/google/start?next={dashboard.GOOGLE_OAUTH_PURCHASE_CONTINUE_URL}", follow_redirects=False)
    state = parse_qs(urlparse(inicio.headers["location"]).query)["state"][0]
    r = client.get("/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.headers["location"] == dashboard.GOOGLE_OAUTH_PURCHASE_CONTINUE_URL


# ── link mágico ─────────────────────────────────────────────────────────────

def link(uid, query="", ua=None):
    """Abre o `/d/{code}` real e segue os redirecionamentos internos: devolve o caminho final."""
    client = TestClient(dashboard.app)
    primeiro = client.get(f"/d/{db.create_dashboard_session(uid)}{query}", headers={"User-Agent": ua} if ua else {}, follow_redirects=False)
    assert primeiro.status_code == 302, primeiro.text
    r = client.get(primeiro.headers["location"], headers={"User-Agent": ua} if ua else {}, follow_redirects=True)
    return primeiro.headers["location"], r.url.path, r.status_code


def test_d_sem_next_de_afiliado_sem_plano_chega_a_area(user_id):
    conta(user_id)
    assert link(user_id)[1:] == ("/afiliado", 200)


def test_d_com_next_afiliado_chega_a_area(user_id):
    conta(user_id)
    assert link(user_id, "?next=/afiliado") == ("/afiliado", "/afiliado", 200)


def test_d_no_webview_chega_a_area(user_id):
    conta(user_id)
    assert link(user_id, ua=UA_APP)[1:] == ("/afiliado", 200)


def test_d_com_next_precos_respeita_o_next(user_id):
    """O "assinar" do bot usa `next=/precos`: o afiliado que quer assinar não é desviado."""
    conta(user_id)
    assert link(user_id, "?next=/precos")[:2] == ("/precos", "/precos")


def test_d_com_next_home_cai_na_area_pelo_gate(user_id):
    conta(user_id)
    assert link(user_id, "?next=/home")[1:] == ("/afiliado", 200)


def test_d_de_nao_afiliado_sem_plano_segue_na_precos(user_id):
    conta(user_id, afiliado=False)
    assert link(user_id)[1] == "/precos"


def test_d_com_next_fora_da_allowlist_cai_no_padrao(user_id):
    conta(user_id)
    assert link(user_id, "?next=/qualquer")[1:] == ("/afiliado", 200)


# ── laço ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pagina", ["/afiliado", *PAGINAS])
@pytest.mark.parametrize("afiliado", [True, False], ids=["afiliado", "nao_afiliado"])
@pytest.mark.parametrize("plano", [True, False], ids=["com_plano", "sem_plano"])
@pytest.mark.parametrize("credencial", [True, False], ids=["com_senha", "sem_senha"])
@pytest.mark.parametrize("v2", [True, False], ids=["com_chave", "sem_chave"])
def test_nenhuma_combinacao_entra_em_laco(pagina, afiliado, plano, credencial, v2, monkeypatch, user_id):
    """O `TestClient` estoura em TooManyRedirects: a combinação inteira, não só os casos pensados."""
    s = conta(user_id, afiliado=afiliado, plano=plano, credencial=credencial)
    if v2:
        libera(monkeypatch, user_id)
    client = TestClient(dashboard.app, follow_redirects=True)
    client.cookies.set("auth_token", s["access"])
    client.cookies.set("dashboard_token", s["dashboard"])
    assert client.get(pagina).status_code == 200
