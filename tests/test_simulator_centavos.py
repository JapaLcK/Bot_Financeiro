"""Dinheiro em centavos na entrada; taxas percentuais mantêm sua precisão."""
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from core.services.decision_simulator import Simulacao, _decision_events


def _pedido(campo, valor):
    pedido = {"reserva_minima": 0.29, "cenarios": [{
        "nome": "Compra", "preco": 200.29, "entrada": 0.29, "parcelas": 2,
        "custos_unicos": 0.29, "despesa_mensal_nova": 0.29,
    }]}
    destino = pedido if campo == "reserva_minima" else pedido["cenarios"][0]
    destino[campo] = valor
    return pedido


@pytest.mark.parametrize("campo", [
    "preco", "entrada", "custos_unicos", "despesa_mensal_nova", "reserva_minima",
])
@pytest.mark.parametrize("valor", [0.004, 0.025, 100.025, 999_999_999.999])
def test_subcentavos_sao_recusados_no_campo_de_origem(campo, valor):
    with pytest.raises(ValidationError) as exc:
        Simulacao.model_validate(_pedido(campo, valor))
    assert any(e["loc"][-1] == campo and "duas casas decimais" in e["msg"]
               for e in exc.value.errors())


@pytest.mark.parametrize("taxa", [0, 1e-15, 0.123456789])
def test_centavos_validos_e_taxa_precisa_preservam_contrato(taxa):
    sim = Simulacao.model_validate(_pedido("juros_mensal_pct", taxa))
    cen = sim.cenarios[0]
    assert (cen.preco, cen.entrada, cen.custos_unicos, cen.despesa_mensal_nova,
            sim.reserva_minima, cen.juros_mensal_pct) == (200.29, 0.29, 0.29, 0.29, 0.29, taxa)
    hoje = date.today()
    eventos, contrato = _decision_events(cen, hoje, hoje + timedelta(days=90))
    assert contrato["juros_totais"] >= 0
    assert contrato["total_pago"] == round(contrato["pago_na_compra"]
                                           + contrato["valor_financiado"]
                                           + contrato["juros_totais"], 2)
    parcelas = [-e[3] for e in eventos if e[1] == "simulacao_parcela"]
    assert round(sum(parcelas), 2) == round(contrato["valor_financiado"]
                                            + contrato["juros_totais"], 2)
    if taxa == 0:
        assert (contrato["valor_financiado"], contrato["total_pago"], contrato["juros_totais"]) == (200, 200.58, 0)
