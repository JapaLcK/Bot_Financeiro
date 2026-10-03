"""Q40 — review Codex no #633, 4ª rodada.

P1: com a pergunta "dinheiro ou banco?" viva, "me fala meu saldo em dinheiro"
era lido como a RESPOSTA "dinheiro" (marcador em qualquer frase curta): gravava
o gasto e engolia o comando. Agora só frase com FORMATO de resposta resolve —
toda palavra é marcador de forma ou ligação (`forma_pagamento._LIGACAO`).

P2: "paguei a luz em dinheiro e no pix" (misto) caía na mesma pergunta binária
da forma desconhecida, e a resposta seguinte reescrevia o misto: a conta inteira
saía da Carteira ou toda pelo banco. Agora conta mista não paga nem pergunta,
igual ao lançamento misto (`test_a10_misto_nao_grava`).
"""
from __future__ import annotations

import pytest

import db
import db.bills as B
from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
from tests.test_conta_paga_forma import carteira, conta_fixa
from tests.test_forma_pagamento_conversa import com_of, manuais, pendencia  # noqa: F401
from tests.test_bill_amount_pending import _monta_conta_variavel

PERGUNTA = "payment_method_choice"


# ── P1: comando com marcador não é resposta ─────────────────────────────────

# Comando claro (tier 1/2 do classificador) abandona a pergunta com aviso e
# executa. "me fala meu saldo em dinheiro" e "qual meu saldo no pix" saem
# `out_of_scope 0.4` (dica de domínio, iriam para a IA): com a pergunta viva o
# route() a repete, como faz com "hmm" (A7) — nada gravado nos dois casos.
@pytest.mark.parametrize("comando,executa", [
    ("quanto gastei em dinheiro esse mês", True),
    ("me fala meu saldo em dinheiro", False), ("qual meu saldo no pix", False),
])
def test_comando_com_marcador_nao_resolve_a_pergunta(com_of, ia_fora, comando, executa):
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, comando)
    assert manuais(com_of) == 0, r
    assert "Não registrei" not in r and not ia_fora, r
    assert ("Cancelei a pergunta anterior" in r) is executa, r
    assert pendencia(com_of) == (None if executa else PERGUNTA), r


@pytest.mark.parametrize("resposta,grava", [
    ("dinheiro", True), ("foi em dinheiro", True), ("cash", True),
    ("pix", False), ("no pix mesmo", False), ("paguei no pix", False),
    ("pelo banco", False),
])
def test_resposta_de_verdade_continua_resolvendo(com_of, ia_fora, resposta, grava):
    manda(com_of, "gastei 500 no mercado")
    r = manda(com_of, resposta)
    assert manuais(com_of) == (1 if grava else 0), r
    assert pendencia(com_of) != PERGUNTA, r
    assert "Cancelei" not in r and not ia_fora, r


# ── P2: conta mista não paga nem pergunta ───────────────────────────────────

@pytest.mark.parametrize("frase", [
    "paguei a luz em dinheiro e no pix", "paguei a luz metade no pix metade em dinheiro"])
def test_conta_mista_nao_paga_nem_pergunta(com_of, ia_fora, frase):
    conta = conta_fixa(com_of)
    antes = carteira(com_of)
    r = manda(com_of, frase)
    assert "separado" in r, r
    assert pendencia(com_of) is None, r
    manda(com_of, "dinheiro")  # uma resposta depois não reescreve o misto
    assert B.get_bill(com_of, conta["id"])["status"] == "pending"
    assert manuais(com_of) == 0 and carteira(com_of) == pytest.approx(antes)


@pytest.mark.parametrize("frase,lancamentos", [
    ("paguei a luz em dinheiro", 1), ("paguei a luz no pix", 0)])
def test_conta_de_uma_forma_so_continua_pagando(com_of, ia_fora, frase, lancamentos):
    conta = conta_fixa(com_of)
    manda(com_of, frase)
    assert B.get_bill(com_of, conta["id"])["status"] == "paid"
    assert manuais(com_of) == lancamentos and pendencia(com_of) is None


def test_valor_de_conta_mista_de_antes_do_banco_nao_vira_pergunta(com_of, ia_fora):
    # `bill_amount_expected` armado SEM banco com a forma mista ("em dinheiro e
    # no pix" na conta variável); o banco conecta antes do valor chegar.
    conta = _monta_conta_variavel(com_of)
    db.set_pending_action(com_of, "bill_amount_expected", {
        "bill_id": int(conta["id"]), "bill_name": "Luz", "forma_pagamento": "misto"})
    r = manda(com_of, "132")
    assert "separado" in r, r
    assert pendencia(com_of) is None, r
    assert B.get_bill(com_of, conta["id"])["status"] == "pending" and manuais(com_of) == 0
