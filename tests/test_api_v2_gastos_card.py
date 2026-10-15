"""O que o card de compras de /gastos (`webapp/src/dashboard/widgets/GastosCompras.tsx`) promete na tela.

O card mostra o total do servidor (`dias[]` de `/gastos`, ou o `total` de `/gastos/categoria`) e, embaixo, as
linhas de `/lancamentos?mes&dia&tipo=saida&guardado=1`, com o interno, o guardado e a outra moeda MARCADOS. A nota
"ficam fora do total" só é verdade se a soma das saídas NÃO marcadas for exatamente aquele total. Este teste cruza
as duas rotas, num dia com saída normal, interna, em outra moeda, guardada e no cartão.
Controle negativo (medido à mão, relatado no PR): tirar o filtro de outra moeda de `db/gastos._em_reais`
deixa este arquivo vermelho; o positivo é a igualdade no mesmo teste.
"""
from __future__ import annotations

from decimal import Decimal

import db
from conftest import usuario_pagante
from tests._patrimonio_helpers import conexao, conta, tx_banco
from tests.test_api_v2_gastos import _l
from tests.test_api_v2_lancamentos import pede, todos
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_resumo_mes_regra import INICIO, _lanc

D = Decimal
M = f"{INICIO:%Y-%m}"
DIA = INICIO.replace(day=5)


def _semeia(uid):
    _l(uid, 100, DIA, categoria="mercado", hora=10)
    _l(uid, 20, DIA, categoria="mercado", hora=11)
    _l(uid, 7, DIA, categoria="farmacia", hora=12)
    usd = conta(conexao(uid, f"item-usd-{uid}"), "acc-usd", "10", moeda="USD", code=None)  # a moeda da linha é a da conta do banco
    dolar = _l(uid, 40, DIA, categoria="mercado", hora=13, source="open_finance")  # outra moeda
    tx_banco(usd, "tx-usd", "-40", imported_launch_id=dolar)
    _lanc(uid, "despesa", 500, 5, interno=True)  # movimento entre contas
    _lanc(uid, "deposito_caixinha", 200, 5, interno=True)  # guardado
    cartao = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, cartao, 80, "mercado", "no cartão", DIA)  # fatura do mês, data da compra


def _nao_marcadas(itens):
    """A conta que o card promete: o que a nota diz que fica FORA do total, fora."""
    return sum((D(i["valor"]) for i in itens if not i["interno"] and "outra_moeda" not in i["motivos"]), D(0))


def test_soma_das_saidas_sem_marca_e_o_total_do_dia_e_o_da_categoria(libera):
    a = usuario_pagante()
    _semeia(a)
    libera(a)

    gastos = pede(a, "/api/v2/gastos", mes=M).json()
    total_dia = D(next(d for d in gastos["dias"] if d["dia"] == f"{DIA}")["total"])
    itens = todos(a, mes=M, dia=f"{DIA}", tipo="saida", guardado=1)
    assert {i["id"][0] for i in itens} == {"l", "c"}  # laçamento e cartão, os dois no card
    assert any(i["interno"] and not i["guardado"] for i in itens) and any(i["guardado"] for i in itens)  # o card os marca
    assert any("outra_moeda" in i["motivos"] for i in itens)
    assert _nao_marcadas(itens) == total_dia == D(207)
    assert sum((D(i["valor"]) for i in itens), D(0)) != total_dia  # negativo: sem as marcas a conta não fecha

    cat = pede(a, "/api/v2/gastos/categoria", mes=M, categoria="mercado").json()
    do_mes = todos(a, mes=M, tipo="saida", categoria="mercado", guardado=1)
    assert _nao_marcadas(do_mes) == D(cat["total"]) == D(200)  # 100 + 20 + 80; a de dólar fica de fora
    do_dia = D(next(d for d in cat["dias"] if d["dia"] == f"{DIA}")["total"])
    assert _nao_marcadas(todos(a, mes=M, dia=f"{DIA}", tipo="saida", categoria="mercado", guardado=1)) == do_dia
