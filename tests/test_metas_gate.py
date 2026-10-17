"""PR C4: o gate `core/services/metas_gate.py` (`create_meta` grava direto?) é uma GRAMÁTICA POSITIVA da frase inteira:
o que não encaixa de ponta a ponta vira "Só confirmando". Funções puras, sem banco. Quatro corpora:
Tester (251, `_metas_gate_corpus`), Manager (71 negativas + 26 informais; "treino", a regra NÃO foi ajustada a eles) e o
PRÓPRIO do Coder (86 negativas em 10 categorias + 37 informais, escrito antes de medir: `_metas_gate_proprio`).
Falso negativo é aceito (custa um "sim"); falso positivo grava sem pedido. Controles (mutantes, `mut.py`): `fullmatch`
virar `match`, `_FIM` virar `.*`, aceitar `?`, sem `_STOP`, sem a regra de infinitivo, sem a conferência dos args.
"""
from __future__ import annotations

import pytest

from core.services.metas_gate import pede_criar_meta_ja as gate
from tests._metas_gate_corpus import COMANDOS, HEDGEADAS, MGR_HEDGE, MGR_LEGIT, TESTER3
from tests._metas_gate_proprio import LEG, LEG3, NEG, NEG3, RESIDUO

SEIS = ["cria uma meta de 5000 pra viagem", "faz uma meta de R$ 20.000 pra casa até dezembro de 2027", "criar meta de 5000",
        "registra uma meta chamada Carro de 30 mil", "pode criar uma meta de 1500 pro notebook", "cria a meta Viagem de 5 mil"]
# Falsos negativos ACEITOS do corpus do Tester (18 de 63): "faça", "da pra", "abre", nome solto/minúsculo antes do valor,
# nome de 2 palavras, cauda extra, emoji, "comprar um carro", "por mes", "registra a meta X" (pode ser depósito), "cadastrar meta".
FN_TESTER = ["criar meta viagem 5000", "faça uma meta de 3000 pro carro", "cadastrar meta de 1500", "da pra criar uma meta de 5000",
             "abre uma meta de 5000", "abre uma meta pra viagem de 5000", "cria a meta viagem 5000", "cria meta 5000 viagem",
             "cria uma meta de 5000 pra viagem, fonte tudo investido", "cria uma meta de 5000 pra viagem acompanhando o nubank",
             "cria uma meta de 5000 pra viagem de 2027", "cria uma meta de 5000 pra viagem 😀", "cria uma meta pra comprar um carro de 40 mil",
             "cria uma meta de 500 por mes pra viagem", "faz a meta da viagem de 5000", "registra a meta viagem de 5000",
             "cria uma meta chamada Viagem Europa de 15000", "cria uma meta Viagem de 5000 e prazo junho de 2027"]
FN_PROPRIO = ["cria meta viagem 5000", "cria minha meta de 4000 pro celular", "registre a meta Tablet de 2 mil",
              "cadastre uma meta de 8000 para o casamento"]  # "celular": infinitivo
MGR_OK = ["cria uma meta de 5k pra trip", "Por favor, crie uma meta de 5000 reais para viagem",
          "cria uma meta de cinco mil reais pra viagem", "cria uma meta de dez mil pra casa"]


@pytest.mark.parametrize("frase", SEIS)
def test_os_seis_comandos_legitimos_gravam_direto(frase):
    assert gate(frase), frase


@pytest.mark.parametrize("frase", [f for f in COMANDOS if f not in FN_TESTER])
def test_comando_do_tester_grava_direto(frase):
    assert gate(frase), frase


@pytest.mark.parametrize("frase", FN_TESTER + FN_PROPRIO)
def test_falso_negativo_aceito_pergunta_em_vez_de_gravar(frase):
    assert not gate(frase), frase


@pytest.mark.parametrize("frase", HEDGEADAS)
def test_hedgeada_do_tester_nao_grava_direto(frase):
    assert not gate(frase), frase


