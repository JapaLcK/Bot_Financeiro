"""PR-B (ADR 0006), G-B2: com a flag LIGADA o cartão nunca declara (B1/B2 intactos).

"banco" vira declaração só para Pix, débito e transferência. Compra no cartão sincronizado segue sem gravar (T8: só o
cartão "chega sozinho"), cartão manual segue indo para a fatura, o pagamento manual de fatura de cartão do banco segue
recusado (`CartaoDoBanco`) e o parcelado nunca declara.

Controle negativo: `declara` verdadeiro dentro de `_compra_no_credito` (ou o `no_cartao` fora da condição do `resolver`)
deixa vermelhos os de compra no cartão. Positivo: `test_cartao_manual_*` (continua gravando na fatura) e o Pix da
conversa (`test_declaracao_b_conversa.py`).
"""
from __future__ import annotations

import pytest

import db
from core.handlers import declaracao_banco_textos as tx
from tests._declaracao_b import ana, carteira, declaracoes, fala, liga, manda_wamid, n_lancamentos, pendencia  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests._pendencia_credito_helpers import diga
from tests.test_fatura_cartao_do_banco_sem_pagar import banco, pagamentos, pago, recusou
from tests.test_of_fatura_quitada_pelo_banco import rodar  # noqa: F401 (fixture)

T8 = "as compras no cartão de crédito conectado chegam sozinhas pelo Open Finance"


def _compras(uid) -> int:
    from tests._of_cash_helpers import q
    return q("select count(*) as n from credit_transactions where user_id=%s", (uid,), True)[0]["n"]


def test_pagar_fatura_do_cartao_do_banco_segue_recusado(uid_pro, rodar, ia_fora, liga):
    banco(uid_pro, rodar)
    antes = (pago(uid_pro), db.get_balance(uid_pro))
    for frase in ("pagar fatura nubank mastercard", "paguei a fatura do nubank mastercard pelo banco"):
        r = diga(uid_pro, frase)
        assert "registrada pelo banco" not in r, r
    assert recusou(diga(uid_pro, "pagar fatura nubank mastercard"))
    assert (pagamentos(uid_pro), pago(uid_pro), db.get_balance(uid_pro)) == (0, antes[0], antes[1])
    assert declaracoes(uid_pro) == [] and n_lancamentos(uid_pro) == 0


def test_compra_no_cartao_sincronizado_nao_declara_e_diz_t8(uid_pro, rodar, ia_fora, liga):
    banco(uid_pro, rodar)
    compras, lancamentos = _compras(uid_pro), n_lancamentos(uid_pro)
    r = manda_wamid(uid_pro, "gastei 50 no mercado no cartão")[0]
    assert T8 in r and "Pix, cartão e débito" not in r, r
    assert (_compras(uid_pro), n_lancamentos(uid_pro)) == (compras, lancamentos) and declaracoes(uid_pro) == []


def test_resposta_cartao_a_pergunta_nao_declara(ana, ia_fora, liga):
    manda_wamid(ana, "gastei 50 no mercado")
    r = manda_wamid(ana, "cartão")[0]
    assert T8 in r and declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_parcelado_nunca_declara(ana, liga):
    r = manda_wamid(ana, "comprei uma geladeira de 3000 em 10x no cartão")[0]
    assert "registrada pelo banco" not in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0


def test_cartao_manual_segue_indo_para_a_fatura(ana, liga):
    """Positivo do grupo: a flag não tira o cartão manual da fatura."""
    db.set_default_card(ana, db.create_card(ana, "Manual", closing_day=10, due_day=17))
    manda_wamid(ana, "gastei 50 no mercado")
    r = manda_wamid(ana, "cartão")[0]
    assert "Compra no Crédito Registrada" in r, r
    assert _compras(ana) == 1 and declaracoes(ana) == [] and n_lancamentos(ana) == 0
    manda_wamid(ana, "gastei 30 no uber no cartão")
    assert _compras(ana) == 2 and declaracoes(ana) == []


@pytest.mark.parametrize("resposta", ["cartão", "cartão de débito"])  # "cartão de débito": rodada 10, decisão 4
def test_recibo_por_imagem_respondido_com_cartao_nao_declara(ana, liga, resposta):
    """O fluxo `entities` (recibo por imagem, IA no WhatsApp) também não declara cartão: segue `msg_banco` com T8."""
    db.set_pending_action(ana, "confirm_media_launch", {
        "tipo": "despesa", "valor": 50.0, "alvo": "mercado", "categoria": "mercado", "data": None,
        "platform": "whatsapp", "text": "gastei 50,00 mercado"})
    manda_wamid(ana, "sim")
    r = manda_wamid(ana, resposta)[0]
    assert T8 in r and declaracoes(ana) == [] and n_lancamentos(ana) == 0, r


# B1 (#1310): o D8 também no áudio de dois gastos respondido com "cartão" (`rotear_partes`), não só no `add`.
DOIS = "gastei 50 no mercado e 30 na padaria"


