"""A SEXTA porta: o vocabulário dos portões deixa de ser CÓPIA (#323).

Os dois defeitos que este arquivo mede são o mesmo defeito — duas listas
escritas à mão que deviam ser uma (§0.7), e que derivaram em silêncio:

1. O vocabulário de VERBO do portão de nome de cartão era cópia das regexes de
   `core/intent_classifier`, e perdeu `debitei`. Como
   `debitei 50 no cartao Nubank` é `credit.handle/0.95` — intent que
   deliberadamente NÃO abandona —, a poda livre de prefixo sobrava `nubank` e a
   fatura era PAGA. Vetar por INTENT não serviria: `a fatura do nubank`,
   resposta legítima, tem exatamente o mesmo `credit.handle/0.95`. Quem separa
   as duas é o VERBO. A rodada seguinte mostrou que a lista de verbos era o
   remédio errado (ela vazava por baixo, ver P3) e a inverteu num ALLOWLIST de
   prefixo; o que sobreviveu foi a exigência de fonte única, agora medida como
   interseção vazia em vez de inclusão.
2. `_NEGATIVAS_EXATAS` era cópia das entradas `confirm.no` do `_EXACT`, e
   perdia CINCO das dez (`nope`, `negativo`, `melhor nao`, `deixa pra la`,
   `deixa quieto`). Num step de sim/não o portão devolvia `None`, o `route()`
   abandonava, e a resposta era **"Nada a cancelar."**.

Os dois agora IMPORTAM a fonte, então a deriva deixa de ser possível — é o que
os dois controles abaixo medem, cada um desligando uma das importações.

O CATÁLOGO DE CONTROLES (A–V) do grupo mora em
`tests/_pendencia_credito_helpers.py`. Os dois desta rodada moram AQUI e não
lá, e é limitação de teto, não de assunto: aquele arquivo já está no limite do
`tests/test_max_lines_python.py` (medir com `wc -l`, não confiar num número
escrito aqui — §2).

CONTROLE NEGATIVO W — ponha um verbo de dinheiro no `_SELECAO`
(`_SELECAO = {..., "somei", "coloca"}`), que é o jeito de a fonte única ser
desfeita hoje. VERMELHO:

    test_verbo_que_move_dinheiro_nunca_e_podavel

Só ele, e é o ponto: `somei 50 no nubank` segue VERDE porque o `50` também é
barreira. Um allowlist frouxo não vira bug na hora — vira bug na primeira
mensagem sem número. Por isso a interseção é medida como constante, e não só
pelo comportamento. A versão anterior deste controle injetava `somei` num teste
que olhava só o `VERBOS_DE_LANCAMENTO` (onde `somei` não está) e saía 316
verdes — controle que não discrimina, corrigido antes de reportar.

CONTROLE NEGATIVO X — desfaça a importação do `_is_no`
(`_NEGATIVAS_EXATAS = {"nao", "não", "n", "no", "cancelar", "cancela",
"agora nao", "agora não"}`). VERMELHOS:

    test_negativas_exatas_contem_todo_confirm_no_do_classificador
    test_is_no_reconhece_toda_negativa_do_classificador[nope]
    test_is_no_reconhece_toda_negativa_do_classificador[negativo]
    test_is_no_reconhece_toda_negativa_do_classificador[melhor nao]
    test_is_no_reconhece_toda_negativa_do_classificador[deixa pra la]
    test_is_no_reconhece_toda_negativa_do_classificador[deixa quieto]
    test_reminder_opt_in_aceita_negativa_do_classificador[nope]
    test_reminder_opt_in_aceita_negativa_do_classificador[negativo]
    test_reminder_opt_in_aceita_negativa_do_classificador[melhor nao]
    test_reminder_opt_in_aceita_negativa_do_classificador[deixa pra la]
    test_reminder_opt_in_aceita_negativa_do_classificador[deixa quieto]
    test_delete_card_negativa_do_classificador_mantem_o_cartao[nope]
    test_delete_card_negativa_do_classificador_mantem_o_cartao[negativo]
    test_delete_card_negativa_do_classificador_mantem_o_cartao[melhor nao]

POSITIVO do grupo, e é o que o allowlist mais arrisca quebrar: um conjunto
podável apertado demais recusa RESPOSTA LEGÍTIMA.
`test_legitimas_continuam_pagando_com_o_allowlist` e
`test_pay_bill_choice_paga_cartao_com_nome_de_palavra_podavel` ficam VERDES em
todos os controles — as respostas que uma pessoa dá para "qual fatura?"
continuam pagando, inclusive quando o próprio nome do cartão é uma palavra
podável (`Vai`, `Pode`, `Minha Conta`).

O QUE ESTE ARQUIVO NÃO PEGA: `_is_yes` tem o mesmo buraco (perde `quero sim`,
`pode sim`, `claro que sim`) e fica de FORA de propósito — alargar o SIM alarga
o gatilho de uma confirmação DESTRUTIVA (`_resolve_delete_card`). Não
reconhecer um "sim" é fail-safe; não reconhecer um "não" é só confuso.
"""
from __future__ import annotations