@pytest.mark.parametrize("grupo,frase", [(g, f) for g, v in MGR_HEDGE.items() for f in v])
def test_negativa_do_manager_nao_grava_direto(grupo, frase):
    assert not gate(frase), (grupo, frase)


@pytest.mark.parametrize("frase", [f for v in MGR_LEGIT.values() for f in v])
def test_informal_do_manager_grava_ou_pergunta_nunca_e_o_contrario_do_esperado(frase):
    assert gate(frase) is (frase in MGR_OK), frase  # só 4 de 26 encaixam; os 22 que perguntam custam um "sim"


@pytest.mark.parametrize("categoria,frase", [(c, f) for c, v in NEG.items() for f in v])
def test_negativa_propria_nao_grava_direto(categoria, frase):
    assert not gate(frase), (categoria, frase)


@pytest.mark.parametrize("frase", [f for f in LEG if f not in FN_PROPRIO])
def test_informal_proprio_grava_direto(frase):
    assert gate(frase), frase


@pytest.mark.parametrize("frase", ["cria uma meta de 5000 pra depois", "cria uma meta de 5000 pra ver", "cria uma meta de 5000 pra desistir",
                                   "cria uma meta de 5000 pra testar", "cria uma meta gastos 500", "cria uma meta de 5000 n",
                                   "cria uma meta de 5000 sla", "cria uma meta ne 5000", "cria uma meta de 5000 pra viagem ne"])
def test_palavra_de_hesitacao_ou_infinitivo_no_lugar_do_nome_nao_e_nome(frase):
    assert not gate(frase), frase


@pytest.mark.parametrize("frase", TESTER3)
def test_dirigida_do_tester_ao_slot_do_nome_nao_grava_direto(frase):
    assert not gate(frase), frase


@pytest.mark.parametrize("categoria,frase", [(c, f) for c, v in NEG3.items() for f in v])
def test_qualificador_e_hedge_no_slot_do_nome_nao_grava_direto(categoria, frase):
    assert not gate(frase), (categoria, frase)


@pytest.mark.parametrize("frase", LEG3)
def test_informal_da_passada_3_grava_direto(frase):
    assert gate(frase), frase


@pytest.mark.parametrize("frase", RESIDUO)
def test_residuo_conhecido_hedge_desconhecido_no_nome_grava_direto_e_esta_declarado(frase):
    assert gate(frase), frase  # se isto passar a perguntar, tire a frase de RESIDUO (e do limite em docs/CLAUDE.md)


