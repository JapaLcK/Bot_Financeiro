"""OF-01, defeito 1: `/investments` era UMA chamada sem `page`, e `results`
ausente/não-lista virava `[]` — indistinguível de carteira vazia.

Por que isso passou a ser dinheiro: o retorno de `list_pluggy_investments`
autoriza a reconciliação de `save_open_finance_investments` (posição que não veio
= posição que saiu do banco → a caixinha dela é removida). Leitura parcial com
cara de lista curta apaga caixinha com dinheiro dentro. O sinal de "não consegui
ler" é a EXCEÇÃO — `core/services/pluggy_sync.py` já a lê como
`investments_ok=False` e não remove nada.

Tudo passa pela fixture `pluggy_responde` (tests/conftest.py), então o caminho
exercitado é o real: `_pluggy_get` → `_raise_for_pluggy_response`.

CONTROLE NEGATIVO do grupo (medido, ver relato): repor o corpo antigo de
`list_pluggy_investments` (uma chamada, `results` ou `[]`) deixa 6 dos 8
vermelhos. CONTROLE POSITIVO: `test_carteira_vazia_valida_nao_levanta` e
`test_le_todas_as_paginas` — o conserto RESTRINGE (passa a recusar resposta), e
sem eles o grupo passaria num código que recusasse toda leitura, que é pior que
o bug.
"""
import pytest

from core.services.pluggy import PluggyApiError
from core.services.pluggy_investments import list_pluggy_investments


def _pos(pid: str) -> dict:
    return {"id": pid, "name": f"CDB {pid}", "type": "FIXED_INCOME",
            "subtype": "CDB", "balance": 100.0}


def test_le_todas_as_paginas(pluggy_responde):
    pluggy_responde([
        {"page": 1, "totalPages": 3, "total": 5, "results": [_pos("a"), _pos("b")]},
        {"page": 2, "totalPages": 3, "total": 5, "results": [_pos("c"), _pos("d")]},
        {"page": 3, "totalPages": 3, "total": 5, "results": [_pos("e")]},
        # Releitura de TODAS as páginas, estável: devolve o mesmo.
        {"page": 1, "totalPages": 3, "total": 5, "results": [_pos("a"), _pos("b")]},
        {"page": 2, "totalPages": 3, "total": 5, "results": [_pos("c"), _pos("d")]},
        {"page": 3, "totalPages": 3, "total": 5, "results": [_pos("e")]},
    ])

    out = list_pluggy_investments("item-1", "k")

    assert [i["id"] for i in out] == ["a", "b", "c", "d", "e"]
    assert [c["page"] for c in pluggy_responde.chamadas] == [1, 2, 3, 1, 2, 3]
    assert len(pluggy_responde.chamadas) == 6, "N páginas + N relidas, nem uma a mais"
    assert all(c["itemId"] == "item-1" for c in pluggy_responde.chamadas)
    # `pageSize` fica no default do servidor: o irmão /v2/transactions devolve 400 com ele.
    assert all("pageSize" not in c for c in pluggy_responde.chamadas)


def test_carteira_vazia_valida_nao_levanta(pluggy_responde):
    """CONTROLE POSITIVO: carteira vazia de verdade continua sendo `[]` sem erro —
    é ela que autoriza o D0 (reconciliar a conexão inteira)."""
    pluggy_responde({"page": 1, "total": 0, "totalPages": 0, "results": []})

    assert list_pluggy_investments("item-1", "k") == []
    assert len(pluggy_responde.chamadas) == 1


@pytest.mark.parametrize("payload", [{}, {"results": {}}])
def test_results_ausente_ou_nao_lista_levanta(pluggy_responde, payload):
    pluggy_responde(payload)

    with pytest.raises(PluggyApiError, match="incompleta"):
        list_pluggy_investments("item-1", "k")


def test_page_zero_da_referencia_e_leitura_incompleta(pluggy_responde):
    """A referência da Pluggy mostra `"page": 0` num exemplo; o guia manda começar
    em 1. Pedimos 1: se a API for 0-based, a página 0 inteira some — e página não
    lida vira posição "ausente" → caixinha removida. Eco divergente é leitura
    incompleta, não adaptação silenciosa."""
    pluggy_responde({"page": 0, "totalPages": 1, "total": 1, "results": [_pos("a")]})

    with pytest.raises(PluggyApiError, match="page_incoerente"):
        list_pluggy_investments("item-1", "k")