import pytest

import core.handlers.credit as credit
import core.handlers.pockets as pockets
import core.intent_classifier as ic
import db
from utils_text import DEPOSIT_VERBS
from _pendencia_credito_helpers import (
    AVISO as _AVISO,
    arma_delete_card as _arma_delete_card,
    arma_pay_bill_choice as _arma_pay_bill_choice,
    cartao as _cartao,
    diga as _diga,
    novo_uid as _uid,
)


# ---------------------------------------------------------------------------
# §0.7 — as listas que eram cópia
# ---------------------------------------------------------------------------

# As TRÊS famílias de verbo que MOVEM dinheiro, cada uma na sua fonte — nenhuma
# copiada (§0.7). Se um verbo novo entrar em qualquer uma delas, ele entra aqui
# junto, sem ninguém precisar lembrar deste arquivo.
_VERBOS_QUE_MOVEM_DINHEIRO = (frozenset(ic.VERBOS_DE_LANCAMENTO)
                              | frozenset(DEPOSIT_VERBS)
                              | frozenset(pockets._WITHDRAW_VERBS))


def test_verbo_que_move_dinheiro_nunca_e_podavel():
    """O MESMO defeito do veto, medido pelo lado invertido — e é o lado que fecha.

    O veto (`_VERBO_DE_COMANDO`, removido) enumerava o PERIGOSO e por isso
    vazava por baixo: `somei`, `depositei`, `investi`, `saquei` nunca estiveram
    nele — eles moram nas regexes de caixinha/investimento, não nas de
    lançamento — e `somei 50 no nubank` PAGAVA a fatura de R$ 300.

    Hoje quem decide é o allowlist do prefixo, e a garantia é esta interseção
    vazia: verbo que move dinheiro não é podável, logo toda leitura de uma
    mensagem que comece com um CONTINUA começando com ele — e verbo nenhum é
    nome de cartão.

    Não é tautologia: ponha `coloca` ou `guarda` no `_SELECAO` "porque soa
    conversacional" e isto fica vermelho na hora. Foi essa a injeção que provou
    a versão anterior deste teste ser fraca demais — ela olhava só o
    `VERBOS_DE_LANCAMENTO`, e `coloca` não está lá.

    `_CORTESIA_FINAL` fica FORA da interseção de propósito: `por` está no
    `DEPOSIT_VERBS` e em "por favor", e seria falso positivo. Quem guarda a
    ponta do SUFIXO é o controle P do catálogo, não este teste.
    """
    vazou = _VERBOS_QUE_MOVEM_DINHEIRO & credit._PODAVEL_NO_PREFIXO
    assert not vazou, f"verbo de dinheiro virou podável: {sorted(vazou)}"
    assert "debitei" not in credit._PODAVEL_NO_PREFIXO


def test_negativas_exatas_contem_todo_confirm_no_do_classificador():
    do_exact = {k for k, v in ic._EXACT.items() if v == "confirm.no"}
    faltando = do_exact - credit._NEGATIVAS_EXATAS
    assert not faltando, f"negativas do classificador fora do portão: {sorted(faltando)}"


# ---------------------------------------------------------------------------
# P1 — verbo de lançamento não escolhe fatura
# ---------------------------------------------------------------------------

