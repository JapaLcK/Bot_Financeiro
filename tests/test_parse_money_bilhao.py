"""bi/bilhão/trilhão são multiplicadores: o valor real (> TETO_ENTRADA) cai nas recusas existentes.

Antes: "gastei 2 bilhões no mercado" == 2.0 e gravava R$ 2 em silêncio."""
from decimal import Decimal

import re

import pytest

import db
from conftest import usuario_pagante
from core.handlers.credit import PARCELA_DE_VALOR
from parsers import _extract_target_after_amount
from test_bill_amount_pending import _monta_conta_variavel
from test_valor_nao_finito_dinheiro import _compras, _conta, _diga, _usuario_com_cartao
from utils_text import PT_VALOR_COM_MULT, parse_money, parse_pt_number

LANC = "select valor::text v, alvo from launches where user_id=%s"


@pytest.mark.parametrize("texto,esperado", [
    ("gastei 2 bilhões no mercado", 2e9), ("gastei 2 bi no mercado", 2e9), ("gastei 2bi", 2e9),
    ("gastei dois bilhões", 2e9), ("gastei um bilhão no mercado", 1e9), ("gastei 1,5 bilhão", 1.5e9),
    ("gastei 0,5 bi no mercado", 5e8), ("gastei 9 trilhoes", 9e12), ("gastei 1 bilhão e 1", 1000000001),  # (o divisor " e " de itens da conversa é outro assunto)
    ("2 Bilhões", 2e9),
    # F1: "um/dois bi" por extenso é artigo + gíria; não sequestra o valor em dígitos
    ("gastei 30 com um bi", 30), ("gastei 50 no mercado com dois bi", 50), ("gastei dois bi", None),
    # pré-existente na main, só travado: milhão sem número é 1e6 (o corte de bi/tri é > 10**6)
    ("milhão", 1e6), ("gastei milhões no shopping", 1e6),
    # F4: sem ruído de float (0,067 * 1e9 dava 67000000.00000001)
    ("gastei 0,067 bilhão no mercado", 67000000.0),
    # centavos: arredonda em 2 casas (round(,1) zera, round(,3) deixaria 0.0149 / 1000000.0149)
    ("gastei 0,00001 mil", 0.01), ("gastei 1,00000001 milhão", 1000000.01),
    ("gastei 0,0000149 mil", 0.01), ("gastei 1,0000000149 milhão", 1000000.01),
    # bi/bilhão sem número antes não é valor
    ("gastei bilhões no shopping", None), ("bilhão", None), ("paguei o bi 50", 50), ("paguei o bi 10 mil", 10000),
    # controle positivo: nada do que já valia mudou
    ("gastei 10 mil", 10000), ("gastei mil reais", 1000), ("gastei dez mil e quinhentos", 10500),
    ("gastei 1 mil e 500", 1500), ("gastei 2 milhões", 2e6), ("gastei 2,5 mil", 2500),
    ("gastei 1.000 no mercado", 1000), ("gastei 50 no bilhar", 50), ("gastei 30 no bilhete unico", 30),
    ("gastei 50 na trilha", 50), ("gastei 50 bicicleta", 50), ("1 tri", 1),
    # base decimal com ponto: mesma regra do número comum ("1.5" decimal, "1.500" milhar)
    ("gastei 0.5 bilhão", 5e8), ("gastei 1.5 mil", 1500), ("gastei 2.5 milhões", 2500000),
    ("gastei 1.000 mil", 1e6), ("gastei 1.500 milhões", 1.5e9),
])
def test_parse_money(texto, esperado):
    assert parse_money(texto) == esperado


# "dois bi" por extenso não é valor: "bi" curto só vale depois de dígito (F1: "um bi" é artigo).
@pytest.mark.parametrize("texto,esperado", [
    ("um bilhão", 1e9), ("dois bilhões", 2e9), ("dois bi", None), ("bi", None), ("mil", 1000)])
def test_parse_pt_number(texto, esperado):
    assert parse_pt_number(texto) == esperado


