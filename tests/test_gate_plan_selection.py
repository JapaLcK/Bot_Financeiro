"""Gate server-side de escolha de plano (frontend/routes/shared.gate_plan_selection).

Enforcement REAL do funil de cadastro: antes de servir o HTML do dashboard
(home/app/settings), o servidor manda pra /precos quem ainda não escolheu um
plano. Os redirects em JS são só UX; este gate é o que não dá pra burlar.

Convenção de monkeypatch (ver docstring de shared): patchar
`frontend.routes.shared.<nome>` e `core.services.plan_service.needs_plan_selection`.
"""

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

import core.services.plan_service as plan_service
import frontend.routes.shared as shared


class _Req:
    """Request falso: só o que gate_plan_selection/_resolve_page_user_id tocam."""
    def __init__(self, ua=""):
        self.headers = {"user-agent": ua}
        self.cookies = {}


def _patch(monkeypatch, *, token="tok", payload=None, needs=True, session=None,
           dashboard_uid=None):
    monkeypatch.setattr(shared, "get_auth_token_from_request", lambda req, creds: token)
    monkeypatch.setattr(shared, "decode_jwt", lambda t: payload)
    monkeypatch.setattr(shared, "get_active_session", lambda jti: session)
    monkeypatch.setattr(plan_service, "needs_plan_selection", lambda uid: needs)

    # Fallback do dashboard_token (12h): resolve o user OU levanta 401.
    def _fake_dash(req):
        if dashboard_uid is None:
            raise HTTPException(status_code=401, detail="x")
        return dashboard_uid
    monkeypatch.setattr(shared, "resolve_dashboard_user_id", _fake_dash)


def test_cadastro_sem_plano_redireciona_pra_precos(monkeypatch):
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_plano_escolhido_serve_a_pagina(monkeypatch):
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False)
    assert shared.gate_plan_selection(_Req()) is None


def test_deslogado_nao_forca_precos(monkeypatch):
    # Sem token válido em nenhum cookie: deixa o HTML carregar (ele vai pro login).
    _patch(monkeypatch, token=None, payload=None, needs=True, dashboard_uid=None)
    assert shared.gate_plan_selection(_Req()) is None


def test_app_ios_e_isento(monkeypatch):
    # UA do WebView do app: nunca redireciona pra tela de compra (3.1.1).
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    assert shared.gate_plan_selection(_Req(ua="Mozilla/5.0 PigBankApp/1.2")) is None


def test_dashboard_token_vale_mesmo_com_access_expirado(monkeypatch):
    # auth_token expirado (decode_jwt → None) mas dashboard_token (12h) válido:
    # tem que resolver o user e AINDA aplicar o gate (bug pego pelo Codex).
    _patch(monkeypatch, token=None, payload=None, needs=True, dashboard_uid=7)
    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_jti_invalido_cai_no_dashboard_token(monkeypatch):
    # auth_token com jti mas sessão revogada → tenta o dashboard_token; se ele
    # também não vale, trata como deslogado (serve, sem forçar /precos).
    _patch(monkeypatch, payload={"type": "auth", "sub": "7", "jti": "x"},
           needs=True, session=None, dashboard_uid=None)
    assert shared.gate_plan_selection(_Req()) is None


# ── Detecção da origem do cadastro (signup_source_from_request) ──────────────

def test_signup_source_web_por_padrao():
    assert shared.signup_source_from_request(_Req(ua="Mozilla/5.0")) == "web"


def test_signup_source_app_pela_ua():
    assert shared.signup_source_from_request(
        _Req(ua="Mozilla/5.0 PigBankApp/1.2")) == "app"


def test_signup_source_google_web_e_app():
    assert shared.signup_source_from_request(
        _Req(ua="Mozilla/5.0"), google=True) == "google"
    assert shared.signup_source_from_request(
        _Req(ua="PigBankApp/1.0"), google=True) == "google_app"
