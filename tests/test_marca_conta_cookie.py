"""O marcador `pb_conta` (sinal de troca de conta do /painel): quem o grava, quem o apaga.

Sem banco: chama as duas funções do monólito sobre um `Response` solto. O marcador nunca
autentica; quem prova que o servidor não o lê é `tests/test_painel_pagina.py` (com banco).
"""
import re

from fastapi import Response

import frontend.finance_bot_websocket_custom as dashboard
from frontend.routes.shared import CONTA_COOKIE_NAME, FRONTEND_DIR


def _cookies(resp: Response) -> dict[str, list[str]]:
    """{nome: [atributos em minúsculo, um Set-Cookie por item]}."""
    saida: dict[str, list[str]] = {}
    for h in resp.headers.getlist("set-cookie"):
        saida.setdefault(h.split("=", 1)[0], []).append(h.lower())
    return saida


def test_sessao_grava_o_marcador_legivel_e_o_token_segue_httponly(monkeypatch):
    monkeypatch.setattr(dashboard, "COOKIE_SECURE", True)
    resp = Response()
    dashboard._set_dashboard_cookie(resp, 7)
    c = _cookies(resp)
    [marca] = c[CONTA_COOKIE_NAME]
    [token] = c[dashboard.DASHBOARD_COOKIE_NAME]
    assert marca.startswith(f"{CONTA_COOKIE_NAME}=7;")
    assert "httponly" not in marca
    assert "samesite=lax" in marca and "path=/" in marca and "domain" not in marca
    assert f"max-age={int(dashboard.DASHBOARD_SESSION_HOURS * 3600)}" in marca
    assert "secure" in marca and "secure" in token
    assert "httponly" in token


def test_secure_acompanha_o_do_token_em_dev(monkeypatch):
    monkeypatch.setattr(dashboard, "COOKIE_SECURE", False)
    resp = Response()
    dashboard._set_dashboard_cookie(resp, 7)
    c = _cookies(resp)
    assert "secure" not in c[CONTA_COOKIE_NAME][0]
    assert "secure" not in c[dashboard.DASHBOARD_COOKIE_NAME][0]


def test_fim_de_sessao_expira_o_marcador_nos_tres_dominios(monkeypatch):
    monkeypatch.setattr(dashboard, "DASHBOARD_URL", "https://pigbankai.com")
    resp = Response()
    dashboard._clear_session_cookies(resp)
    apagados = [h for h in _cookies(resp)[CONTA_COOKIE_NAME] if "max-age=0" in h]
    dominios = {(re.search(r"domain=([^;]+)", h) or [None, None])[1] for h in apagados}
    assert dominios == {None, "pigbankai.com", ".pigbankai.com"}


def test_o_nome_do_cliente_e_o_do_servidor():
    # §0.7: o cliente não importa Python; o nome é comparado aqui.
    fonte = (FRONTEND_DIR.parent / "webapp" / "src" / "dashboard" / "lib" / "sessao.ts").read_text(encoding="utf-8")
    assert f'const NOME = "{CONTA_COOKIE_NAME}="' in fonte
