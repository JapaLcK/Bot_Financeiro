"""GA4: evento em página sem a tag base não vira erro — vira funil vazio.

`gtag` é criado pelo snippet do `<head>` (`shared.ga4_snippet`). Quando ele não
está na página, as chamadas de evento caem na própria guarda (`if (window.gtag)`)
e somem: nada no console, nada no GA4, e a impressão de que "o Analytics não
pegou". Foi exatamente o estado em que este PR encontrou o repositório — a tag
chumbada só na `index.html`, e os eventos disparando na /precos, /cadastro,
/completar-cadastro, /onboarding e /home.

Por isso os testes abaixo são de CATEGORIA, não de instância: eles varrem quem
dispara evento e exigem a tag de todos, em vez de conferir as páginas que alguém
lembrou de listar.
"""

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from frontend.routes import shared

RAIZ = Path(__file__).resolve().parent.parent
FRONTEND = RAIZ / "frontend"

# `gtag("event", ...)` e `gtag('event', ...)`. O `config` do snippet base não
# casa aqui de propósito: quem precisa da tag é quem dispara evento.
_CHAMA_EVENTO = re.compile(r"""gtag\(\s*["']event["']""")

_ID_FALSO = "G-TESTE00000"

# A tag base é reconhecida pela URL do script, não pela string "gtag": esta
# aparece no corpo de qualquer página que dispare evento, então casaria mesmo
# com a injeção desligada — o teste passaria medindo nada.
_TAG_BASE = "https://www.googletagmanager.com/gtag/js?id="


def _paginas_que_disparam_evento() -> set[str]:
    return {
        p.name
        for p in FRONTEND.glob("*.html")
        if _CHAMA_EVENTO.search(p.read_text(encoding="utf-8", errors="replace"))
    }


def _cadeia_frontend_dir(no) -> list[str] | None:
    """`FRONTEND_DIR / "a.html"` → `["a.html"]`. Qualquer outra coisa → None."""
    partes: list[str] = []
    while isinstance(no, ast.BinOp) and isinstance(no.op, ast.Div):
        if not (isinstance(no.right, ast.Constant) and isinstance(no.right.value, str)):
            return None
        partes.insert(0, no.right.value)
        no = no.left
    if isinstance(no, ast.Name) and no.id == "FRONTEND_DIR" and partes:
        return partes
    return None


def _paginas_servidas_sem_rastreio() -> set[str]:
    """Páginas entregues com `html_file(..., pixel=False)`.

    `ast` e não regex pelo mesmo motivo do `test_frontend_assets_e_rotas.py`:
    docstring e comentário citando a chamada não são chamada.
    """
    arvore = ast.parse((FRONTEND / "routes" / "static_pages.py").read_text(encoding="utf-8"))
    fora = set()
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                and no.func.id == "html_file" and no.args):
            continue
        desligado = any(
            k.arg == "pixel" and isinstance(k.value, ast.Constant) and k.value.value is False
            for k in no.keywords
        )
        partes = _cadeia_frontend_dir(no.args[0])
        if desligado and partes:
            fora.add(partes[-1])
    return fora


def test_toda_pagina_que_dispara_evento_recebe_a_tag_base(monkeypatch):
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", _ID_FALSO)
    paginas = _paginas_que_disparam_evento()

    # Sem isto o teste ficaria verde varrendo lista vazia — o modo de falha real
    # de todo teste que itera sobre uma varredura.
    assert len(paginas) >= 4, f"varredura não achou os disparos de evento: {paginas}"

    for nome in sorted(paginas):
        corpo = shared.html_file(FRONTEND / nome).body.decode()
        assert _TAG_BASE + _ID_FALSO in corpo, f"{nome} dispara evento sem a tag base do GA4"


def test_comecar_html_recebe_a_tag(monkeypatch):
    """O `onboarding_complete` mora no `comecar.js`, que a varredura acima não
    alcança (ela lê HTML). A página que carrega esse JS é esta."""
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", _ID_FALSO)
    assert 'gtag("event", "onboarding_complete"' in (
        FRONTEND / "comecar.js").read_text(encoding="utf-8")
    assert _TAG_BASE in shared.html_file(FRONTEND / "comecar.html").body.decode()


