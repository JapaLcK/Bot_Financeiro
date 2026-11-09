"""`POST /api/v2/feedback` (`api/v2/feedback.py`) pelo monólito real: sessão, CSRF e banco reais, só o
envio de e-mail trocado. O corpo traz `tipo` e `mensagem`: conta, destino e assunto saem do servidor.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf
from conftest import usuario_pagante
from api.v2 import feedback
from test_api_v2_guia import cliente, uid  # noqa: F401 (uid é fixture)

URL = "/api/v2/feedback"
OK = {"tipo": "ideia", "mensagem": "Queria exportar o extrato"}


@pytest.fixture
def enviados(monkeypatch):
    """Captura o que iria por e-mail; `ok` escolhe o retorno de `send_email`."""
    cap = {"chamadas": [], "ok": True}

    def falso(to, subject, html_body, text_body=None, headers=None, **_):
        cap["chamadas"].append({"to": to, "subject": subject, "html": html_body, "text": text_body, "headers": headers})
        return cap["ok"]

    monkeypatch.setattr(feedback, "send_email", falso)
    return cap


def enviar(quem, corpo=None, **extra):
    c, h = cliente(quem)
    return c.post(URL, json=OK if corpo is None else corpo, headers=h, **extra)


def test_envia_ao_time_com_reply_to_da_conta_e_html_escapado(uid, enviados):
    email = db.get_auth_user(uid)["email"]
    r = enviar(uid, {"tipo": "problema", "mensagem": "  <script>alert(1)</script> & \"oi\"  "})
    assert r.status_code == 200, r.text
    assert r.json() == {"enviado": True}
    (m,) = enviados["chamadas"]
    assert m["to"] == feedback.DESTINO
    assert m["subject"] == "Feedback do painel: Problema"
    assert m["headers"] == {"Reply-To": email}
    assert "<script>" not in m["html"] and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;oi&quot;" in m["html"]
    assert f"Usuário: {uid}" in m["text"] and f"E-mail da conta: {email}" in m["text"]
    assert "<script>alert(1)</script> & \"oi\"" in m["text"]


@pytest.mark.parametrize("extra", [{"destino": "x@y.com"}, {"user_id": 1}, {"assunto": "oi"}, {"email": "x@y.com"}])
def test_corpo_nao_escolhe_conta_destino_nem_assunto(uid, enviados, extra):
    assert enviar(uid, OK | extra).status_code == 422
    assert enviados["chamadas"] == []


def test_assunto_vem_do_tipo_nao_do_texto(uid, enviados):
    enviar(uid, {"tipo": "elogio", "mensagem": "Assunto: pix\r\nBcc: a@b.com"})
    assert enviados["chamadas"][0]["subject"] == "Feedback do painel: Elogio"


def test_sem_sessao_401(enviados):
    c = TestClient(dashboard.app)
    assert c.post(URL, json=OK, headers=csrf(c)).status_code == 401  # CSRF válido, sem sessão
    assert enviados["chamadas"] == []


@pytest.mark.parametrize("corpo", [
    {"tipo": "outro", "mensagem": "oi"},
    {"tipo": "ideia", "mensagem": ""},
    {"tipo": "ideia", "mensagem": "   \n\t "},
    {"tipo": "ideia", "mensagem": "a" * 4001},
    {"tipo": "ideia"},
])
def test_corpo_invalido_422_no_envelope(uid, enviados, corpo):
    r = enviar(uid, corpo)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error", r.text
    assert enviados["chamadas"] == []


def test_4000_caracteres_passam_e_o_limite_vale_depois_do_trim(uid, enviados):
    assert enviar(uid, {"tipo": "ideia", "mensagem": "a" * 4000}).status_code == 200
    assert enviar(uid, {"tipo": "ideia", "mensagem": " " + "a" * 4000 + " "}).status_code == 200


def test_falha_do_envio_502_sem_citar_o_destino(uid, enviados):
    enviados["ok"] = False
    r = enviar(uid)
    assert r.status_code == 502, r.text
    assert "pigbankai" not in r.text and feedback.DESTINO not in r.text


def test_sexta_tentativa_429_e_conta_mesmo_se_o_envio_falha(uid, enviados):
    enviados["ok"] = False
    assert [enviar(uid).status_code for _ in range(5)] == [502] * 5
    r = enviar(uid)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited", r.text
    assert len(enviados["chamadas"]) == 5


def test_limite_e_por_conta(uid, enviados, monkeypatch):
    outra = usuario_pagante()
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{uid},{outra}")
    assert [enviar(uid).status_code for _ in range(5)] == [200] * 5
    assert enviar(uid).status_code == 429
    assert enviar(outra).status_code == 200


def test_email_da_conta_vai_escapado_no_html(uid, enviados, monkeypatch):
    monkeypatch.setattr(feedback, "get_user_email", lambda _uid: "a<b>@x.com")
    assert enviar(uid).status_code == 200
    (m,) = enviados["chamadas"]
    assert "a&lt;b&gt;@x.com" in m["html"] and "a<b>" not in m["html"]
