"""Escolha de fatura por NOME DE MÊS — o ramo `period_end.month == mo`.

O ramo vive em `core/handlers/credit.py`, no `_resolve_pay_bill_choice`, e até
aqui só era exercitado por UM caso: o mês em que a suíte rodasse. Como o caso
escrevia `"setembro"` à mão e a compra era `date.today()`, ele só passava nos
31 dias de [11/ago, 10/set] — 334 dias vermelhos por ano.

Aqui a compra tem data EXPLÍCITA e os doze meses entram. Arquivo separado do
`test_pendencia_credito_respostas_legitimas.py` porque aquele está a 14 linhas
do teto de 350 (`tests/test_max_lines_python.py`).

O segundo assunto do arquivo é a MESMA linha vista pelo outro lado: NÚMERO POR
EXTENSO não pode virar mês quando a pergunta é uma lista numerada. `sete`
pagava SETEMBRO e `dez` pagava DEZEMBRO — dinheiro na fatura errada.

O CATÁLOGO DE CONTROLES NEGATIVOS do grupo mora em
`tests/_pendencia_credito_helpers.py`. Os desta série:

CONTROLE NEGATIVO — no `_resolve_pay_bill_choice`, o ramo de mês vira
`if False:`. VERMELHOS: os doze casos de
`test_pay_bill_choice_paga_pelo_nome_do_mes_nos_doze_meses`.

CONTROLE NEGATIVO W — devolva `"sete": 9` ao `_MONTH_BY_TOKEN`. VERMELHOS:

    test_sete_nao_paga_setembro
    test_nenhum_outro_numero_por_extenso_e_apelido_de_mes

CONTROLE NEGATIVO X — o ramo de índice volta a ser só o `m_num` de dígitos
(`if m_num:` / `idx = int(m_num.group(1)) - 1`). VERMELHOS:

    test_numero_por_extenso_escolhe_o_item_da_lista[sete-7]
    test_numero_por_extenso_escolhe_o_item_da_lista[dez-10]

O W sozinho não fecha o `dez` (é abreviação LEGÍTIMA de dezembro, ao contrário
de `sete`, cuja abreviação canônica é `set`), e o X sozinho não fecha o `sete`
numa lista de 2 itens, onde índice 7 não existe. Por isso os dois.

POSITIVOS do grupo: `7`, `#7`, `setembro`, `set` e os doze meses continuam
pagando a fatura certa, e `dez` continua sendo dezembro quando não há item 10.
Sem eles, um conserto que recusasse tudo passaria.
"""
from __future__ import annotations

from datetime import date

import pytest

import db
from _pendencia_credito_helpers import (
    AVISO as _AVISO,
    arma_pay_bill_choice as _arma_pay_bill_choice,
    cartao as _cartao,
    diga as _diga,
    novo_uid as _uid,
)
from core.handlers.credit import _MONTH_NAMES_PT


def _faturas(uid: int, *meses: int) -> list[dict]:
    """Uma fatura por mês pedido, no MESMO cartão, com a escolha pendente.

    Compra no dia 5 — antes do fechamento (dia 10) — para a fatura cair no
    PRÓPRIO mês, sem depender do calendário de hoje. Um cartão só porque doze
    cartões estouram o limite do plano (`PlanLimitExceeded`), e doze faturas
    atrasadas num cartão é o caso real de qualquer jeito.

    Devolve na mesma ordem do `list_open_bills` (`period_end asc`), que é a
    ordem em que o bot numera a lista.
    """
    card_id = _cartao(uid)
    for mes in meses:
        db.add_credit_purchase_installments(
            user_id=uid, card_id=card_id, valor_total=300.0, categoria="outros",
            nota="mercado", purchased_at=date(2026, mes, 5), installments=1,
        )
    faturas = db.list_open_bills(uid)
    db.set_pending_action(uid, "pay_bill_choice",
                          {"bill_ids": [int(b["id"]) for b in faturas], "amount": None})
    return faturas


@pytest.mark.parametrize("mes", range(1, 13))
def test_pay_bill_choice_paga_pelo_nome_do_mes_nos_doze_meses(mes):
    uid = _uid()
    # Dia 5 é antes do fechamento (dia 10), então a fatura é a do PRÓPRIO mês.
    _arma_pay_bill_choice(uid, purchased_at=date(2026, mes, 5))
    token = _MONTH_NAMES_PT[mes - 1].lower()
    assert db.list_open_bills(uid)[0]["period_end"].month == mes, "setup errado"

    resposta = _diga(uid, token)

    assert _AVISO not in resposta, f"{token!r} foi abandonada: {resposta!r}"
    assert db.list_open_bills(uid) == [], f"{token!r} não pagou: {resposta!r}"


