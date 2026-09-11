"""`LAUNCH_TYPE_LABELS` mora num arquivo só (CLAUDE.md §0.7, issue #293).

O rótulo de `tipo` era um literal dentro do `dashboard.js`, inalcançável pela
Início — que por isso imprimia `credito`/`deposito_caixinha` crus no
`#greeting-sub`. Agora as duas páginas carregam `frontend/launch-type-labels.js`.

Este grupo lacra a ESTRUTURA (uma declaração só, e as duas páginas a carregam).
O COMPORTAMENTO (que rótulo sai na tela) é
`tests/frontend/home_tipo_atividade.test.mjs`.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

FONTE = FRONTEND / "launch-type-labels.js"
HOME = (FRONTEND / "home.html").read_text(encoding="utf-8")
DASH = (FRONTEND / "dashboard.html").read_text(encoding="utf-8")


def test_a_fonte_unica_declara_o_mapa():
    texto = FONTE.read_text(encoding="utf-8")
    assert "const LAUNCH_TYPE_LABELS = {" in texto
    # os dois acréscimos que fazem a Início funcionar sem mudar o dashboard
    assert 'despesa: "despesa"' in texto
    assert 'receita: "receita"' in texto


def test_dashboard_js_nao_redeclara_o_mapa():
    """Duas declarações = `SyntaxError` no dashboard (mesmo escopo global) e a
    fonte única virando duas versões da mesma regra."""
    texto = (FRONTEND / "dashboard.js").read_text(encoding="utf-8")
    assert "LAUNCH_TYPE_LABELS" in texto, "o dashboard continua CONSUMINDO o mapa"
    assert not re.search(r"\b(const|let|var)\s+LAUNCH_TYPE_LABELS\b", texto), (
        "dashboard.js voltou a declarar LAUNCH_TYPE_LABELS; ele só pode consumir "
        "o que frontend/launch-type-labels.js declara"
    )


def _tag(html: str, src: str):
    """A TAG `<script src="…">`, não a string solta.

    Asserir `"/launch-type-labels.js" in html` passava com a tag APAGADA: o
    comentário do home.html:945 cita o caminho e satisfazia o `in`. O `?v=` é
    opcional porque o `stamp_asset_versions` recarimba a versão em produção.
    """
    return re.search(r'<script[^>]*\ssrc="' + re.escape(src) + r'(\?[^"]*)?"[^>]*>', html)


def test_as_duas_paginas_carregam_a_fonte_unica():
    for nome, html in (("home.html", HOME), ("dashboard.html", DASH)):
        assert _tag(html, "/launch-type-labels.js"), (
            f"{nome} não tem a TAG <script src='/launch-type-labels.js'> "
            "(citar o caminho num comentário não carrega o arquivo)"
        )


def test_no_dashboard_a_fonte_vem_antes_do_dashboard_js():
    """`defer` executa na ordem do documento: o mapa tem de ser parseado antes
    de o dashboard.js rodar."""
    fonte = _tag(DASH, "/launch-type-labels.js")
    dash_js = _tag(DASH, "/dashboard.js")
    assert fonte, "dashboard.html não tem a tag de /launch-type-labels.js"
    assert dash_js, "dashboard.html não tem a tag de /dashboard.js"
    assert fonte.start() < dash_js.start(), (
        "a fonte única tem de vir antes do dashboard.js no documento"
    )


# NÃO existe aqui um `test_na_home_a_fonte_carrega_sem_defer`, e a remoção é
# deliberada. A razão que ele trazia era FALSA: dizia que o boot inline
# (`PBNav.boot("home")`) roda durante o parse e alcança o `renderGreeting` — mas
# o `data-pb-boot` é o ÚLTIMO script do documento (home.html:2394) e as duas
# chamadas de `renderGreeting` estão atrás de um `await` no /auth/validate
# (home.html:1539 e :1632). Sobrava um risco estreitíssimo (download lento do
# .js + /auth/validate resolvendo instantâneo com o parser ainda girando) que,
# desde que o home.html lê o mapa com `typeof` + `hasOwnProperty`, custa um
# rótulo genérico por milissegundos — e não um `ReferenceError`. Lacre sem
# defeito para prender não merece um caso.