@pytest.mark.parametrize("texto,alvo", [
    ("gastei 1 bilhão no mercado", "mercado"), ("gastei 2 bi no mercado", "mercado"),
    ("paguei bi 50", "bi 50"), ("gastei 10 mil reais no mercado", "mercado"),
    # valor por extenso + multiplicador também sai da descrição
    ("gastei um bilhão no mercado", "mercado"), ("gastei dois milhões no mercado", "mercado"),
    ("gastei dez mil no mercado", "mercado"), ("gastei cento e cinquenta mil no mercado", "mercado"),
    ("gastei dez mil e quinhentos no mercado", "mercado"),
    # controles: sem multiplicador (ou "bi" curto por extenso) o número é parte da descrição
    ("gastei dois cafés", "dois cafés"), ("gastei um bi no mercado", "um bi no mercado"),
    ("gastei um bilhete", "um bilhete"),
    # bilhão/trilhão SEM valor antes é palavra da descrição (parse_money também não o lê como valor)
    ("gastei bilhão de coisas 50", "bilhão de coisas 50"), ("gastei trilhão de coisas 50", "trilhão de coisas 50"),
    ("gastei 50 no clube do bilhão", "clube do bilhão"),
])
def test_descricao_nao_carrega_o_multiplicador(texto, alvo):
    assert _extract_target_after_amount(texto) == alvo


def _grava_50(uid, antes):
    assert "Despesa registrada" in _diga(uid, "gastei 50 no mercado")
    assert [float(r["v"]) for r in _conta(LANC, uid)][antes:] == [50.0]


@pytest.mark.parametrize("texto", [
    "gastei 2 bilhões no mercado", "gastei 2 bi no mercado", "gastei dois bilhões no mercado",
    "recebi 2 bilhões de salario", "gastei 9 trilhoes",
])
def test_conversa_acima_do_teto_nao_grava_e_a_conversa_segue(texto):
    uid = usuario_pagante()
    _diga(uid, texto)
    assert _conta(LANC, uid) == [], f"{texto!r} gravou"
    _grava_50(uid, 0)


@pytest.mark.parametrize("texto,valor", [
    ("gastei 1 bilhão no mercado", 1e9), ("gastei um bilhão no mercado", 1e9),
    ("gastei dez mil no mercado", 10000.0),
])
def test_conversa_um_bilhao_e_a_borda_e_registra_com_descricao(texto, valor):
    uid = usuario_pagante()
    assert "Despesa registrada" in _diga(uid, texto)
    [r] = _conta(LANC, uid)
    assert float(r["v"]) == valor and r["alvo"] == "mercado"


def test_conversa_ponto_decimal_na_base_do_bilhao():
    uid = usuario_pagante()
    assert "Despesa registrada" in _diga(uid, "gastei 0.5 bilhão no mercado")
    assert [float(r["v"]) for r in _conta(LANC, uid)] == [5e8]  # não 5 bi (o ponto não é milhar)


def test_conversa_10_mil_grava_10000():
    uid = usuario_pagante()
    assert "Despesa registrada" in _diga(uid, "gastei 10 mil no mercado")
    assert [float(r["v"]) for r in _conta(LANC, uid)] == [10000.0]


@pytest.mark.parametrize("texto", [
    "gastei 2 bilhões no cartão", "parcelei 10x de 2 bilhões no cartão nubank",
    "parcelei 10x de um bilhão no cartão nubank", "parcelei 3x de 100 bi", "parcelei 12x de 100 bi no cartão nubank",
    "parcelei 10x de 2 bi",
])
def test_conversa_cartao_acima_do_teto_nao_grava(texto):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    assert _compras(uid) == [], f"{texto!r} gravou compra"
    _grava_50(uid, 0)


def test_conversa_cartao_2bi_colado_e_recusado():
    uid, _ = _usuario_com_cartao()  # sem espaço, "2bi" é 2 bi (parse_money já lia): não R$ 2 em 10x
    assert "Valor inválido" in _diga(uid, "parcelei 10x de 2bi no cartão nubank")
    assert _compras(uid) == []
    _grava_50(uid, 0)


@pytest.mark.parametrize("texto", [
    "parcelei 3x de 100 bicicleta no cartão nubank", "parcelei 3x de 100bicicleta no cartão nubank",
])
def test_conversa_cartao_bicicleta_nao_e_lida_como_bi(texto):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    assert len(_compras(uid)) == 3  # 3 parcelas de R$ 100, não "100 bi"


