"""Rotas de frontend/routes/static_pages.py (refactor Fase 1, Etapa 1).

Rede de segurança da extração: cada rota movida do monólito continua
registrada no app e respondendo com o mesmo status/content-type/headers.
Nenhuma toca banco — TestClient sem lifespan basta.
"""

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard

client = TestClient(dashboard.app)

HTML_PAGES = [
    "/",
    "/app",
    "/home",
    "/settings",
    "/reset-password",
    "/onboarding",
    "/privacy",
    "/termos",
    "/changelog",
    "/whatsapp",
    "/funcionalidades",
    "/comandos",
    "/comandos-app",
    "/como-funciona",
    "/precos",
    "/suporte",
]


def test_html_pages_respondem_com_no_store():
    for path in HTML_PAGES:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith("text/html"), path
        assert resp.headers["cache-control"] == "no-store", path


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_robots_txt():
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "Disallow: /app" in resp.text
    assert "Sitemap:" in resp.text


def test_sitemap_xml():
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "<urlset" in resp.text
    assert "/precos" in resp.text


def test_commands_catalog():
    resp = client.get("/api/commands-catalog")
    assert resp.status_code == 200
    assert "catalog" in resp.json()


def test_auth_refresh_js_com_cache_publico():
    resp = client.get("/static/auth-refresh.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert resp.headers["cache-control"] == "public, max-age=300"


def test_service_worker_headers():
    resp = client.get("/service-worker.js")
    assert resp.status_code == 200
    assert resp.headers["service-worker-allowed"] == "/"
    assert resp.headers["cache-control"] == "no-cache"


def test_assets_estaticos():
    for path, content_type in [
        ("/modals.js", "application/javascript"),
        ("/favicon.png", "image/png"),
        ("/manifest.json", "application/manifest+json"),
    ]:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith(content_type), path
