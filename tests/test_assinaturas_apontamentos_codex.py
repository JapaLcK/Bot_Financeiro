"""Apontamentos do Codex no #746: a "próxima" de uma assinatura ativa nunca é
uma data passada, e movimento interno em QUALQUER ocorrência tira o grupo (não
só na última). Banco real."""
from datetime import date, timedelta

from _apoio_assinaturas import HOJE, conta, mensais, rp, semeia
from core.services.assinaturas import listar_assinaturas


def test_proxima_de_ativa_cobrada_ha_mais_de_um_mes_nao_fica_no_passado(user_id):
    # última em 24/08 vista em 01/10: 38 dias, ainda ativa; 24/09 já passou.
    txs = mensais("sp", [-21.9] * 3, ultima=date(2026, 8, 24))
    semeia(user_id, [conta("acc-1", txs)], [rp("Spotify", -21.9, txs)])
    (it,) = listar_assinaturas(user_id, HOJE)["servicos"]
    assert (it["status"], it["proxima"]) == ("ativa", "2026-10-24")


def test_positivo_proxima_do_mes_que_vem_nao_muda(user_id):
    txs = mensais("sp", [-21.9] * 3, ultima=HOJE - timedelta(days=10))
    semeia(user_id, [conta("acc-1", txs)], [rp("Spotify", -21.9, txs)])
    (it,) = listar_assinaturas(user_id, HOJE)["servicos"]
    assert it["proxima"] == "2026-10-21"


def test_movimento_interno_numa_ocorrencia_antiga_tira_o_grupo(user_id):
    fatura = mensais("ft", [-800] * 3, category="Credit card payment")
    fatura[-1]["category"] = None  # a mais recente veio sem categoria
    semeia(user_id, [conta("acc-1", fatura)], [rp("PAGAMENTO FATURA", -800, fatura)])
    lista = listar_assinaturas(user_id, HOJE)
    assert (lista["servicos"], lista["outras"]) == ([], [])
