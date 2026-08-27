"""Gate server-side do wizard de primeira configuração
(frontend/routes/shared.gate_onboarding).

Enforcement REAL da primeira configuração: antes de servir o HTML de /home e
/app, o servidor manda pro /onboarding quem ainda não passou pelo wizard.

Dois invariantes deste gate DIVERGEM do gate de plano vizinho, e é por isso que
eles têm teste próprio aqui — quem "uniformizar" os dois quebra o produto:

  1. o app iOS NÃO é isento. A isenção de lá existe pela diretriz 3.1.1 da App
     Store, que é sobre TELA DE COMPRA; o wizard é saldo, cartão, WhatsApp e
     resumo. Isentar o app faria nenhum usuário de iPhone ver o onboarding.
  2. /settings NÃO leva este gate. O passo do dinheiro manda o usuário pra
     /settings?view=open-finance&onb=1, então gatear lá vira loop de redirect.

Convenção de monkeypatch (ver docstring de shared): patchar
`frontend.routes.shared.<nome>`; `needs_onboarding` é importado dentro da função
(no momento da chamada), então o patch vai no módulo `db`.
"""

import db
import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

import core.services.plan_service as plan_service
import frontend.routes.shared as shared
import frontend.routes.static_pages as static_pages


class _Req:
    """Request falso: só o que gate_onboarding/_resolve_page_user_id tocam."""

    def __init__(self, ua="", query=None):
        self.headers = {"user-agent": ua}
        self.cookies = {}
        self.query_params = query or {}


def _patch(monkeypatch, *, token="tok", payload=None, needs=True,
           session=None, dashboard_uid=None):
    monkeypatch.setattr(shared, "get_auth_token_from_request", lambda req, creds: token)
    monkeypatch.setattr(shared, "decode_jwt", lambda t: payload)
    monkeypatch.setattr(shared, "get_active_session", lambda jti: session)
    monkeypatch.setattr(db, "needs_onboarding", lambda uid: needs)

    def _fake_dash(req):
        if dashboard_uid is None:
            raise HTTPException(status_code=401, detail="x")
        return dashboard_uid

    monkeypatch.setattr(shared, "resolve_dashboard_user_id", _fake_dash)


# ── O gate em si ─────────────────────────────────────────────────────────────

def test_conta_sem_onboarding_vai_pro_wizard(monkeypatch):
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    out = shared.gate_onboarding(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/onboarding"


def test_onboarding_concluido_serve_a_pagina(monkeypatch):
    # Controle POSITIVO do grupo: prova que o gate não recusa todo mundo.
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=False)
    assert shared.gate_onboarding(_Req()) is None


def test_deslogado_nao_cai_no_wizard(monkeypatch):
    # Sem token válido em nenhum cookie: deixa o HTML carregar (vai pro login).
    _patch(monkeypatch, token=None, payload=None, needs=True, dashboard_uid=None)
    assert shared.gate_onboarding(_Req()) is None


def test_app_ios_NAO_e_isento(monkeypatch):
    """Contraintuitivo de propósito — ver o invariante 1 no topo do arquivo.

    Controle negativo: acrescentar `if _is_pigbank_app(request): return None` no
    começo de gate_onboarding faz este teste ficar vermelho.
    """
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    out = shared.gate_onboarding(_Req(ua="Mozilla/5.0 PigBankApp/1.2"))
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/onboarding"


def test_upgrade_success_e_isento(monkeypatch):
    """/home?upgrade=success roda handleUpgradeReturn(), que dispara a conversão
    do Meta Pixel e a tela de quem acabou de pagar. Redirecionar essa URL queima
    a conversão de anúncio."""
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    assert shared.gate_onboarding(_Req(query={"upgrade": "success"})) is None
    # Sem o param, segue redirecionando (senão o teste passaria com o gate morto)
    assert isinstance(shared.gate_onboarding(_Req()), RedirectResponse)


def test_gate_falha_aberto(monkeypatch):
    """Onboarding é UX, não paywall: erro no gate nunca pode trancar o usuário
    fora do próprio produto."""
    def _boom(uid):
        raise RuntimeError("banco fora")

    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    monkeypatch.setattr(db, "needs_onboarding", _boom)
    assert shared.gate_onboarding(_Req()) is None


def test_dashboard_token_vale_mesmo_com_access_expirado(monkeypatch):
    # auth_token expirado mas dashboard_token (12h) válido: resolve o user e
    # AINDA aplica o gate.
    _patch(monkeypatch, token=None, payload=None, needs=True, dashboard_uid=7)
    out = shared.gate_onboarding(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/onboarding"


# ── Ordem e alcance nas páginas ──────────────────────────────────────────────

def test_gate_de_plano_vem_antes_do_de_onboarding(monkeypatch):
    """Quem ainda não escolheu plano não pode ser desviado da /precos pro wizard.

    Controle negativo: inverter as duas chamadas em serve_home deixa isto
    vermelho (o location vira /onboarding).
    """
    _patch(monkeypatch, payload={"type": "auth", "sub": "7"}, needs=True)
    monkeypatch.setattr(plan_service, "needs_plan_selection", lambda uid: True)

    out = shared.gate_plan_selection(_Req())
    assert isinstance(out, RedirectResponse)
    assert out.headers["location"] == "/precos?escolha=1"

    # E a ordem está escrita no handler, não só na intenção:
    import inspect
    src = inspect.getsource(static_pages.serve_home)
    assert src.index("gate_plan_selection") < src.index("gate_onboarding")


def test_settings_nao_tem_gate_de_onboarding():
    """Protege contra o loop settings → onboarding → settings.

    Controle negativo: acrescentar gate_onboarding em serve_settings deixa isto
    vermelho.
    """
    import inspect
    assert "gate_onboarding" not in inspect.getsource(static_pages.serve_settings)


@pytest.mark.parametrize("handler", ["serve_home", "serve_dashboard"])
def test_home_e_app_tem_o_gate(handler):
    import inspect
    assert "gate_onboarding" in inspect.getsource(getattr(static_pages, handler))