def test_conversa_caixinha_e_conta_acima_do_teto_nao_mexem():
    uid = usuario_pagante()
    db.set_balance(uid, 5e9)
    db.create_pocket(uid, "viagem")
    _diga(uid, "guardei 2 bilhões na caixinha viagem")
    assert [float(p["balance"]) for p in db.list_pockets(uid)] == [0.0]

    conta = _monta_conta_variavel(uid)
    _diga(uid, "paguei a luz 2 bilhões")
    assert [b["status"] for b in _conta("select status from bill_instances where user_id=%s", uid)] == ["pending"], conta


def test_parse_money_sem_ruido_de_float():
    for i in range(1, 1000):
        assert parse_money(f"gastei 0,{i:03d} bilhão") == round(i / 1000 * 1e9, 2), i
    assert parse_money("gastei 1,0000000001 bilhão") > 10**9  # continua acima do teto


def test_conversa_067_bilhao_grava_exato():
    uid = usuario_pagante()
    assert "Despesa registrada" in _diga(uid, "gastei 0,067 bilhão no mercado")
    assert [Decimal(r["v"]) for r in _conta(LANC, uid)] == [Decimal("67000000")]


@pytest.mark.parametrize("texto,antes", [("gastei 30 com um bi", 30.0), ("gastei 50 no mercado com dois bi", 50.0)])
def test_conversa_um_bi_por_extenso_nao_sequestra_o_valor(texto, antes):
    uid = usuario_pagante()
    assert "Despesa registrada" in _diga(uid, texto)
    assert [float(r["v"]) for r in _conta(LANC, uid)] == [antes]


def test_conversa_valor_cru_acima_do_teto_sem_descricao_nao_grava():
    uid = usuario_pagante()
    _diga(uid, "gastei 2000000000")
    assert _conta(LANC, uid) == []
    _grava_50(uid, 0)


@pytest.mark.parametrize("texto", ["parcelei 10x de bi", "parcelei 12x de bi no cartão nubank"])
def test_conversa_parcelei_de_bi_sem_numero_nao_engole_a_mensagem_seguinte(texto):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    _grava_50(uid, 0)  # "gastei 50 no mercado" não vira a descrição do parcelamento


# G2: "bi" curto só entra no valor do parcelamento colado em dígito; por extenso é gíria e a main já lia só o número.
@pytest.mark.parametrize("texto", [
    "parcelei 10x de cem bi no cartão nubank", "parcelei 10x de dois bi no cartão nubank", "parcelei 10x de um bi",
])
def test_conversa_cartao_extenso_mais_bi_nao_vira_valor_acima_do_teto(texto):
    uid, _ = _usuario_com_cartao()
    assert "Valor inválido" not in _diga(uid, texto)
    _grava_50(uid, 0)  # a mensagem seguinte registra, não vira o nome do parcelamento


@pytest.mark.parametrize("texto,nota", [
    ("parcelei 3x de 100 pro bi no cartão nubank", "pro bi"), ("parcelei 3x de 100 no bi no cartão nubank", "bi"),
])
def test_conversa_cartao_descricao_preserva_o_bi(texto, nota):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    notas = [r["n"] or "" for r in _conta("select nota n from credit_transactions where user_id=%s", uid)]
    assert len(notas) == 3 and all(nota in n.lower() for n in notas), notas


def _notas(uid):
    return [r["n"] for r in _conta("select nota n from credit_transactions where user_id=%s", uid)]


# O total do parcelamento ("1 bi", a borda do teto) sai da nota; "pro bi" solto fica (controle positivo).
@pytest.mark.parametrize("texto,nota", [
    ("parcelar 1 bi em 2x carro", "carro"), ("parcelar 1bi em 2x carro", "carro"),
    ("parcelar 1 bilhão em 2x carro", "carro"), ("parcelei 600 em 3x pro bi", "pro bi"),
    ("parcelar um bilhão em 2x carro", "carro"), ("parcelar em 3x 1 bilhão carro", "carro"),
    # o multiplicador é do valor, não da descrição: "bilhão"/"trilhão" solto fica na nota (controle positivo)
    ("parcelar 600 em 3x clube do bilhão", "clube do bilhão"), ("parcelar 600 em 3x clube do trilhão", "clube do trilhão"),
    ("parcelar 600 em 3x bilhão de coisas", "bilhão de coisas"),
    ("parcelei 3x de 100 bicicleta", "bicicleta"), ("parcelei 2 milhões em 10x carro", "carro"),
])
def test_conversa_parcelar_total_com_bi_nao_vaza_na_nota(texto, nota):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    assert _notas(uid) and set(_notas(uid)) == {nota}, _notas(uid)


