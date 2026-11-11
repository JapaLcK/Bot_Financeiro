"""O que os testes da adesão ao aviso de insight (PL-06 B6, PR 1) compartilham.

Sem prefixo `test_`: o pytest não coleta. Os três arquivos `tests/test_insights_adesao*.py`
importam daqui para não carregar três cópias de `_cliente`/`_aderir` (§0.7).
"""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.services import insights_notificacao
from core.services.email_service import make_unsub_url
from db.connection import get_conn
from db.reports import insight_email_permitido

CSRF = "csrf-b6"


@pytest.fixture
def ambiente(monkeypatch):
    """Plano v2 e todo mundo liberado. A trava do `*` (`AVISO_ABERTO_A_TODOS`) é aberta AQUI, só
    para os testes que não são sobre ela; os da trava (`test_insights_adesao_direito.py`) a deixam fechada."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("INSIGHT_EMAIL_USERS", "*")
    monkeypatch.setattr(insights_notificacao, "AVISO_ABERTO_A_TODOS", True)


def sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        linhas = cur.fetchall() if cur.description else None
        conn.commit()
    return linhas


def cliente(uid):
    email = db.get_auth_user(uid)["email"]
    c = TestClient(dashboard.app, raise_server_exceptions=False)
    c.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    c.headers[dashboard.CSRF_HEADER_NAME] = CSRF
    return c


def get(uid):
    r = cliente(uid).get(f"/settings/{uid}/notifications")
    assert r.status_code == 200, r.text
    return r.json()


def patch(uid, **corpo):
    return cliente(uid).patch(f"/settings/{uid}/notifications", json=corpo)


def ok(uid, **corpo):
    r = patch(uid, **corpo)
    assert r.status_code == 200, r.text
    return r.json()


def aderir(uid):
    assert ok(uid, insight_alert_email_enabled=True)["insight_alert_email_enabled"] is True
    assert insight_email_permitido(uid) is True


def flags(uid):
    return sql(
        "select engagement_opt_out e, tip_email_opt_out t, insight_email_opt_out i,"
        " insight_aviso_aderiu_em aderiu, insight_aviso_coberto_ate coberto"
        " from auth_accounts where user_id = %s", (uid,))[0]


def eventos(uid):
    return sql("select event, ip, user_agent, details from audit_events"
               " where user_id = %s and event like 'insight_aviso%%' order by id", (uid,))


def unsub(uid, metodo="post"):
    email = db.get_auth_user(uid)["email"]
    token = parse_qs(urlparse(make_unsub_url(uid, email)).query)["token"][0]
    r = getattr(TestClient(dashboard.app), metodo)("/unsubscribe", params={"uid": uid, "token": token})
    assert r.status_code == 200, r.text
    return r


def diga(uid, texto):
    import core.handle_incoming as hi
    from core.types import IncomingMessage
    out = hi.handle_incoming(IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                                             message_id=uuid.uuid4().hex, attachments=[], external_id="", raw={}))
    return "\n".join(m.text for m in out)
