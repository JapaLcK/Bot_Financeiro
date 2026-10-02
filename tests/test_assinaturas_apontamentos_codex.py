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


def _ap(prefixo, valor, n, ultima):
    return mensais(prefixo, [valor] * n, ultima=ultima, desc="APPLE.COM/BILL")


def _semeia_ap(uid, grupos):
    txs = [t for g in grupos for t in g]
    semeia(uid, [conta("acc-1", txs, tipo="CREDIT")],
           [rp("APPLE.COM/BILL", float(g[0]["amount"]), g) for g in grupos])


def test_reajuste_cola_na_cadeia_dele_e_nao_vira_terceiro_item(user_id):
    # iCloud jan–jun 14,90; Music mar–set 21,90; iCloud reajustado jul–set 16,90.
    _semeia_ap(user_id, [_ap("ic", -14.9, 6, date(2026, 6, 10)),
                         _ap("am", -21.9, 7, date(2026, 9, 12)),
                         _ap("ic2", -16.9, 3, date(2026, 9, 10))])
    lista = listar_assinaturas(user_id, HOJE)
    assert [(x["valor"], x["valor_anterior"]) for x in lista["servicos"]] == [(21.9, None), (16.9, 14.9)]
    assert lista["total_mensal"] == 38.8


def test_reajuste_vai_para_a_cadeia_de_valor_mais_proximo(user_id):
    # iCloud jan–mar 14,90; Music fev–abr 21,90 (cancelado); iCloud mai–set 16,90:
    # as duas terminaram antes de maio, e o reajuste é do iCloud, não do Music.
    _semeia_ap(user_id, [_ap("ic", -14.9, 3, date(2026, 3, 10)),
                         _ap("am", -21.9, 3, date(2026, 4, 12)),
                         _ap("ic2", -16.9, 5, date(2026, 9, 10))])
    itens = {x["valor"]: x for x in listar_assinaturas(user_id, HOJE)["servicos"]}
    assert (itens[16.9]["valor_anterior"], itens[16.9]["meses"]) == (14.9, 8)
    assert itens[21.9]["status"] == "possivelmente_cancelada"


def test_conta_em_moeda_estrangeira_fica_fora(user_id):
    usd = mensais("us", [-20] * 3, desc="NETFLIX.COM")
    brl = mensais("br", [-39.9] * 3, ultima=date(2026, 9, 6), desc="Spotify")
    semeia(user_id, [{**conta("acc-usd", usd), "currency": "USD"}, conta("acc-brl", brl)],
           [rp("NETFLIX.COM", -20, usd), rp("Spotify", -39.9, brl)])
    lista = listar_assinaturas(user_id, HOJE)
    assert [x["chave"] for x in lista["servicos"]] == ["spotify"]
    assert lista["total_mensal"] == 39.9