def test_total_menor_que_o_lido_levanta(pluggy_responde):
    pluggy_responde([
        {"page": 1, "totalPages": 2, "total": 5, "results": [_pos("a"), _pos("b")]},
        {"page": 2, "totalPages": 2, "total": 5, "results": [_pos("c")]},
    ])

    with pytest.raises(PluggyApiError, match="total_incoerente"):
        list_pluggy_investments("item-1", "k")


def test_total_pages_ausente_levanta(pluggy_responde):
    """Sem `totalPages` não há critério de parada: ler uma página e devolver seria
    inventar que a carteira acabou."""
    pluggy_responde({"results": [_pos("a")]})

    with pytest.raises(PluggyApiError, match="total_pages_ausente"):
        list_pluggy_investments("item-1", "k")


def test_falha_na_pagina_intermediaria_propaga(pluggy_responde):
    """HTTP de erro pelo `_raise_for_pluggy_response` REAL, não exceção mockada."""
    pluggy_responde([
        {"page": 1, "totalPages": 3, "total": 3, "results": [_pos("a")]},
        (500, {"code": "500", "message": "boom"}),
    ])

    with pytest.raises(PluggyApiError) as exc:
        list_pluggy_investments("item-1", "k")

    assert exc.value.status_code == 500
    assert len(pluggy_responde.chamadas) == 2, "parou na falha, não seguiu paginando"


def test_teto_de_paginas_levanta_em_vez_de_truncar(pluggy_responde):
    """Truncar seria o mesmo `[]` mentiroso de antes, só que maior.

    `max_pages=3` em vez do default 20: o que se mede é a parada no teto, e o
    número não muda o caminho de código. Assertar em `.chamadas` é o que separa
    "levantou" de "levantou depois de paginar para sempre"."""
    pluggy_responde([{"page": p, "totalPages": 999, "results": [_pos(f"p{p}")]}
                     for p in range(1, 9)])

    with pytest.raises(PluggyApiError, match="teto_de_paginas"):
        list_pluggy_investments("item-1", "k", max_pages=3)

    assert len(pluggy_responde.chamadas) == 3, "parou exatamente no teto"


# ── Passada 1 do Tester: a metadata mentindo de formas mais discretas ────────
# Todos cabem na mesma regra já aprovada (metadata incoerente = leitura
# incompleta). Cada um foi provado por mutação: com o código de antes do
# conserto, o caso abaixo fica VERMELHO; com o conserto, verde.

def test_total_pages_que_encolhe_no_meio_e_incoerente(pluggy_responde):
    """2a: `totalPages` 3 → 2 fazia a página 3 nunca ser pedida, e o que estava
    nela virava posição "ausente" → caixinha removida. A primeira página é o
    contrato."""
    pluggy_responde([
        {"page": 1, "totalPages": 3, "total": 6, "results": [_pos("a"), _pos("b")]},
        {"page": 2, "totalPages": 2, "total": 4, "results": [_pos("c"), _pos("d")]},
    ])

    with pytest.raises(PluggyApiError, match="metadata_divergente"):
        list_pluggy_investments("item-1", "k")


def test_total_ausente_na_ultima_pagina_nao_desliga_a_conferencia(pluggy_responde):
    """2b: sem o `total` da última resposta a conferência final sumia, e uma
    página curta no fim devolvia 3 de 4 afirmando leitura completa. O `total` da
    PRIMEIRA continua valendo. (A página 2 era `[]`; página vazia sem prova hoje
    cai antes, em `vazia_sem_prova`, então ela ganhou uma posição para o caso
    seguir medindo o `total`.)"""
    pluggy_responde([
        {"page": 1, "totalPages": 2, "total": 4, "results": [_pos("a"), _pos("b")]},
        {"page": 2, "totalPages": 2, "results": [_pos("c")]},
    ])

    with pytest.raises(PluggyApiError, match="total_incoerente"):
        list_pluggy_investments("item-1", "k")