@pytest.mark.parametrize("frase,args,esperado", [
    # MÉDIO 3(a): "reserva" no nome da meta não é reserva de emergência; o tipo vem da ESTRUTURA da frase
    ("cria uma meta de 5000 pra reserva", {"nome": "Reserva", "tipo": "reserva", "meses": 6}, False),
    ("cria uma meta de 5000 pra reserva", {"nome": "Reserva", "tipo": "reserva", "alvo": 5000}, False),
    ("cria uma meta de 5000 pra reserva", {"nome": "Reserva", "tipo": "meta", "alvo": 5000}, True),
    ("cria a reserva de emergência", {"nome": "Reserva", "tipo": "reserva"}, True),
    ("cria a reserva de emergência de 6 meses", {"nome": "Reserva", "tipo": "reserva", "meses": 6}, True),
    ("cria a reserva de emergência de 6 meses", {"nome": "Reserva", "tipo": "reserva", "meses": 12}, False),
    ("cria uma meta de 5000 pra viagem", {"nome": "Viagem", "tipo": "meta", "alvo": 5000.0}, True),
    ("cria uma meta de 5000 pra viagem", {"nome": "Viagem", "tipo": "meta", "alvo": 3000}, False),  # valor do histórico
    ("cria uma meta de 5000 pra viagem", {"nome": "Viagem", "tipo": "meta", "alvo": None}, False),
    ("cria uma meta de 5 mil pra viagem", {"nome": "Viagem", "tipo": "meta", "alvo": "5000"}, True),
    ("cria uma meta de R$ 5.000,50 pra viagem", {"nome": "Viagem", "tipo": "meta", "alvo": "5000.50"}, True),
    ("cria uma meta de 2,5k pro som", {"nome": "Som", "tipo": "meta", "alvo": 2500}, True),
    ("cria uma meta de cinco mil pra casa", {"nome": "Casa", "tipo": "meta", "alvo": 5000}, True),
    ("cria uma meta de mil reais pra casa", {"nome": "Casa", "tipo": "meta", "alvo": 1000}, True),
    ("cria uma meta de 5.000 pra casa", {"nome": "Casa", "tipo": "meta", "alvo": 5000}, True),
    ("cria a meta Viagem de 5 mil", {"nome": "Viagem", "tipo": "meta", "alvo": "5000"}, True),
    ("cria uma meta de 5000 pra viagem", {"nome": "Viagem", "tipo": "meta", "alvo": "abc"}, False),
    # MÉDIO 3(b): o valor vem do MESMO match, não de um número solto dentro do nome
    ("cria uma meta chamada ps5 de 3000", {"nome": "ps5", "tipo": "meta", "alvo": 5}, False),
    ("cria uma meta chamada ps5 de 3000", {"nome": "ps5", "tipo": "meta", "alvo": 3000}, True),
    ("cria uma meta familia de 5000", {"nome": "familia", "tipo": "meta", "alvo": 1000}, False),
    # BAIXO 4: nome, prazo e fonte dos args têm de estar na frase
    ("cria uma meta de 5000 pra viagem", {"nome": "Casa", "tipo": "meta", "alvo": 5000}, False),
    ("cria uma meta de 5000 pra viagem", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2031-01-01"}, False),
    ("cria uma meta de 5000 pra viagem até 2031", {"nome": "Viagem", "tipo": "meta", "alvo": 5000}, False),
    ("cria uma meta de 5000 pra viagem até 2031", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2031-12-31"}, True),
    ("cria uma meta de 5000 pra viagem", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "fonte": "Nubank"}, False),
    # BAIXO 2: não basta haver "até"; o ano (e o mês, se a frase diz) têm de ser os da data_alvo
    ("cria uma meta de 5000 pra viagem até dezembro de 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-31"}, True),
    ("cria uma meta de 5000 pra viagem até dezembro de 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2031-01-31"}, False),
    ("cria uma meta de 5000 pra viagem até dezembro de 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-11-30"}, False),
    ("cria uma meta de 5000 pra viagem até dezembro de 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "amanhã"}, False),
    ("cria uma meta de 5000 pra viagem até 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-31"}, True),
    ("cria uma meta de 5000 pra viagem até 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2028-12-31"}, False),
    ("cria uma meta de 5000 pra viagem até 12/2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-31"}, True),
    ("cria uma meta de 5000 pra viagem até 12/2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-11-01"}, False),
    ("cria uma meta de 5000 pra viagem até 31/12/2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-31"}, True),
    ("cria uma meta de 5000 pra viagem até 31/12/2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-01-31"}, False),
    ("cria uma meta de 5000 pra viagem até 5 de março de 2029", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2029-03-05"}, True),
    # Codex P1 (#1051): o DIA dito também tem de ser o da data_alvo (mês sem dia segue aceitando qualquer dia do mês)
    ("cria uma meta de 5000 pra viagem até dia 10 de dezembro de 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-10"}, True),
    ("cria uma meta de 5000 pra viagem até dia 10 de dezembro de 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-01"}, False),
    ("cria uma meta de 5000 pra viagem até dia 10 de dezembro de 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-31"}, False),
    ("cria uma meta de 5000 pra viagem até 10/12/2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-10"}, True),
    ("cria uma meta de 5000 pra viagem até 10/12/2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-31"}, False),
    ("cria uma meta de 5000 pra viagem até 5 de março de 2029", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2029-03-31"}, False),
    # Codex P1 #2 (#1051): mês sem dia = ÚLTIMO dia do mês; ano sem mês = 31/12; data impossível pergunta
    ("cria uma meta de 5000 pra viagem até dezembro de 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-01"}, False),
    ("cria uma meta de 5000 pra viagem até fevereiro de 2028", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2028-02-29"}, True),
    ("cria uma meta de 5000 pra viagem até fevereiro de 2028", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2028-02-28"}, False),
    ("cria uma meta de 5000 pra viagem até 12/2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-12-15"}, False),
    ("cria uma meta de 5000 pra viagem até 2027", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2027-01-01"}, False),
    ("cria uma meta de 5000 pra viagem até 31/02/2028", {"nome": "Viagem", "tipo": "meta", "alvo": 5000, "data_alvo": "2028-02-29"}, False),
    ("cria uma meta de 5000", {"nome": "Meta de 5000", "tipo": "meta", "alvo": 5000}, False)])  # nome inventado
