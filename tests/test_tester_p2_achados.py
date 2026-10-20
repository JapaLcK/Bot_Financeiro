"""Tester P2: achados PROVADOS (vermelhos hoje). Cada teste afirma o comportamento certo; devem ficar verdes
quando o Coder consertar (ou o Manager decidir aceitar e apagar o teste com a razão)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from decimal import Decimal as D

from tests.test_previsao_variavel import HOJE, cartao, fixo, gasto, historico, ler_em, mensal, tres_janelas


def test_estorno_no_cartao_nao_abate_a_estimativa(user_id):
    """D8 (`estorno não abate`): valor negativo no cartão não entra. Estorno -30 (lazer) + compra 50 (mercado)
    nas 3 janelas: sem o filtro `valor > 0` a soma (20) segue positiva e o conserto da soma <= 0 não mascara."""
    historico(user_id)
    cartao(user_id, [(-30, a, 10, {}) for a in (10, 40, 70)])
    cartao(user_id, [(50, a, 10, {"categoria": "mercado"}) for a in (11, 41, 71)], nome="B")
    s = ler_em(user_id)
    assert mensal(s) == {"mercado": D("50.00")} and s.estimativa_variavel["despesa_mensal"] == D("50.00")


def test_estimativa_zero_nao_diz_que_inclui_estimativa(user_id):
    """Gasto real de 300 em 5 dias, mas só 1 de 3 janelas tem gasto: mediana 0. O contrato diz
    `inclui_estimativa_variavel=true` e some o motivo `gastos_variaveis_nao_estimados` com R$ 0 de estimativa."""
    historico(user_id)
    gasto(user_id, 300, 5)
    s = ler_em(user_id)
    zero = s.estimativa_variavel is not None and s.estimativa_variavel["despesa_mensal"] == 0
    assert not zero or "gastos_variaveis_nao_estimados" in {m.codigo for m in s.motivos}


def test_fixo_de_nome_generico_nao_come_gasto_que_so_divide_uma_palavra(user_id):
    """D14: o fixo 'Conta de luz' tira da mediana qualquer gasto cujo nome tenha o token 'conta'."""
    historico(user_id)
    fixo(user_id, "Conta de luz", 180, 10, start_date=HOJE - timedelta(days=120))
    tres_janelas(user_id, (300, 300, 300), categoria="restaurante", alvo="Conta do restaurante")
    assert mensal(ler_em(user_id)) == {"restaurante": D("300.00")}


def test_fixo_de_nome_curto_nao_come_gasto_de_palavra_maior(user_id):
    """D14 (passada 2): `merchant_contido` é substring de caractere, não de palavra: o fixo 'Gás' (R$ 90,
    dia 10) tira da mediana a 'Gasolina Posto' de R$ 300 que não é a mesma coisa."""
    historico(user_id)
    fixo(user_id, "Gás", 90, 10, start_date=HOJE - timedelta(days=120))
    tres_janelas(user_id, (300, 300, 300), categoria="transporte", alvo="Gasolina Posto")
    assert mensal(ler_em(user_id)) == {"transporte": D("300.00")}


@pytest.mark.parametrize("fixo_nome,alvo", [("Conta de luz", "Conta"), ("Pix pra diarista", "Pix"), ("Uber One", "Uber")])
def test_fixo_de_nome_maior_nao_come_gasto_de_uma_palavra(user_id, fixo_nome, alvo):
    """B1: só o nome do fixo contido na linha tira; a linha contida no nome do fixo fica na mediana."""
    historico(user_id)
    fixo(user_id, fixo_nome, 180, 10, start_date=HOJE - timedelta(days=120))
    tres_janelas(user_id, (333, 333, 333), categoria="x", alvo=alvo)
    assert mensal(ler_em(user_id)) == {"x": D("333.00")}
