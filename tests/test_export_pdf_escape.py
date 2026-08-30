"""Texto do usuário no PDF é CONTEÚDO, não marcação (#145).

`reportlab.platypus.Paragraph` interpreta um dialeto de mini-XML. Todo texto livre
do extrato — `categoria`, `alvo`, `nota` e o nome do cartão — passa pelo `par()` do
`_render_pdf`, então sem escape o usuário escreve marcação no PDF do próprio mês.

Medido na `main` (reportlab 5.0.0), antes do conserto:

    "<b>gastos"                -> ValueError: a exportação do MÊS INTEIRO devolve
                                  500 e CONTINUA 500 enquanto o lançamento existir
    "<img src='/caminho'/>"    -> OSError com fileName='/caminho': o processo ABRE
                                  o arquivo do servidor (leitura de arquivo local);
                                  com uma URL no lugar do caminho, a request sai

CONTROLE NEGATIVO — tire o `escape(...)` do `par()`
(`frontend/finance_bot_websocket_custom.py`) e volte ao `Paragraph(str(text), ...)`:
`test_markup_na_nota_nao_derruba_o_pdf` e `test_img_nao_abre_arquivo_do_servidor`
ficam VERMELHOS. Injetado nos dois casos, que estavam verdes.

CONTROLE POSITIVO — `test_texto_legitimo_continua_no_pdf`: o conserto RESTRINGE
(passa a escapar), então precisa provar que não mutila o texto de quem escreveu
certo. `McDonald's`, `café & pão` e `a*b` continuam saindo legíveis no PDF, com o
caractere original e não com a entidade.

CLASSE CEGA desta verificação: ela cobre o `_render_pdf`. As outras duas saídas do
mesmo `items` — `_render_xlsx` e o CSV — têm a sua própria classe de injeção
(fórmula começando com `=`/`+`/`-`/`@`), que NÃO é esta e não é testada aqui.
"""
from __future__ import annotations

from datetime import datetime

import pytest

pytest.importorskip("reportlab", reason="exportação de PDF não é exercitável sem reportlab")
pytest.importorskip("pypdf", reason="ler o PDF de volta é o que prova que o texto sobreviveu")

import frontend.finance_bot_websocket_custom as app_mod


def _item(*, categoria: str = "alimentação", descricao: str = "mercado") -> dict:
    """Uma despesa de conta — a forma que o `_fetch_export_items` produz."""
    return {
        "data": datetime(2026, 8, 15, 12, 0),
        "natureza": "despesa",
        "label": "Despesa",
        "sign": "-",
        "categoria": categoria,
        "descricao": descricao,
        "valor": 39.90,
    }


def _texto_do_pdf(blob: bytes) -> str:
    import io

    from pypdf import PdfReader

    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(blob)).pages)


def test_markup_na_nota_nao_derruba_o_pdf():
    """`<b>gastos` na nota: 500 permanente na `main`, PDF normal com o conserto."""
    blob = app_mod._render_pdf([_item(descricao="<b>gastos")], 2026, 8)

    assert blob.startswith(b"%PDF")
    # A tag sai como TEXTO do lançamento, não como negrito que vaza pelo resto.
    assert "<b>gastos" in _texto_do_pdf(blob)


def test_img_nao_abre_arquivo_do_servidor(tmp_path):
    """`<img src=...>` deixa de ser tag: o arquivo apontado não é aberto.

    O alvo é um arquivo que EXISTE e não é imagem. Sem escape, o reportlab o abre e
    estoura ao tentar decodificar — é a prova de que a leitura aconteceu. Com
    escape, o caminho é só texto e nada no disco é tocado.
    """
    alvo = tmp_path / "segredo.txt"
    alvo.write_text("conteudo que o PDF nao pode ler", encoding="utf-8")

    blob = app_mod._render_pdf(
        [_item(categoria=f'<img src="{alvo}" width="1" height="1"/>')], 2026, 8
    )

    assert blob.startswith(b"%PDF")
    assert "conteudo que o PDF nao pode ler" not in _texto_do_pdf(blob)


def test_texto_legitimo_continua_no_pdf():
    """CONTROLE POSITIVO: escapar não pode mutilar quem escreveu certo.

    `&` é o caractere que o escape reescreve (`&amp;`) — se o PDF mostrasse a
    entidade em vez do caractere, o conserto teria trocado um bug por outro.
    """
    blob = app_mod._render_pdf(
        [
            _item(categoria="café & pão", descricao="McDonald's"),
            _item(categoria="a*b", descricao="conta 100% paga"),
        ],
        2026,
        8,
    )
    texto = _texto_do_pdf(blob)

    for esperado in ("café & pão", "McDonald's", "a*b", "conta 100% paga"):
        assert esperado in texto, f"{esperado!r} sumiu do PDF"
    assert "&amp;" not in texto