# Compra natural no cartão: o valor sai da nota INTEIRO (dígito + multiplicador, ou extenso + multiplicador).
@pytest.mark.parametrize("texto,nota", [
    ("gastei 1 bilhão no cartão nubank mercado", "mercado"), ("gastei 1 bi no cartão nubank mercado", "mercado"),
    ("gastei 1bi no cartão nubank mercado", "mercado"), ("gastei um bilhão no cartão nubank mercado", "mercado"),
    ("gastei 2 milhões no cartão nubank iate", "iate"), ("gastei dez mil e quinhentos no cartão nubank mercado", "mercado"),
    ("gastei 1.234,56 no cartão nubank mercado", "mercado"),  # antes sobrava "56 mercado" (mesma classe, sem multiplicador)
    # controles: sem multiplicador (ou "bi" curto solto) o texto é descrição
    ("gastei 50 no cartão nubank pro bi", "pro bi"), ("gastei 50 no cartão nubank com um bilhete", "um bilhete"),
    ("gastei dois cafés no cartão nubank 30", "dois cafes"),
])
def test_conversa_cartao_natural_nota_sem_o_multiplicador(texto, nota):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    assert _notas(uid) == [nota], _notas(uid)


# "mil" antes de "milhões" na alternância do PT_VALUE (sem \b) cortava em "mil": 10x de 2 milhões virava 10x de 2.000 e a descrição "hões".
@pytest.mark.parametrize("texto,valor", [
    ("parcelei 10x de dois milhões iate no cartão nubank", "2000000.00"),
    ("parcelei 10x de 2 milhões iate no cartão nubank", "2000000.00"),
    ("parcelei 10x de 200 mil iate no cartão nubank", "200000.00"),  # controle positivo: igual à main
])
def test_conversa_cartao_milhoes_nao_para_em_mil(texto, valor):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    rows = _conta("select valor::text v, nota n from credit_transactions where user_id=%s", uid)
    assert [(r["v"], r["n"]) for r in rows] == [(valor, "iate")] * 10


# O resto depois do " e " leva o PRÓPRIO multiplicador (antes era somado como reais crus:
# "0,8 bilhão e 300 milhões" = 800.000.300 e furava o teto de R$ 1 bi). Controles: o que não muda.
@pytest.mark.parametrize("texto,esperado", [
    ("0,8 bilhão e 300 milhões", 1.1e9), ("1 bilhão e 500 milhões", 1.5e9), ("1 bilhão e 1", 1000000001),
    ("dois bilhões e quinhentos milhões", 2.5e9), ("dois bilhões e 500 mil", 2000500000),
    # resto decimal no ramo dígito (o grupo 3 era só \d+ e somava 0)
    ("1 bilhão e 0,5 bilhão", 1.5e9), ("0,9 bilhão e 0,2 bilhão", 1.1e9), ("1 bi e 0,5 bi", 1.5e9),
    ("1 mil e 2,5", 1002.5), ("1 mil e 500.", 1500),  # o ponto final de frase não é decimal
    # mudança vs main (mesma classe, mil/milhão): era 2000300 / 1000300
    ("2 milhões e 300 mil", 2300000), ("dois milhões e trezentos mil", 2300000), ("1 milhão e 300 mil", 1300000),
    # controles: resto sem multiplicador continua em reais
    ("10 mil e 500", 10500), ("1 mil e 500 reais", 1500), ("1 mil e 2 bicicletas", 1002),
    ("dez mil e quinhentos", 10500), ("dois mil e 500", 2500), ("cento e cinquenta mil e duzentos", 150200),
    # ramo por extenso: o ponto do resto segue a regra do número comum (0.5 = meio, 1.500 = mil e quinhentos)
    ("um milhão e 0.5 milhão", 1.5e6), ("um milhão e 0,5 milhão", 1.5e6), ("dois bilhões e 0.5 bilhão", 2.5e9),
    ("dois mil e 1.500", 3500), ("um milhão e 1.5", 1000001.5), ("dois mil e 2.", 2002),
    # "bi" curto por extenso nunca vale (artigo + gíria), nem como resto; "bilhete" não é multiplicador
    ("dois mil e um bi", 2001), ("dois mil e um bilhete", 2001),
    # limites declarados, fora de escopo: "e meio" e a soma sem "e"
    ("um bilhão e meio", 1e9), ("1 bilhão 500 milhões", 1e9),
    # a vírgula/ponto final da frase não é parte do resto
    ("1 mil e 500, no mercado", 1500), ("dois mil e 500, no mercado", 2500),
])
def test_parse_money_resto_composto(texto, esperado):
    assert parse_money(texto) == esperado


