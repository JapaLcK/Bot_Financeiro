"""/assinar (funil v3, PR 5): o que só se prova no HTML que o servidor entrega.

O fragmento da URL traz nome, e-mail e WhatsApp. O Pixel e o GA4 leem
`location.href` na hora em que rodam, então o `assinar.js` (que limpa a URL) tem de
rodar ANTES deles: síncrono, no <head>, antes do ponto de injeção do
`inject_tracking`. O teste de navegador (tests/frontend/assinar_pii.test.mjs) mede
o efeito; este mede a ordem na página REAL, com a injeção de verdade.
"""

import re

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from core.services.plan_service import TIER_TO_STORED_PLAN
from frontend.routes import shared
from frontend.routes.shared import FRONTEND_DIR

client = TestClient(dashboard.app)


def _assinar_com_rastreio(monkeypatch) -> str:
    monkeypatch.setattr(shared, "META_PIXEL_ID", "123456789")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE00000")
    resp = client.get("/assinar")
    assert resp.status_code == 200
    return resp.text


def test_script_do_fragmento_roda_antes_do_pixel_e_do_ga4(monkeypatch):
    html = _assinar_com_rastreio(monkeypatch)
    tag = re.search(r'<script[^>]*src="/assinar\.js[^"]*"[^>]*>', html)
    assert tag, "a /assinar não carrega o assinar.js"
    # Guarda anti-tautologia: sem a injeção, os índices abaixo seriam -1.
    assert "fbq('init'" in html and "gtag('config'" in html
    assert tag.start() < html.index("fbq('init'")
    assert tag.start() < html.index("gtag('config'")
    assert not re.search(r"\b(defer|async)\b", tag.group(0)), tag.group(0)
    assert tag.start() < html.lower().index("</head>")


def test_assinar_sem_clarity(monkeypatch):
    monkeypatch.setattr(shared, "CLARITY_PROJECT_ID", "teste123")
    assert "clarity" not in _assinar_com_rastreio(monkeypatch).lower()


def test_assinar_html_sem_script_inline_nem_handler_inline():
    fonte = (FRONTEND_DIR / "assinar.html").read_text(encoding="utf-8")
    assert re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", fonte) == []
    assert not re.search(r"\son[a-z]+\s*=", fonte)


def _lista_js(nome: str) -> set[str]:
    fonte = (FRONTEND_DIR / "purchase-intent.js").read_text(encoding="utf-8")
    m = re.search(rf"const {nome} = \[([^\]]*)\];", fonte)
    assert m, f"{nome} sumiu do purchase-intent.js"
    return set(re.findall(r'"([a-z_]+)"', m.group(1)))


def test_planos_do_purchase_intent_sao_os_do_plan_service():
    """A /assinar valida `plano` com o `isValidChoice` do purchase-intent.js; a lista
    de lá tem de ser a mesma do servidor (§0.7: JS não importa Python)."""
    assert _lista_js("PLANS") == set(TIER_TO_STORED_PLAN)
    assert _lista_js("CYCLES") == {"monthly", "annual"}
