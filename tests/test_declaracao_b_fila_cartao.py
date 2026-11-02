"""Rodada 10 (#1310), opção A: a fila de valor ("Faltou o valor de *luz*") respondida com valor + "cartão" não grava.

O predicado é o da rodada 9 (`fp._no_cartao(limpo, debito=True)`: "cartão", "crédito", também com "débito" ou negação).
Com o item entrado por "banco" e a flag ligada, a resposta não declara, não vai para a fatura, não avança a fila (sem CAS)
e repete a pergunta do MESMO item, como o `perigo` de `resolve_multi_launch_value`. A reentrega dá a mesma recusa.
Fecha os achados do Tester r9: B1 ("1,5k no cartão" gravava R$ 1,50 na fatura), B2 (a reentrega consumia o item
seguinte), M1 (duas respostas simultâneas duplicavam o item), M2 (o item sumia sem gravar), M3 (a negação ia à fatura) e
a receita que saía da fila com T8.

Rodada 11: só despesa respondida com crédito (sem "débito") recebe o texto da fatura (`FILA_CARTAO_MANUAL` com cartão
manual, `FILA_CARTAO_OF` sem); débito com cartão e receita recebem `FILA_CARTAO_NEUTRO`. A recusa é a mesma nos três.

Controle negativo: sem o ramo de cartão de `resolve_multi_launch_value`, a fila declara R$ 1.500 (vermelhos abaixo).
Positivos: "1500", "1500 no débito" e "1500 no pix" declaram; *deixa* descarta; depois do descarte a frase inteira vai
para a fatura do cartão manual; a fila de item "dinheiro" segue como a `main` (Carteira).
"""
from __future__ import annotations

import threading

import pytest

import db
from core.handlers import declaracao_banco_textos as tx
from tests._declaracao_b import ana, carteira, declaracoes, fala, liga, manda_wamid, n_lancamentos  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests._of_cash_helpers import q

LUZ = "🐷 Faltou o valor de *luz*. Quanto foi? (só o número)"
CARTAO = ["1500 no cartão", "1500, foi no cartão", "foi 1500 no crédito", "1500 no cartão de crédito", "1500 cartão",
          "R$ 1500 no cartão", "1500 no crédito",  # rodada 9 (+ "1500 no crédito", r11)
          "1,5k no cartão", "2,2k no cartão", "2 reais e 50 no cartão", "mil e quinhentos no cartão",
          "1500,555 no cartão",  # B1
          "1500 no cartão do nubank", "1500 no cartão parcelado em 3x", "cartão 1500", "quinhentos no cartão",  # M2
          "1500 não foi no cartão"]  # M3
DEBITO = ["1500 no cartão de débito", "1500 no débito, não no cartão"]  # rodada 11: texto neutro (MÉDIO 2 do Tester r10)


def _manual(uid):
    db.set_default_card(uid, db.create_card(uid, "Manual", closing_day=10, due_day=17))


def _compras(uid) -> int:
    return q("select count(*) as n from credit_transactions where user_id=%s", (uid,), True)[0]["n"]


def _fila(uid):
    p = db.get_pending_action(uid)
    if not p or p["action_type"] != "multi_launch_values":
        return None
    return [(i["desc"], i["tipo"], i["forma_pagamento"]) for i in p["payload"]["queue"]]


def _entra(uid, monkeypatch, forma="banco", frase="paguei a luz e paguei o aluguel"):
    fala(monkeypatch, uid, frase)
    manda_wamid(uid, forma)
    return _fila(uid)


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("resposta", CARTAO + DEBITO)
def test_fila_com_cartao_recusa_sem_gravar_nem_avancar(ana, liga, monkeypatch, resposta, manual):
    if manual:
        _manual(ana)
    fila = _entra(ana, monkeypatch)
    r = manda_wamid(ana, resposta, "wamid.r10-fila")
    reentrega = manda_wamid(ana, resposta, "wamid.r10-fila")  # B2
    texto = tx.FILA_CARTAO_NEUTRO if resposta in DEBITO else tx.FILA_CARTAO_MANUAL if manual else tx.FILA_CARTAO_OF
    assert r == reentrega == [f"{texto}\n\n{LUZ}"], r
    assert fila == _fila(ana) == [("luz", "despesa", "banco"), ("aluguel", "despesa", "banco")]
    assert declaracoes(ana) == [] and _compras(ana) == 0 and n_lancamentos(ana) == 0


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("resposta", ["1500 no cartão", "1500 no crédito", "caiu 1500 de crédito na conta"])
def test_fila_de_receita_com_cartao_mantem_o_item(ana, liga, monkeypatch, resposta, manual):
    """Rodada 11: receita nunca recebe o texto da fatura (o exemplo do cartão perderia a receita)."""
    if manual:
        _manual(ana)
    fila = _entra(ana, monkeypatch, frase="recebi o salário e paguei o aluguel")
    r = manda_wamid(ana, resposta)
    assert fila == _fila(ana) and fila[0][1] == "receita", (fila, r)
    assert r == [f"{tx.FILA_CARTAO_NEUTRO}\n\n🐷 Faltou o valor de *{fila[0][0]}*. Quanto você recebeu? (só o número)"], r
    assert declaracoes(ana) == [] and _compras(ana) == 0 and n_lancamentos(ana) == 0


