"""Ações da página de erro: renderização segura e fallback com navegação útil."""
from html.parser import HTMLParser

import pytest

from frontend.routes import shared


ACTIONS = (("Tentar novamente", "/conta"), ("Voltar para configurações", "/settings"))


class Links(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.links = []
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


def test_acoes_customizadas_e_default_nao_vazam_entre_respostas():
    custom = shared.error_page_response(502, actions=ACTIONS)
    following = shared.error_page_response(404)
    assert [a["href"] for a in Links(custom.body.decode()).links] == ["/conta", "/settings"]
    assert [a["href"] for a in Links(following.body.decode()).links] == ["/"]
    assert "← Página inicial" in following.body.decode()
    assert "Tentar novamente" not in following.body.decode()
    assert custom.status_code == 502
    assert custom.headers["cache-control"] == "no-store"


def test_rotulo_e_href_escapados_sem_reexpandir_placeholders():
    label = '<img src=x onerror="alert(1)"> {{MESSAGE}} {{ACTIONS}}'
    href = '/conta?texto="<script>"&origem=erro'
    response = shared.error_page_response(502, actions=((label, href),))
    body = response.body.decode()
    assert '<img src=x' not in body
    assert '&lt;img src=x' in body
    assert '{{MESSAGE}} {{ACTIONS}}' in body
    links = Links(body).links
    assert len(links) == 1 and links[0]["href"] == href
    assert not any(name.startswith("on") for name in links[0])


@pytest.mark.parametrize("actions", [
    None, (), "texto", 1, (("Rótulo",),), (("Rótulo", "/", "extra"),),
    ((None, "/"),), (("Rótulo", None),),
    (("Rótulo", "javascript:alert(1)"),), (("Rótulo", "https://example.com"),),
    (("Rótulo", "//example.com"),), (("Rótulo", "/\\example.com"),),
    (("Rótulo", "/\n/evil"),), (("Rótulo", "/\t/evil"),),
    (("Rótulo", "/\x00evil"),), (("Rótulo", "/conta aqui"),),
])
def test_acoes_invalidas_caem_no_padrao_sem_derrubar_erro(actions):
    response = shared.error_page_response(502, actions=actions)
    assert response.status_code == 502
    assert [a["href"] for a in Links(response.body.decode()).links] == ["/"]


@pytest.mark.parametrize("missing", [True, False], ids=["arquivo-ausente", "sem-placeholder-acoes"])
def test_fallback_preserva_as_acoes_e_status(monkeypatch, tmp_path, missing):
    if not missing:
        raw = (shared.FRONTEND_DIR / "error.html").read_text(encoding="utf-8")
        (tmp_path / "error.html").write_text(raw.replace("{{ACTIONS}}", ""), encoding="utf-8")
    monkeypatch.setattr(shared, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(shared, "_error_template", None)
    response = shared.error_page_response(502, actions=ACTIONS)
    body = response.body.decode()
    assert response.status_code == 502
    assert [a["href"] for a in Links(body).links] == ["/conta", "/settings"]
    assert "safe-area.js" not in body
    assert "{{ACTIONS}}" not in body
    assert shared._error_template is None