def test_conversa_cartao_composto_acima_do_teto_e_recusado():
    uid, _ = _usuario_com_cartao()
    assert "Valor inválido" in _diga(uid, "credito 0,8 bilhão e 300 milhões carro")  # 1,1 bi, não 800.000.300
    assert _compras(uid) == []
    _grava_50(uid, 0)


# ── Consistência dos recortes do valor ──────────────────────────────────────────────────────────────
# Três leitores do MESMO trecho: `parse_money` (o valor), `PT_VALOR_COM_MULT` (o que sai da NOTA) e
# `PT_VALUE` (o valor do "Nx de Y" no parcelamento). Cada forma tem de ser lida e recortada inteira pelos três.

FORMAS = [  # (trecho do valor, valor lido)
    ("500", 500), ("1.234,56", 1234.56), ("0.5", 0.5),  # 1: dígito sozinho
    ("10 mil", 1e4), ("0,5 bilhão", 5e8), ("1 bi", 1e9), ("1bi", 1e9), ("2 milhões", 2e6), ("1,5 trilhão", 1.5e12),  # 2
    ("dois mil", 2000), ("um bilhão", 1e9), ("cento e cinquenta mil", 150000), ("dois milhões e quinhentos", 2000500),  # 3
    ("0,8 bilhão e 300 milhões", 1.1e9), ("0,5 bi e 100 milhões", 6e8), ("1 bilhão e 0,5 bilhão", 1.5e9),  # 4: composto
    ("1 mil e 500", 1500), ("1 mil e 2,5", 1002.5), ("dois bilhões e quinhentos milhões", 2.5e9),
    ("um milhão e 0.5 milhão", 1.5e6), ("dois mil e 500", 2500), ("1 bi e 0,5 bi", 1.5e9),
    ("1 bilhão e 300 milhões e 5 mil", 1300005000), ("2 bilhões e 0,5 bi", 2.5e9),
    # o numeral do resto é o completo do número comum (milhar E decimal), em qualquer ramo
    ("0,5 bilhão e 1.234,56 mil", 501234560), ("1 bi e 1.234,56 mil", 1001234560), ("1 mil e 1.234,56", 2234.56),
    ("dois mil e 1.234,56", 3234.56), ("um milhão e 1.234,56 mil", 2234560), ("1 mil e 1.5", 1001.5),
    ("1 bilhão e 300 milhões e 1.234,56 mil", 1301234560), ("dois bilhões e 1.234,56 mil", 2001234560),
]


@pytest.mark.parametrize("valor,esperado", FORMAS)
def test_cada_forma_e_lida_e_recortada_inteira_pelos_tres_leitores(valor, esperado):
    assert parse_money(valor) == esperado
    assert re.fullmatch(PT_VALOR_COM_MULT, valor, re.IGNORECASE)  # a nota
    assert re.search(PARCELA_DE_VALOR, f"parcelei 2x de {valor} carro", re.IGNORECASE).group(1) == valor  # o parcelamento
    assert _extract_target_after_amount(f"gastei {valor} mercado") == "mercado"