_ATAQUES = [f"{verbo} 50 no cartao Nubank" for verbo in
            ("gastei", "paguei", "comprei", "debitei", "mandei", "enviei",
             "pixei", "torrei", "queimei")]


@pytest.mark.parametrize("frase", _ATAQUES)
def test_pay_bill_choice_nao_paga_com_verbo_de_lancamento(frase):
    """A conversa, não a função: a pendência é armada e a frase entra pelo
    `handle_incoming` (§3). O que se mede é DINHEIRO, não a mensagem — abandonar
    ou re-perguntar são as duas saídas aceitáveis; pagar não é."""
    uid = _uid()
    _arma_pay_bill_choice(uid)
    saldo_antes = float(db.get_balance(uid))

    resposta = _diga(uid, frase)

    assert db.list_open_bills(uid), f"{frase!r} PAGOU a fatura: {resposta!r}"
    assert float(db.get_balance(uid)) > saldo_antes - 300, \
        f"{frase!r} debitou a fatura: {resposta!r}"


_LEGITIMAS = ["a fatura do nubank", "fatura do nubank", "quero o nubank",
              "pode ser o nubank", "escolho o nubank"]


@pytest.mark.parametrize("resposta_do_user", _LEGITIMAS)
def test_legitimas_continuam_pagando_com_o_allowlist(resposta_do_user):
    """O POSITIVO do grupo. Trocar o veto pelo allowlist RESTRINGE a poda; um
    portão que recusa tudo é pior que o bug."""
    uid = _uid()
    _arma_pay_bill_choice(uid)

    resposta = _diga(uid, resposta_do_user)

    assert _AVISO not in resposta, f"{resposta_do_user!r} foi abandonada: {resposta!r}"
    assert db.list_open_bills(uid) == [], \
        f"{resposta_do_user!r} não pagou: {resposta!r}"


# ---------------------------------------------------------------------------
# P2 — a negativa que o classificador conhece e o portão não conhecia
# ---------------------------------------------------------------------------

# As CINCO que a cópia à mão perdia. `cancelar`/`cancela` ficam fora dos casos
# ponta a ponta de propósito: o `handle_billing_command` as intercepta ANTES do
# `route()`, então elas não medem este portão — e já estavam na lista antiga,
# logo nada muda para elas.
_NEGATIVAS_NOVAS = ["nope", "negativo", "melhor nao", "deixa pra la", "deixa quieto"]


@pytest.mark.parametrize("negativa", _NEGATIVAS_NOVAS)
def test_is_no_reconhece_toda_negativa_do_classificador(negativa):
    assert credit._is_no(negativa), negativa


@pytest.mark.parametrize("negativa", _NEGATIVAS_NOVAS)
def test_reminder_opt_in_aceita_negativa_do_classificador(negativa):
    uid = _uid()
    card_id = _cartao(uid)
    db.set_pending_action(uid, "credit_card_setup", {
        "step": "reminder_opt_in", "card_name": "Nubank", "card_id": card_id,
        "closing_day": 10, "ask_primary": False,
    })

    resposta = _diga(uid, negativa)

    assert "nada a cancelar" not in resposta.lower(), \
        f"{negativa!r} saiu pelo ramo confirm.no: {resposta!r}"
    assert _AVISO not in resposta, f"{negativa!r} foi abandonada: {resposta!r}"
    assert "limite de crédito" in resposta.lower(), \
        f"{negativa!r} não seguiu o cadastro: {resposta!r}"


@pytest.mark.parametrize("negativa", ["nope", "negativo", "melhor nao"])
def test_delete_card_negativa_do_classificador_mantem_o_cartao(negativa):
    """A ponta destrutiva do mesmo portão."""
    uid = _uid()
    _arma_delete_card(uid)

    resposta = _diga(uid, negativa)

    assert len(db.list_cards(uid)) == 1, f"{negativa!r} apagou o cartão: {resposta!r}"
    assert "mantive" in resposta.lower(), f"{negativa!r}: {resposta!r}"


# ---------------------------------------------------------------------------
# P3 — o verbo que veto NENHUM ia alcançar (a inversão para o allowlist)
# ---------------------------------------------------------------------------

