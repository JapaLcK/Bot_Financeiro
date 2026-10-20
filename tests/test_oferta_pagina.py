"""/oferta (frontend/oferta.html): LP de comparação convertida da exportação do Claude Design.

Três coisas podem quebrar em silêncio e são as que este arquivo prende:
  * sobra de runtime/Google Fonts/`{{ }}` na conversão (o CSP do site não deixa rodar);
  * caminho de asset errado ou arquivo órfão em `frontend/brand/oferta/`;
  * preço, nº de bancos ou link de compra no markup divergirem da fonte (precos.html,
    plan_limits, purchase-intent.js). O markup é estático, então só um teste liga as duas pontas.
"""
import html
import pathlib
import re
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from core.services.plan_limits import limits_for_tier
from core.services.plan_service import FEATURE_MIN_TIER_V2

FRONTEND = pathlib.Path(__file__).resolve().parent.parent / "frontend"
client = TestClient(dashboard.app)
TIERS = ("essencial", "plus", "pro")


def _pagina() -> str:
    return client.get("/oferta").text


def test_oferta_responde_html_sem_runtime_nem_google_fonts():
    r = client.get("/oferta")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    csp = r.headers["content-security-policy"]
    assert "script-src" in csp and "unsafe-eval" not in csp
    assert '<meta name="robots" content="noindex"' in r.text
    for sobra in ("dc-runtime", "fonts.googleapis", "fonts.gstatic", "{{", "<sc-", "x-dc", '"assets/'):
        assert sobra not in r.text, sobra


def test_todo_caminho_local_existe_e_nenhum_asset_e_orfao():
    corpo = _pagina()
    caminhos = {html.unescape(c) for c in re.findall(r'(?:src|href|poster)="(/[^"]*)"', corpo)}
    caminhos |= set(re.findall(r"url\((/[^)]+)\)", corpo))
    assert len(caminhos) > 30, caminhos  # controle positivo: a varredura achou a página
    for c in sorted(caminhos):
        assert client.get(c).status_code == 200, c
    citados = set(re.findall(r"/brand/oferta/([0-9a-f]+\.[a-z0-9]+)", corpo))
    em_disco = {p.name for p in (FRONTEND / "brand" / "oferta").iterdir()}
    assert citados == em_disco, (sorted(em_disco - citados), sorted(citados - em_disco))


def _plan_prices() -> dict[str, dict[str, int]]:
    """PLAN_PRICES da precos.html (fonte única do preço), em centavos."""
    fonte = (FRONTEND / "precos.html").read_text(encoding="utf-8")
    bloco = re.search(r"const PLAN_PRICES = \{(.*?)\};", fonte, re.S).group(1)
    cents = lambda rotulo: round(float(re.sub(r"[^\d,]", "", rotulo).replace(",", ".")) * 100)
    return {t: {"monthly": cents(m), "annual": cents(a)}
            for t, m, a in re.findall(r'(\w+):\s*\{\s*monthly:\s*"([^"]+)",\s*annual:\s*"([^"]+)"', bloco)}


def _brl(c: int) -> str:
    return f"R$ {c // 100},{c % 100:02d}" if c % 100 else f"R$ {c // 100}"


def _intent(nome: str) -> list[str]:
    js = (FRONTEND / "purchase-intent.js").read_text(encoding="utf-8")
    return re.findall(r'"(\w+)"', re.search(rf"const {nome} = \[(.*?)\]", js).group(1))


def _cards() -> dict[str, str]:
    corpo = _pagina()
    partes = re.split(r'<div data-plano="(\w+)"', corpo)[1:]
    return {t: texto for t, texto in zip(partes[::2], partes[1::2])}


def test_cartoes_de_plano_batem_com_a_precos_os_limites_e_a_compra():
    precos, planos, ciclos, cards = _plan_prices(), _intent("PLANS"), _intent("CYCLES"), _cards()
    assert tuple(cards) == TIERS and set(precos) == set(TIERS)
    for tier, card in cards.items():
        mensal, anual = precos[tier]["monthly"], precos[tier]["annual"]
        pegar = lambda cls: re.search(rf'class="pb-d {cls}"[^>]*>([^<]*)<', card).group(1)
        assert pegar("so-mensal") == _brl(mensal), tier
        assert pegar("so-anual") == _brl(anual // 12), tier  # "por mês" = anual ÷ 12, truncado
        assert f'{_brl(anual)} por ano · 2 meses grátis' in card, tier
        assert "Cobrado por mês" in card
        n = limits_for_tier(tier)["of_banks_max"]
        assert f"{n} {'banco conectado' if n == 1 else 'bancos conectados'}" in card, tier
        for ciclo, cls in (("annual", "so-anual"), ("monthly", "so-mensal")):
            href = re.search(rf'href="([^"]+)" class="[^"]*{cls}"', card).group(1)
            q = parse_qs(urlsplit(html.unescape(href)).query)
            assert q == {"plano": [tier], "ciclo": [ciclo]}, href
            assert tier in planos and ciclo in ciclos


def test_itens_dos_planos_seguem_os_gates_do_codigo(monkeypatch):
    from core.services import plan_service

    cards = _cards()
    assert FEATURE_MIN_TIER_V2["household_budget"] == "plus" and FEATURE_MIN_TIER_V2["cashflow"] == "pro"
    assert "Orçamento Doméstico" in cards["plus"] and "Orçamento Doméstico" not in cards["essencial"]

    def maior_horizonte(tier: str) -> int:
        # O horizonte vem do código (forecast_horizons_for), com o tier fixado no lugar do usuário.
        monkeypatch.setattr(plan_service, "get_plan_tier", lambda _uid: tier)
        monkeypatch.setattr(plan_service, "plans_v2_enabled", lambda: True)
        return max(plan_service.forecast_horizons_for(1), default=0)

    assert maior_horizonte("essencial") == 0 and "previsão de" not in cards["essencial"].lower()
    plus, pro = maior_horizonte("plus"), maior_horizonte("pro")
    assert 0 < plus < pro
    assert f"previsão de {plus} dias" in cards["plus"]
    assert f"Previsão de {pro} dias" in cards["pro"]
    assert f"{pro} dias" not in cards["plus"]


def test_selo_dos_agentes_e_rodape_sem_promessa_nem_placeholder():
    """O selo do time diz quantos agentes cada plano ativa (o Plus não roda os 7) e o
    rodapé não mostra razão social/CNPJ de modelo."""
    pagina = _pagina()
    plus, pro = limits_for_tier("plus")["agents_max"], limits_for_tier("pro")["agents_max"]
    assert 0 < plus < pro
    assert f"Até {plus} no Plus, os {pro} no Pro" in pagina
    assert "Nos planos Plus e Pro" not in pagina
    for sobra in ("Razão social", "00.000.000", "[CNPJ"):
        assert sobra not in pagina, sobra