def test_sete_nao_paga_setembro():
    """Lista CURTA (2 itens): o item 7 não existe e setembro está na lista.

    `sete` sai do dicionário porque a abreviação canônica de setembro é `set` —
    `sete` só existia como sinônimo redundante, e é o número 7. `dez` NÃO sai
    (é a canônica de dezembro): o caso dele é o de índice, logo abaixo.
    """
    uid = _uid()
    faturas = _faturas(uid, 1, 9)
    assert len(faturas) == 2, faturas

    resposta = _diga(uid, "sete")

    assert db.list_open_bills(uid) == faturas, (
        f"'sete' pagou Setembro numa lista de 2 itens: {resposta!r}")
    assert "Não entendi qual fatura" in resposta, resposta


@pytest.mark.parametrize("palavra,indice", [("sete", 7), ("dez", 10)])
def test_numero_por_extenso_escolhe_o_item_da_lista(palavra, indice):
    """Lista LONGA: a palavra é o índice, e a fatura paga é a da posição — não
    a do mês homônimo, que também está na lista."""
    uid = _uid()
    # 12 faturas, uma por mês: a posição N é o mês N (ordem é `period_end asc`).
    faturas = _faturas(uid, *range(1, 13))
    assert len(faturas) == 12, faturas
    alvo = faturas[indice - 1]

    resposta = _diga(uid, palavra)

    abertas = {int(b["id"]) for b in db.list_open_bills(uid)}
    assert int(alvo["id"]) not in abertas, \
        f"{palavra!r} não pagou o item {indice}: {resposta!r}"
    assert len(abertas) == 11, f"{palavra!r} mexeu em mais de uma fatura: {abertas}"


# ---------------------------------------------------------------------------
# POSITIVOS — o conserto não pode recusar o caminho legítimo.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resposta_do_user", ["7", "#7", "setembro", "set"])
def test_setembro_e_o_numero_continuam_pagando(resposta_do_user):
    """`sete` saiu do dicionário; o dígito e as duas formas legítimas de
    setembro (`setembro`, a abreviação canônica `set`) continuam."""
    uid = _uid()
    faturas = _faturas(uid, *range(1, 13))
    alvo = faturas[6]  # 7º item = fatura de julho por índice, setembro por mês
    if resposta_do_user in ("setembro", "set"):
        alvo = faturas[8]

    resposta = _diga(uid, resposta_do_user)

    abertas = {int(b["id"]) for b in db.list_open_bills(uid)}
    assert int(alvo["id"]) not in abertas, \
        f"{resposta_do_user!r} não pagou a fatura certa: {resposta!r}"
    assert len(abertas) == 11, abertas


def test_dez_continua_sendo_dezembro_na_lista_curta():
    """`dez` é a abreviação CANÔNICA de dezembro (jan/fev/…/dez) — tirá-la do
    dicionário seria o conserto errado. Sem item 10 na lista, ela segue
    pagando dezembro."""
    uid = _uid()
    faturas = _faturas(uid, 1, 12)

    resposta = _diga(uid, "dez")

    abertas = {int(b["id"]) for b in db.list_open_bills(uid)}
    assert int(faturas[1]["id"]) not in abertas, resposta
    assert len(abertas) == 1, abertas


def test_nenhum_outro_numero_por_extenso_e_apelido_de_mes():
    """A ENUMERAÇÃO (§2: é categoria, não instância). Os 12 números por extenso
    contra as chaves do dicionário de meses — se alguém acrescentar `nove` ou
    `um` como apelido, este teste cai antes de virar dinheiro na conta errada.
    """
    from core.handlers.credit import _MONTH_BY_TOKEN

    numeros = ["um", "dois", "tres", "quatro", "cinco", "seis", "sete",
               "oito", "nove", "dez", "onze", "doze"]
    colisoes = {n: _MONTH_BY_TOKEN[n] for n in numeros if n in _MONTH_BY_TOKEN}

    assert colisoes == {"dez": 12}, (
        "número por extenso virando mês. `dez` é a única colisão aceita "
        f"(abreviação canônica de dezembro); veio: {colisoes}")