# Estes seis NÃO são `VERBOS_DE_LANCAMENTO`: moram nas regexes de caixinha,
# investimento e saque (`pockets.deposit`, `investments.deposit`,
# `funds.withdraw`…). Medidos na árvore com o veto JÁ corrigido, todos os seis
# PAGAVAM a fatura de R$ 300 — porque a poda era livre e o veto só conhecia o
# vocabulário de LANÇAMENTO. Estender o veto com eles seria a sexta rodada da
# mesma revisão; o allowlist os fecha sem citar nenhum.
#
# A varredura que motivou a inversão, medida em 2026-09-10: extrair as palavras
# de alternância inicial de `_ALIAS_PATTERNS`, montar `"<palavra> 50 no nubank"`
# com cada uma e perguntar se `_leituras_da_resposta` gera a leitura `nubank`.
# Deu 95/133 com a poda livre e 0/133 com o allowlist. REMEDIR antes de reusar
# (§2): o script está no relato do #323, e `_ALIAS_PATTERNS` cresce.
_ATAQUES_FORA_DO_VETO = [f"{verbo} 50 no nubank" for verbo in
                         ("somei", "depositei", "investi", "saquei",
                          "transferi", "coloquei")]


@pytest.mark.parametrize("frase", _ATAQUES_FORA_DO_VETO)
def test_pay_bill_choice_nao_paga_com_verbo_de_outro_dominio(frase):
    uid = _uid()
    _arma_pay_bill_choice(uid)
    saldo_antes = float(db.get_balance(uid))

    resposta = _diga(uid, frase)

    assert db.list_open_bills(uid), f"{frase!r} PAGOU a fatura: {resposta!r}"
    assert float(db.get_balance(uid)) > saldo_antes - 300, \
        f"{frase!r} debitou a fatura: {resposta!r}"


# ---------------------------------------------------------------------------
# P4 — o risco NOVO do allowlist: cartão que se chama como palavra podável
# ---------------------------------------------------------------------------
#
# `Vai`, `Pode` e `Minha Conta` são nomes que colidem com o `_SELECAO`/`_FILLER`.
# O allowlist poda o prefixo pela esquerda, então o nome só sobrevive por ser
# SUFIXO da mensagem — o `min(podavel, fim - 1)` é o que garante que a leitura
# nunca fica vazia e que o último token nunca é comido.


@pytest.mark.parametrize("nome, resposta_do_user", [
    ("Vai", "vai"),
    ("Vai", "o vai"),
    ("Vai", "quero o vai"),
    ("Vai", "vai por favor"),
    ("Pode", "pode"),
    ("Pode", "quero o pode"),
    ("Minha Conta", "minha conta"),
    ("Minha Conta", "a minha conta"),
])
def test_pay_bill_choice_paga_cartao_com_nome_de_palavra_podavel(nome, resposta_do_user):
    """O positivo do P4: o nome não pode ser engolido pela própria poda."""
    uid = _uid()
    _arma_pay_bill_choice(uid, nome)

    resposta = _diga(uid, resposta_do_user)

    assert _AVISO not in resposta, f"{resposta_do_user!r} foi abandonada: {resposta!r}"
    assert db.list_open_bills(uid) == [], \
        f"{resposta_do_user!r} não pagou o {nome!r}: {resposta!r}"


@pytest.mark.parametrize("nome, frase", [
    ("Vai", "excluir cartao vai"),
    ("Vai", "vai excluir"),
    ("Vai", "quanto gastei no vai"),
    ("Vai", "somei 50 no vai"),
    ("Pode", "limite do pode"),
    ("Minha Conta", "quanto tem na minha conta"),
    ("Minha Conta", "excluir cartao minha conta"),
])
def test_pay_bill_choice_nao_paga_comando_com_nome_de_palavra_podavel(nome, frase):
    """O negativo do P4: nome podável não pode transformar comando em escolha."""
    uid = _uid()
    _arma_pay_bill_choice(uid, nome)
    saldo_antes = float(db.get_balance(uid))

    resposta = _diga(uid, frase)

    assert db.list_open_bills(uid), f"{frase!r} PAGOU a fatura do {nome!r}: {resposta!r}"
    assert float(db.get_balance(uid)) > saldo_antes - 300, \
        f"{frase!r} debitou a fatura: {resposta!r}"
