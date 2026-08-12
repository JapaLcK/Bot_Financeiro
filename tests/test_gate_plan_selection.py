"""Gate server-side de escolha de plano (frontend/routes/shared.gate_plan_selection).

Enforcement REAL do funil de cadastro: antes de servir o HTML do dashboard
(home/app/settings), o servidor manda pra /precos quem ainda não escolheu um
plano. Os redirects em JS são só UX; este gate é o que não dá pra burlar.

Convenção de monkeypatch (ver docstring de shared): patchar
`frontend.routes.shared.<nome>` e `core.services.plan_service.needs_plan_selection`.
"""

from fastapi.responses import RedirectResponse

import core.services.plan_service as plan_service
import frontend.routes.shared as shared


class _Req:
    """Request falso: só o que gate_plan_selection toca."""
    def __init__(self, ua=""):
        self.headers = {"user-agent": ua}


def _patch(monkeypatch, *, token="tok", payload=None, needs=True, session=None):
    monkeypatch.setattr(shared, "get_auth_token_from_request", lambda req, creds: token)
    monkeypatch.setattr(shared, "decode_jwt", lambda t: payload)
    monkeypatch.setattr(shared, "get_active_session", lambda jti: session)
    monkeypatch.setattr(plan_service, "needs_plan_selection", lambda uid: needs)


def test_cadastro_sem_plano_redireciona_pra_precos(monkeypatch):
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"


def test_plano_escolhido_serve_a_pagina(monkeypatch):
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False)
    assert shared.gate_plan_selection(_Req()) is None


def test_deslogado_nao_forca_precos(monkeypatch):
    # Sem token válido: deixa o HTML carregar (ele redireciona pro login).
    _patch(monkeypatch, token=None, payload=None, needs=True)
    assert shared.gate_plan_selection(_Req()) is None


def test_app_ios_e_isento(monkeypatch):
    # UA do WebView do app: nunca redireciona pra tela de compra (3.1.1).
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    assert shared.gate_plan_selection(_Req(ua="Mozilla/5.0 PigBankApp/1.2")) is None


def test_sessao_jti_invalida_nao_redireciona(monkeypatch):
    # Token com jti mas sessão revogada: não força /precos (o HTML trata o login).
    _patch(monkeypatch, payload={"type": "auth", "sub": "7", "jti": "x"},
           needs=True, session=None)
    assert shared.gate_plan_selection(_Req()) is None