def test_id_repetido_entre_paginas_e_incoerente(pluggy_responde):
    """3: janela deslizante. `total=4`, a página 2 repete `b` e `d` nunca vem — a
    contagem BRUTA dava 4 e a leitura passava com 3 posições distintas."""
    pluggy_responde([
        {"page": 1, "totalPages": 2, "total": 4, "results": [_pos("a"), _pos("b")]},
        {"page": 2, "totalPages": 2, "total": 4, "results": [_pos("b"), _pos("c")]},
    ])

    with pytest.raises(PluggyApiError, match="id_repetido"):
        list_pluggy_investments("item-1", "k")


@pytest.mark.parametrize("page", [1.9, 1.0, True])
def test_page_que_nao_e_inteiro_nao_passa_como_eco(pluggy_responde, page):
    """4: `int()` cru aprovava o eco com `1.9`, `1.0` e `True`."""
    pluggy_responde({"page": page, "totalPages": 1, "total": 1, "results": [_pos("a")]})

    with pytest.raises(PluggyApiError, match="page_incoerente"):
        list_pluggy_investments("item-1", "k")


def test_total_pages_fracionario_nao_vira_uma_pagina_so(pluggy_responde):
    """4: `1.7` truncava em 1 — uma página lida no lugar de duas, e a segunda
    inteira contava como carteira que sumiu."""
    pluggy_responde({"page": 1, "totalPages": 1.7, "results": [_pos("a")]})

    with pytest.raises(PluggyApiError, match="total_pages_ausente"):
        list_pluggy_investments("item-1", "k")


@pytest.mark.parametrize("item", [
    "nao sou dict",
    {"name": "sem id"},
    {"id": "", "name": "id vazio"},
    {"id": None, "name": "id nulo"},
])
def test_posicao_sem_id_usavel_e_incoerente(pluggy_responde, item):
    """7: ela caía no `continue` de `save_open_finance_investments` e a leitura
    seguia valendo como COMPLETA. Com todos os ids vazios, a reconciliação
    apagaria a carteira inteira."""
    pluggy_responde({"page": 1, "totalPages": 1, "total": 1, "results": [item]})

    with pytest.raises(PluggyApiError, match="item_invalido"):
        list_pluggy_investments("item-1", "k")


def test_metadata_em_string_de_digitos_e_aceita(pluggy_responde):
    """CONTROLE POSITIVO do `_inv_int` estrito: a Pluggy manda número OU string, e
    apertar o tipo não pode recusar a resposta legítima."""
    pluggy_responde([
        {"page": "1", "totalPages": "2", "total": "3", "results": [_pos("a"), _pos("b")]},
        {"page": "2", "totalPages": "2", "total": "3", "results": [_pos("c")]},
        {"page": "1", "totalPages": "2", "total": "3", "results": [_pos("a"), _pos("b")]},
        {"page": "2", "totalPages": "2", "total": "3", "results": [_pos("c")]},
    ])

    assert [i["id"] for i in list_pluggy_investments("item-1", "k")] == ["a", "b", "c"]


@pytest.mark.parametrize("page", ["²", "٢"])
def test_digito_nao_ascii_nao_e_numero_de_pagina(pluggy_responde, page):
    """`"²".isdigit()` é True e `int("²")` LEVANTA — o erro sairia da função como
    exceção não tratada, não como leitura incompleta. `"٢"` (arábico-índico) é
    pior: `int()` converte para 2 em silêncio, e o eco da página passaria."""
    pluggy_responde({"page": page, "totalPages": 1, "total": 1, "results": [_pos("a")]})

    with pytest.raises(PluggyApiError, match="page_incoerente"):
        list_pluggy_investments("item-1", "k")


@pytest.mark.parametrize("payload", [
    {"page": 1, "totalPages": 0, "total": 2, "results": [_pos("a"), _pos("b")]},
    {"page": 1, "totalPages": 0, "results": [_pos("a"), _pos("b")]},
])
def test_total_pages_zero_com_posicoes_e_incoerente(pluggy_responde, payload):
    """`totalPages: 0` com posições na página: o laço parava na página 1 como se
    fosse a última, e a reconciliação removia o que estivesse nas outras."""
    pluggy_responde(payload)

    with pytest.raises(PluggyApiError, match="pagina_alem_do_total"):
        list_pluggy_investments("item-1", "k")


