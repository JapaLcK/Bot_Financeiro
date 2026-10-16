"""O que o card de compras de /gastos (`webapp/src/dashboard/widgets/GastosCompras.tsx`) promete na tela.
Cruza as duas rotas e o calendário: quantidade/categorias por dia, centavos, fora da grade, isolamento e arredondamento por dia.

O card mostra o total do servidor (`dias[]` de `/gastos`, ou o `total` de `/gastos/categoria`) e, embaixo, as
linhas de `/lancamentos?mes&dia&tipo=saida&guardado=1`, com o interno, o guardado e a outra moeda MARCADOS. A nota
"ficam fora do total" só é verdade se a soma das saídas NÃO marcadas for exatamente aquele total. O primeiro teste cruza
as duas rotas, num dia com saída normal, interna, em outra moeda, guardada e no cartão.
Controle negativo (medido à mão, relatado no PR): tirar o filtro de outra moeda de `db/gastos._em_reais`
deixa este arquivo vermelho; o positivo é a igualdade no mesmo teste.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

import db
from conftest import usuario_pagante
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_api_v2_gastos import _l
from tests.test_api_v2_lancamentos import pede, todos
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_resumo_mes_regra import FIM, INICIO, _lanc, semeia_b
from utils_date import _tz

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


def _dia(u, rota="/api/v2/gastos", dia=DIA, **p):
    return next(d for d in pede(u, rota, mes=M, **p).json()["dias"] if d["dia"] == f"{dia}")


def _cats(d):
    return [(c["chave"], D(c["valor"])) for c in d["categorias"]]


def test_dia_traz_quantidade_e_categorias_das_mesmas_linhas_do_total(libera):
    """O card do dia (PR B) não soma dinheiro: `categorias` e `quantidade` fecham com `total` e com a lista.
    Controles medidos à mão (relatados no PR): sem o filtro de moeda/interno em `_em_reais`, ou com
    `_por_categoria` lendo só `maiores`, ou `quantidade` errada, este teste fica vermelho."""
    a = usuario_pagante()
    _semeia(a)
    libera(a)

    d = _dia(a)
    assert _cats(d) == [("mercado", D(200)), ("farmacia", D(7))]  # USD, interno e guardado ficam fora
    assert sum(v for _, v in _cats(d)) == D(d["total"]) == D(207)  # positivo: fecha com o total
    itens = todos(a, mes=M, dia=f"{DIA}", tipo="saida", guardado=1)
    assert d["quantidade"] == 4 == len([i for i in itens if not i["interno"] and "outra_moeda" not in i["motivos"]])
    assert d["quantidade"] > len(d["maiores"])  # `maiores` é só o topo

    c = _dia(a, "/api/v2/gastos/categoria", categoria="mercado")  # a categoria sozinha
    assert _cats(c) == [("mercado", D(200))] and c["quantidade"] == 3


def test_compra_fora_da_grade_nao_entra_nas_categorias_do_dia(libera):
    a = usuario_pagante()
    _l(a, 10, DIA, categoria="mercado")
    futura = _l(a, 25, DIA, categoria="lazer", source="open_finance")
    q("update launches set posted_at = %s where id = %s returning id",
      (datetime.combine(FIM + timedelta(days=5), time(12), tzinfo=_tz()), futura))
    libera(a)

    r = pede(a, "/api/v2/gastos", mes=M).json()
    assert r["fora_da_grade"]["quantidade"] == 1 and D(r["fora_da_grade"]["valor"]) == D(25)
    assert [_cats(d) for d in r["dias"]] == [[("mercado", D(10))]]  # "lazer" só no total do mês
    assert [d["quantidade"] for d in r["dias"]] == [1]  # a de fora também não conta na quantidade do dia


def test_dia_de_um_usuario_nao_mostra_nada_do_outro(libera):
    a, b = usuario_pagante(), usuario_pagante()
    _semeia(a)
    semeia_b(b)
    libera(a, b)

    d = _dia(b)
    assert _cats(d) == [("mercado", D(888))] and d["quantidade"] == 1
    assert all(c["chave"] != "farmacia" for x in pede(b, "/api/v2/gastos", mes=M).json()["dias"] for c in x["categorias"])


def test_categoria_com_acento_sem_categoria_e_empate_ordenam_pela_chave(libera):
    a = usuario_pagante()
    _l(a, 10, DIA, categoria="Zeta & Café")
    _l(a, 10, DIA, categoria="Alimentação")
    _l(a, 10, DIA)  # sem categoria
    _l(a, 30, DIA, categoria="meio")
    libera(a)

    r = pede(a, "/api/v2/gastos", mes=M).json()
    d = r["dias"][0]
    assert [c["chave"] for c in d["categorias"]] == ["meio", *sorted(c["chave"] for c in d["categorias"][1:])]
    assert {c["chave"] for c in d["categorias"]} == {c["chave"] for c in r["categorias"]} and len(d["categorias"]) == 4
    assert "sem categoria" in {c["chave"] for c in d["categorias"]} and d["quantidade"] == 4


def test_centavos_quebrados_fecham_com_o_texto_exato_do_total(libera):
    """Float na soma (`0.1 + 0.2`) deixaria Σ categorias != total; compara o TEXTO, não `Decimal` (que ignora escala)."""
    a = usuario_pagante()
    for i, v in enumerate(["0.10", "0.20", "0.10", "33.33", "33.33", "33.34"]):
        _l(a, D(v), DIA, categoria="mercado", hora=8 + i)
    _l(a, D("0.07"), DIA, categoria="farmacia", hora=20)
    libera(a)

    d = pede(a, "/api/v2/gastos", mes=M).json()["dias"][0]
    assert d["total"] == "100.47" and d["quantidade"] == 7
    assert [(c["chave"], c["valor"]) for c in d["categorias"]] == [("mercado", "100.40"), ("farmacia", "0.07")]
    assert sum(D(c["valor"]) for c in d["categorias"]) == D(d["total"])


def test_subcentavo_so_no_dia_levanta_arredondamento_por_grupo(libera):
    """Só o dia 3 (o do meio) diverge: 0,004 + 0,004 = 0,008 (0,01) mas as fatias exibem 0,00 + 0,00.
    O mês (2,01 = 2,01) e os totais dos dias (1,00 + 0,01 + 1,00) fecham; os dias 2 e 4 são limpos.
    `b` é o controle positivo: subcentavo dentro de UMA categoria só (dia 3) não levanta o motivo."""
    a, b = usuario_pagante(), usuario_pagante()
    _l(a, 1, INICIO.replace(day=2), categoria="a")
    _l(a, D("0.004"), INICIO.replace(day=3), categoria="a")
    _l(a, D("0.004"), INICIO.replace(day=3), categoria="b")
    _l(a, D("1.002"), INICIO.replace(day=4), categoria="b")
    _l(b, 10, DIA, categoria="mercado")  # positivo: centavos inteiros não levantam o motivo
    _l(b, D("5.50"), DIA, categoria="lazer")
    _l(b, D("0.004"), INICIO.replace(day=3), categoria="c")
    _l(b, D("0.004"), INICIO.replace(day=3), categoria="c")
    libera(a, b)

    assert "arredondamento_por_grupo" in pede(a, "/api/v2/gastos", mes=M).json()["motivos"]
    assert "arredondamento_por_grupo" not in pede(b, "/api/v2/gastos", mes=M).json()["motivos"]
    assert "arredondamento_por_grupo" not in pede(b, "/api/v2/gastos/categoria", mes=M, categoria="mercado").json()["motivos"]