def test_audio_respondido_com_cartao_diz_t8(ana, liga, monkeypatch):
    fala(monkeypatch, ana, DOIS)
    r = manda_wamid(ana, "cartão")[0]
    assert T8 in r and tx.UM_POR_MENSAGEM not in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_audio_respondido_com_cartao_manual_vai_para_a_fatura(ana, liga, monkeypatch):
    db.set_default_card(ana, db.create_card(ana, "Manual", closing_day=10, due_day=17))
    fala(monkeypatch, ana, DOIS)
    r = manda_wamid(ana, "cartão")[0]
    assert _compras(ana) == 2 and declaracoes(ana) == [] and n_lancamentos(ana) == 0, r


def test_audio_respondido_com_banco_segue_t16(ana, liga, monkeypatch):
    """Positivo do B1: a recusa de dois gastos pelo banco continua."""
    fala(monkeypatch, ana, DOIS)
    assert tx.UM_POR_MENSAGEM in manda_wamid(ana, "banco")[0]
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0


# R2 da rodada 8 (#1310): o pedaço SEM valor do áudio respondido com "cartão" também segue o cartão (o mesmo `add` de
# "paguei o aluguel no cartão": T8, ou "não achei o valor" com cartão manual); nada entra na fila nem declara depois.
# Controle negativo: sem o ramo `cartao` do `rotear_partes` (ou sem marcar o pedaço sem valor no `resolver`), o aluguel
# vai para a fila com a forma BANCO e o "1500" o declara. Positivo: `test_audio_sem_valor_respondido_com_banco_declara`.
ALUGUEL = "gastei 50 no mercado e paguei o aluguel"
NAO_ACHEI = "Não achei o valor da compra no crédito"


def _sem_declaracao_depois(uid, *respostas):
    """Os valores mandados depois (duas vezes o mesmo wamid: a reentrega) não declaram nem gravam, e não havia fila."""
    fila = pendencia(uid)
    for valor in respostas:
        manda_wamid(uid, valor, "wamid.r8-" + valor)
        manda_wamid(uid, valor, "wamid.r8-" + valor)
    assert declaracoes(uid) == [] and n_lancamentos(uid) == 0
    assert fila != "multi_launch_values"


def test_audio_sem_valor_respondido_com_cartao_nao_declara(ana, liga, monkeypatch):
    fala(monkeypatch, ana, ALUGUEL)  # "2 reais e 50" e "1,5k" + aluguel: recusa do valor, na rodada 9 (abaixo)
    r = manda_wamid(ana, "cartão", "wamid.r8-cartao")[0]
    manda_wamid(ana, "cartão", "wamid.r8-cartao")  # a resposta reentregue também não arma fila
    _sem_declaracao_depois(ana, "1500")
    assert r.count(T8) >= 2 and "Quanto foi" not in r, r  # o aluguel recebe T8 como o mercado, não a pergunta da fila


def test_audio_sem_valor_respondido_com_cartao_manual(ana, liga, monkeypatch):
    db.set_default_card(ana, db.create_card(ana, "Manual", closing_day=10, due_day=17))
    fala(monkeypatch, ana, ALUGUEL)
    r = manda_wamid(ana, "cartão")[0]
    _sem_declaracao_depois(ana, "1500")
    assert NAO_ACHEI in r and _compras(ana) == 1, r  # o mercado vai para a fatura; o aluguel, como o texto sem valor


def test_audio_dois_sem_valor_respondido_com_cartao(ana, liga, monkeypatch):
    fala(monkeypatch, ana, "paguei a luz e paguei o aluguel")
    r = manda_wamid(ana, "cartão")[0]
    _sem_declaracao_depois(ana, "100", "1500")
    assert r.count(T8) == 2, r


def test_texto_sem_valor_respondido_com_cartao(ana, liga):
    manda_wamid(ana, ALUGUEL)
    r = manda_wamid(ana, "cartão")[0]
    _sem_declaracao_depois(ana, "1500")
    assert r.count(T8) == 2, r


def test_audio_sem_valor_respondido_com_banco_declara(ana, liga, monkeypatch):
    """Positivo da R2: com "banco", o gasto declara e o aluguel vai para a fila e declara com o valor."""
    fala(monkeypatch, ana, ALUGUEL)
    assert "Quanto foi" in manda_wamid(ana, "banco")[0]
    manda_wamid(ana, "1500")
    assert [float(d["valor"]) for d in declaracoes(ana)] == [50.0, 1500.0]


def test_audio_dois_sem_valor_respondido_com_banco_declara(ana, liga, monkeypatch):
    """Positivo do pedaço sem valor marcado no `resolver`: com "banco" (nada marcado) a fila declara um por um."""
    fala(monkeypatch, ana, "paguei a luz e paguei o aluguel")
    manda_wamid(ana, "banco")
    manda_wamid(ana, "100")
    manda_wamid(ana, "1500")
    assert [(float(d["valor"]), d["alvo"]) for d in declaracoes(ana)] == [(100.0, "luz"), (1500.0, "aluguel")]


# A fila respondida com valor + "cartão" (rodada 9, refeita na rodada 10): `test_declaracao_b_fila_cartao.py`.
def _manual(uid):
    db.set_default_card(uid, db.create_card(uid, "Manual", closing_day=10, due_day=17))


