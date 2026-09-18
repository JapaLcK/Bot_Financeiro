"""Simulador de decisão financeira (Pro) — a parte PURA: parcelas (`installments`),
calendário (`_add_months`) e os eventos e o contrato de cada cenário
(`_decision_events`). Nada aqui lê fonte de saldo; `simulate`, validação e
isolamento por usuário estão em test_decision_simulator.py."""
import json
import math
from datetime import date, timedelta
from decimal import Decimal, localcontext

import pytest

from core.services.decision_simulator import (
    DIAS, Cenario, Simulacao, _decision_events, installments,
)

HOJE = date.today()


def _sim(*cenarios, reserva=0.0):
    return Simulacao.model_validate({"reserva_minima": reserva, "cenarios": list(cenarios)})


# ─── installments ───────────────────────────────────────────────────────────

def test_installments_price_144k_a_1_49_em_48x():
    p = installments(144000.0, 0.0149, 48)
    assert len(p) == 48
    assert p[:47] == [4220.98] * 47
    assert p[-1] == 4220.78  # resíduo do arredondamento na última
    assert round(sum(p), 2) == 202606.84


def test_installments_sem_juros_residuo_na_ultima():
    assert installments(1000.01, 0.0, 3) == [333.34, 333.34, 333.33]


def test_installments_uma_parcela_e_o_valor_todo():
    assert installments(1234.56, 0.0, 1) == [1234.56]
    assert installments(1000.0, 0.02, 1) == [1020.0]  # Price com n=1: principal + um mês de juros


@pytest.mark.parametrize("pct", [1e-15, 1e-12, 1e-10])
def test_installments_juros_minusculos_batem_com_a_formula_exata(pct):
    """`(1 + r) ** -n` vira 1.0 em float com r minúsculo: dividia por zero (500 na
    rota) ou dava juros negativos. Referência exata em Decimal com 60 dígitos."""
    n, principal = 420, 1_000_000.0
    p = installments(principal, pct / 100, n)
    with localcontext() as ctx:
        ctx.prec = 60
        r = Decimal(pct) / 100
        exata = Decimal(principal) * r / (1 - (1 + r) ** -n)
    assert all(abs(Decimal(str(v)) - exata) <= Decimal("0.01") for v in p[:-1])
    assert abs(Decimal(str(sum(p))) - exata * n) <= Decimal("0.01") * n
    assert round(math.fsum(p) - principal, 2) >= 0


# ─── eventos no calendário e contrato ────────────────────────────────────────

def test_compra_em_31_01_parcela_no_ultimo_dia_de_fevereiro_sem_pular_nem_duplicar():
    cen = Cenario(nome="Moto", preco=3000.0, entrada=0.0, parcelas=3, data_compra=date(2028, 1, 31))
    events, contrato = _decision_events(cen, HOJE, date(2028, 12, 31))
    assert [e[0] for e in events] == [date(2028, 2, 29), date(2028, 3, 31), date(2028, 4, 30)]
    assert contrato["primeira_parcela"] == "2028-02-29"


def test_despesa_mensal_nova_comeca_um_mes_depois_e_vai_para_o_contrato():
    compra = date(2027, 1, 10)
    cen = Cenario(nome="Carro", preco=100.0, data_compra=compra, despesa_mensal_nova=450.0)
    events, contrato = _decision_events(cen, HOJE, date(2027, 4, 15))
    despesas = [(e[0], e[3]) for e in events if "despesa mensal" in e[2]]
    assert despesas == [(date(2027, 2, 10), -450.0), (date(2027, 3, 10), -450.0), (date(2027, 4, 10), -450.0)]
    assert contrato["despesa_mensal_nova"] == 450.0


def test_contrato_nao_devolve_menos_zero():
    """100,004 em 12x sem juros: soma das parcelas (100,00) − financiado dá −0,004,
    que arredonda para −0,0 sem o `+ 0.0`."""
    _, contrato = _decision_events(Cenario(nome="x", preco=100.004, parcelas=12), HOJE, date(2030, 1, 1))
    assert contrato["juros_totais"] == 0.0 and math.copysign(1, contrato["juros_totais"]) == 1
    assert "-0.0" not in json.dumps(contrato)