def test_a_tool_tem_de_dizer_o_que_a_frase_disse(frase, args, esperado):
    assert gate(frase, args) is esperado, (frase, args)


@pytest.mark.parametrize("frase", ["cria uma meta de 5000 pra viagem até lixo", "cria uma meta de 5000 pra viagem até o fim do mundo",
                                   "cria uma meta de 5000 pra viagem até 12", "cria uma meta de 5000 pra viagem até lixo 2031", "cria uma meta de 5000 pra viagem até o fim do mundo de 2031", "cria uma meta de 5000 pra viagem até dezembro"])
def test_lixo_depois_de_ate_nao_e_data(frase):
    assert not gate(frase), frase


# Lista FIXA, independente do `_STOP` (derivar dele seria circular: tirar o termo tiraria o teste junto).
STOP_ESPERADO = ['acha', 'acho', 'agora', 'ai', 'ainda', 'algo', 'amanha', 'antes', 'apaga', 'aproximadamente', 'ate', 'blz', 'caixinhas', 'cancela', 'cartao', 'cartoes', 'categorias', 'cerca', 'cofrinhos', 'conta', 'cria', 'criar', 'crie', 'deixa', 'deleta', 'depois', 'desisti', 'despesas', 'digamos', 'duvida', 'e', 'entao', 'esquece', 'faturas', 'favor', 'faz', 'fazer', 'gastos', 'hein', 'hoje', 'ignora', 'investimentos', 'jamais', 'kk', 'la', 'mais', 'mas', 'maximo', 'menos', 'mentira', 'minimo', 'n', 'nada', 'nao', 'ne', 'ninguem', 'nn', 'nunca', 'obrigada', 'obrigado', 'ok', 'ontem', 'orcamentos', 'ou', 'outra', 'outro', 'pelo', 'perto', 'pf', 'pfv', 'pix', 'pode', 'quando', 'quase', 'que', 'quem', 'receitas', 'rola', 'saldo', 'se', 'sei', 'sempre', 'sla', 'so', 'supondo', 'tal', 'talvez', 'tambem', 'tipo', 'um', 'uma', 'uns', 'valeu', 'vlw', 'volta']


@pytest.mark.parametrize("palavra", STOP_ESPERADO)
def test_cada_palavra_do_stop_nao_e_nome(palavra):
    """BAIXO 4: o `nao` do `_STOP` não tinha teste (mutante passava). Cada termo da lista, no slot do nome depois do valor,
    tem de fazer a frase perguntar; tirar um termo da lista põe a palavra aqui e o teste cai."""
    assert not gate(f"cria uma meta de 5000 pra {palavra}"), palavra
    assert not gate(f"cria uma meta de 5000 chamada {palavra}"), palavra