# Onde o valor ACABA: o que vem depois fica na nota. (valor, o que cada recorte consome)
@pytest.mark.parametrize("texto,nota_consome,parcela_consome", [
    ("1 mil e carro", "1 mil", "1 mil"), ("1 mil e 2 bicicletas", "1 mil e 2", "1 mil e 2"),  # parse_money lê 1002
    ("100 bicicleta", "100", "100"), ("100 bi-cicleta", "100", "100"),
    ("1 bilhão e 300 milhões e 5 mil carro", "1 bilhão e 300 milhões e 5 mil", "1 bilhão e 300 milhões e 5 mil"),
    ("10 mil e dois cafés", "10 mil", "10 mil e dois"),  # base em dígito: o parse_money não lê o "e dois" (o encadeamento do PT_VALUE, da main, o come)
    ("50 e 30", "50", "50 e 30"),  # dígito sozinho não abre composto na nota; o encadeamento do PT_VALUE é anterior
    ("dois cafés", None, "dois"),
    ("paguei bi 50", None, None), ("pro bi", None, None), ("clube do bilhão", None, None), ("um bi no mercado", None, "um"),
])
def test_recortes_param_onde_o_valor_acaba(texto, nota_consome, parcela_consome):
    m = re.match(PT_VALOR_COM_MULT, texto, re.IGNORECASE)
    assert (m.group(0) if m else None) == nota_consome
    p = re.search(PARCELA_DE_VALOR, f"parcelei 2x de {texto}", re.IGNORECASE)
    assert (p.group(1) if p else None) == parcela_consome


@pytest.mark.parametrize("texto,nota_consome", [
    ("1 mil e 500.", "1 mil e 500"), ("1 mil e 500, no mercado", "1 mil e 500"),
    ("dois mil e 500, no mercado", "dois mil e 500"), ("0,5 bi e 1.234,56 mil, no mercado", "0,5 bi e 1.234,56 mil"),
])
def test_nota_nao_engole_a_pontuacao_final_do_resto(texto, nota_consome):
    assert re.match(PT_VALOR_COM_MULT, texto, re.IGNORECASE).group(0) == nota_consome


def test_parse_money_bi_com_hifen_nao_e_bi():
    assert parse_money("gastei 100 bi-cicleta") == 100


# Conversa: os dois recortes, com estado real. Total do parcelamento = parcela x N.
def test_conversa_parcelei_composto_acima_do_teto_e_recusado():
    uid, _ = _usuario_com_cartao()
    # 0,5 bi e 100 milhões = 600 mi por parcela; x2 = R$ 1,2 bi (antes: só "0,5 bi" -> R$ 1 bi, aceito)
    assert "Valor inválido" in _diga(uid, "parcelei 2x de 0,5 bi e 100 milhões carro")
    assert _compras(uid) == []
    _grava_50(uid, 0)


@pytest.mark.parametrize("texto,total,nota", [
    ("parcelei 2x de 0,2 bi e 100 milhões carro", "600000000.00", "carro"),  # 300 mi x 2 (dentro do teto)
    ("parcelei 2x de 1 mil e 500 carro", "3000.00", "carro"),
    ("parcelei 2x de 0,2 bilhão e 100 milhões carro", "600000000.00", "carro"),
])
def test_conversa_parcelei_composto_dentro_do_teto_registra_e_a_nota_fica_limpa(texto, total, nota):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    rows = _conta("select valor::text v, nota n from credit_transactions where user_id=%s", uid)
    assert [r["n"] for r in rows] == [nota] * 2, rows
    assert round(sum(float(r["v"]) for r in rows), 2) == float(total)


@pytest.mark.parametrize("texto,valor,nota", [
    ("gastei 0,5 bilhão e 300 milhões no cartão nubank mercado", 8e8, "mercado"),  # antes: "e 300 milhoes mercado"
    ("gastei 0,5 bi e 100 milhões no cartão nubank mercado", 6e8, "mercado"),
    ("gastei 1 mil e 500 no cartão nubank mercado", 1500, "mercado"),
    ("gastei dois mil e 500 no cartão nubank mercado", 2500, "mercado"),
    ("gastei 0,5 bilhão e 1.234,56 mil no cartão nubank mercado", 501234560, "mercado"),
])
def test_conversa_cartao_natural_composto_nota_sem_o_resto_do_valor(texto, valor, nota):
    uid, _ = _usuario_com_cartao()
    _diga(uid, texto)
    rows = _conta("select valor::text v, nota n from credit_transactions where user_id=%s", uid)
    assert [(float(r["v"]), r["n"]) for r in rows] == [(valor, nota)]


def test_conversa_cartao_natural_composto_acima_do_teto_e_recusado():
    uid, _ = _usuario_com_cartao()
    _diga(uid, "gastei 0,8 bilhão e 300 milhões no cartão nubank mercado")  # 1,1 bi
    assert _compras(uid) == []
    _grava_50(uid, 0)
