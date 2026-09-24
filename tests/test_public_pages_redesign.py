"""Contratos publicados de conteúdo: uma fonte real para catálogo e energia."""
import asyncio
import re
from html import unescape
from pathlib import Path

from core.commands_catalog import CATALOG
from core.services.plan_limits import AGENT_ENERGY_COST, PLUS_LIMITS, PRO_LIMITS
from frontend.routes import static_pages

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def test_catalogo_publico_contem_os_exemplos_oficiais():
    page = (FRONTEND / "comandos.html").read_text()
    # Cada exemplo é um parágrafo simples, sem markup ou preço gerado por JS.
    examples = {
        key: unescape(re.sub(r"<[^>]+>", "", value)).strip()
        for key, value in re.findall(r'<p[^>]*id="([^"]+)"[^>]*>(.*?)</p>', page, re.S)
    }
    for category in CATALOG:
        for index, command in enumerate(category["commands"]):
            assert examples[f'{category["id"]}-{index}'] == command["text"]


def test_energia_publicada_corresponde_ao_motor_real():
    page = (FRONTEND / "agents.html").read_text()
    cards = re.findall(r'<article\b([^>]+)>(.*?)</article>', page, re.S)
    seen = {}
    for attrs, content in cards:
        kind = re.search(r'data-agent-kind="([^"]+)"', attrs)
        if not kind:
            continue
        cost = int(re.search(r'data-energy="(\d+)"', attrs).group(1))
        tag = re.search(r'<span\b[^>]*class="lp-energy-tag".*?</span>', content, re.S).group(0)
        assert str(cost) == re.sub(r'<[^>]+>', '', tag).strip()
        seen[kind.group(1)] = cost
    assert seen == AGENT_ENERGY_COST
    for tier, limits in [('plus', PLUS_LIMITS), ('pro', PRO_LIMITS)]:
        match = re.search(r'<article\b(?=[^>]*data-plan="'+tier+r'")(?=[^>]*data-energy="(\d+)")[^>]*>', page)
        assert match and int(match.group(1)) == limits['agents_energy_budget']


def test_novas_apresentacoes_usam_html_sem_cache_e_scripts_reais():
    for route, marker in [(static_pages.serve_recuperar_senha, '/auth/forgot-password'),
                          (static_pages.serve_contato, '/contact.js?v=')]:
        response = asyncio.run(route())
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert marker in response.body.decode()
        assert 'data-demo' not in response.body.decode()


def test_links_de_fragmento_apontam_para_secoes_existentes():
    from html.parser import HTMLParser
    from urllib.parse import urlsplit

    class Links(HTMLParser):
        def __init__(self, text):
            super().__init__()
            self.ids, self.hrefs = set(), []
            self.feed(text)

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'id' in attrs:
                self.ids.add(attrs['id'])
            if tag == 'a' and 'href' in attrs:
                self.hrefs.append(attrs['href'])

    pages = {name: Links((FRONTEND / f'{name}.html').read_text()) for name in
             ['agents', 'funcionalidades', 'precos', 'comandos', 'suporte', 'contato',
              'privacy', 'termos', 'whatsapp', 'como-funciona']}
    for name, page in pages.items():
        for href in page.hrefs:
            url = urlsplit(href)
            target = url.path.strip('/') or name
            if url.fragment and not url.netloc and target in pages:
                assert url.fragment in pages[target].ids, f'{name}: {href} sem destino'
