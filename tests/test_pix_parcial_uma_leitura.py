"""#1504: um `PAYMENT_PARTIALLY_REFUNDED` lê o Asaas UMA vez; o efeito do afiliado e o alerta ao
admin usam o mesmo `refunds[]` (memo em `evt["_estornos"]`). Dreno real; conta as chamadas de
`buscar_pagamento`. Controle negativo: `ler_estornos` sem o memo deixa (a) e (b) vermelhos."""
import core.services.asaas as asaas
from _dreno_pix_helpers import entregar, evento
from test_afiliado_comissao_pix import _afiliado, _anual, _cara, _conta, _revisao, externo  # noqa: F401


def _conta_leituras(monkeypatch, *respostas):
    """Cada chamada devolve a próxima lista de `refunds` (a última se repete)."""
    chamadas = []

    def _get(pid):
        chamadas.append(pid)
        return {"id": pid, "status": "RECEIVED", "refunds": respostas[min(len(chamadas), len(respostas)) - 1]}

    monkeypatch.setattr(asaas, "buscar_pagamento", _get)
    return chamadas


def _alerta(externo):
    (a,) = [m for m in externo["alerta"] if "PAYMENT_PARTIALLY_REFUNDED" in m]
    return a


def test_a_com_afiliado_uma_leitura_e_mesmo_numero(externo, monkeypatch):
    _, cobranca = _cara(_afiliado())
    chamadas = _conta_leituras(monkeypatch, [{"value": 50.0, "status": "DONE"}])
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert len(chamadas) == 1 and evento(evt)["processed_at"] is not None
    assert _revisao(cobranca["external_reference"])["refunded_cents"] == 5000
    assert "devolvido: R$ 50.00" in _alerta(externo)


def test_b_asaas_que_muda_na_2a_leitura_nao_separa_alerta_e_revisao(externo, monkeypatch):
    _, cobranca = _cara(_afiliado())
    chamadas = _conta_leituras(monkeypatch, [{"value": 50.0, "status": "DONE"}],
                               [{"value": 70.0, "status": "DONE"}])
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert len(chamadas) == 1
    assert _revisao(cobranca["external_reference"])["refunded_cents"] == 5000
    assert "devolvido: R$ 50.00" in _alerta(externo)


def test_c_sem_afiliado_uma_leitura_asaas_fora_e_reentrega(externo, monkeypatch):
    cobranca = _anual(_conta(plano=False))
    entregar("PAYMENT_RECEIVED", cobranca)
    chamadas = _conta_leituras(monkeypatch, [{"value": 30.0, "status": "DONE"}])
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert len(chamadas) == 1 and "devolvido: R$ 30.00" in _alerta(externo)
    # o parcial repete a cada evento: a 2ª entrega também lê uma vez
    externo["alerta"].clear()
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert len(chamadas) == 2
    # Asaas fora: texto "indisponível" e o evento conclui
    externo["alerta"].clear()
    monkeypatch.setattr(asaas, "buscar_pagamento", lambda p: (_ for _ in ()).throw(RuntimeError("fora")))
    evt = entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert "devolvido: acumulado indisponível" in _alerta(externo) and evento(evt)["processed_at"] is not None


def test_c2_com_afiliado_reentrega_do_parcial_le_uma_vez_por_evento(externo, monkeypatch):
    _, cobranca = _cara(_afiliado())
    chamadas = _conta_leituras(monkeypatch, [{"value": 50.0, "status": "DONE"}])
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert len(chamadas) == 2


def test_d_concluido_e_pendente_sem_afiliado(externo, monkeypatch):
    cobranca = _anual(_conta(plano=False))
    entregar("PAYMENT_RECEIVED", cobranca)
    _conta_leituras(monkeypatch, [{"value": 30.0, "status": "DONE"}, {"value": 20.0, "status": "PENDING"}])
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert "devolvido: R$ 30.00 + R$ 20.00 em andamento" in _alerta(externo)
