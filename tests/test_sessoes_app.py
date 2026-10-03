"""Sessões do app nativo: o rótulo vem do User-Agent que o app monta.

O formato do UA mora em `tests/fixtures/user_agents_app.json`, lido também pelo
Jest do app (`app/__tests__/aparelho.test.ts`) — uma fonte só para o que o app
gera e o que o servidor reconhece (§0.7).

Controle positivo: o UA do WebView do Capacitor (que só ANEXA `PigBankApp/1.0`)
continua com o rótulo antigo. Controle negativo, rodado à mão: apagar o ramo
`_UA_APP` de `device_label` deixa vermelhos os casos do app nativo.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import UID, login_http
from conftest import promote_to_pro
from core.sessions import create_session, device_label, get_active_session

CASOS = json.loads(
    (Path(__file__).parent / "fixtures" / "user_agents_app.json").read_text(encoding="utf-8")
)
UA_APP = CASOS[0]["ua"]
CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Chrome/120.0.0.0 Safari/537.36"


@pytest.mark.parametrize("caso", CASOS, ids=[c["label"][:30] for c in CASOS])
def test_rotulo_do_user_agent(caso):
    assert device_label(caso["ua"]) == caso["label"]


def _bearer(token: str) -> dict[str, str]:
    # Escrita do app declara JSON: é a condição da isenção de CSRF por Bearer.
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_lista_de_sessoes_pelo_bearer_do_app(monkeypatch):
    """Login pela rota com o UA do app, e a lista lida com o Bearer dele."""
    dados, _ = login_http(monkeypatch, como_app=True, ua=UA_APP)
    login_http(monkeypatch, como_app=False, ua=CHROME)  # 2ª sessão, do navegador
    promote_to_pro(UID)

    r = TestClient(dashboard.app).get(
        f"/settings/{UID}/sessions", headers=_bearer(dados["access_token"])
    )
    assert r.status_code == 200, r.text
    sessoes = r.json()["sessions"]
    atuais = [s for s in sessoes if s["is_current"]]
    assert len(atuais) == 1
    assert atuais[0]["device_label"] == "iPhone 17 Pro • PigBank app"
    assert "Chrome • macOS" in [s["device_label"] for s in sessoes if not s["is_current"]]


def test_bearer_de_a_nao_alcanca_sessao_de_b(monkeypatch, user_id):
    dados, _ = login_http(monkeypatch, como_app=True, ua=UA_APP)
    promote_to_pro(UID)
    jti_b = create_session(user_id, ip="203.0.113.9", user_agent=CHROME)
    # Um cliente por pedido: o app não tem cookie jar, e o GET planta o
    # `csrf_token` no jar do TestClient — o DELETE seguinte deixaria de ser
    # "sem credencial ambiente" e morreria no CSRF, não na autorização.
    lista = TestClient(dashboard.app).get(f"/settings/{user_id}/sessions", headers=_bearer(dados["access_token"]))
    assert lista.status_code == 403, lista.text

    # Pelo path de A, com o jti de B: a query de revogação filtra por user_id.
    revoga = TestClient(dashboard.app).delete(f"/settings/{UID}/sessions/{jti_b}", headers=_bearer(dados["access_token"]))
    assert revoga.status_code == 404, revoga.text
    assert get_active_session(jti_b) is not None
