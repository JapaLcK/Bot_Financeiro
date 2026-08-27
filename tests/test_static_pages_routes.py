"""Rotas de frontend/routes/static_pages.py (refactor Fase 1, Etapa 1).

Rede de segurança da extração: cada rota movida do monólito continua
registrada no app e respondendo com o mesmo status/content-type/headers.
Nenhuma toca banco — TestClient sem lifespan basta.
"""

import re

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from frontend.routes.shared import FRONTEND_DIR, _asset_hash, stamp_asset_versions

client = TestClient(dashboard.app)

HTML_PAGES = [
    "/",
    "/app",
    "/home",
    "/settings",
    "/reset-password",
    "/onboarding",
    "/completar-cadastro",
    "/privacy",
    "/termos",
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


def test_stamp_asset_versions_usa_hash_de_conteudo():
    # Número hardcoded no HTML é ignorado e trocado pelo hash do arquivo real.
    mtime = (FRONTEND_DIR / "app-mode.css").stat().st_mtime_ns
    esperado = _asset_hash("app-mode.css", mtime)
    out = stamp_asset_versions('<link href="/app-mode.css?v=29">')
    assert f"/app-mode.css?v={esperado}" in out
    assert "?v=29" not in out
    # Assets distintos → hashes distintos (não é um número global compartilhado).
    js = re.search(r"/app-mode\.js\?v=(\w+)", stamp_asset_versions('"/app-mode.js?v=1"'))
    assert js and js.group(1) != esperado
    # Asset que não existe em frontend/ fica intacto.
    assert stamp_asset_versions('"/naoexiste.js?v=7"') == '"/naoexiste.js?v=7"'


def test_home_serve_app_mode_com_cache_buster_de_hash():
    # A página logada real: o ?v= servido tem que ser hash, nunca o v=29 do arquivo.
    html = client.get("/home").text
    m = re.search(r"/app-mode\.css\?v=([0-9a-f]+)", html)
    assert m, "app-mode.css deveria aparecer com ?v=<hash>"
    assert m.group(1) != "29"
    assert m.group(1) == _asset_hash(
        "app-mode.css", (FRONTEND_DIR / "app-mode.css").stat().st_mtime_ns
    )


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


def test_pb_nav_js_com_cache_publico():
    resp = client.get("/pb-nav.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert resp.headers["cache-control"] == "public, max-age=300"
    # o motor exige o contrato: sem PBNav.boot as páginas convertidas não iniciam
    assert "PBNav" in resp.text and "boot" in resp.text


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
        ("/dashboard.js", "application/javascript"),
        ("/dashboard-chat.js", "application/javascript"),
    ]:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["content-type"].startswith(content_type), path
