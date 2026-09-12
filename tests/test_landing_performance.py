"""Guardas das otimizações de caminho crítico e imagens da landing."""

import re
from types import SimpleNamespace

from PIL import Image

from frontend.routes.shared import FRONTEND_DIR, _asset_hash, stamp_asset_versions
from frontend.routes.static_pages import _CACHE_IMUTAVEL, _cache_asset_versionado


def _landing_servida() -> str:
    return stamp_asset_versions(
        (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    )


def test_css_da_landing_sai_versionado_por_conteudo():
    html = _landing_servida()
    folhas = re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)', html)
    assert len(folhas) == 4
    for nome in ("brand.css", "phosphor.css", "site.css", "site-redesign.css"):
        esperado = _asset_hash(nome, (FRONTEND_DIR / nome).stat().st_mtime_ns)
        assert f"/{nome}?v={esperado}" in folhas


def test_assets_versionados_tem_cache_imutavel_sem_cachear_url_nua():
    html = _landing_servida()
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
    assert 'srcset="/brand/landing-mascot-150.webp?v=1"' in html


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


def test_safe_area_nao_bloqueia_primeira_pintura():
    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert '<script defer src="/safe-area.js?v=1"></script>' in html
