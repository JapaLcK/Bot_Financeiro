"""PR-B (ADR 0006): "2 reais e 50" não é um valor só nem dois gastos: o splitter parte em " e N" e a declaração (valor
travado, DP-4) gravaria R$ 50 (ou R$ 2 e R$ 50). Na decisão BANCO a mensagem é recusada sem gravar (T11); fila: o item volta.

Controle negativo: `recusa_valor` sem o ramo `_AMBIGUO_RE` (texto) e a recusa de `rotear_partes` sem efeito (áudio) deixam vermelhos
os casos de recusa. Positivos: dois pedaços COM descrição continuam gravando dois; valor claro grava; Carteira, cartão e
flag desligada não mudam.
"""
from __future__ import annotations

import pytest

from tests._declaracao_b import (ana, carteira, declaracoes, fala, liga, manda_wamid, n_lancamentos,  # noqa: F401
                                 pendencia)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401

T11 = "não sei se *"


@pytest.mark.parametrize("texto", [
    "gastei 2 reais e 50 no mercado pelo banco", "gastei R$ 12 e 90 no mercado no pix",
    "gastei 1 real e 50 no mercado no pix", "gastei dois e cinquenta no mercado no pix",
    "gastei dois reais e cinquenta no mercado no pix", "gastei 1 mil e 200 no mercado no pix",
    "gastei 50 no mercado e 30 pelo banco", "gastei 20 e 30 no mercado no pix",
])
def test_valor_que_pode_ser_um_ou_dois_recusa_sem_gravar(ana, liga, texto):
    r = manda_wamid(ana, texto)[0]
    assert r.startswith("🐷 Não registrei: não sei se *") and "Manda de novo só com números" in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0


@pytest.mark.parametrize("fala_", ["gastei 2 reais e 50 no mercado no pix", "gastei dois reais e cinquenta no mercado no pix",
                                   "gastei R$ 12 e 90 no mercado pelo banco"])
def test_audio_equivalente_recusa_sem_gravar(ana, liga, monkeypatch, fala_):
    r = fala(monkeypatch, ana, fala_)
    assert any(T11 in x for x in r), r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0
    assert pendencia(ana) in (None, "recategorize_launch_offer", "undo_audio")


def test_audio_cuja_forma_vem_na_resposta_tambem_recusa(ana, liga, monkeypatch):
    fala(monkeypatch, ana, "gastei 2 reais e 50 no mercado")
    assert pendencia(ana) == "payment_method_choice"
    assert T11 in manda_wamid(ana, "pix")[0]
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0


@pytest.mark.parametrize("texto,valores", [
    ("gastei 20 no mercado e 30 na padaria pelo banco", [20.0, 30.0]),
    ("gastei 50 no mercado e mais 20 na padaria no pix", [20.0, 50.0]),
    ("gastei 2200 no mercado pelo banco", [2200.0]), ("gastei 12,90 no mercado no pix", [12.9]),
    ("gastei dois mil e duzentos reais no mercado no pix", [2200.0]),
    ("gastei vinte e cinco reais no mercado no pix", [25.0]),
])
def test_positivo_valor_claro_e_dois_pedacos_com_descricao_gravam(ana, liga, texto, valores):
    manda_wamid(ana, texto)
    assert sorted(float(d["valor"]) for d in declaracoes(ana)) == valores


def test_positivo_audio_com_dois_pedacos_com_descricao_grava_dois(ana, liga, monkeypatch):
    fala(monkeypatch, ana, "gastei 20 no mercado e 30 na padaria pelo banco")
    assert sorted(float(d["valor"]) for d in declaracoes(ana)) == [20.0, 30.0]


def test_fila_de_valores_devolve_o_item(ana, liga, monkeypatch):
    fala(monkeypatch, ana, "paguei o aluguel e paguei a academia no pix")
    r = manda_wamid(ana, "2 reais e 50")[0]
    assert T11 in r and "aluguel" in r and declaracoes(ana) == [], r
    assert pendencia(ana) == "multi_launch_values"
    assert "registrada pelo banco" in manda_wamid(ana, "2,50")[0]
    assert [float(d["valor"]) for d in declaracoes(ana)] == [2.5]


def test_carteira_cartao_e_flag_desligada_nao_mudam(ana, liga):
    r = manda_wamid(ana, "gastei 2 reais e 50 no mercado em dinheiro")
    assert T11 not in "".join(r) and declaracoes(ana) == [] and n_lancamentos(ana) >= 1
    r = manda_wamid(ana, "gastei 2 reais e 50 no mercado no cartão")
    assert T11 not in "".join(r) and "cartão de crédito conectado" in "".join(r)


def test_flag_desligada_a_recusa_de_hoje(ana):
    r = manda_wamid(ana, "gastei 2 reais e 50 no mercado no pix")[0]
    assert "Não registrei" in r and T11 not in r and declaracoes(ana) == []