def test_janela_que_move_sem_mudar_de_tamanho_e_incoerente(pluggy_responde):
    """O cenário do Codex: `A` sai e `E` entra no fim entre as requisições. A
    página 2 começa em `D`, o `C` nunca é lido, e ids distintos + `total` batendo
    não denunciam nada — só a releitura da página 1 mostra que ela mudou."""
    pluggy_responde([
        {"page": 1, "totalPages": 2, "total": 4, "results": [_pos("a"), _pos("b")]},
        {"page": 2, "totalPages": 2, "total": 4, "results": [_pos("d"), _pos("e")]},
        {"page": 1, "totalPages": 2, "total": 4, "results": [_pos("b"), _pos("c")]},
    ])

    with pytest.raises(PluggyApiError, match="janela_moveu"):
        list_pluggy_investments("item-1", "k")


def test_posicao_perdida_na_fronteira_da_ultima_pagina_e_incoerente(pluggy_responde):
    """O contraexemplo do Manager: entre a página 2 e a 3, `d` sai e `f` entra no
    fim, então a página 3 lê `[f]` e o `e` nunca é lido; antes da releitura a
    carteira volta ao original. Reler só 1..N−1 batia e o `e` sumia — a posição
    perdida era da própria página N, então a última página também é relida."""
    def pg(n, *ids):
        return {"page": n, "totalPages": 3, "total": 5, "results": [_pos(i) for i in ids]}

    pluggy_responde([pg(1, "a", "b"), pg(2, "c", "d"), pg(3, "f"),
                     pg(1, "a", "b"), pg(2, "c", "d"), pg(3, "e")])

    with pytest.raises(PluggyApiError, match="janela_moveu"):
        list_pluggy_investments("item-1", "k")


def test_uma_pagina_so_nao_rele(pluggy_responde):
    """CONTROLE POSITIVO: a releitura custa zero no caso de todo usuário real."""
    pluggy_responde([
        {"page": 1, "totalPages": 1, "total": 2, "results": [_pos("a"), _pos("b")]},
    ])

    assert [i["id"] for i in list_pluggy_investments("item-1", "k")] == ["a", "b"]
    assert len(pluggy_responde.chamadas) == 1


# ── Página vazia só com prova de carteira vazia ─────────────────────────────
# `[]` numa página só vale com `totalPages: 0` ou `total: 0`. Sem isso, a última
# página vazia (ou a única, sem `total`) passava como fim da carteira, e a
# reconciliação removia o que não veio — a carteira inteira, no caso da única.
# CONTROLE NEGATIVO (medido, ver relato): tirar a regra deixa os 3 primeiros
# vermelhos; os 2 positivos seguem verdes com e sem ela.

# As leituras de várias páginas vêm programadas em dobro (passada + releitura
# estável): sem a regra, a função DEVOLVE `[a]` em vez de esbarrar no fim da
# fixture — é isso que o controle negativo tem de mostrar.
_P1 = {"page": 1, "totalPages": 2, "results": [_pos("a")]}
_P2 = {"page": 2, "totalPages": 2, "results": []}
_P1_T = {**_P1, "total": 1}
_P2_T = {**_P2, "total": 1}


@pytest.mark.parametrize("paginas", [
    [{"page": 1, "totalPages": 1, "results": []}],
    [_P1, _P2, _P1, _P2],
    [_P1_T, _P2_T, _P1_T, _P2_T],
], ids=["unica_vazia_sem_total", "ultima_vazia_sem_total", "ultima_vazia_com_total_1"])
def test_pagina_vazia_sem_prova_levanta(pluggy_responde, paginas):
    pluggy_responde(paginas)

    with pytest.raises(PluggyApiError, match="vazia_sem_prova"):
        list_pluggy_investments("item-1", "k")


@pytest.mark.parametrize("payload", [
    {"page": 1, "totalPages": 0, "results": []},
    {"page": 1, "totalPages": 1, "total": 0, "results": []},
], ids=["total_pages_zero_sem_total", "total_zero"])
def test_carteira_vazia_com_prova_nao_levanta(pluggy_responde, payload):
    """CONTROLE POSITIVO: a regra restringe, e a carteira vazia de verdade continua `[]`."""
    pluggy_responde(payload)

    assert list_pluggy_investments("item-1", "k") == []
