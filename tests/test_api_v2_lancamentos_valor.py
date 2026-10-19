"""`GET /api/v2/lancamentos?valor_min=&valor_max=` (Extrato do app: botão Filtros). Postgres real.

Controles por mutação (relato do PR): `x.valor >= %s` neutralizado → vermelho em `test_so_minimo`,
`test_faixa_e_inclusiva`; `x.valor <= %s` neutralizado → vermelho em `test_so_maximo`; sem o
`x.moeda = 'BRL'` → vermelho em `test_outra_moeda_nao_casa`; predicado ignorado na perna do cartão
(`x.tabela = 'c' or ...`) → vermelho em `test_perna_do_cartao_obedece_o_valor_e_nao_vaza`; `>=` no lugar de
`>` na checagem min/max da rota → vermelho em `test_faixa_e_inclusiva`. O isolamento por usuário é o das
pernas (`user_id`), que o filtro não toca; `test_b_nao_ve_o_de_a` o fixa para o filtro novo.
"""
from datetime import timedelta
from decimal import Decimal

import pytest

import db

from conftest import usuario_pagante
from tests._patrimonio_helpers import q
from tests.test_api_v2_lancamentos import M, ok, pede, todos
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_resumo_mes_regra import INICIO, _lanc

D = Decimal


def valores(quem, **params) -> list[Decimal]:
    return sorted(D(i["valor"]) for i in todos(quem, mes=M, **params))


@pytest.fixture
def ab(libera):
    a, b = usuario_pagante(), usuario_pagante()
    _lanc(a, "despesa", 10, 3)
    q("update launches set alvo = 'padaria' where id = %s returning id", (_lanc(a, "despesa", 50, 3),))
    _lanc(a, "receita", 100, 4)
    _lanc(a, "despesa", 200, 5)
    usd = _lanc(a, "despesa", 20, 6)
    q("update launches set currency = 'USD' where id = %s returning id", (usd,))
    _lanc(b, "despesa", 50, 3)
    _lanc(b, "despesa", 7777, 3)
    libera(a, b)
    return a, b


def test_sem_filtro_a_lista_e_a_de_antes(ab):
    assert valores(ab[0]) == [D(10), D(20), D(50), D(100), D(200)]


def test_so_minimo(ab):
    assert valores(ab[0], valor_min="50") == [D(50), D(100), D(200)]  # inclusivo


def test_so_maximo(ab):
    assert valores(ab[0], valor_max="50.00") == [D(10), D(50)]


def test_faixa_e_inclusiva(ab):
    assert valores(ab[0], valor_min="50", valor_max="100.00") == [D(50), D(100)]
    assert valores(ab[0], valor_min="50.00", valor_max="50") == [D(50)]
    assert valores(ab[0], valor_min="0.01", valor_max="9.99") == []


def test_combina_com_tipo_dia_e_busca(ab):
    a = ab[0]
    assert valores(a, valor_min="50", tipo="saida") == [D(50), D(200)]
    assert valores(a, valor_min="50", tipo="entrada") == [D(100)]
    assert valores(a, valor_min="10", dia=INICIO.replace(day=3).isoformat()) == [D(10), D(50)]
    assert valores(a, valor_min="50", dia=INICIO.replace(day=3).isoformat()) == [D(50)]
    assert valores(a, valor_min="10", q="padaria") == [D(50)]
    assert valores(a, valor_max="40", q="padaria") == []


def test_outra_moeda_nao_casa(ab):
    a = ab[0]
    assert D(20) in valores(a)
    assert valores(a, valor_min="0") == [D(10), D(50), D(100), D(200)]  # o de USD (valor não convertido) sai
    assert valores(a, valor_max="999999999.99") == [D(10), D(50), D(100), D(200)]


def test_b_nao_ve_o_de_a(ab):
    a, b = ab
    assert valores(b, valor_min="50") == [D(50), D(7777)]
    assert valores(b, valor_min="100", valor_max="1000") == []  # o 100 e o 200 de A não aparecem
    assert valores(a, valor_min="1000") == []  # o 7777 de B não aparece
    assert valores(a, valor_min="50", valor_max="50") == [D(50)] and valores(b, valor_min="50", valor_max="50") == [D(50)]


@pytest.mark.parametrize("params", [
    {"valor_min": "10", "valor_max": "9.99"}, {"valor_min": "100.00", "valor_max": "50"},
])
def test_minimo_maior_que_maximo_e_422(ab, params):
    r = pede(ab[0], mes=M, **params)
    assert r.status_code == 422 and r.json()["error"]["details"][0]["loc"] == ["query", "valor_min"]


@pytest.mark.parametrize("campo, valor", [
    ("valor_min", "abc"), ("valor_max", "-1"), ("valor_min", "1e3"), ("valor_max", "1,50"), ("valor_min", "1.234"),
    ("valor_max", "1234567890"), ("valor_min", "١٢"), ("valor_max", ""),
])
def test_valor_fora_do_formato_e_422(ab, campo, valor):
    r = pede(ab[0], mes=M, **{campo: valor})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["loc"] == ["query", campo]


def test_perna_do_cartao_obedece_o_valor_e_nao_vaza(libera):
    """Compra à vista (80) e a 1ª parcela de 300 em 3x (100) no cartão: `c<id>`, outra perna do SQL."""
    a, b = usuario_pagante(), usuario_pagante()
    cartao = db.create_card(a, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(a, cartao, 80, "mercado", "vista", INICIO.replace(day=5))
    db.add_credit_purchase_installments(a, cartao, 300, "casa", "3x", (INICIO - timedelta(days=1)).replace(day=20), 3)
    _lanc(a, "despesa", 90, 3)
    cartao_b = db.create_card(b, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(b, cartao_b, 85, "mercado", "do B", INICIO.replace(day=5))
    libera(a, b)
    assert {i["id"][0] for i in todos(a, mes=M, cartao=cartao)} == {"c"}
    assert valores(a) == [D(80), D(90), D(100)]
    assert valores(a, valor_min="85") == [D(90), D(100)]
    assert valores(a, valor_max="85") == [D(80)]
    assert valores(a, valor_min="80", valor_max="90") == [D(80), D(90)]
    assert valores(a, valor_min="100", valor_max="100", cartao=cartao) == [D(100)]  # parcela, min == max
    assert valores(a, valor_min="85", cartao=cartao) == [D(100)]
    assert valores(b, valor_min="80") == [D(85)] and valores(b, valor_min="86") == []
