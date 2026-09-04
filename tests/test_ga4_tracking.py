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

# Os DOIS jeitos de disparar evento neste repositório: `gtag("event", ...)` e o
# `pbTrack(...)` (que é o mesmo evento, esperando o envio antes de navegar — ver
# o snippet). O `config` do snippet base não casa aqui de propósito: quem precisa
# da tag é quem dispara evento.
# Contar só o `gtag("event"` deixaria de fora justamente as páginas que passaram
# a usar pbTrack — a varredura encolheria sem ninguém notar.
_CHAMA_EVENTO = re.compile(r"""gtag\(\s*["']event["']|\bpbTrack\(""")

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
    assert 'pbTrack("onboarding_complete"' in (
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


def _rodar_snippet(url: str, referrer: str = "", depois: str = "") -> dict:
    """Executa o snippet REAL (saída de `ga4_snippet`) no node, com `location` e
    `document.referrer` falsos, e devolve o que o programa imprimir.

    Executa em vez de procurar texto: o que interessa é o que o navegador manda
    pro Google, não o que está escrito no Python. `setTimeout` é substituído por
    uma fila — assim o teto de 1s do `pbTrack` é disparado à mão, sem esperar.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node não disponível nesta máquina")
    inline = shared.ga4_snippet().split("<script>", 1)[1].split("</script>", 1)[0]
    programa = (
        "global.window = global;\n"
        f"global.location = {{ href: {json.dumps(url)} }};\n"
        f"global.document = {{ referrer: {json.dumps(referrer)} }};\n"
        "const agendados = [];\n"
        "global.setTimeout = function(fn, ms){ agendados.push({fn: fn, ms: ms}); return 0; };\n"
        f"{inline}\n"
        "const config = dataLayer.map(a => Array.from(a)).filter(a => a[0] === 'config').pop();\n"
        "const saida = { config: config ? config[2] : null };\n"
        f"{depois or 'console.log(JSON.stringify(saida));'}\n"
    )
    saida = subprocess.run([node, "-e", programa], capture_output=True, text=True, timeout=30)
    assert saida.returncode == 0, saida.stderr
    return json.loads(saida.stdout)


def test_page_view_nao_leva_token_nem_id_de_transacao(monkeypatch):
    """O page_view automático sai junto com o `config`, no <head> — ANTES de
    qualquer JS da página limpar a URL. Sem esta limpeza, o `token` do
    /completar-cadastro (uma credencial) e o `sid` da sessão do Stripe iriam
    inteiros pro Google, e cada compra viraria uma "página" diferente."""
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", _ID_FALSO)

    cfg = _rodar_snippet(
        "https://pigbankai.com/home?upgrade=success&sid=cs_test_123&ev=purchase&pl=plus")["config"]
    assert "cs_test_123" not in cfg["page_location"]
    assert "sid=" not in cfg["page_location"]
    # O resto da query SOBREVIVE: sem isto, a limpeza podia estar apagando tudo
    # (inclusive o que separa a volta do checkout de um /home comum).
    assert "upgrade=success" in cfg["page_location"]
    assert "ev=purchase" in cfg["page_location"]

    cfg = _rodar_snippet("https://pigbankai.com/completar-cadastro?token=SEGREDO-123")["config"]
    assert "SEGREDO-123" not in cfg["page_location"]

    # Página sem query nenhuma continua com a URL inteira.
    cfg = _rodar_snippet("https://pigbankai.com/precos")["config"]
    assert cfg["page_location"] == "https://pigbankai.com/precos"


def test_referrer_tambem_e_limpo(monkeypatch):
    """A outra ponta do mesmo vazamento: sair de `/completar-cadastro?token=…`
    (Cancelar, Termos, Privacidade) põe a URL de origem INTEIRA no
    `document.referrer`, e o GA4 tira o `page_referrer` dali. Limpar só o
    `page_location` deixava o token sair mesmo assim, pela página seguinte."""
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", _ID_FALSO)

    cfg = _rodar_snippet(
        "https://pigbankai.com/termos",
        referrer="https://pigbankai.com/completar-cadastro?token=SEGREDO-123",
    )["config"]
    assert "SEGREDO-123" not in json.dumps(cfg)
    assert cfg["page_referrer"].startswith("https://pigbankai.com/completar-cadastro")

    # Sem referrer (visita direta) o campo simplesmente não vai.
    cfg = _rodar_snippet("https://pigbankai.com/precos")["config"]
    assert "page_referrer" not in cfg


def test_pbtrack_navega_mesmo_quando_o_gtag_js_nao_responde(monkeypatch):
    """O caso que este helper existe para resolver: `gtag` já está definido pelo
    snippet, mas o gtag.js ainda não carregou, então o `event_callback` NUNCA é
    chamado. Sem o teto, o usuário ficaria preso na página; com ele, navega em 1s.

    E o outro lado: quando o gtag.js responde, `depois` roda UMA vez só — o teto
    não pode disparar uma segunda navegação."""
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", _ID_FALSO)

    programa = """
    // (1) gtag.js NÃO carregou: o stub do snippet só empilha no dataLayer.
    let semResposta = 0;
    pbTrack('begin_checkout', { value: 9.9 }, function(){ semResposta++; });
    const antesDoTeto = semResposta;
    const tetoMs = agendados[agendados.length - 1].ms;
    agendados.pop().fn();                       // o teto de segurança
    const depoisDoTeto = semResposta;

    // (2) gtag.js carregou e chama o callback.
    let comResposta = 0;
    pbTrack('sign_up', { method: 'email' }, function(){ comResposta++; });
    const evento = dataLayer.map(a => Array.from(a)).filter(a => a[0] === 'event').pop();
    evento[2].event_callback();
    const aposCallback = comResposta;
    agendados.pop().fn();                       // o teto ainda dispara depois
    const aposTeto = comResposta;

    saida.pb = { antesDoTeto, depoisDoTeto, tetoMs, aposCallback, aposTeto,
                 params: evento[2], nome: evento[1] };
    console.log(JSON.stringify(saida));
    """
    r = _rodar_snippet("https://pigbankai.com/precos", depois=programa)["pb"]

    # (1) sem resposta do gtag.js, quem navega é o teto — e ele existe
    assert r["antesDoTeto"] == 0
    assert r["depoisDoTeto"] == 1
    assert r["tetoMs"] <= 1000, "teto longo demais trava o usuário na página"

    # (2) com resposta, navega no callback e o teto NÃO repete
    assert r["aposCallback"] == 1
    assert r["aposTeto"] == 1

    # os parâmetros do chamador sobrevivem ao empacotamento
    assert r["nome"] == "sign_up"
    assert r["params"]["method"] == "email"
    assert r["params"]["event_timeout"] <= 1000


def test_csp_libera_o_host_do_gtag():
    """Sem `googletagmanager.com` no `script-src`, o navegador bloqueia o script e
    o GA4 inteiro morre em silêncio — o único sinal fica no console do usuário."""
    from frontend.finance_bot_websocket_custom import _SECURITY_HEADERS

    csp = _SECURITY_HEADERS["Content-Security-Policy"]
    script_src = csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "https://www.googletagmanager.com" in script_src
