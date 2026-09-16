"""Guardas do adiamento de Meta Pixel/GA4/Clarity na landing.

Split de `test_landing_performance.py` (assunto de tracking, não de CSS/imagem)
para não estourar o teto de 350 linhas por arquivo (CLAUDE.md §0.5).
"""

import asyncio
import json
import re
import shutil
import subprocess

import pytest

from frontend.routes import shared
from frontend.routes.shared import (
    FRONTEND_DIR,
    _deferred_tracking_bootstrap,
    clarity_snippet,
    ga4_snippet,
    html_file,
    inject_tracking,
    meta_pixel_snippet,
)
from frontend.routes.static_pages import serve_landing


def _landing_servida() -> str:
    return asyncio.run(serve_landing()).body.decode("utf-8")


def test_clarity_da_landing_so_carrega_apos_caminho_critico(monkeypatch):
    monkeypatch.setattr(shared, "META_PIXEL_ID", "pixel-teste")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE")
    monkeypatch.setattr(shared, "CLARITY_PROJECT_ID", "clarity-teste")
    html = _landing_servida()

    # Download do SDK adiado nos dois grupos: nem gtag.js nem fbevents.js saem
    # síncronos na landing.
    assert '<script async src="https://www.googletagmanager.com/gtag/js?' not in html
    assert 'src="https://connect.facebook.net/en_US/fbevents.js"' not in html

    # Duas filas SEPARADAS: pbAdiarMarketing (Meta+GA4, bootstrap + 2 chamadas)
    # e pbAdiarTracking (só Clarity, bootstrap + 1 chamada).
    assert html.count("pbAdiarMarketing") == 3
    assert html.count("pbAdiarTracking") == 2

    # Ordem: cada bootstrap vem antes do snippet que usa a fila correspondente.
    marketing_bootstrap = html.index('w.pbAdiarMarketing=function')
    meta_uso = html.index("window.pbAdiarMarketing(function(){\n  var t=")
    ga4_uso = html.index("window.pbAdiarMarketing(function(){\n  var s=")
    clarity_bootstrap = html.index('w.pbAdiarTracking=function')
    clarity_uso = html.index("www.clarity.ms/tag/")
    assert marketing_bootstrap < meta_uso < ga4_uso < clarity_bootstrap < clarity_uso

    # Page views de atribuição não entram na fila adiada: se o visitante clicar
    # num CTA antes do timer, Meta e GA4 já estão no caminho normal de entrega.
    assert "fbq('track', 'PageView')" in html
    assert "window.dataLayer = window.dataLayer || []" in html
    assert "c[a]=c[a]||function()" in html


def test_sem_defer_external_meta_e_ga4_continuam_sincronos(monkeypatch):
    """Controle negativo: é o estado ATUAL do código antes deste fix (o bug de
    escopo em `inject_tracking` fazia `defer_external` nunca chegar no Meta/GA4).
    Sem `defer_external=True`, os dois SDKs continuam carregando de forma
    imediata, sem wrapper nenhum."""
    monkeypatch.setattr(shared, "META_PIXEL_ID", "pixel-teste")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE")
    meta = meta_pixel_snippet(defer_external=False)
    ga4 = ga4_snippet(defer_external=False)

    assert "connect.facebook.net" in meta
    assert 's.parentNode.insertBefore(t,s)}(window,\ndocument,' in meta
    assert "pbAdiarMarketing" not in meta

    assert '<script async src="https://www.googletagmanager.com/gtag/js?' in ga4
    assert "pbAdiarMarketing" not in ga4


def test_download_do_sdk_so_ocorre_apos_timer_ou_interacao(monkeypatch):
    """Controle positivo: reproduz o carregamento real no Node (mesmo padrão do
    teste de Clarity) e prova as 3 garantias do wrapper de marketing: (a) sem
    timer nem interação, nada carrega; (b) a interação libera os DOIS SDKs;
    (c) as chamadas síncronas (fbq/gtag) já rodaram mesmo sem o SDK ter
    carregado — ficam na fila do `dataLayer`/`fbq`, não na fila adiada."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node não disponível nesta máquina")
    monkeypatch.setattr(shared, "META_PIXEL_ID", "pixel-teste")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE")
    monkeypatch.setattr(shared, "CLARITY_PROJECT_ID", "")

    html = inject_tracking(
        "<html><head></head><body></body></html>", clarity=False, defer_external=True
    )
    scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", html, re.DOTALL)
    programa = f"""
