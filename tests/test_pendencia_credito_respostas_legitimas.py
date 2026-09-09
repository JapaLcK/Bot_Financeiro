"""O ESPELHO, parte 1: DESCRIÇÃO de compra e escolha de FATURA.

A pergunta inversa à dos arquivos de abandono, e a que faltou nas primeiras
rodadas — um portão que recusa tudo passa em qualquer teste de ataque. Aqui
estão as descrições de compra reais (com verbo e com número na frente) e os
nomes de cartão com acento, pontuação e filler. As confirmações e os steps do
cadastro estão em `test_pendencia_credito_steps_legitimos.py`.

O CATÁLOGO DE CONTROLES NEGATIVOS (A–M) do grupo inteiro mora em
`tests/_pendencia_credito_helpers.py`, uma vez só. Leia lá antes de mexer aqui.
"""
from __future__ import annotations

from datetime import date

import pytest

import db
from _pendencia_credito_helpers import (
    AVISO as _AVISO,
    AS_CINCO as _AS_CINCO,
    arma_card_setup as _arma_card_setup,
    arma_delete_card as _arma_delete_card,
    arma_installment as _arma_installment,
    arma_pay_bill_choice as _arma_pay_bill_choice,
    arma_set_primary as _arma_set_primary,
    cartao as _cartao,
    diga as _diga,
    escrituras as _escrituras,
    novo_uid as _uid,
)


# ===========================================================================
# POSITIVOS — a resposta LEGÍTIMA continua sendo resolvida.
# Sem estes, o grupo inteiro passaria num código que abandona tudo.
# ===========================================================================

def test_installment_descricao_real_registra():
    """`installment_pending` + "tv samsung" → as 5 parcelas, com a descrição."""
    uid = _uid()
    _arma_installment(uid)

    resposta = _diga(uid, "tv samsung")

    assert "parcelamento registrado" in resposta.lower(), resposta
    assert _AVISO not in resposta, f"abandonou uma descrição legítima: {resposta!r}"
    with db.get_conn() as conn, conn.cursor() as cur:
        linhas = cur.execute(
            "select nota from credit_transactions where user_id = %s", (uid,)
        ).fetchall()
    assert len(linhas) == 5, f"esperava 5 parcelas, veio {len(linhas)}"
    assert all("tv samsung" in (r["nota"] or "").lower() for r in linhas), linhas
    assert db.get_pending_action(uid) is None, "a pendência não foi consumida"


# Descrições de compra REAIS, não o "mercado" bonitinho de sempre (§3 do
# CLAUDE.md): é a entrada que o usuário digita que precisa sobreviver ao
# predicado, não a que o teste projetou.
_DESCRICOES = [
    # substantivo puro
    "tv samsung", "iphone", "notebook dell", "ps5", "airfryer",
    "passagem aérea", "pneu do carro", "material escolar", "farmácia",
    "mercado", "sofá da sala", "curso de inglês",
    # COM VERBO na frente — o tier 2 casa `^(gastei|paguei|comprei…)` como
    # `launches.add`/0.95. Era a metade da classe que a blacklist comia.
    "comprei uma tv", "comprei um sofa", "paguei o notebook",
    "gastei com o celular",
    # COM NÚMERO na frente — o tier 2 casa `^\\d+\\s+[a-z]`, também
    # `launches.add`/0.95, e o valor virava R$ 2,00 / R$ 10,00.
    "2 passagens", "10 cadeiras", "3 camisas", "2 pneus",
]


@pytest.mark.parametrize("descricao", _DESCRICOES)
def test_installment_todas_as_descricoes_registram(descricao):
    """Nenhuma descrição de compra real pode ser lida como "outro comando"."""
    uid = _uid()
    _arma_installment(uid)

    resposta = _diga(uid, descricao)

    assert "parcelamento registrado" in resposta.lower(), \
        f"{descricao!r} foi abandonada: {resposta!r}"
    with db.get_conn() as conn, conn.cursor() as cur:
        n = cur.execute("select count(*) as n from credit_transactions "
                        "where user_id = %s", (uid,)).fetchone()["n"]
    assert n == 5, f"{descricao!r}: esperava 5 parcelas, veio {n}"
    # A metade que estava CEGA: a blacklist não só abandonava — o comando
    # misclassificado EXECUTAVA e gravava uma despesa com o número da descrição
    # ("2 passagens" → R$ 2,00). Contar parcelas não pegava isso.
    assert db.list_launches(uid) == [], \
        f"{descricao!r} virou lançamento: {db.list_launches(uid)!r}"


@pytest.mark.parametrize("resposta_do_user", ["1", "nubank", "setembro"])
def test_pay_bill_choice_paga_com_resposta_legitima(resposta_do_user):
    """O número e o nome do cartão sozinhos continuam escolhendo a fatura."""
    uid = _uid()
    _arma_pay_bill_choice(uid)

    resposta = _diga(uid, resposta_do_user)

    assert _AVISO not in resposta, f"{resposta_do_user!r} foi abandonada: {resposta!r}"
    assert db.list_open_bills(uid) == [], \
        f"{resposta_do_user!r} não pagou a fatura: {resposta!r}"


