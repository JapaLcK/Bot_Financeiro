"""A ilha do chat chega ao dashboard pelo mesmo contrato HTTP dos outros assets.

O navegador testa a renderização; estes testes cobrem os arquivos, as rotas e as
URLs versionadas entregues pelo servidor, incluindo a ordem dos controladores.
"""
from html.parser import HTMLParser

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from frontend.routes import static_pages
from frontend.routes.shared import FRONTEND_DIR, _asset_hash


class ChatMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.styles = []
        self.hosts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs.get("href", ""))
        if attrs.get("id") == "pigbank-chat-root":
            self.hosts.append(tag)


@pytest.fixture
def chat_client(monkeypatch):
    monkeypatch.setattr(static_pages, "gate_plan_selection", lambda request: None)
    monkeypatch.setattr(static_pages, "gate_onboarding", lambda request: None)
    app = FastAPI()
    app.include_router(static_pages.router)
    return TestClient(app)


@pytest.mark.parametrize("filename,mime", [
    ("chat-app.js", "javascript"), ("chat-app.css", "text/css"),
])
def test_chat_assets_http_servem_build_real_e_cache_versionado(chat_client, filename, mime):
    path = FRONTEND_DIR / filename
    content = path.read_bytes()
    assert content, "Build vazio não pode ser servido como sucesso."
    digest = _asset_hash(filename, path.stat().st_mtime_ns)
    for suffix in ["", f"?v={digest}"]:
        response = chat_client.get(f"/{filename}{suffix}")
        assert response.status_code == 200
        assert mime in response.headers["content-type"]
        assert response.content == content
        cache = response.headers["cache-control"]
        assert "no-cache" in cache or (suffix and "immutable" in cache)


def test_dashboard_entrega_uma_ilha_e_carrega_bundle_antes_dos_controladores(chat_client):
    response = chat_client.get("/app")
    assert response.status_code == 200
    markup = ChatMarkup()
    markup.feed(response.text)
    assert markup.hosts == ["div"]
    scripts = {item["src"].split("?")[0]: (index, item)
               for index, item in enumerate(markup.scripts)}
    names = ["chat-app.js", "dashboard-chat.js", "dashboard-agent-chat.js"]
    for name in names:
        _, item = scripts[f"/{name}"]
        expected = _asset_hash(name, (FRONTEND_DIR / name).stat().st_mtime_ns)
        assert item["src"] == f"/{name}?v={expected}"
        assert "defer" in item, "A ilha e os controladores dependem do DOM completo."
    assert scripts["/chat-app.js"][0] < scripts["/dashboard-chat.js"][0]
    assert scripts["/chat-app.js"][0] < scripts["/dashboard-agent-chat.js"][0]
    css = next(url for url in markup.styles if url.startswith("/chat-app.css?"))
    expected_css = _asset_hash("chat-app.css", (FRONTEND_DIR / "chat-app.css").stat().st_mtime_ns)
    assert css == f"/chat-app.css?v={expected_css}"
    assert chat_client.get(css).status_code == 200


def test_chat_build_emite_apenas_assets_com_rotas():
    assert sorted(path.name for path in FRONTEND_DIR.glob("chat-app.*")) == [
        "chat-app.css", "chat-app.js",
    ]
