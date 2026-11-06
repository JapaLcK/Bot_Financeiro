"""`GET /afiliado` e a saída do portão de plano: quem recebe a página, quem vai para onde.

Sessão real, banco real e o `TestClient` no monólito. Negativos: cada porta redireciona para
o lugar certo, e o afiliado sem credencial não entra em laço. Positivos: afiliado com plano
segue no /painel, e não afiliado sem plano segue na /precos.
"""
import re

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import libera, sessao_de, sql
from conftest import promote_to_pro
from db.affiliates import create_affiliate, get_affiliate_by_user, set_affiliate_status
from frontend.routes.shared import AUTH_COOKIE_NAME, CONTA_COOKIE_NAME, DASHBOARD_COOKIE_NAME, FRONTEND_DIR

UA_APP = "Mozilla/5.0 PigBankApp/1.0"
PRECOS = "/precos?escolha=1"


@pytest.fixture(autouse=True)
def _ninguem_liberado(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")


def conta(uid, *, afiliado=True, plano=False, credencial=True):
    """Conta com sessão real. Sem plano: como o cadastro que nunca escolheu (`test_painel_pagina`)."""
    promote_to_pro(uid)
    db.mark_onboarding_completed(uid)
    s = sessao_de(uid)
    if not plano:
        sql("update auth_accounts set plan='free', plan_selected_at=null where user_id=%s", uid)
    if afiliado:
        create_affiliate(uid)
    if not credencial:
        sql("update auth_accounts set password_hash=null where user_id=%s", uid)
    return s


def get(path, s=None, ua=None):
    client = TestClient(dashboard.app)
    if s:
        client.cookies.set(AUTH_COOKIE_NAME, s["access"])
        client.cookies.set(DASHBOARD_COOKIE_NAME, s["dashboard"])
    return client.get(path, headers={"User-Agent": ua} if ua else {}, follow_redirects=False)


def vai_para(r, destino):
    assert r.status_code == 302, (r.status_code, r.text[:200])
    assert r.headers["location"] == destino


def serve_a_casca(r):
    assert r.status_code == 200, (r.status_code, r.headers.get("location"))
    assert 'id="pigbank-dashboard"' in r.text
    assert r.headers["cache-control"] == "no-store"


def test_sem_sessao_vai_ao_login_com_next():
    vai_para(get("/afiliado"), "/login?next=/afiliado")


def test_nao_afiliado_vai_ao_painel(user_id):
    vai_para(get("/afiliado", conta(user_id, afiliado=False, plano=True)), "/painel")


def test_afiliado_sem_credencial_vai_ao_app(user_id):
    vai_para(get("/afiliado", conta(user_id, plano=True, credencial=False)), "/app")


@pytest.mark.parametrize("afiliado, destino", [(True, "/app"), (False, "/painel")])
def test_conta_sem_credencial_nunca_recebe_a_casca_e_a_api_segue_fechada(user_id, afiliado, destino):
    """A linha `libera` de `test_rotas_senha_obrigatoria`: a rota sai por 302, sem servir a página,
    e quem bloqueia o dado é a API (403 `password_required`, afiliado ou não)."""
    s = conta(user_id, afiliado=afiliado, plano=True, credencial=False)
    vai_para(get("/afiliado", s), destino)
    r = get("/api/affiliate/me", s)
    assert r.status_code == 403 and r.json()["detail"] == {"error": "password_required"}


def test_afiliado_sem_plano_entra_e_recebe_o_marcador_da_conta(user_id):
    r = get("/afiliado", conta(user_id))
    serve_a_casca(r)
    assert f"{CONTA_COOKIE_NAME}={user_id}" in r.headers["set-cookie"]


def test_afiliado_desativado_sem_plano_entra(user_id):
    s = conta(user_id)
    set_affiliate_status(get_affiliate_by_user(user_id)["id"], "disabled")
    serve_a_casca(get("/afiliado", s))


def test_nao_depende_da_chave_do_v2_e_o_ua_do_app_entra(user_id):
    serve_a_casca(get("/afiliado", conta(user_id), ua=UA_APP))


@pytest.mark.parametrize("pagina", ["/painel", "/app"])
def test_afiliado_sem_plano_sai_do_portao_para_a_area(pagina, monkeypatch, user_id):
    s = conta(user_id)
    libera(monkeypatch, user_id)
    vai_para(get(pagina, s), "/afiliado")


def test_webview_do_app_sem_plano_tambem_vai_para_a_area(user_id):
    vai_para(get("/app", conta(user_id), ua=UA_APP), "/afiliado")


@pytest.mark.parametrize("pagina", ["/painel", "/app"])
def test_nao_afiliado_sem_plano_continua_na_precos(pagina, monkeypatch, user_id):
    """Controle positivo do desvio: quem não é afiliado segue o gate como antes."""
    s = conta(user_id, afiliado=False)
    libera(monkeypatch, user_id)
    vai_para(get(pagina, s), PRECOS)


def test_afiliado_com_plano_no_afiliado_vai_ao_painel(user_id):
    """Só quem está sem plano vê a tela solta, com "Ver planos"."""
    vai_para(get("/afiliado", conta(user_id, plano=True)), "/painel#/afiliado")


def test_afiliado_com_plano_segue_no_painel(monkeypatch, user_id):
    """Controle positivo: o desvio só vale quando o gate de plano barraria."""
    s = conta(user_id, plano=True)
    libera(monkeypatch, user_id)
    serve_a_casca(get("/painel", s))


def test_sem_credencial_e_sem_plano_nao_entra_em_laco(user_id):
    """/app → /afiliado → /app seria o laço: sem credencial, o gate manda criar a senha."""
    s = conta(user_id, credencial=False)
    vai_para(get("/app", s), "/recuperar-senha")
    vai_para(get("/afiliado", s), "/app")


def test_retorno_do_checkout_nao_e_desviado(user_id):
    """`?upgrade=success` dispensa o gate: quem acabou de pagar não vai para o /afiliado."""
    r = get("/app?upgrade=success", conta(user_id))
    assert r.headers.get("location") != "/afiliado"


def test_erro_ao_ler_o_afiliado_cai_no_gate(monkeypatch, user_id):
    """Falha aqui não derruba o /app: fica o destino do gate."""
    s = conta(user_id)

    def falha(_uid):
        raise RuntimeError("sem banco")

    monkeypatch.setattr("db.affiliates.get_affiliate_by_user", falha)
    vai_para(get("/app", s), PRECOS)


@pytest.mark.parametrize("funcao", ["get_affiliate_by_user", "conta_sem_credencial"])
def test_afiliado_com_falha_de_leitura_vai_ao_destino_seguro(funcao, monkeypatch, user_id):
    """Falha ao ler o cadastro não vira 500 na rota: vai ao /painel, que decide sozinho."""
    s = conta(user_id)

    def falha(_uid):
        raise RuntimeError("sem banco")

    monkeypatch.setattr(f"frontend.routes.static_pages.{funcao}", falha)
    vai_para(get("/afiliado", s), "/painel")


def test_api_v2_me_sobrevive_a_falha_da_leitura_de_afiliado(monkeypatch, caplog, user_id):
    """É a entrada do painel de todo mundo: falhar aqui só esconde o item, sem derrubar o /me."""
    s = conta(user_id, plano=True)
    libera(monkeypatch, user_id)

    def falha(_uid):
        raise RuntimeError("segredo@example.com")

    monkeypatch.setattr("api.v2.me.get_affiliate_by_user", falha)
    with caplog.at_level("WARNING"):
        r = get("/api/v2/me", s)
    assert r.status_code == 200, r.text
    assert r.json()["afiliado"] is False
    assert "afiliado do /me falhou" in caplog.text and "segredo@example.com" not in caplog.text


def test_texto_da_exclusao_e_o_que_a_tela_reconhece(monkeypatch, user_id):
    """A tela só mostra este 403 pelo prefixo: se o texto mudar no servidor, ela vira "sessão terminou"."""
    s = conta(user_id, plano=True)
    monkeypatch.setattr("db.is_account_scheduled_for_deletion", lambda _uid: {"deletion_scheduled_for": "2026-12-01"})
    r = get("/api/affiliate/me", s)
    assert r.status_code == 403
    fonte = (FRONTEND_DIR.parent / "webapp/src/dashboard/afiliado/api.ts").read_text(encoding="utf-8")
    prefixo = re.search(r'startsWith\("([^"]+)"\)', fonte).group(1)
    assert r.json()["detail"].startswith(prefixo)


def test_limites_do_historico_batem_com_a_rota():
    """A nota "Mostrando as mais recentes" só aparece no corte da API: os dois números têm de ser iguais (§0.7)."""
    rota = (FRONTEND_DIR / "routes/affiliates.py").read_text(encoding="utf-8")
    tela = (FRONTEND_DIR.parent / "webapp/src/dashboard/afiliado/Historico.tsx").read_text(encoding="utf-8")
    assert re.search(r'list_affiliate_commissions, affiliate\["id"\], (\d+)', rota).group(1) == re.search(r"LIMITE_COMISSOES = (\d+)", tela).group(1)
    assert re.search(r'list_affiliate_payouts, affiliate\["id"\], (\d+)', rota).group(1) == re.search(r"LIMITE_SAQUES = (\d+)", tela).group(1)
    app = (FRONTEND_DIR / "dashboard.js").read_text(encoding="utf-8")
    assert re.search(r"AFF_LIMITE_COMISSOES = (\d+), AFF_LIMITE_SAQUES = (\d+)", app).groups() == (
        re.search(r"LIMITE_COMISSOES = (\d+)", tela).group(1), re.search(r"LIMITE_SAQUES = (\d+)", tela).group(1))


def test_robots_nao_indexa_a_area():
    assert "Disallow: /afiliado" in TestClient(dashboard.app).get("/robots.txt").text