@pytest.mark.parametrize("frase, resposta", [("paguei a luz e paguei o aluguel", "1500 no cartão de débito"),
                                             ("paguei a luz e paguei o aluguel", "1500 no débito, não no cartão"),
                                             ("recebi o salário e paguei o aluguel", "1500 no crédito"),
                                             ("recebi o salário e paguei o aluguel", "caiu 1500 de crédito na conta")])
def test_depois_do_neutro_so_o_valor_declara(ana, liga, monkeypatch, frase, resposta):
    """Positivo da rodada 11: o caminho que o texto neutro ensina ("me manda só o valor") declara o item."""
    fila = _entra(ana, monkeypatch, frase=frase)
    assert manda_wamid(ana, resposta)[0].startswith(tx.FILA_CARTAO_NEUTRO)
    manda_wamid(ana, "1500")
    assert [(d["tipo"], float(d["valor"])) for d in declaracoes(ana)] == [(fila[0][1], 1500.0)], fila
    assert _fila(ana) == fila[1:] and _compras(ana) == 0


def test_textos_da_fila_aprovados_pelo_dono():
    """Rodada 11, palavra por palavra (ADR 0006, seção DP-10, décima primeira rodada, item 3)."""
    assert tx.FILA_CARTAO_MANUAL == (
        "🐷 Não registrei e mantive este item na fila. Se foi no cartão, responda *deixa* (isso descarta todos os itens "
        "que faltam) e envie o lançamento completo, por exemplo: *paguei o aluguel 1500 no cartão*. Se não foi, me manda "
        "só o valor.")
    assert tx.FILA_CARTAO_OF == (
        "🐷 Não registrei e mantive este item na fila: compra no cartão de crédito conectado chega sozinha pelo Open "
        "Finance. Se foi Pix, débito ou transferência, me manda só o valor; se foi no cartão, responda *deixa* (isso "
        "descarta todos os itens que faltam).")
    assert tx.FILA_CARTAO_NEUTRO == (
        "🐷 Não registrei e mantive este item na fila: não sei se foi pelo banco ou no cartão. Se foi Pix, débito ou "
        "transferência, me manda só o valor (ex.: *1500*); para desistir, responda *deixa* (isso descarta todos os itens "
        "que faltam).")


@pytest.mark.parametrize("rep", range(8))
def test_fila_respostas_simultaneas_nao_duplicam(ana, liga, monkeypatch, rep):
    """M1: "100 no cartão" e "200" ao mesmo tempo: só o "200" grava, uma vez, no item da frente."""
    _manual(ana)
    _entra(ana, monkeypatch)
    barreira, saida = threading.Barrier(2), {}

    def manda(texto):
        barreira.wait()
        saida[texto] = manda_wamid(ana, texto)

    threads = [threading.Thread(target=manda, args=(t,)) for t in ("100 no cartão", "200")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert [(float(d["valor"]), d["alvo"]) for d in declaracoes(ana)] == [(200.0, "luz")], saida
    assert _fila(ana) == [("aluguel", "despesa", "banco")] and _compras(ana) == 0, saida


@pytest.mark.parametrize("resposta", ["1500", "1500 no débito", "1500 no pix"])
def test_fila_sem_cartao_declara(ana, liga, monkeypatch, resposta):
    _entra(ana, monkeypatch)
    manda_wamid(ana, resposta)
    assert [(float(d["valor"]), d["alvo"]) for d in declaracoes(ana)] == [(1500.0, "luz")] and _compras(ana) == 0
    assert _fila(ana) == [("aluguel", "despesa", "banco")]


def test_deixa_do_texto_descarta_e_a_frase_inteira_vai_para_a_fatura(ana, liga, monkeypatch):
    """A palavra que o texto manda responder (*deixa*) descarta mesmo; a frase do exemplo vai para a fatura."""
    _manual(ana)
    _entra(ana, monkeypatch)
    assert "*deixa*" in manda_wamid(ana, "1500 no cartão")[0]
    assert manda_wamid(ana, "deixa") == ["❌ Beleza, deixei de lado: luz, aluguel."]
    assert _fila(ana) is None
    assert "Compra no Crédito Registrada" in manda_wamid(ana, "paguei o aluguel 1500 no cartão")[0]
    assert _compras(ana) == 1 and declaracoes(ana) == [] and n_lancamentos(ana) == 0


@pytest.mark.parametrize("manual", [False, True])
def test_fila_de_item_em_dinheiro_segue_na_carteira(ana, liga, monkeypatch, manual):
    """Item entrado com "dinheiro": "1500 no cartão" grava na Carteira, como na `main` (o ramo é só do item "banco")."""
    if manual:
        _manual(ana)
    _entra(ana, monkeypatch, forma="dinheiro")
    manda_wamid(ana, "1500 no cartão")
    assert n_lancamentos(ana) == 1 and carteira(ana) == 200.0 - 1500
    assert declaracoes(ana) == [] and _compras(ana) == 0 and _fila(ana) == [("aluguel", "despesa", "dinheiro")]