def test_sem_measurement_id_nao_injeta_nada(monkeypatch):
    """Controle negativo dos dois testes acima: com a injeção desligada, os
    asserts têm de ficar vermelhos. Se passassem assim, não mediriam nada.

    É também o contrato de dev/staging: `GA4_MEASUREMENT_ID=` vazio → site sem
    rastreio nenhum, como o `META_PIXEL_ID` já fazia."""
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "")
    monkeypatch.setattr(shared, "META_PIXEL_ID", "")
    corpo = shared.html_file(FRONTEND / "precos.html").body.decode()
    assert "googletagmanager" not in corpo
    assert "connect.facebook.net" not in corpo


def test_paginas_do_funil_nao_sao_servidas_com_pixel_false():
    """A tag só entra quando a rota chama `html_file` sem `pixel=False`. Este é o
    outro lado do primeiro teste: lá o arquivo tem a tag depois de injetada; aqui,
    que a rota que o entrega não desliga a injeção."""
    sem_rastreio = _paginas_servidas_sem_rastreio()

    # Guarda anti-varredura-vazia: estas duas são `pixel=False` por decisão
    # explícita (área logada). Se sumirem, o `ast` parou de enxergar as chamadas
    # e o assert seguinte passou a ser sobre um conjunto vazio.
    assert {"dashboard.html", "settings.html"} <= sem_rastreio

    conflito = _paginas_que_disparam_evento() & sem_rastreio
    assert not conflito, f"páginas disparam evento mas são servidas sem rastreio: {conflito}"


def _config_do_snippet(url: str) -> dict:
    """Roda o snippet REAL (saída de `ga4_snippet`) no node, com `location`
    falsa, e devolve o 3º argumento do `gtag('config', ...)`.

    Executa o código em vez de procurar texto nele: o que interessa é o que o
    navegador manda pro Google, não o que está escrito no Python.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node não disponível nesta máquina")
    inline = shared.ga4_snippet().split("<script>", 1)[1].split("</script>", 1)[0]
    programa = (
        "global.window = global;\n"
        f"global.location = {{ href: {json.dumps(url)} }};\n"
        f"{inline}\n"
        "const cfg = dataLayer.map(a => Array.from(a)).filter(a => a[0] === 'config').pop();\n"
        "console.log(JSON.stringify(cfg));\n"
    )
    saida = subprocess.run([node, "-e", programa], capture_output=True, text=True, timeout=30)
    assert saida.returncode == 0, saida.stderr
    return json.loads(saida.stdout)[2]


def test_page_view_nao_leva_token_nem_id_de_transacao(monkeypatch):
    """O page_view automático sai junto com o `config`, no <head> — ANTES de
    qualquer JS da página limpar a URL. Sem esta limpeza, o `token` do
    /completar-cadastro (uma credencial) e o `sid` da sessão do Stripe iriam
    inteiros pro Google, e cada compra viraria uma "página" diferente."""
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", _ID_FALSO)

    cfg = _config_do_snippet(
        "https://pigbankai.com/home?upgrade=success&sid=cs_test_123&ev=purchase&pl=plus")
    assert "cs_test_123" not in cfg["page_location"]
    assert "sid=" not in cfg["page_location"]
    # O resto da query SOBREVIVE: sem isto, a limpeza podia estar apagando tudo
    # (inclusive o que separa a volta do checkout de um /home comum).
    assert "upgrade=success" in cfg["page_location"]
    assert "ev=purchase" in cfg["page_location"]

    cfg = _config_do_snippet("https://pigbankai.com/completar-cadastro?token=SEGREDO-123")
    assert "SEGREDO-123" not in cfg["page_location"]

    # Página sem query nenhuma continua com a URL inteira.
    cfg = _config_do_snippet("https://pigbankai.com/precos")
    assert cfg["page_location"] == "https://pigbankai.com/precos"


def test_csp_libera_o_host_do_gtag():
    """Sem `googletagmanager.com` no `script-src`, o navegador bloqueia o script e
    o GA4 inteiro morre em silêncio — o único sinal fica no console do usuário."""
    from frontend.finance_bot_websocket_custom import _SECURITY_HEADERS

    csp = _SECURITY_HEADERS["Content-Security-Policy"]
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "https://www.googletagmanager.com" in script_src