global.window = global;
global.location = {{ href: 'https://pigbankai.com/', origin: 'https://pigbankai.com' }};
const carregados = [], ouvintes = {{}};
global.document = {{
  readyState: 'loading', referrer: '',
  head: {{ appendChild: s => carregados.push(s.src) }},
  createElement: () => ({{}}),
  getElementsByTagName: () => [{{ parentNode: {{ insertBefore: s => carregados.push(s.src) }} }}],
}};
global.addEventListener = (nome, fn) => {{ ouvintes[nome] = fn; }};
global.setTimeout = (fn, ms) => {{ ouvintes.timer = {{ fn, ms }}; return 1; }};
eval({json.dumps(chr(10).join(scripts))});
ouvintes.load();  // arma o setTimeout (não dispara: só o load, o teto ainda não venceu)
const antesDaInteracao = carregados.slice();
const fbqNaFila = typeof window.fbq === 'function';
const gtagNaFila = typeof window.gtag === 'function';
ouvintes.pointerdown();  // interação chega ANTES do teto de {shared.MARKETING_DEFER_DELAY_MS}ms
const aposInteracao = carregados.slice();
console.log(JSON.stringify({{ antesDaInteracao, aposInteracao, fbqNaFila, gtagNaFila, atraso: ouvintes.timer.ms }}));
"""
    resultado = subprocess.run([node, "-e", programa], capture_output=True, text=True, timeout=30)
    assert resultado.returncode == 0, resultado.stderr
    saida = json.loads(resultado.stdout)

    # (a) nada carregado antes de timer ou interação
    assert saida["antesDaInteracao"] == []
    # (c) fbq e gtag já existem (chamadas síncronas rodaram e ficaram na fila deles)
    assert saida["fbqNaFila"] is True
    assert saida["gtagNaFila"] is True
    # (b) a interação libera os dois SDKs
    assert any("fbevents.js" in url for url in saida["aposInteracao"])
    assert any("gtag/js" in url for url in saida["aposInteracao"])
    assert saida["atraso"] == shared.MARKETING_DEFER_DELAY_MS


def test_clarity_guarda_seu_proprio_atraso_isolado_do_marketing():
    """O Clarity continua em `pbAdiarTracking`/5000, isolado de
    `pbAdiarMarketing`/`MARKETING_DEFER_DELAY_MS` — um ajuste futuro no delay de
    marketing não pode arrastar o delay do Clarity junto por engano."""
    bootstrap_clarity = _deferred_tracking_bootstrap(5_000, "pbAdiarTracking")
    bootstrap_marketing = _deferred_tracking_bootstrap(
        shared.MARKETING_DEFER_DELAY_MS, "pbAdiarMarketing"
    )

    assert "w.pbAdiarTracking=function" in bootstrap_clarity
    assert "w.setTimeout(carregar,5000)" in bootstrap_clarity
    assert "pbAdiarMarketing" not in bootstrap_clarity

    assert "w.pbAdiarMarketing=function" in bootstrap_marketing
    assert f"w.setTimeout(carregar,{shared.MARKETING_DEFER_DELAY_MS})" in bootstrap_marketing
    assert "pbAdiarTracking" not in bootstrap_marketing

    # Delays diferentes de propósito — se algum dia forem iguais, a separação de
    # fila deixa de ter motivo (mas continua sendo a estrutura certa).
    assert shared.MARKETING_DEFER_DELAY_MS != 5_000


def test_defer_nao_vaza_do_funil_meta_ga4_continuam_sincronos_fora_da_landing(monkeypatch):
    """Pedido explícito do dono do repo: o adiamento é exclusivo da landing.
    /cadastro e /precos continuam com Meta e GA4 síncronos e imediatos — sem
    `pbAdiarMarketing`, sem wrapper, igual a hoje."""
    monkeypatch.setattr(shared, "META_PIXEL_ID", "pixel-teste")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE")

    # Mesma chamada que static_pages.py faz para cada rota (nenhuma passa
    # defer_tracking=True fora da landing).
    for nome, extra in (("cadastro.html", {}), ("precos.html", {"clarity": True})):
        html = html_file(FRONTEND_DIR / nome, **extra).body.decode("utf-8")
        assert "pbAdiarMarketing" not in html
        assert 'data-pb-tracking="deferred"' not in html
        assert '<script async src="https://www.googletagmanager.com/gtag/js?' in html
        assert 's.parentNode.insertBefore(t,s)}(window,\ndocument,' in html


def test_clarity_deferido_respeita_o_atraso(monkeypatch):
    node = shutil.which("node")
    if not node:
        pytest.skip("node não disponível nesta máquina")
    monkeypatch.setattr(shared, "META_PIXEL_ID", "pixel-teste")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE")
    monkeypatch.setattr(shared, "CLARITY_PROJECT_ID", "clarity-teste")

    snippets = _deferred_tracking_bootstrap() + clarity_snippet(defer_external=True)
    scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", snippets, re.DOTALL)
    programa = f"""
global.window = global;
global.location = {{ href: 'https://pigbankai.com/', origin: 'https://pigbankai.com' }};
const carregados = [], ouvintes = {{}};
global.document = {{
  readyState: 'loading', referrer: '',
  head: {{ appendChild: s => carregados.push(s.src) }},
  createElement: () => ({{}}),
  getElementsByTagName: () => [{{ parentNode: {{ insertBefore: s => carregados.push(s.src) }} }}],
}};
global.addEventListener = (nome, fn) => {{ ouvintes[nome] = fn; }};
global.setTimeout = (fn, ms) => {{ ouvintes.timer = {{ fn, ms }}; return 1; }};
eval({json.dumps(chr(10).join(scripts))});
const antes = carregados.slice();
const fila = {{ clarity: typeof clarity }};
ouvintes.load();
const aposLoad = carregados.slice();
ouvintes.timer.fn();
console.log(JSON.stringify({{ antes, aposLoad, depois: carregados, atraso: ouvintes.timer.ms, fila }}));
"""
    resultado = subprocess.run(
        [node, "-e", programa], capture_output=True, text=True, timeout=30
    )
    assert resultado.returncode == 0, resultado.stderr
    saida = json.loads(resultado.stdout)

    assert saida["antes"] == []
    assert saida["aposLoad"] == []
    assert saida["atraso"] == 5_000
    assert saida["fila"] == {"clarity": "function"}
    assert any("clarity.ms" in url for url in saida["depois"])