# FIXADO, NÃO CONSERTADO. Estas cinco já NÃO escolhem fatura na `main`: o
# `_parse_month_year_token` (`core/handlers/credit.py:78`) só lê `maio` ou
# `05/2026`, e o `normalize_text` apaga a barra antes (`09/2026` vira `09 2026`,
# que a regex não casa). Antes caíam no fallback e re-perguntavam; agora o
# portão devolve `None` e o `route()` abandona com aviso.
#
# O que MUDA é a mensagem; o que NÃO muda é o dinheiro — nunca pagam. Fazê-las
# funcionar é feature, e feature não entra em PR de dinheiro.
# Medido em duas colunas (main × branch): as 20 dão o MESMO resultado nas duas.
_PAY_LEGITIMAS = [
    "1", "#1", "setembro", "nubank", "Nubank", "NUBANK", "o nubank",
    "a do nubank", "a fatura do nubank", "minha fatura do nubank",
    "fatura do nubank", "essa do nubank",
]


@pytest.mark.parametrize("resposta_do_user", _PAY_LEGITIMAS)
def test_pay_bill_choice_legitimas_continuam_pagando(resposta_do_user):
    uid = _uid()
    _arma_pay_bill_choice(uid)

    resposta = _diga(uid, resposta_do_user)

    assert _AVISO not in resposta, f"{resposta_do_user!r} foi abandonada: {resposta!r}"
    assert db.list_open_bills(uid) == [], \
        f"{resposta_do_user!r} não pagou: {resposta!r}"


@pytest.mark.parametrize("resposta_do_user", ["mercado pago", "o mercado pago",
                                              "a do mercado pago",
                                              "a fatura do mercado pago"])
def test_pay_bill_choice_nome_de_duas_palavras(resposta_do_user):
    """O `_FILLER` não pode comer parte de um nome composto."""
    uid = _uid()
    card_id = db.create_card(uid, "Mercado Pago", 10, 17)
    db.add_credit_purchase_installments(
        user_id=uid, card_id=card_id, valor_total=300.0, categoria="outros",
        nota="mercado", purchased_at=date.today(), installments=1)
    bill_ids = [int(b["id"]) for b in db.list_open_bills(uid)]
    db.set_pending_action(uid, "pay_bill_choice", {"bill_ids": bill_ids, "amount": None})

    resposta = _diga(uid, resposta_do_user)

    assert db.list_open_bills(uid) == [], f"{resposta_do_user!r} não pagou: {resposta!r}"
# ===========================================================================
# O ESPELHO: o portão recusa RESPOSTA LEGÍTIMA? (rodada 6)
# A pergunta destes é a inversa da de cima, e é a que faltava.
# ===========================================================================

# Nome de cartão com acento, pontuação, filler DENTRO do nome, e nome que É uma
# palavra de filler. Medido em duas colunas: a versão anterior de
# `_card_name_da_resposta` quebrava 12/39 destas, porque normalizava só a
# resposta e ia ao `get_card_id_by_name`, que só faz `lower()`.
_NOMES_E_RESPOSTAS = [
    ("Itaú", "itau"), ("Itaú", "a do itau"), ("Itaú", "o itau"),
    ("Itaú", "a fatura do itau"), ("Itaú", "minha fatura do itau"),
    ("Cartão Único", "cartao unico"), ("Cartão Único", "a do cartao unico"),
    ("Méliuz", "meliuz"), ("Méliuz", "a do meliuz"),
    ("C6-Carbon", "c6 carbon"), ("C6-Carbon", "a do c6 carbon"),
    ("BTG+", "btg"), ("BTG+", "a do btg"),
    # filler DENTRO do nome: um filtro global de filler comeria o "do" do meio.
    ("Banco do Brasil", "banco do brasil"),
    ("Banco do Brasil", "a do banco do brasil"),
    ("Banco do Brasil", "a fatura do banco do brasil"),
    # o nome É uma palavra de filler.
    ("Conta", "a conta"), ("Conta", "minha conta"),
    ("Mercado Pago", "a do mercado pago"),
]


@pytest.mark.parametrize("nome_do_cartao,resposta_do_user", _NOMES_E_RESPOSTAS,
                         ids=[f"{n}-{r}" for n, r in _NOMES_E_RESPOSTAS])
def test_pay_bill_choice_aceita_nome_com_acento_pontuacao_e_filler(
        nome_do_cartao, resposta_do_user):
    """O espelho do `_card_name_da_resposta`: normalizar os DOIS lados."""
    uid = _uid()
    card_id = db.create_card(uid, nome_do_cartao, 10, 17)
    db.add_credit_purchase_installments(
        user_id=uid, card_id=card_id, valor_total=300.0, categoria="outros",
        nota="mercado", purchased_at=date.today(), installments=1)
    bill_ids = [int(b["id"]) for b in db.list_open_bills(uid)]
    db.set_pending_action(uid, "pay_bill_choice", {"bill_ids": bill_ids, "amount": None})

    resposta = _diga(uid, resposta_do_user)

    assert db.list_open_bills(uid) == [], \
        f"{nome_do_cartao!r} + {resposta_do_user!r} não pagou: {resposta!r}"


# Respostas de FORMA que os parsers deste arquivo entendem e que a regex
# ancorada anterior derrubava (14 destas). `(step, resposta, o que aparece)`.
