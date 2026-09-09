"""Corpo JSON de topo não-objeto não pode virar 500 (issue #310).

`await request.json()` aceita qualquer JSON válido — `[1,2,3]`, `"abc"`, `42`,
`null`, `true`. O `try/except` em volta só pega falha de parse, então o topo
não-objeto passava e o `payload.get(...)` seguinte levantava AttributeError,
virando 500 com stack trace.

São 6 endpoints e um defeito só. Dois tratamentos:
- recusa com 400 — login admin, afiliados, webhook Pluggy;
- normaliza para `{}` — saques, onde o corpo é opcional por design e o
  `except` já dizia "corpo ausente/ruim não impede a ação".

Os corpos vão como `content=json.dumps(...)`, nunca `json=`: httpx traduz
`json=None` para "sem corpo", e aí o caso `null` cairia no `except` do parse
em vez da guarda de topo — o teste ficaria verde sem medir a guarda.

O MESMO mecanismo um nível abaixo (topo objeto, campo filho não-objeto) mora em
`test_corpo_json_campo_filho.py`; o número não finito, em
`test_corpo_json_nao_finito.py`. Os helpers dos três estão em
`_corpo_json_helpers.py`.
"""
import pytest

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard
from _corpo_json_helpers import (  # noqa: F401  (fixtures autouse)
    CSRF,
    IDS,
    INEXISTENTE,
    NAO_OBJETOS,
    _admin_client,
    _admin_tables,
    _client,
    _pluggy_post,
    _post_bruto,
    configured_admin,
)


# --------------------------------------------------------------------------
# Grupo admin: recusa com 400 (era 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_admin_login_topo_nao_objeto_da_400(corpo):
    """O pior dos seis: rota anônima (o CSRF é double-submit, um GET qualquer
    entrega o cookie), então o 500 com stack trace era alcançável sem conta."""
    r = _post_bruto(_client(), "/admin/auth/login", corpo)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_admin_affiliate_create_topo_nao_objeto_da_400(corpo):
    r = _post_bruto(_admin_client(), "/admin/api/affiliates", corpo)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_admin_affiliate_status_topo_nao_objeto_da_400(corpo):
    r = _post_bruto(
        _admin_client(), f"/admin/api/affiliates/{INEXISTENTE}/status", corpo
    )
    assert r.status_code == 400, r.text


# --------------------------------------------------------------------------
# Grupo Pluggy: recusa com 400 (era 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_pluggy_webhook_topo_nao_objeto_da_400(corpo):
    r = _pluggy_post(corpo)
    assert r.status_code == 400, r.text


# --------------------------------------------------------------------------
# Grupo saque: normaliza para {} e segue (era 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("acao", ["paid", "reject"])
@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_payout_topo_nao_objeto_normaliza_e_segue(corpo, acao):
    """404 (saque inexistente), não 400: aqui o corpo é opcional por design.
    Virar 400 mudaria um caminho que hoje funciona."""
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        corpo,
    )
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------
# Controles positivos: o caminho legítimo continua funcionando.
# Sem eles o arquivo inteiro passaria num código que recusa tudo.
# --------------------------------------------------------------------------

def test_controle_positivo_login_valido_ainda_autentica():
    """Rota de autenticação: regressão aqui tranca o painel."""
    client = _client()
    r = client.post(
        "/admin/auth/login",
        headers={dashboard.CSRF_HEADER_NAME: CSRF},
        json={"username": "admin", "password": "secret-admin"},
    )
    assert r.status_code == 200, r.text
    assert client.cookies.get(admin_dashboard.ADMIN_AUTH_COOKIE_NAME)


def test_controle_positivo_affiliate_status_objeto_valido():
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/{INEXISTENTE}/status",
        {"status": "disabled"},
    )
    # 404 porque o afiliado não existe; o que não pode é 400 (corpo recusado).
    assert r.status_code == 404, r.text


def test_controle_positivo_pluggy_webhook_objeto_valido():
    r = _pluggy_post({"event": "item/updated", "itemId": ""})
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True}


@pytest.mark.parametrize("acao", ["paid", "reject"])
def test_controle_positivo_payout_sem_corpo_nenhum(acao):
    """Prova que o fallback `payload = {}` do `except` continua de pé: sem
    corpo, a ação segue e termina em 404 por saque inexistente, igual a hoje."""
    client = _admin_client()
    r = client.post(
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        headers={dashboard.CSRF_HEADER_NAME: CSRF},
    )
    assert r.status_code == 404, r.text
