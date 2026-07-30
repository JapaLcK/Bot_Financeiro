"""
Unit da separação de múltiplos lançamentos num único áudio/mensagem.

Regressão: "gastei 500 no ifood e mais 800 no mercado" registrava só o 500.
O conector "e mais" não estava na lista e o segundo trecho ("800 no mercado")
não começa com verbo — precisa herdar "gastei" do lançamento anterior.
"""
from __future__ import annotations

import pytest

from core.handle_incoming import _split_audio_transactions as split


def test_bug_e_mais_valor_implicito_herda_verbo():
    # o caso reportado pelo Lucas
    assert split("gastei 500 no ifood e mais 800 no mercado") == [
        "gastei 500 no ifood",
        "gastei 800 no mercado",
    ]


def test_mais_sozinho_como_conector():
    assert split("gastei 50 no uber mais 30 no lanche") == [
        "gastei 50 no uber",
        "gastei 30 no lanche",
    ]


def test_tambem_com_valor_implicito():
    assert split("paguei 100 de luz também 200 de água") == [
        "paguei 100 de luz",
        "paguei 200 de água",
    ]


def test_verbo_explicito_no_segundo_ainda_funciona():
    # comportamento antigo preservado (não herda, cada um tem seu verbo)
    assert split("recebi 600 da mãe e gastei 100 no mercado") == [
        "recebi 600 da mãe",
        "gastei 100 no mercado",
    ]


def test_tres_lancamentos_mistos():
    assert split("gastei 500 no ifood e mais 800 no mercado e 20 no pão") == [
        "gastei 500 no ifood",
        "gastei 800 no mercado",
        "gastei 20 no pão",
    ]


def test_receita_nao_vira_despesa():
    assert split("recebi 1000 de salário e mais 200 de freela") == [
        "recebi 1000 de salário",
        "recebi 200 de freela",
    ]


def test_real_com_cifrao_implicito():
    assert split("gastei 40 no almoço e mais R$ 15 no café") == [
        "gastei 40 no almoço",
        "gastei R$ 15 no café",
    ]


def test_lancamento_unico_nao_muda():
    assert split("gastei 50 no mercado") == ["gastei 50 no mercado"]


def test_e_conjuncao_sem_valor_nao_quebra():
    # "e no domingo" não tem valor nem verbo depois → não separa
    txt = "gastei 50 no mercado e no domingo fui embora"
    assert split(txt) == [txt]


@pytest.mark.parametrize("txt", ["", "   "])
def test_vazio(txt):
    assert split(txt) == [txt]
