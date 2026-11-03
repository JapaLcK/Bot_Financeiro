"""POST /cupom (pop-up do cupom) e GET /cupom-popup.js. Banco real, e-mail em monkeypatch.

Controles negativos provados no PR:
- tirar o `on conflict do nothing` de db/cupom_leads.registrar -> test_repetido_*;
- tirar o `remover` na falha de envio (route) -> test_falha_no_envio_*.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import core.services.email_service as email_service
import frontend.finance_bot_websocket_custom as dashboard
from db.connection import get_conn

CODIGO = "PIG10-TESTE"


@pytest.fixture
def envios(monkeypatch):
    monkeypatch.setenv("STRIPE_CUPOM_PRIMEIRA_COMPRA", CODIGO)
    chamadas = []
    monkeypatch.setattr(email_service, "send_cupom_email", lambda to, codigo, **kw: chamadas.append((to, codigo, kw)) or True)
    return chamadas


def _client(ip="198.51.100.7"):
    return TestClient(dashboard.app, client=(ip, 50000))


def _email():
    return f"cupom-{uuid.uuid4().hex[:10]}@example.com"


def _linha(email):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from cupom_leads where email = %s", (email.lower(),))
        return cur.fetchone()


def _post(c, email, **kw):
    corpo = {"email": email, "consent": True, "origem": "home", **kw}
    return c.post("/cupom", json=corpo)


def test_positivo_envia_codigo_e_grava_prova(envios):
    e = _email()
    r = _post(_client(), e)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert envios == [(e, CODIGO, {"log_recipient": False})]
    row = _linha(e)
    assert row["consent_at"] and row["consent_text"] and row["sent_at"]
    assert row["ip_hash"] and "198.51.100.7" not in row["ip_hash"]


def test_repetido_com_outra_caixa_nao_reenvia_e_resposta_igual(envios):
    e = _email()
    c = _client()
    r1 = _post(c, e)
    r2 = _post(c, e.upper())
    assert len(envios) == 1
    assert (r1.status_code, r1.content) == (r2.status_code, r2.content)


def test_honeypot_nao_faz_nada(envios):
    e = _email()
    r = _post(_client(), e, website="http://spam")
    assert r.json() == {"ok": True}
    assert envios == [] and _linha(e) is None


@pytest.mark.parametrize("corpo", [{"consent": False}, {"email": "sem-arroba"}])
def test_consent_falso_ou_email_invalido_400(envios, corpo):
    e = _email()
    base = {"email": e, "consent": True, **corpo}
    r = _client().post("/cupom", json=base)
    assert r.status_code == 400
    assert envios == [] and _linha(base["email"]) is None


def test_env_vazia_503_sem_gravar(envios, monkeypatch):
    monkeypatch.setenv("STRIPE_CUPOM_PRIMEIRA_COMPRA", "")
    e = _email()
    assert _post(_client(), e).status_code == 503
    assert envios == [] and _linha(e) is None


def test_rate_limit_por_ip(envios):
    c = _client("203.0.113.10")
    assert [_post(c, _email()).status_code for _ in range(5)] == [200] * 5
    assert _post(c, _email()).status_code == 429
    assert _post(_client("203.0.113.11"), _email()).status_code == 200


def test_falha_no_envio_apaga_linha_e_nova_tentativa_envia(envios, monkeypatch):
    e = _email()
    monkeypatch.setattr(email_service, "send_cupom_email", lambda to, codigo, **kw: False)
    assert _post(_client(), e).status_code == 200
    assert _linha(e) is None
    monkeypatch.setattr(email_service, "send_cupom_email", lambda to, codigo, **kw: envios.append((to, codigo, kw)) or True)
    _post(_client(), e)
    assert envios == [(e, CODIGO, {"log_recipient": False})] and _linha(e)["sent_at"]


def test_excecao_no_envio_200_apaga_linha_e_nova_tentativa_envia(envios, monkeypatch):
    e = _email()
    def boom(to, codigo, **kw): raise RuntimeError("smtp caiu")
    monkeypatch.setattr(email_service, "send_cupom_email", boom)
    assert _post(_client(), e).status_code == 200
    assert _linha(e) is None
    monkeypatch.setattr(email_service, "send_cupom_email", lambda to, codigo, **kw: envios.append((to, codigo, kw)) or True)
    _post(_client(), e)
    assert envios == [(e, CODIGO, {"log_recipient": False})] and _linha(e)["sent_at"]


def test_asset_js():
    r = TestClient(dashboard.app).get("/cupom-popup.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")


def test_csrf_com_header_200_sem_header_403(envios):
    # https fixo: independe de COOKIE_SECURE/DASHBOARD_URL do ambiente (padrão de test_admin_security)
    c = TestClient(dashboard.app, base_url="https://testserver", client=("198.51.100.7", 50000))
    c.get("/precos")  # planta o cookie csrf_token, como no navegador
    tok = c.cookies.get("csrf_token")
    assert tok
    assert c.post("/cupom", json={"email": _email(), "consent": True},
                  headers={"x-csrf-token": tok}).status_code == 200
    assert c.post("/cupom", json={"email": _email(), "consent": True},
                  headers={"x-csrf-token": ""}).status_code == 403


@pytest.mark.parametrize("campo", ["email", "origem"])
def test_nul_no_corpo_nao_da_500(envios, campo):
    corpo = {"email": "a@example.com", "consent": True, "origem": "home", campo: "x\x00y@example.com"}
    r = _client().post("/cupom", json=corpo)
    assert r.status_code in (400, 422) and envios == []


def test_marcar_enviado_quebrado_nao_remove_nem_erra(envios, monkeypatch):
    import db.cupom_leads as cl
    def boom(_): raise RuntimeError("x")
    monkeypatch.setattr(cl, "marcar_enviado", boom)
    e = _email()
    assert _post(_client(), e).status_code == 200
    assert len(envios) == 1 and _linha(e) is not None and _linha(e)["sent_at"] is None
