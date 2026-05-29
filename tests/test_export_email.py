"""
Export por email: POST /export/{uid} gera PDF + XLSX + CSV do mês e envia pro
email cadastrado via send_email (com anexos base64). Testa a lógica do handler
com auth mockada e send_email espionado — não toca rede.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime

import pytest
from fastapi import HTTPException

import frontend.finance_bot_websocket_custom as app_mod


@pytest.fixture
def _no_auth(monkeypatch):
    monkeypatch.setattr(app_mod, "_authorize_dashboard_access", lambda req, user_id: None)
    monkeypatch.setattr(app_mod, "_require_pro", lambda user_id, feature: None)
    # chamamos export_email direto (sem Request HTTP); desliga o rate-limit do slowapi
    monkeypatch.setattr(app_mod.limiter, "enabled", False)


@pytest.fixture
def spy_email(monkeypatch):
    import core.services.email_service as es
    captured: dict = {}

    def fake_send(to, subject, html_body, text_body=None, from_addr=None, headers=None, attachments=None):
        captured.update(to=to, subject=subject, attachments=attachments)
        return True

    monkeypatch.setattr(es, "send_email", fake_send)
    return captured


def _add_launch(uid: int, tipo: str, valor: float, alvo: str, categoria: str) -> None:
    from db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into launches (user_id, tipo, valor, alvo, nota, categoria, criado_em) "
                "values (%s, %s, %s, %s, '', %s, now())",
                (uid, tipo, valor, alvo, categoria),
            )
        conn.commit()


def test_export_email_envia_3_anexos(_no_auth, spy_email, pro_user_id):
    _add_launch(pro_user_id, "receita", 3000, "Salário", "salário")
    _add_launch(pro_user_id, "despesa", 120.5, "Mercado", "alimentação")

    now = datetime.now()
    res = asyncio.run(app_mod.export_email(None, pro_user_id, now.year, now.month))

    assert res["ok"] is True
    assert "***@" in res["email"]

    atts = spy_email["attachments"]
    assert {a["filename"].rsplit(".", 1)[1] for a in atts} == {"pdf", "xlsx", "csv"}
    by_ext = {a["filename"].rsplit(".", 1)[1]: base64.b64decode(a["content"]) for a in atts}
    assert by_ext["pdf"][:4] == b"%PDF"
    assert by_ext["xlsx"][:2] == b"PK"  # xlsx é um zip


def test_export_email_404_sem_lancamentos(_no_auth, spy_email, pro_user_id):
    now = datetime.now()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(app_mod.export_email(None, pro_user_id, now.year + 1, 1))
    assert exc.value.status_code == 404
    assert spy_email.get("attachments") is None
