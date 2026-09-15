"""Guardas das otimizações de caminho crítico e imagens da landing."""

import asyncio
import re
from types import SimpleNamespace

from PIL import Image

from frontend.routes.shared import (
    FRONTEND_DIR,
    _asset_hash,
    html_file,
    stamp_asset_versions,
)
from frontend.routes.static_pages import (
    _CACHE_IMUTAVEL,
    _cache_asset_versionado,
    serve_landing,
)


def _landing_servida() -> str:
    return asyncio.run(serve_landing()).body.decode("utf-8")


def _landing_com_links_versionados() -> str:
    return stamp_asset_versions(
        (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    )


def test_css_da_landing_e_incorporado_no_html_para_nao_bloquear_a_primeira_pintura():
    html = _landing_servida()
    nomes = ("brand.css", "phosphor.css", "site.css", "site-redesign.css")
    assert re.findall(r'<style data-pb-inline="([^"]+)">', html) == list(nomes)
    for nome in nomes:
        assert f'href="/{nome}?v=' not in html
        inicio_css = (FRONTEND_DIR / nome).read_text(encoding="utf-8")[:200]
        assert inicio_css in html


def test_fonte_do_titulo_e_descoberta_antes_do_css_inline():
    html = _landing_servida()
    brand_css = (FRONTEND_DIR / "brand.css").read_text(encoding="utf-8")
    url_fonte = re.search(
        r'url\("(/fonts/Inter-Variable\.woff2\?v=[0-9a-f]{12})"\)',
        brand_css,
    ).group(1)
    preload = re.search(
        rf'<link rel="preload" href="{re.escape(url_fonte)}" '
        r'as="font" type="font/woff2" crossorigin\s*/?>',
        html,
    )
    assert preload
    assert preload.start() < html.index('<style data-pb-inline="brand.css">')


def test_css_continua_externo_nas_demais_paginas_publicas():
    html = html_file(FRONTEND_DIR / "precos.html", pixel=False).body.decode("utf-8")
    assert re.search(r'href="/brand\.css\?v=[0-9a-f]{12}"', html)
    assert 'data-pb-inline="brand.css"' not in html


def test_assets_versionados_tem_cache_imutavel_sem_cachear_url_nua():
    html = _landing_com_links_versionados()
    versao = re.search(r'/site\.css\?v=([0-9a-f]{12})', html).group(1)
    assert _cache_asset_versionado(SimpleNamespace(query_params={"v": versao})) == _CACHE_IMUTAVEL
    assert _cache_asset_versionado(SimpleNamespace(query_params={})) == "no-cache"
    assert _cache_asset_versionado(SimpleNamespace(query_params={"v": "1"})) == "no-cache"


def test_landing_tem_landmark_principal():
    html = _landing_servida()
    assert html.count("<main ") == 1
    assert html.count("</main>") == 1


def test_imagens_exclusivas_da_landing_tem_dimensoes_e_orcamento():
    esperadas = {
        "landing-logo.webp": ((202, 60), 8_000),
        "landing-mascot.webp": ((300, 486), 35_000),
        "landing-mascot-150.webp": ((150, 243), 12_000),
        "landing-icon.webp": ((52, 55), 3_000),
        "vsl-poster-860.webp": ((860, 484), 35_000),
    }
    for nome, (dimensoes, teto) in esperadas.items():
        caminho = FRONTEND_DIR / "brand" / nome
        with Image.open(caminho) as imagem:
            assert imagem.size == dimensoes
        assert caminho.stat().st_size <= teto

    html = _landing_servida()
    for nome in esperadas:
        assert f"/brand/{nome}" in html
    assert 'preload="none"' in html
    assert 'width="101" height="30"' in html
    assert 'width="300" height="486"' in html
    assert 'media="(max-width: 560px)"' in html
    assert (
        'srcset="/brand/landing-mascot-150.webp?v=1 1x, '
        '/brand/landing-mascot.webp?v=1 2x"' in html
    )


def test_mascote_preserva_proporcao_quando_o_mobile_reduz_a_largura():
    css = (FRONTEND_DIR / "site.css").read_text(encoding="utf-8")
    regra = re.search(r"\.hero-mascot\s*\{([^}]+)\}", css)
    assert regra
    assert "height: auto" in regra.group(1)


def test_lcp_nao_fica_em_animacao_nao_composta():
    css = (FRONTEND_DIR / "site-redesign.css").read_text(encoding="utf-8")
    regra = re.search(r"\.rd \.grad\s*\{([^}]+)\}", css)
    assert regra
    assert "animation:" not in regra.group(1)
    assert "@keyframes rdGrad" not in css


def test_safe_area_critica_e_inicializada_inline_antes_da_primeira_pintura():
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert 'src="/safe-area.js' not in html
    assert 'document.documentElement.classList.add("pb-safe")' in html
    assert "viewport-fit=cover" in html
    assert "html.pb-safe body" in html
