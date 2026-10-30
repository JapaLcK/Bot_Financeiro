"""Leitor ESTRITO do valor da declaração (issue #1310, DP-10 do ADR 0006; `core/handlers/declaracao_valor.py`).

A declaração "pelo banco" grava UM gasto por mensagem (E1), e só quando o leitor acha UM número solto (E3: data, hora,
medida e número colado a letras não contam), exato (E2), e o parser da Carteira lê o mesmo número. Tudo pelo `handle_incoming`, Postgres real, texto e áudio (o Whisper é trocado por
`fala`).

G1 recusa sem gravar · G2 a saída que o texto ensina grava (anti-loop) · G3 positivos · G4 fila de valores ·
G5 pendência de descrição/valor · G6 valor do classificador (fallback de `add`) · G7 fora da declaração nada muda ·
G8 tempo · corpus de frases legítimas (`tests/fixtures/declaracao_frases_legitimas.json`, escrito por quem desenvolve,
não é de usuário). A tabela regra → teste vermelho do controle negativo está no relato do PR.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from core.handlers import declaracao_banco_textos as tx
from tests._declaracao_b import (ana, carteira, declaracoes, fala, liga, manda_wamid, n_lancamentos,  # noqa: F401
                                 pendencia, q, sem_pergunta)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401

T10, T11, T12 = "não tenho certeza do valor", "não sei se *", tx.VALOR_INCERTO
T14, T15, T16, T17 = tx.PARCELADO, tx.LONGA, tx.UM_POR_MENSAGEM, tx.APROXIMADO
MODOS = pytest.mark.parametrize("modo", ["texto", "audio"])


def chaves(uid) -> int:
    return q("select count(*) n from launches where user_id=%s and external_id like 'wa:%%'", (uid,))["n"]


def diz(uid, modo, monkeypatch, texto) -> str:
    return "\n".join(manda_wamid(uid, texto) if modo == "texto" else fala(monkeypatch, uid, texto))


def valores(uid) -> list[float]:
    return sorted(float(d["valor"]) for d in declaracoes(uid))


def nada_gravou(uid) -> bool:
    return declaracoes(uid) == [] and n_lancamentos(uid) == 0 and carteira(uid) == 200.0 and chaves(uid) == 0


# --- G1: recusa sem gravar, sem consumir a chave, sem deixar pergunta ---
RECUSAS = [
    ("gastei dia 5 50 no mercado", T12),  # o parser lê 550
    ("comprei 2 camisetas por 80", T12), ("comprei 3 pizzas por 90", T12), ("gastei 1 pizza por 45", T12),
    ("gastei 2kg de carne por 40", T12), ("paguei a parcela 3 de 10 do celular 250", T12),
    ("comprei 2 camisetas por R$ 80", T12), ("comprei duas camisetas por R$ 80 na renner", T12),
    ("gastei R$ 80 em 2 camisetas", T12), ("gastei R$ 80 no mercado ou 30", T12), ("gastei R$ 40 cerveja 2", T12),
    # E3: qualquer número solto além do valor, mesmo com R$ ("99 pop", "forever 21", correção, segundo gasto)
    ("gastei 15 no 99", T12), ("gastei R$ 15 no 99", T12), ("gastei 15 reais no 99", T12), ("recebi R$ 200 da 99", T12),
    ("gastei 80 no 99 pop", T12), ("gastei R$ 80 no 99 pop", T12), ("gastei R$ 35 na forever 21", T12),
    ("gastei R$ 30 com 2 amigos", T12), ("gastei 30 com 2 amigos", T12), ("gastei 50 pra 2", T12),
    ("gastei 2 vírgula 50 no mercado", T12), ("gastei R$ 2 vírgula 50 no mercado", T12),
    ("gastei 20 a 30 no mercado", T12), ("gastei 3 mil 200 no mercado", T12),
    ("gastei 50x2 no mercado", T12), ("gastei 30 min de uber", T12), ("gastei 10 km de uber", T12),
    ("gastei 50 no mercado e outros 30 na feira", T12), ("paguei aluguel 1500 e luz 200", T12),
    ("gastei dois vírgula cinquenta no mercado", T12), ("gastei dez com cinco amigos", T12),
    ("gastei vinte no mercado e trinta na padaria", T12), ("gastei vinte reais e uns cinquenta no mercado", T12),
    ("gastei trinta no mercado dia 5", T12), ("gastei cinquenta no mercado 24h", T12),
    ("gastei cem reais em um tênis", T12), ("gastei um real no mercado", T12),
    ("gastei R$ 50 no mercado foi 60", T12), ("gastei R$ 50 no mercado pera 60", T12),
    ("gastei R$ 50 no mercado desculpa 60", T12), ("gastei R$ 50 no mercado na vdd 60", T12),
    ("gastei R$ 50 no mercado opa 60", T12), ("gastei 50 reais no mercado era 60", T12),
    ("gastei R$ 50 no mercado depois 30 na padaria", T12), ("gastei R$ 50 no mercado; 30 na padaria", T12),
    ("gastei R$ 50 no mercado. 30 na padaria", T12), ("gastei R$ 50 no mercado tb 30 na padaria", T12),
    ("gastei R$ 50 no mercado | 30 na padaria", T12),
    ("gastei R$ 50 no mercado & 30 na padaria", T12), ("gastei R$ 50 no mercado aí 30 na padaria", T12),
    ("gastei R$ 50 no mercado\n30 na padaria", T12), ("gastei R$ 50 no mercado R$ 30 na padaria", T12),
    ("gastei R$ 50 no mercado e outros R$ 30 na feira", T12),
    # B1: o valor colado na moeda é o valor, a quantidade antes dele não
    ("comprei 2 coxinhas 10reais", T12), ("gastei 2 cafés R10", T12), ("comprei 2 pastel 7,50reais", T12),
    ("paguei 3 meses de academia 300reais", T12), ("gastei 2 lanche 30tão", T12), ("recebi 2 pix 300reais", T12),
    ("gastei 2 cafés 10r", T12),
    # rodada 4 (D): "$", "US$", "reais" errado, preço por unidade; "30no" = `_LIGA_COLADA` (extensão; r8: ficou, ADR)
    ("comprei 2 coxinhas 10$", T12), ("comprei 2 coxinhas $10", T12), ("comprei 2 coxinhas US$10", T12),
    ("comprei 2 coxinhas 10reias", T12), ("comprei 2 coxinhas 10reaiss", T12), ("comprei 2 coxinhas 10rais", T12),
    ("comprei 2 pastéis 7,50cada", T12), ("gastei 2 lanches 30no ifood", T12), ("gastei R$ 30 cada no bar", T12),
    ("gastei R$ 50 no mercado depois 30$ na padaria", T12),
    # B5: conta
    ("gastei R$ 30 no bar × 3", T12), ("gastei R$ 30 no bar * 3", T12), ("gastei R$ 30 no bar x3", T12),
    ("gastei R$ 50 no mercado - 10 de desconto", T12), ("gastei R$ 50 no mercado menos 10 de desconto", T12),
    ("gastei R$ 50 no mercado tirando 10", T12), ("gastei 2 vezes 50 no mercado", T12),
    ("gastei R$ 40 no ps5 x 2 controles", T12), ("gastei R$ 600 na tv 3 vezes", T12),
    # B2/B3: separador sem parte inteira, milhar fora do lugar, vírgula de milhar
    ("gastei ,50 no chiclete", T10), ("gastei R$,50 no chiclete", T10), ("gastei .50 no chiclete", T10),
    ("gastei 1.23.456 no mercado", T10), ("gastei 1.2.345 no mercado", T10), ("gastei 1,000 no mercado", T10),
    ("gastei R$ 2,000 no notebook", T10), ("paguei 1,200 de aluguel", T10),
    ("gastei 2 reais com 50 no mercado", T11), ("gastei 100 no mercado dia 5 e 6 de outubro de 2026", T12),
    ("gastei dia 5 e 30 na padaria", T12), ("gastei 100 no mercado dia 20 e 21 de outubro", T12),
    # D5: parcelado (compra dividida)
    ("paguei R$ 600 em 3x", T14), ("paguei cem em três vezes", T14), ("comprei a tv parcelado 600", T14),
    ("paguei 2 parcelas de 150", T14), ("paguei 12x de 99", T14), ("paguei 3x de 200", T14),
    ("comprei a tv de R$ 600 parcelando", T14), ("comprei a tv a prazo por 600", T14),
    # rodada 4 (A): "3x" junto do valor, crediário, "dividido em"
    ("paguei R$ 600 na tv 3x", T14), ("paguei 600 na tv 3x", T14), ("comprei uma tv de 600 3x", T14),
    ("comprei tv R$ 600 10x", T14), ("paguei R$ 600 na tv dividido 3x", T14),
    ("comprei o celular R$ 1.200 no crediário", T14), ("paguei R$ 600 dividido em 3", T14),
    ("gastei R$ 30 no bar 3x", T14), ("gastei R$ 20 no uber 2x", T14), ("gastei R$ 50 na loja 3x", T14),
    ("paguei R$ 200 da parcela 1/3 da tv", T14),  # M4: sem isto o "1/3" vira a data 01/03, travada
    ("gastei 50 no mercado " + "muito bom " * 100, T15),
    # E1: um gasto por mensagem
    ("gastei 20 no mercado e 30 na padaria", T16), ("paguei luz 120 e paguei internet 100", T16),
    ("gastei 50 no mercado dia 5 e 30 na padaria", T16), ("gastei 80 em 3 pizzas e 2 refris", T16),
    ("gastei 50 no mercado 10h e 30 min", T12), ("gastei 50 no mercado e 20 valeu", T16), ("gastei 50 uber, 30 café", T16),
    ("gastei 100 no mercado e 20 de desconto", T16), ("gastei 30 na padaria e 2 de troco", T16),
    ("gastei 100 no mercado e ganhei 5 de cashback", T16), ("gastei 100 no mercado dia 5 e 6 do mês passado", T16),
    # E2: valor aproximado (o marcador ligado ao número do valor)
    ("gastei mais de 100 no mercado", T17), ("paguei acima de 100 no mercado", T17), ("gastei menos de 50 no bar", T17),
    ("gastei quase 50 no mercado", T17), ("gastei até 50 no mercado", T17), ("gastei uns 30 no bar", T17),
    ("gastei umas 40 pila no bar", T17), ("gastei cerca de 50 no mercado", T17), ("gastei por volta de 50 no uber", T17),
    ("gastei aproximadamente 50 no mercado", T17), ("gastei 50 aproximadamente no mercado", T17),
    ("gastei mais ou menos 50 no mercado", T17), ("gastei 50 mais ou menos no mercado", T17),
    ("gastei 50 e pouco no mercado", T17), ("gastei cem e poucos no mercado", T17), ("gastei 100 e tanto no mercado", T17),
    ("gastei 50 e tantos no mercado", T17), ("gastei 50 e poucas no mercado", T17),
    ("gastei 50 conto e pouco no mercado", T17),
    ("gastei 50 pila e pouco no mercado", T17), ("gastei R$ 50 e pouco no mercado", T17),
    ("gastei no máximo 50 no mercado", T17), ("gastei no mínimo 50 no mercado", T17), ("gastei pelo menos 50 no mercado", T17),
    ("gastei abaixo de 50 no mercado", T17), ("gastei acima dos 50 no mercado", T17), ("gastei uns R$ 50 no mercado", T17),
    ("gastei lá pelos 50 no mercado", T17), ("gastei na faixa de 50 no mercado", T17), ("gastei em torno de 50 no mercado", T17),
    ("gastei perto de 50 no mercado", T17), ("gastei beirando 50 no mercado", T17), ("gastei aprox 50 no mercado", T17),
    ("gastei aprox. 50 no mercado", T17), ("gastei +- 50 no mercado", T17), ("gastei +/- 50 no mercado", T17),
    ("gastei ~50 no mercado", T17), ("gastei ≈50 no mercado", T17), ("gastei 50 por aí no mercado", T17),
    ("gastei tipo 50 no mercado", T17), ("gastei uns cinquenta no mercado", T17), ("gastei quase uns 50 no mercado", T17),
    ("gastei coisa de 50 no mercado", T17), ("gastei algo como 50 no mercado", T17), ("gastei uns 50 conto no mercado", T17),
    ("gastei cinquenta e poucos no mercado", T17), ("gastei R$ 50, mais ou menos, no mercado", T17),
    ("gastei 50 reais aproximadamente no mercado", T17), ("gastei aproximadamente R$ 50 no mercado", T17),
    # rodada 4 (B): o marcador em qualquer posição
    ("gastei 50 no mercado mais ou menos", T17), ("gastei 50 no mercado, mais ou menos", T17),
    ("gastei 200 de luz aproximadamente", T17), ("paguei 80 no bar, por aí", T17), ("gastei 50 no mercado aprox", T17),
    ("gastei 50 no mercado e pouco", T17), ("gastei 50 e poucos reais no mercado", T17), ("gastei R$ 50 aprox. no mercado", T17),
    # rodada 5 (B4a): "e pouco(s)"/"e tanto(s)" logo depois do valor recusam mesmo com outro número depois
    ("gastei 50 e pouco 22h no bar", T17), ("gastei 50 e poucos 2kg de carne", T17), ("gastei R$ 50 e pouco 10 litros", T17),
    ("gastei 50 e pouco 99pop", T17), ("gastei 50 e pouco 10% de desconto", T17), ("gastei 50 e tanto 10 litros", T17),
    ("gastei R$ 50 no mercado e pouco depois fui embora", T17),  # falso positivo aceito (limite no ADR)
    # rodada 5 (A4a): "Nvezes"/"Nvzs" colado é "Nx"; rodada 6: qualquer "cada" no pedaço do valor (como na r4)
    ("paguei R$ 600 na tv 3vezes", T14), ("paguei R$ 600 na tv 3vzs", T14),
    ("comprei 2 coxinhas R$ 5 cada", T12), ("gastei 7,50cada no pastel", T12), ("gastei 30 reais cada no bar", T12),
    ("gastei R$ 5 a cada coxinha", T12), ("gastei R$ 5, cada coxinha", T12),
    ("paguei R$ 120 de internet que pago cada mês", T12), ("paguei R$ 120 de internet que vence cada dia 10", T12),
    ("gastei 50 no bar e cada um pagou o seu", T12),  # falsos positivos aceitos (limite no ADR)
    ("gastei 20 no mercado e 7,50cada no pastel", T16),  # `_LIGA_COLADA` (extensão, não a D): só o TEXTO, T16 × T12
    ("gastei 50 conto e pouco 22h no bar", T17),
    # rodada 6: vírgula entre o marcador e o número
    ("gastei uns, cinquenta reais no mercado", T17), ("gastei uns,R$50 no mercado", T17),
]


@MODOS
@pytest.mark.parametrize("texto,esperado", RECUSAS)
def test_g1_recusa_sem_gravar(ana, liga, monkeypatch, modo, texto, esperado):
    r = diz(ana, modo, monkeypatch, texto + " pelo banco")
    assert esperado in r, r
    assert nada_gravou(ana) and sem_pergunta(ana)


@pytest.mark.parametrize("texto,esperado", [
    # Pela conversa estas não chegam ao leitor: "não" e "sem" fazem o `fp.detectar` não ver a forma (o Piggy pergunta
    # "dinheiro ou banco?") e "vou parcelar" não é lançamento. A resposta "banco" à pergunta passa pelo leitor.
    ("gastei R$ 50 no mercado não 60 pelo banco", T12), ("paguei R$ 600 na tv em 10x sem juros pelo banco", T14),
    ("gastei R$ 50 no mercado sem contar 30 na padaria pelo banco", T12),
    ("vou parcelar a tv de R$ 600 pelo banco", T14),
])
def test_g1_leitor_recusa_o_que_a_conversa_nao_entrega(texto, esperado):
    from core.handlers import declaracao_valor
    assert declaracao_valor.recusa(texto) == esperado


@pytest.mark.parametrize("texto", [
    "fui 2x no mercado e gastei R$ 100 pelo banco",  # "2x" é medida: um número solto (o classificador manda à IA)
    "gastei 10 reais e paguei o aluguel pelo banco",  # um número solto: declara 10 e a fila pergunta o aluguel
])
def test_g3_leitor_nao_recusa_um_numero_solto(texto):
    from core.handlers import declaracao_valor
    assert declaracao_valor.recusa(texto) is None


def test_g1_cartao_vem_antes_da_recusa_de_valor(ana, liga):
    """D8: com cartão de crédito na mensagem o usuário vê o T8, não o T12 (cartão nunca declara)."""
    r = manda_wamid(ana, "comprei 2 camisetas por 80 no cartão e 30 no débito")[0]
    assert "as compras no cartão de crédito conectado chegam sozinhas" in r and T12 not in r, r
    assert nada_gravou(ana)


# --- G2: a reformulação que o texto ensina grava o valor certo (sem isto a recusa vira loop) ---
SAIDAS = [  # (recusada, a saída que o texto recebido ensina, valor): nenhuma recusa vira loop
    ("gastei 15 no 99", "gastei R$ 15 no 99pop", 15.0), ("gastei R$ 15 no 99", "gastei R$ 15 no 99pop", 15.0),
    ("recebi R$ 200 da 99", "recebi R$ 200 da 99pop", 200.0), ("gastei R$ 80 no 99 pop", "gastei R$ 80 no 99pop", 80.0),
    ("gastei R$ 35 na forever 21", "gastei R$ 35 na forever21", 35.0), ("gastei 120 na ps 5", "gastei R$ 120 na ps5", 120.0),
    ("comprei 2 camisetas por 80", "gastei R$ 80 em camisetas", 80.0), ("gastei R$ 80 em 2 camisetas", "gastei R$ 80 na Renner", 80.0),
    ("gastei 30 com 2 amigos", "gastei R$ 30 com os amigos", 30.0), ("gastei 50 pra 2", "gastei R$ 50 no jantar", 50.0),
    ("gastei 50 no mercado com 3 itens", "gastei R$ 50 no mercado", 50.0),
    ("paguei IPVA 2026 de 1500", "paguei R$ 1500 de IPVA", 1500.0),
    ("paguei a parcela 3 de 10 do celular 250", "paguei R$ 250 da parcela do celular", 250.0),
    ("comprei 2 coxinhas 10reais", "gastei R$ 10 em coxinhas", 10.0),
    ("gastei R$ 50 no mercado opa 60", "gastei R$ 60 no mercado", 60.0),
    ("gastei R$ 50 no mercado depois 30 na padaria", "gastei R$ 50 no mercado", 50.0),
    ("dia 5 gastei 50 no mercado", "gastei R$ 50 no mercado dia 5", 50.0),
    # M3: o parser não lê e o classificador sem IA não traz valor: "Não consegui identificar o valor. Tente: *gastei 50…*"
    ("torrei 80 conto no bar", "gastei 80 no bar", 80.0), ("dia 20 paguei 300", "paguei 300 dia 20", 300.0),
    ("gastei R$ 30 na foto 3 x 4", "gastei R$ 30 na foto 3x4", 30.0),
    ("gastei R$ 50 na academia 2 vezes por semana", "gastei R$ 50 na academia", 50.0),
    ("gastei 2 reais com 50 no mercado", "gastei 2,50 no mercado", 2.5),
    ("paguei R$ 600 em 3x", "paguei R$ 200 da parcela da geladeira", 200.0),
    ("paguei R$ 200 da parcela 1/3 da tv", "paguei R$ 200 da parcela da tv", 200.0),
    ("gastei 20 no mercado e 30 na padaria", "gastei 20 no mercado", 20.0),
    ("gastei uns 30 no bar", "gastei 30 no bar", 30.0), ("gastei ,50 no chiclete", "gastei 0,50 no chiclete", 0.5),
    ("comprei umas 3 cervejas por R$ 30", "gastei R$ 30 em cervejas", 30.0),
]


@MODOS
@pytest.mark.parametrize("recusada,saida,valor", SAIDAS)
def test_g2_a_saida_ensinada_grava(ana, liga, monkeypatch, modo, recusada, saida, valor):
    diz(ana, modo, monkeypatch, recusada + " pelo banco")  # recusa, ou "Não consegui identificar o valor" (fallback)
    assert declaracoes(ana) == []
    diz(ana, modo, monkeypatch, saida + " pelo banco")
    assert valores(ana) == [valor] and chaves(ana) == 1


@pytest.mark.parametrize("texto", [tx.VALOR_CASAS, tx.VALOR_ABREVIADO, tx.VALOR_AMBIGUO, T12, T14, T15, T16, T17])
def test_g2_o_exemplo_de_cada_texto_grava(ana, liga, texto):
    exemplo = re.findall(r"\*([^*]+)\*", texto)[-1]
    assert "registrada pelo banco" in manda_wamid(ana, exemplo)[0], exemplo
    assert len(declaracoes(ana)) == 1


# --- G3: o que é um valor só, em qualquer forma, continua gravando ---
POSITIVOS = [
    ("gastei 2200 no mercado", [2200.0]), ("gastei 12,90 no mercado", [12.9]), ("gastei 1.234,56 no mercado", [1234.56]),
    ("gastei 1,5 mil no mercado", [1500.0]), ("gastei 50 no mercado 24h", [50.0]), ("gastei 30 em arroz 5 kg", [30.0]),
    ("gastei 25 na 25 de março", [25.0]), ("gastei 50 no mercado dia 5", [50.0]), ("gastei 50 no mercado 05/10", [50.0]),
    ("gastei R$ 80 na Renner", [80.0]), ("gastei R$ 80 no 99pop", [80.0]), ("gastei R$ 35 na forever21", [35.0]),
    ("gastei R$ 120 na ps5", [120.0]), ("gastei R$ 30 na foto 3x4", [30.0]), ("gastei R$ 50 nas fotos 3x4", [50.0]),
    ("gastei vinte e cinco no mercado", [25.0]), ("gastei vinte e um no mercado", [21.0]),
    ("gastei dois mil e quinhentos no mercado", [2500.0]), ("gastei mais 30 que o combinado no uber", [30.0]),
    ("gastei 49,90 no café às 15:30", [49.9]), ("gastei 10reais no pastel", [10.0]), ("gastei R10 no pastel", [10.0]),
    ("gastei 30tão no lanche", [30.0]), ("gastei 50 BRL no mercado", [50.0]), ("gastei 50 conto no mercado", [50.0]),
    ("gastei R$ 2.50 no mercado", [2.5]), ("gastei R$ 1,200.00 no aluguel", [1200.0]), ("gastei 0,50 no chiclete", [0.5]),
    ("gastei R$ 1.000.000 na casa", [1000000.0]), ("gastei R$ 120.000,00 no carro", [120000.0]),
    ("paguei R$ 200 da parcela da geladeira", [200.0]), ("paguei R$ 200 da 1ª parcela da tv", [200.0]),
    ("paguei R$ 80 da parcela do carnê do baú", [80.0]), ("paguei o carnê do baú R$ 80", [80.0]),
    ("gastei R$ 80 no carne", [80.0]), ("paguei R$ 200 do parcelamento da dívida", [200.0]),
    ("gastei R$ 50 no mercado 2x por semana", [50.0]), ("gastei 30no mercado", [30.0]),
    ("gastei 50 no mercado até hoje de manhã", [50.0]), ("gastei 50 em uns lanches", [50.0]),
    ("gastei R$ 40 em uns 2 kg de carne", [40.0]), ("paguei R$ 20 no estacionamento quase 2 horas", [20.0]),
    ("gastei R$ 100 na loja com até 50% off", [100.0]), ("paguei R$ 100 do boleto que vence até 10/11", [100.0]),
    ("gastei R$ 50 no bar por volta de 22h", [50.0]), ("gastei 100 no hotel dia 5 e dia 6", [100.0]),
    ("gastei *R$ 50* no mercado", [50.0]), ("gastei *50* no mercado", [50.0]), ("gastei R$ 50 no *mercado*", [50.0]),
    ("gastei 50 no mercado", [50.0]),
]


@MODOS
@pytest.mark.parametrize("texto,esperado", POSITIVOS)
def test_g3_positivo_grava(ana, liga, monkeypatch, modo, texto, esperado):
    diz(ana, modo, monkeypatch, texto + " pelo banco")
    assert valores(ana) == esperado and chaves(ana) == len(esperado) and carteira(ana) == 200.0


# --- G4: a fila confere o valor que ELA grava (`_extract_valor` da resposta, com a data dentro: "05/10 50" grava R$ 5 na
# main). O `limpa_pontuacao_final` da fila deixa "05 /10 50", que o leitor lê como dois números vizinhos (T11). ---
@pytest.mark.parametrize("resposta,esperado", [("2 vírgula 50", T12), ("05/10 50", T11), ("1200 e 300", T11),
                                               ("uns 1200", T17), ("1200 conto e pouco", T17), ("R$ 1200 foi 1300", T12),
                                               ("#99pop 30", T12),  # a fila gravaria 99: só o `valor` da fila decide
                                               ("#99pop - 30", T12),
                                               ("2 coxinhas 10reais", T12), ("1,200", T10),
                                               ("1200 e pouco 22h", T17), ("1200 cada", T12),  # rodada 5
                                               ("1200 a cada", T12)])  # rodada 6
def test_g4_fila_recusa_e_o_item_volta(ana, liga, monkeypatch, resposta, esperado):
    fala(monkeypatch, ana, "paguei o aluguel e paguei a academia no pix")
    r = manda_wamid(ana, resposta)[0]
    assert esperado in r and "aluguel" in r and declaracoes(ana) == [], r
    assert pendencia(ana) == "multi_launch_values"
    assert "registrada pelo banco" in manda_wamid(ana, "1200")[0] and valores(ana) == [1200.0]


@pytest.mark.parametrize("resposta", ["1200", "R$ 1.200"])
def test_g4_fila_valor_claro_grava(ana, liga, monkeypatch, resposta):
    fala(monkeypatch, ana, "paguei o aluguel e paguei a academia no pix")
    assert "registrada pelo banco" in manda_wamid(ana, resposta)[0] and valores(ana) == [1200.0]


# --- G5: pendência de valor e de descrição (a resposta é colada ao texto e o `add` roda de novo) ---
def test_g5_pendencia_de_valor(ana, liga):
    manda_wamid(ana, "paguei o aluguel")
    manda_wamid(ana, "banco")
    assert T11 in manda_wamid(ana, "1200 e 300")[0] and declaracoes(ana) == []
    manda_wamid(ana, "paguei o aluguel")
    manda_wamid(ana, "banco")
    assert "registrada pelo banco" in manda_wamid(ana, "1200")[0] and valores(ana) == [1200.0]


def test_g5_descricao_com_numero_recusa_e_a_pendencia_se_perde(ana, liga):
    """Limite aceito: "gastei 50" + "banco" + "loja 3" vira "gastei 50 - loja 3" (dois números): T12, e o usuário manda o
    gasto inteiro de novo."""
    manda_wamid(ana, "gastei 50")
    manda_wamid(ana, "banco")
    assert T12 in manda_wamid(ana, "loja 3")[0]
    assert nada_gravou(ana) and sem_pergunta(ana)


# --- G6: sem tipo no parser, o `add` grava o valor do classificador (LLM): o leitor confere esse valor ---
@pytest.mark.parametrize("frase,do_classificador,grava", [
    ("no mercado gastei 50", 50.0, [50.0]), ("no mercado gastei 50", 500.0, []),
    # o parser não lê a frase (começa com "dia 5"): conferir o "5" que ele leria recusaria o que o fallback grava certo
    ("dia 5 gastei R$ 50 no mercado", 50.0, [50.0]), ("dia 5 gastei R$ 50 no mercado", 5.0, []),
    ("no mercado gastei", 50.0, []),  # o classificador trouxe um valor que não está no texto
])
def test_g6_valor_do_classificador_e_conferido(ana, liga, monkeypatch, frase, do_classificador, grava):
    import core.handle_incoming as hi
    from core.intent_classifier import IntentResult
    monkeypatch.setattr(hi, "classify", lambda *a, **k: IntentResult(
        intent="launches.add", confidence=0.95, entities={"tipo": "despesa", "valor": do_classificador}))
    r = manda_wamid(ana, frase + " pelo banco")[0]
    assert valores(ana) == grava, r
    if not grava:
        assert nada_gravou(ana), r


# --- G7: fora da declaração (dinheiro, flag desligada, fora da lista) nada muda ---
def test_g7_dinheiro_grava_como_a_carteira(ana, liga):
    manda_wamid(ana, "comprei 2 camisetas por 80 em dinheiro")
    assert [float(x["valor"]) for x in q("select valor from launches where user_id=%s", (ana,), fetch=True)] == [2.0]
    assert declaracoes(ana) == [] and carteira(ana) == 198.0


@pytest.mark.parametrize("lista", [None, "999999999"])
def test_g7_flag_desligada_ou_fora_da_lista_recusa_como_hoje(ana, monkeypatch, lista):
    if lista:
        monkeypatch.setenv("DECLARACAO_BANCO", "1")
        monkeypatch.setenv("DECLARACAO_BANCO_USER_IDS", lista)
    r = manda_wamid(ana, "comprei 2 camisetas por 80 pelo banco")[0]
    assert r.startswith("🐷 Não registrei: com banco conectado") and T12 not in r, r
    assert nada_gravou(ana)


# --- G8: o leitor é rápido até o limite; acima dele recusa sem analisar ---
@pytest.mark.parametrize("texto", [
    "gastei 50" + " " * 16000 + "no mercado pelo banco", "gastei " + "1," * 8000, "pix " * 4000,
    "gastei 50 no mercado " + "e 1 " * 4000, "gastei 50 no mercado " + "e 1 " * 240, "gastei " + "1," * 495,
    "pix " * 249, "gastei 50" + " " * 980 + "no mercado", "gastei " + "vinte e " * 124,
])
def test_g8_texto_patologico_nao_trava(texto):
    from core.handlers import declaracao_valor
    t0 = time.perf_counter()
    declaracao_valor.recusa(texto)
    assert time.perf_counter() - t0 < 0.5


# --- Corpus: taxa de recusa pinada (frase sem `recusa_aceita` grava o valor esperado; com, recusa) ---
CORPUS = json.loads((Path(__file__).parent / "fixtures" / "declaracao_frases_legitimas.json").read_text())


@pytest.mark.parametrize("item", CORPUS, ids=[c["frase"] for c in CORPUS])
def test_corpus_de_frases_legitimas(item):
    from core.handlers import declaracao_valor
    from core.handlers import forma_pagamento as fp
    from parsers import _extract_valor, split_financial_transactions, texto_base, tipo_do_texto
    r = declaracao_valor.recusa(item["frase"])
    if "recusa_aceita" in item:  # `recusa_texto`: QUAL recusa sai (o código do texto; a redação mora em `tx`)
        textos = {"T10": T10, "T11": T11, "T12": T12, "T14": T14, "T15": T15, "T16": T16, "T17": T17}
        assert r and textos[item["recusa_texto"]] in r, (item, r)
        return
    assert r is None, r
    # O que o parser grava. `valores == []`: o parser não lê a frase e o `add` grava o valor do classificador, que o
    # leitor confere de novo no fallback (G6).
    bases = [texto_base(p)[0] for p in split_financial_transactions(fp.limpar(item["frase"], banco=True))]
    lidos = [_extract_valor(b) for b in bases if tipo_do_texto(b)]
    assert sorted(round(v, 2) for v in lidos if v) == sorted(item["valores"])