def test_48x_so_as_parcelas_ate_o_horizonte_e_o_resto_no_contrato():
    compra = date(2027, 1, 10)
    cen = Cenario(nome="Carro", preco=180000.0, entrada=36000.0, parcelas=48,
                  juros_mensal_pct=1.49, data_compra=compra, custos_unicos=2500.0)
    events, contrato = _decision_events(cen, HOJE, compra + timedelta(days=90))
    parcelas = [e for e in events if e[1] == "simulacao_parcela"]
    assert [e[0] for e in parcelas] == [date(2027, 2, 10), date(2027, 3, 10), date(2027, 4, 10)]
    assert [e[3] for e in parcelas] == [-4220.98] * 3
    assert sorted(e[3] for e in events if e[1] == "simulacao") == [-36000.0, -2500.0]
    assert contrato["a_vista"] is False
    assert (contrato["entrada"], contrato["pago_na_compra"]) == (36000.0, 38500.0)  # + custos 2.500
    assert contrato["parcelas_fora_do_horizonte"] == {"quantidade": 45, "valor": round(44 * 4220.98 + 4220.78, 2)}
    # total_pago = entrada + parcelas + custos únicos (R$ 2.500)
    assert (contrato["parcela"], contrato["total_pago"], contrato["juros_totais"]) == (4220.98, 241106.84, 58606.84)


def test_so_o_preco_e_a_vista_e_o_contrato_ecoa_isso():
    """Só `preco`: paga o valor cheio na data da compra, sem parcela futura."""
    events, contrato = _decision_events(Cenario(nome="TV", preco=3000.0, custos_unicos=200.0),
                                       HOJE, HOJE + timedelta(days=DIAS))
    assert [(e[0], e[2], e[3]) for e in events] == [
        (HOJE, "TV: à vista", -3000.0), (HOJE, "TV: custos únicos", -200.0)]
    assert contrato["a_vista"] is True
    # `entrada` diz a verdade (não houve entrada); quem sai na data é `pago_na_compra`
    assert (contrato["entrada"], contrato["pago_na_compra"]) == (0.0, 3200.0)  # + custos 200
    assert (contrato["parcelas"], contrato["parcela"]) == (1, 3000.0)
    assert contrato["primeira_parcela"] == contrato["ultima_parcela"] == HOJE.isoformat()
    assert (contrato["total_pago"], contrato["valor_financiado"], contrato["juros_totais"]) == (3200.0, 0.0, 0.0)


def test_parcelas_null_e_a_vista_e_parcelas_12_parcela():
    a_vista = _sim({"nome": "x", "preco": 1200.0, "parcelas": None}).cenarios[0]
    assert a_vista.a_vista is True and a_vista.financiado == 0.0
    parcelado = _sim({"nome": "x", "preco": 1200.0, "parcelas": 12}).cenarios[0]
    assert parcelado.a_vista is False and parcelado.financiado == 1200.0
    _, contrato = _decision_events(parcelado, HOJE, HOJE + timedelta(days=DIAS))
    assert (contrato["parcelas"], contrato["parcela"], contrato["total_pago"]) == (12, 100.0, 1200.0)


def test_pedido_que_virou_a_meia_noite_nao_poe_a_compra_no_passado():
    """Validado às 23:59 (data_compra = ontem, que era "hoje") e calculado às 00:00:
    a compra tem de sair no dia do CÁLCULO, não num passado que o saldo de partida
    já teria absorvido."""
    ontem = HOJE - timedelta(days=1)
    cen = Cenario.model_construct(nome="TV", preco=300.0, entrada=0.0, parcelas=None,
                                  juros_mensal_pct=0.0, data_compra=ontem,
                                  custos_unicos=0.0, despesa_mensal_nova=60.0)
    events, contrato = _decision_events(cen, HOJE, HOJE + timedelta(days=DIAS))
    assert contrato["data_compra"] == HOJE.isoformat()
    assert [e[0] for e in events if "à vista" in e[2]] == [HOJE]
    assert min(e[0] for e in events) >= HOJE


@pytest.mark.parametrize("cen,esperado", [
    # à vista com `entrada = preco`: a forma antiga continua aceita, mas entrada sai 0
    ({"nome": "x", "preco": 5000.0, "entrada": 5000.0, "custos_unicos": 300.0}, (True, 0.0, 5300.0, 5300.0)),
    ({"nome": "x", "preco": 1000.0, "custos_unicos": 2500.0}, (True, 0.0, 3500.0, 3500.0)),
    # parcelado: entrada 200 + custos 50 no dia; 800 em 4x sem juros depois
    ({"nome": "x", "preco": 1000.0, "entrada": 200.0, "parcelas": 4, "custos_unicos": 50.0},
     (False, 200.0, 250.0, 1050.0)),
])
def test_pago_na_compra_soma_os_custos_e_total_pago_nao_os_soma_duas_vezes(cen, esperado):
    hoje = date.today()
    c = Simulacao.model_validate({"cenarios": [cen]}).cenarios[0]
    _, contrato = _decision_events(c, hoje, hoje + timedelta(days=DIAS))
    assert (contrato["a_vista"], contrato["entrada"], contrato["pago_na_compra"],
            contrato["total_pago"]) == esperado
    assert contrato["custos_unicos"] == cen["custos_unicos"]  # continua detalhado à parte