# Item 2 (opção A): a resposta "cartão" vence o "débito" dito no pedaço, que recebe T8 sem declarar nem ir para a fatura.
# Controle negativo: sem o T8 do `resolver` (texto) e do `rotear_partes` (áudio), o pedaço declara R$ 30 (ou, com cartão
# manual e negação, grava na fatura). Positivo: o "cartão de débito" digitado direto, sem pergunta, declara como antes.
DEBITO = [("a", "gastei 30 no débito e paguei o aluguel, não foi pix"),
          ("a", "paguei o aluguel e gastei 30 no débito sem nota"),
          ("a", "gastei 30 no mercado no cartão de débito e paguei o aluguel sem desconto"),
          ("t", "gastei 30 no débito no mercado sem nota"),
          ("t", "paguei o aluguel e gastei 30 no débito sem nota")]


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("resposta", ["cartão", "crédito", "cartão de crédito"])
@pytest.mark.parametrize("tipo,frase", DEBITO)
def test_debito_respondido_com_cartao_recebe_t8(ana, liga, monkeypatch, tipo, frase, resposta, manual):
    if manual:
        _manual(ana)
    fala(monkeypatch, ana, frase) if tipo == "a" else manda_wamid(ana, frase)
    r = manda_wamid(ana, resposta)[0]
    for depois in ("mercado", "1500"):
        manda_wamid(ana, depois)
    assert T8 in r and declaracoes(ana) == [] and _compras(ana) == 0 and n_lancamentos(ana) == 0, r


# Rodada 10, decisão 4: a RESPOSTA "cartão de débito" (e o que o `e_resposta` lê como débito com "cartão") à pergunta de
# forma também dá T8, sem declarar nem ir para a fatura: o cartão vence o débito. Controle negativo: sem o `so_t8` do
# `resolver`, declara R$ 50 (texto) ou R$ 50 + o aluguel da fila (áudio). Positivo: "débito"/"pix"/"banco" declaram.
CARTAO_DE_DEBITO = ["cartão de débito", "no cartão de débito", "foi no cartão de débito", "débito no cartão"]
PORTAS_FORMA = {"texto": lambda u, m: manda_wamid(u, "gastei 50 no mercado"),
                "audio": lambda u, m: fala(m, u, DOIS),
                "audio_sem_valor": lambda u, m: fala(m, u, ALUGUEL)}


@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("porta", list(PORTAS_FORMA))
@pytest.mark.parametrize("resposta", CARTAO_DE_DEBITO)
def test_cartao_de_debito_respondido_a_pergunta_de_forma_da_t8(ana, liga, monkeypatch, resposta, porta, manual):
    if manual:
        _manual(ana)
    PORTAS_FORMA[porta](ana, monkeypatch)
    r = manda_wamid(ana, resposta)[0]
    _sem_declaracao_depois(ana, "1500")
    assert r.count(T8) == (1 if porta == "texto" else 2) and _compras(ana) == 0, r


@pytest.mark.parametrize("resposta", ["débito", "no débito", "pix", "banco"])
def test_resposta_sem_cartao_a_pergunta_de_forma_segue_declarando(ana, liga, resposta):
    manda_wamid(ana, "gastei 50 no mercado")
    assert "registrada pelo banco" in manda_wamid(ana, resposta)[0]
    assert [float(d["valor"]) for d in declaracoes(ana)] == [50.0] and _compras(ana) == 0


@pytest.mark.parametrize("manual", [False, True])
def test_cartao_de_debito_digitado_direto_segue_declarando(ana, liga, manual):
    if manual:
        _manual(ana)
    assert "registrada pelo banco" in manda_wamid(ana, "gastei 30 no cartão de débito pelo banco")[0]
    assert [float(d["valor"]) for d in declaracoes(ana)] == [30.0] and _compras(ana) == 0


LONGO = "gastei 1,5k no mercado e 30 na padaria" + " e aí eu fui andando pela rua pensando na vida" * 25


# Item 3: com cartão o B1 pula o T16, mas não a recusa do VALOR (T10/T11; o T15 na rodada 10): sem ela a fatura lia
# "1,5k" como R$ 1,50 e "2 reais e 50" como R$ 2 + R$ 50 (a main recusava). Controle negativo: com cartão pular toda a recusa (`if not cartao`)
# deixa vermelho o caso com cartão manual. Positivo: os `test_audio_respondido_com_cartao_*` (T8 / fatura do B1).
@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("frase,recusa", [("gastei 1,5k no mercado e paguei o aluguel", "você mandou 1,5k"),
                                          ("gastei 2 reais e 50 no mercado e paguei o aluguel", "*2 reais e 50*"),
                                          (LONGO, "a mensagem ficou longa demais")])  # M4, rodada 10
def test_valor_ambiguo_respondido_com_cartao_recusa(ana, liga, monkeypatch, frase, recusa, manual):
    if manual:
        _manual(ana)
    fala(monkeypatch, ana, frase)
    r = manda_wamid(ana, "cartão")[0]
    assert recusa in r and _compras(ana) == 0, r
    _sem_declaracao_depois(ana, "1500")
