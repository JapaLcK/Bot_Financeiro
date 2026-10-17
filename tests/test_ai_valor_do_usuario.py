"""O dinheiro que a IA grava tem que ter sido escrito pelo usuário
(`core/services/ai_chat/valor_do_usuario.py`).

Conversas com o runner e o banco de verdade; só o modelo é falso
(`tests/_ia_falsa_helpers.py`). Flag WA_IA_PRIMEIRO desligada, como em produção.

Controle negativo: com a âncora desligada (`runner.ancora_valor` devolvendo os
args), N1–N5 ficam vermelhos. Os P* (caminho legítimo) valem nos dois estados.

Contrato: a guarda lê valor SÓ EM DÍGITOS. Valor por extenso ("cinquenta") não ancora e a
conversa recusa com "Qual é o valor?" (falso-rejeite aceito, fail-closed). A CONTAGEM de parcelas
("doze vezes") segue lida por extenso: contagem não é valor.
"""
from __future__ import annotations

import pytest

import db
import db.bills  # noqa: F401 (submódulo usado como db.bills nos testes; não depende da ordem de coleta)
from core.services.ai_chat import runner
from core.services.ai_chat._context import CURRENT_PLATFORM, CURRENT_USER_MESSAGE
from core.services.ai_chat.tools import _BY_NAME, get_tool
from core.services.ai_chat.valor_do_usuario import _centavos_lidos, ancora_valor
from conftest import usuario_pagante
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import (
    chamada, com_tools, desliga_flag, lancamento, lancamentos, openai_falso, texto as texto_ia,
)
from tests._pendencia_credito_helpers import diga
from tests.test_ai_chat_metas_escrita import linhas as metas_gravadas
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank
from tests.test_wa_minhas_metas import chave  # noqa: F401 (fixture)

RECUSA = "Não anotei nada: não achei esse valor no que você escreveu. Qual é o valor?"
RECUSA_PARCELAS = "🐷 Não anotei nada: em quantas parcelas foi? Ex.: 12x."


# ── (1) o que a âncora lê do texto do usuário ───────────────────────────────

@pytest.mark.parametrize("texto,tem,nao_tem", [
    ("50", {5000}, set()),
    ("50,5", {5050}, set()),
    ("R$ 1.234,56", {123456}, set()),
    ("1,2 mil", {120000}, set()),
    ("cinquenta", set(), {5000}),            # extenso não ancora (só dígitos)
    ("mil e duzentos", set(), {120000}),
    ("torrei 80 conto no bar", {8000}, set()),
    ("dia 20 paguei 300", {30000}, {2000}),
    ("12x de 50", {5000}, {1200}),
    ("gastei 50 em 2026", {5000}, {202600}),
    # máscaras: número que não é valor (e o valor legítimo ao lado segue lido)
    ("cartão final 1234", set(), {123400}),
    ("CPF 123.456.789-09", set(), {12345678909}),
    ("CNPJ 12.345.678/0001-95", set(), {1234567800, 12345678000195}),
    ("ano 2026", set(), {202600}),
    ("às 15:30", set(), {1500, 1530}),
    ("aumentou 10%", set(), {1000}),
    ("17/10/2026", set(), {1700, 1000, 202600}),
    ("17/10", set(), {1700, 1000}),
    ("ag 1234 cep 78000-000", set(), {123400, 7800000, 78000000}),
    ("17/10 paguei R$ 1.234,56", {123456}, set()),
    ("recebi 1.250.000", {125000000}, set()),
    ("café 49,90 às 15:30", {4990}, {1500}),
    ("desconto de 10% em 200", {20000}, {1000}),
    ("paguei a conta 150", {15000}, set()),
    # `conta`/`cartão`/`final` antes de dinheiro NÃO mascaram (só "cartão final 1234")
    ("paguei a conta 1200", {120000}, set()),
    ("conta 1500", {150000}, set()),
    ("na conta 1200 reais", {120000}, set()),
    ("comprei no cartão 3000 em 3x", {300000}, set()),
    ("valor final 1500", {150000}, set()),
    ("preço final 2300", {230000}, set()),
    ("conta 1500,50", {150050}, {5000}),   # a vírgula não deixa sobrar ",50" como R$ 50
    ("conta final 1234", set(), {123400}),
    # centavos: "n centavos" é n, não R$ n; "reais e centavos" segue somado
    ("gastei 50 centavos", {50}, {5000}),
    ("50 centavos no bombom", {50}, {5000}),
    ("75 centavos", {75}, {7500}),
    ("gastei 10 no uber e 50 centavos no bombom", {1000, 50}, {5000}),
    ("30 reais e 50 centavos", {3050}, {50, 3000}),
    # extenso não ancora (nem centavos, nem reais, nem composição): só o que está em dígito
    ("gastei cinquenta centavos", set(), {50, 5000}),
    ("vinte e cinco centavos", set(), {25, 2500, 2000, 500}),
    ("um centavo", set(), {1, 100}),
    ("trinta reais e cinquenta centavos", set(), {3000, 50, 3050, 5000}),
    ("trinta reais e 50 centavos", {50}, {5000, 3050}),   # misto: o 50 em dígito são centavos, nunca R$ 50
    # "um"/"uma" soltos são artigo, não R$ 1
    ("comprei uma tv", set(), {100}),
    ("comprei um carro", set(), {100}),
    ("paguei um real", set(), {100}),
    ("paguei uma pizza de 40", {4000}, {100}),
    # extenso nunca ancora, nem por trecho, nem somado
    ("gastei cinquenta no mercado e vinte no uber", set(), {5000, 2000, 7000}),
    ("cinquenta e cinco", set(), {5500, 5000, 500}),
    ("vinte e cinco", set(), {2500, 2000, 500}),
    ("cento e vinte", set(), {12000, 10000}),
    ("dois mil e trezentos e cinquenta", set(), {235000}),
    ("cento e vinte mil e quinhentos", set(), {12050000}),
    ("cinquenta e vinte", set(), {5000, 2000, 7000}),
    ("gastei cinquenta reais no mercado com 2 amigos", {200}, {5000}),   # o dígito ancora, o extenso ao lado não
    # ponto + 1-2 dígitos é decimal (o ai_guard lê ponto como milhar: 49.90 virava 499000)
    ("gastei 49.90", {4990}, {499000}),
    ("gastei 49.9", {4990}, {49900, 499000}),   # 1 casa decimal também é decimal
    ("0.50", {50}, {5000}),
    ("gastei 2.5", {250}, {2500}),
    # ponto + 3 dígitos segue milhar; vírgula decimal e data não mudam
    ("1.200", {120000}, {120}),
    ("1.200,50", {120050}, set()),
    ("12.345", {1234500}, {1234}),
    ("1.000.000", {100000000}, set()),
    ("49,90", {4990}, {499000}),
    ("10.12.2026", set(), {1012, 101200}),
    # multiplicador por extenso não ancora
    ("meu orçamento é dois milhões", set(), {200000000, 200, 20000}),
    ("um milhão", set(), {100000000}),
    ("três bilhões", set(), {300000000000}),
    ("dois milhões de reais", set(), {200000000}),
    ("duzentos mil", set(), {20000000}),
    # "<dígitos> mil|milhão|bilhão": só o valor expandido ancora, nunca o componente (R$ 2) nem o "mil" solto (R$ 1.000)
    ("gastei 2 mil", {200000}, {200, 100000}),
    ("gastei 2 MIL", {200000}, {200, 100000}),
    ("gastei 2mil", {200000}, {200}),
    ("2,5 mil", {250000}, {250, 100000}),
    ("1.5 mil", {150000}, {150, 100000}),
    ("gastei 2 milhões", {200000000}, {200}),
    ("gastei 2 milhoes", {200000000}, {200}),
    ("1 bilhão", {100000000000}, {100}),
    ("2 mil e 500", {250000, 50000}, {200, 100000}),   # o 500 solto segue ancorando (já ancorava)
    ("gastei 2", {200}, set()),
    ("gastei 2 no mercado", {200}, set()),
    ("1.200", {120000}, set()),                          # milhar, não multiplicador
    ("5k", {500}, set()),                                # limite conhecido, não muda
    # extenso antes de marcador de tempo: também não ancora
    ("gastei cinquenta ontem", set(), {5000}),
    ("gastei vinte hoje", set(), {2000}),
    ("paguei trinta anteontem", set(), {3000}),
    ("gastei dez agora", set(), {1000}),
    ("gastei quarenta amanhã", set(), {4000}),
    ("comprei duas tvs", set(), {200}),
    # data: "N de MÊS" e dd-mm não são valor; "50 em março" é (só "N de MÊS" mascara)
    ("anota o boleto da internet para 12 de outubro", set(), {1200}),
    ("boleto de 150 para 12 de outubro de 2026", {15000}, {1200, 202600}),
    ("vence 5 de mar", set(), {500}),
    ("vence 12-10", set(), {1200, 1000}),
    ("vence 12-10-2026", set(), {1200, 1000, 202600}),
    ("gastei 50 em março", {5000}, set()),
    ("gastei 50 de gasolina em outubro", {5000}, set()),
    # "de" opcional: "15 outubro 2026", "15 out 2026", "15 out" são data; ano sem "de" só 20dd
    ("anota o boleto da internet para 15 outubro 2026", set(), {1500, 202600}),
    ("anota o boleto da internet para 15 out 2026", set(), {1500, 202600}),
    ("anota o boleto da internet para 15 out", set(), {1500}),
    ("boleto de 150 para 15 out 2026", {15000}, {1500, 202600}),
    ("boleto de 15 out 150", {15000}, {1500}),               # 150 não é ano: segue dinheiro
    ("gastei 50 em março", {5000}, set()),
    ("gastei 50 mercado em outubro", {5000}, set()),
    # data ISO e pontuada não é valor; milhar e decimal seguem lidos
    ("anota o boleto da internet para 2026-10-20", set(), {202600, 2000, 1000}),
    ("boleto de 150 para 2026-10-20", {15000}, {202600, 2000, 1000}),
    ("vence 10.12.2026", set(), {1012202600}),
    ("vence 10.12.26", set(), {10122600}),
    ("boleto de 150 vence 10.12.2026", {15000}, {1012202600}),
    ("paguei 1.500 em 2026-10-20", {150000}, {202600}),
    # centavos colado ao número, e "1 real e N centavos" compõe o total (o 50 não é R$ 50)
    ("gastei 50centavos", {50}, {5000}),
    ("gastei 50 centavos", {50}, {5000}),
    ("1 real e 50 centavos", {150}, {100, 5000, 50}),
    ("1 real e 50centavos", {150}, {100, 5000, 50}),
    ("30 reais e 50 centavos", {3050}, {3000, 5000, 50}),
    ("um real e 50 centavos", {50}, {5000, 150}),          # "um" por extenso não ancora o real; mistura como "trinta reais e 50 centavos"
    ("21 real e 50 centavos", {2150}, {100, 150, 2100}),    # o "1" tem de ser o número inteiro
    # rótulo de dia com dois-pontos: o dia não é valor; o dinheiro ao lado segue lido
    ("anota o boleto da internet, vencimento dia: 18", set(), {1800}),
    ("boleto de 150, vencimento dia: 18", {15000}, {1800}),
    ("boleto de 150 vencimento dia 18", {15000}, {1800}),
    ("boleto de 150 venc. dia 18", {15000}, {1800}),
    ("boleto de 150 vcto 18", {15000}, {1800}),
    ("vence dia:5 pago 300", {30000}, {500}),
    ("vence 20 e paguei 300", {30000}, {2000}),
    ("paguei a conta 1200 dia 20", {120000}, {2000}),
    ("vencimento: 18/10", set(), {1800, 1000}),             # data: o lookahead deixa a regra de data mascarar inteira
    ("dia 12/10 paguei 300", {30000}, {1200, 1000}),
    ("vence 18:30 paguei 300", {30000}, {1800, 3000}),
    ("diária 150", {15000}, set()),                         # "dia" dentro de palavra não mascara
    ("vence 150", {15000}, set()),                          # 3 dígitos não são dia
    # sufixo ordinal no dia: o \b não casava antes de "º" e o 1 virava R$ 1 (P1 do Codex)
    ("anota o boleto da internet para o dia 1º", set(), {100}),
    ("dia 1º", set(), {100}),
    ("vence 15º", set(), {1500}),
    ("dia 2ª", set(), {200}),
    ("dia 1o", set(), {100}),
    ("vence dia 1o", set(), {100}),
    ("1º de outubro", set(), {100}),
    ("1° de outubro", set(), {100}),
    ("1o de outubro", set(), {100}),
    ("boleto de 150 dia 1º", {15000}, {100}),
    ("boleto de 150 para o dia 1º", {15000}, {100}),
    ("dia 50a", {5000}, set()),                             # letra colada a 2 dígitos não é ordinal
    ("gastei 50a março", {5000}, set()),
    ("paguei o 1º lugar 50", {5000}, set()),                # ordinal solto (sem dia/mês) não mascara: como antes, o 1 segue lido
    # hora com abreviação (hs/hrs/hr): não é valor; "100 hamburguer" segue lido (P1 do Codex)
    ("anota o boleto da internet para amanhã 18hs", set(), {1800}),
    ("18hrs", set(), {1800}), ("18 hs", set(), {1800}), ("18hr", set(), {1800}), ("às 18hs", set(), {1800}),
    ("18hs30", set(), {1800, 3000}), ("18hrs30", set(), {1800, 3000}),
    ("boleto de 150 18hs", {15000}, {1800, 1501800}),
    ("100 hamburguer", {10000}, set()), ("100 hsbc", {10000}, set()),
    # cada multiplicador "N mil" é leitura própria, não só o primeiro do texto (P2 do Codex)
    ("gastei 2 mil no mercado e 3 mil no uber", {200000, 300000}, {200, 300, 3000}),
    ("2 mil", {200000}, {200}), ("R$ 2 mil", {200000}, {200}),
    ("2 mil, 3 mil e 4 mil", {200000, 300000, 400000}, {200, 300, 400}),
    ("2 mil e 500 no mercado e 3 mil no uber", {250000, 50000, 300000}, {300}),   # o 500 solto segue ancorando (limite declarado)
    ("1 milhão e 2 mil", {100000000, 200000}, {100, 200}),
    ("2,5 mil e 3 mil", {250000, 300000}, {300}),
])
def test_centavos_lidos(texto, tem, nao_tem):
    lidos = _centavos_lidos([texto])
    assert tem <= lidos and not (nao_tem & lidos), lidos


# "N reais e M centavos" é UM valor: o inteiro e os centavos soltos nunca ancoram (senão o modelo que
# descarta os centavos grava R$ 30 em vez de 30,50). Dois valores ("30 reais e 50") seguem dois.
@pytest.mark.parametrize("texto,tem,nao_tem", [
    ("paguei 30 reais e 50 centavos", {3050}, {3000, 50, 5000}),
    ("paguei 30 real e 50 centavos", {3050}, {3000, 50}),
    ("30 reais e 50centavos", {3050}, {3000, 50}),
    ("30reais e 50 centavos", {3050}, {3000, 50}),
    ("30 reais   e   50 centavos", {3050}, {3000, 50}),
    ("30 REAIS E 50 CENTAVOS", {3050}, {3000, 50}),
    ("30 reais e 5 centavos", {3005}, {3000, 5}),
    ("R$ 30 e 50 centavos", {3050}, {3000, 50}),
    ("R$30,00 e 50 centavos", {3050}, {3000, 50}),
    ("1.200 reais e 50 centavos", {120050}, {120000, 50}),
    ("2 mil reais e 50 centavos", {200050}, {200000, 50}),
    ("1 real e 50 centavos", {150}, {100, 50}),
    ("um real e 50 centavos", {50}, {100, 150}),
    ("gastei 30 reais e 50 no mercado", {3000, 5000}, {3050}),
    ("paguei 50 centavos", {50}, {5000}),
    ("30 reais e 20 centavos no pão e 10 reais no uber", {3020, 1000}, {3000, 20}),
    ("10 reais no uber e 30 reais e 50 centavos no pão", {3050, 1000}, {3000, 50}),
])
def test_reais_e_centavos_so_o_composto_ancora(texto, tem, nao_tem):
    lidos = _centavos_lidos([texto])
    assert tem <= lidos and not (nao_tem & lidos), lidos


# Valor por extenso não ancora nada, em nenhuma posição (a contagem de coisa tampouco).
@pytest.mark.parametrize("texto", [
    "comprei duas TVs no Nubank", "dois amigos", "três pizzas", "comprei um carro",
    "gastei cinquenta reais no mercado com dois amigos", "gastei cinquenta no mercado", "paguei vinte na farmácia",
    "gastei dois", "gastei dois, ontem", "gastei cinquenta e vinte", "paguei um real", "torrei oitenta conto",
    "achei vinte pilas", "gastei cinquenta centavos", "paguei trinta reais e cinquenta centavos",
    "um real e vinte e cinco centavos", "trinta reais e cinquenta", "comprei dois milhões de pizzas",
    "um milhão no mercado",
])
def test_extenso_nao_ancora(texto):
    assert _centavos_lidos([texto]) == set()


# Dígito ao lado de extenso: só o dígito ancora.
@pytest.mark.parametrize("texto,tem,nao_tem", [
    ("paguei uma pizza de 40", {4000}, {100}),
    ("30 reais e 50 centavos", {3050}, {3000, 50}),
    ("trinta reais e 50 centavos", {50}, {5000, 3050}),
    ("gastei cinquenta reais com 2 amigos", {200}, {5000}),
])
def test_digito_ancora_extenso_ao_lado_nao(texto, tem, nao_tem):
    lidos = _centavos_lidos([texto])
    assert tem <= lidos and not (nao_tem & lidos), lidos


@pytest.mark.parametrize("digitos", [33, 100, 308, 400])
def test_numero_gigante_nao_estoura_nem_anula_o_resto(digitos):  # float("9"*308) é inf; round(inf) estoura
    lixo = "9" * digitos
    _centavos_lidos(["gastei " + lixo])   # não levanta
    assert 5000 in _centavos_lidos(["gastei 50 no mercado " + lixo])   # o 50 legítimo segue lido
    assert 5000 in _centavos_lidos(["gastei " + lixo, "gastei 50"])    # e outro texto da janela também


@pytest.mark.parametrize("texto", ["foi em dinheiro", "pix", "#50", ""])
def test_texto_sem_valor_nao_le_nada(texto):
    assert _centavos_lidos([texto]) == set()


# Classe "número que é quantidade/duração/medida, não dinheiro": não ancora; o valor ao lado segue lido.
@pytest.mark.parametrize("texto,tem,nao_tem", [
    ("daqui 10 dias", set(), {1000}),
    ("em 1 dia", set(), {100}),
    ("daqui 2 semanas", set(), {200}),
    ("daqui a 3 meses", set(), {300}),
    ("daqui 3 mes", set(), {300}),
    ("há 2 anos", set(), {200}),
    ("daqui 5 horas", set(), {500}),
    ("em 2h", set(), {200}),
    ("30 minutos", set(), {3000}),
    ("45 min", set(), {4500}),
    ("rodei 12 km", set(), {1200}),
    ("rodei 12,5 km", set(), {1250, 1200}),
    ("comprei 2 kg", set(), {200}),
    ("500 g de arroz", set(), {50000}),
    ("250 ml", set(), {25000}),
    ("10 l de gasolina", set(), {1000}),
    ("2,5 litros", set(), {250}),
    ("3 metros", set(), {300}),
    ("40 m2", set(), {4000}),
    ("40 m²", set(), {4000}),
    ("2 unidades", set(), {200}),
    ("3 un", set(), {300}),
    ("para 4 pessoas", set(), {400}),
    ("1.500 km", set(), {150000, 500}),
    # o dinheiro ao lado segue ancorando
    ("boleto de 150 para daqui 10 dias", {15000}, {1000}),
    ("gastei 80 em 4 pessoas", {8000}, {400}),
    ("gastei 30 de gasolina, 10 l", {3000}, {1000}),
    ("paguei 300 dia 20", {30000}, {2000}),
    # o que NÃO pode sumir
    ("50 mercado", {5000}, set()),
    ("77,90 mercado", {7790}, set()),
    ("gastei 2 mil", {200000}, set()),
    ("gastei 2 milhões", {200000000}, set()),
    ("gastei 10 na farmácia", {1000}, set()),
    ("gastei 10 reais", {1000}, set()),
    ("gastei 10 conto", {1000}, set()),
    ("gastei 10 pila", {1000}, set()),
    ("gastei 50 centavos", {50}, {5000}),
    ("gastei 100 gasolina", {10000}, set()),     # "g" só vale como palavra inteira
    ("gastei 100 lanche", {10000}, set()),       # idem "l"
    ("gastei 100 mercado", {10000}, set()),      # idem "m"
    ("gastei 100 hamburguer", {10000}, set()),   # idem "h"
    # hora compacta: "18h30" não são R$ 18 e R$ 30; "h" colado em palavra segue dinheiro
    ("para amanhã às 18h30", set(), {1800, 3000, 183000}),
    ("às 18 h 30", set(), {1800, 3000}),
    ("boleto de 150 às 18h30", {15000}, {1800, 3000, 183000}),
    ("gastei 50 hot dog", {5000}, set()),
    # hora solta com crase: "às 18" não é R$ 18; "às 18 reais"/"às 18,50" segue dinheiro; "boleto de 150 às 18" ancora só o 150
    ("anota o boleto da internet para amanhã às 18", set(), {1800}),
    ("às 9", set(), {900}),
    ("boleto de 150 às 18", {15000}, {1800}),
    ("paguei 50 às 18h", {5000}, {1800}),
    ("paguei às 18 reais", {1800}, set()),
    ("paguei às 18,50", {1850}, set()),
    ("paguei às 18 conto", {1800}, set()),
    ("mas 18", {1800}, set()),   # "mas" não é "às"
])
def test_quantidade_duracao_medida_nao_ancora(texto, tem, nao_tem):
    lidos = _centavos_lidos([texto])
    assert tem <= lidos and not (nao_tem & lidos), lidos


def _ancora(uid, nome, args, fala):
    tok = CURRENT_USER_MESSAGE.set(fala)
    try:
        return ancora_valor(uid, get_tool(nome), dict(args))
    finally:
        CURRENT_USER_MESSAGE.reset(tok)


def test_regra_da_ancora(uid_pro):
    assert _ancora(uid_pro, "add_launch", {"valor": 50}, "gastei 50")[1] is None
    assert _ancora(uid_pro, "add_launch", {"valor": -50}, "gastei 50")[1] is None  # abs
    assert _ancora(uid_pro, "add_launch", {"valor": 500}, "gastei 50")[1] == "recusa"
    for ilegivel in (float("nan"), float("inf"), "abc"):  # o execute recusa; args intactos
        assert _ancora(uid_pro, "add_launch", {"valor": ilegivel}, "gastei 50")[1] is None
    # cartão: parcelas × valor lido
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, "12x de 50")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600}, "12x de 50")[1] == "recusa"
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 601, "parcelas": 12}, "12x de 50")[1] == "recusa"
    # parcelas só multiplicam se o usuário as escreveu (A1)
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 3000, "parcelas": 60}, "comprei 50 no nubank")[1] == "recusa_parcelas"
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, "600 em 12x")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 300, "parcelas": 3}, "3 vezes de 100")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 100, "parcelas": 3}, "3 vezes de 100")[1] == "recusa"  # 100 é a parcela
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, "12 parcelas de 50")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 650, "parcelas": 13}, "12x de 50")[1] == "recusa_parcelas"
    # valor de parcela sem "de": "12x R$ 50", "12 parcelas a 50" (P1 do Codex); "12x 50" solto é ambíguo e segue como total
    for fala in ("geladeira 12x R$ 50", "12 parcelas a 50"):
        assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, fala)[1] is None
        assert _ancora(uid_pro, "add_credit_purchase", {"valor": 50, "parcelas": 12}, fala)[1] == "recusa"
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 50, "parcelas": 12}, "12x 50")[1] is None
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, "12x 50")[1] == "recusa"
    # valor da parcela por extenso não ancora nem o total derivado: recusa (a contagem "doze vezes" é lida)
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": 600, "parcelas": 12}, "doze vezes de cinquenta")[1] == "recusa"
    # campo opcional sem âncora: descartado; obrigatório: recusa
    args, acao = _ancora(uid_pro, "mark_bill_paid", {"name": "luz", "amount": 150}, "paguei a luz")
    assert acao == "descarta" and args == {"name": "luz"}
    # opcional com valor escrito que não bate é divergência (recusa); data mascarada não conta como valor
    assert _ancora(uid_pro, "mark_bill_paid", {"name": "luz", "amount": 500}, "paguei 50 da luz")[1] == "recusa"
    assert _ancora(uid_pro, "mark_bill_paid", {"name": "luz", "amount": 120}, "paguei 120 da luz")[1] is None
    args, acao = _ancora(uid_pro, "mark_bill_paid", {"name": "luz", "amount": 500}, "paguei a luz do dia 20")
    assert acao == "descarta" and args == {"name": "luz"}
    assert _ancora(uid_pro, "add_boleto", {"name": "x", "amount": 300}, "anota um boleto")[1] == "recusa"
    assert _ancora(uid_pro, "set_budget", {"categoria": "lazer", "budget": 300}, "orçamento de lazer")[1] == "recusa"


# ── (2) N: a conversa NÃO pode gravar valor que o usuário não escreveu ──────

# "foi em dinheiro" não é comando do roteador: o classificador de intenção (LLM) consulta o
# modelo ANTES do runner. A 1ª rodada do roteiro é dele; falha, e a mensagem cai na IA.
CLASSIFICADOR_FORA = RuntimeError("classificador fora")


def _usa_ia(cliente):
    assert len(cliente.creates) >= 1, "a IA falsa não foi consultada: o teste não mede nada"


def _historico_sem(uid, *numeros):
    """O resultado `tool` que a recusa grava no histórico não carrega o número inventado:
    senão o ai_guard o leria como evidência. Exige ao menos uma linha `tool`."""
    tools = [m["content"] or "" for m in db.ai_get_last_messages(uid, 30) if m["role"] == "tool"]
    assert tools, "a recusa não gravou resultado `tool`: o teste não mede nada"
    for n in numeros:
        assert not any(str(n) in c or str(n).replace(".", ",") in c for c in tools), tools


@pytest.mark.parametrize("valor", [50, 450.73])
def test_n1_torrei_80_conto_foi_em_dinheiro(uid_pro, monkeypatch, valor):
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    cli = openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(valor, forma_pagamento="dinheiro"))
    diga(uid_pro, "torrei 80 conto no bar")   # o roteador não acha o valor: 0 chamadas de IA
    assert cli.creates == []
    r = diga(uid_pro, "foi em dinheiro")
    _usa_ia(cli)
    assert RECUSA in r, r
    assert lancamentos(uid_pro) == []
    _historico_sem(uid_pro, valor)


def test_n2_valor_so_em_mensagem_do_assistente(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    db.ai_append_message(uid_pro, "user", "oi")
    db.ai_append_message(uid_pro, "assistant", "🐷 Esse valor foi gasto ou recebimento? Ex: *450,73 mercado*")
    db.ai_append_message(uid_pro, "tool", '{"status": "done", "message": "450.73"}', tool_call_id="t0", tool_name="add_launch")
    cli = openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(450.73, forma_pagamento="dinheiro"))
    r = diga(uid_pro, "foi em dinheiro")
    _usa_ia(cli)
    assert RECUSA in r, r
    assert lancamentos(uid_pro) == []


def test_n3_quatro_anteriores_nao_ancoram(uid_pro, monkeypatch):  # janela 4 aprovaria
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    for t in ("gastei 500", "oi", "bom dia", "tudo bem"):
        db.ai_append_message(uid_pro, "user", t)
    cli = openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(500, forma_pagamento="dinheiro"))
    r = diga(uid_pro, "foi em dinheiro")
    _usa_ia(cli)
    assert RECUSA in r and lancamentos(uid_pro) == [], r


def test_n3_tres_anteriores_ainda_ancoram(uid_pro, monkeypatch):
    # a atual ("foi em dinheiro") + 3 anteriores: o 500 é a 3ª anterior. Janela 2 recusaria.
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    for t in ("gastei 500", "oi", "bom dia"):
        db.ai_append_message(uid_pro, "user", t)
    openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(500, forma_pagamento="dinheiro"))
    diga(uid_pro, "foi em dinheiro")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 500.0}]


def test_n3_linha_system_corta_a_janela(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    db.ai_append_message(uid_pro, "user", "gastei 500")
    db.ai_append_message(uid_pro, "system", runner._ATENDIDA_FORA)   # o roteador já gravou
    cli = openai_falso(monkeypatch, CLASSIFICADOR_FORA, lancamento(500, forma_pagamento="dinheiro"))
    r = diga(uid_pro, "foi em dinheiro")
    _usa_ia(cli)
    assert RECUSA in r and lancamentos(uid_pro) == [], r


@pytest.mark.parametrize("lixo", [float("inf"), float("-inf"), float("nan"), 1e999, "abc", [3], {"a": 1}])
def test_parcelas_lixo_nao_estoura(uid_pro, lixo):  # B1: OverflowError subia pelo runner
    for nome in ("add_launch", "add_credit_purchase"):
        assert _ancora(uid_pro, nome, {"valor": 50, "parcelas": lixo}, "gastei 50")[1] is None  # lido como 1 parcela
    assert _ancora(uid_pro, "add_launch", {"valor": 500, "parcelas": lixo}, "gastei 50")[1] == "recusa"


@pytest.mark.parametrize("args,fala", [
    ({"valor": 1234}, "cartão final 1234"),
    ({"valor": 2026}, "ano 2026"),
    ({"valor": 10}, "aumentou 10%"),
    ({"valor": 15}, "às 15:30"),
    ({"valor": 123456789}, "CPF 123.456.789-09"),
    ({"valor": 17}, "vence 17/10/2026"),
])
def test_numero_que_nao_e_valor_nao_ancora(uid_pro, args, fala):
    assert _ancora(uid_pro, "add_launch", args, fala)[1] == "recusa"


def test_valor_legitimo_ao_lado_de_data_e_hora_ancora(uid_pro):
    assert _ancora(uid_pro, "add_launch", {"valor": 1234.56}, "dia 17/10 às 15:30 paguei R$ 1.234,56")[1] is None


def test_log_da_recusa_e_do_descarte(uid_pro, monkeypatch):
    achados = []
    monkeypatch.setattr("core.services.ai_chat.valor_do_usuario.log_system_event_sync",
                        lambda nivel, evento, msg, **kw: achados.append((evento, kw["details"])))
    _ancora(uid_pro, "add_launch", {"valor": 500}, "gastei 50")
    _ancora(uid_pro, "mark_bill_paid", {"name": "luz", "amount": 150}, "paguei a luz")
    _ancora(uid_pro, "add_credit_purchase", {"valor": 50, "parcelas": 60}, "comprei 50 no nubank")
    _ancora(uid_pro, "add_launch", {"valor": 50}, "gastei 50")  # ancorado: sem log
    tok = CURRENT_PLATFORM.set("whatsapp")
    try:
        _ancora(uid_pro, "add_launch", {"valor": 500}, "gastei 50")
    finally:
        CURRENT_PLATFORM.reset(tok)
    base = {"tool": "add_launch", "acao": "recusa", "motivo": "valor", "plataforma": "dashboard",
            "texto_tem_numero": True}
    assert achados == [
        ("ai_valor_sem_ancora", base),
        ("ai_valor_sem_ancora", {"tool": "mark_bill_paid", "acao": "descarta", "motivo": "valor",
                                 "plataforma": "dashboard", "texto_tem_numero": False}),
        ("ai_valor_sem_ancora", {**base, "tool": "add_credit_purchase", "motivo": "parcelas"}),
        ("ai_valor_sem_ancora", {**base, "plataforma": "whatsapp"}),
    ]


# Portão contra o próximo caso: escritor sem confirmação com argumento de dinheiro (type "number" no
# schema; contagens como parcelas/meses/ids são "integer") tem que declarar `valor_do_usuario`.
SEM_ANCORA_DE_PROPOSITO = {
    # O alvo do create_meta só grava direto se `pede_criar_meta_ja(frase, args)` confere (metas_gate._confere: o valor da
    # frase == args.alvo); senão vira "Só confirmando" com o valor À VISTA e o "sim" do usuário. Provado por
    # test_meta_*_alvo_inventado_*, não por esta guarda.
    "create_meta": "ancorada pelo gate da frase (metas_gate) ou pelo 'sim' do usuário, não por esta guarda",
}


def test_todo_escritor_sem_confirmacao_com_dinheiro_declara_a_ancora():
    escritores = {}
    for nome, t in _BY_NAME.items():
        props = t.schema["function"]["parameters"].get("properties", {})
        dinheiro = [k for k, v in props.items() if v.get("type") == "number"]
        if t.is_write and not t.requires_confirmation and dinheiro:
            escritores[nome] = (t.valor_do_usuario, dinheiro)
    assert set(escritores) >= {"add_launch", "add_credit_purchase", "add_boleto", "mark_bill_paid", "set_budget"}
    sem = {n for n, (campo, _) in escritores.items() if campo is None}
    assert sem == set(SEM_ANCORA_DE_PROPOSITO), sem ^ set(SEM_ANCORA_DE_PROPOSITO)
    assert all(campo in dinheiro for campo, dinheiro in escritores.values() if campo)


def _credito(uid) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) n from credit_transactions where user_id=%s", (uid,))
        n = cur.fetchone()["n"]
        conn.commit()
    return n


def _compra_cartao(valor, parcelas=None):
    args = {"valor": valor, "card_name": "Nubank", "descricao": "geladeira"}
    if parcelas:
        args["parcelas"] = parcelas
    return com_tools(chamada("add_credit_purchase", args))


def test_n4_cartao_total_derivado_sem_parcelas(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(600))   # 12 × 50 sem dizer as parcelas
    r = runner.chat(uid_pro, "geladeira 12x de 50 no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r


def test_n6_parcelas_forjadas_nao_gravam(uid_pro, monkeypatch):  # A1
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(3000, parcelas=60))
    r = runner.chat(uid_pro, "comprei 50 no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA_PARCELAS in r and _credito(uid_pro) == 0, r


@pytest.mark.parametrize("lixo", [float("inf"), float("-inf"), 1e999])
def test_parcelas_lixo_grava_como_antes(uid_pro, monkeypatch, lixo):  # B1 na conversa
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50, parcelas=lixo))
    runner.chat(uid_pro, "gastei 50 no mercado", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


@pytest.mark.parametrize("digitos", [33, 400])
def test_numero_gigante_na_mensagem_nao_derruba_nem_trava(uid_pro, monkeypatch, digitos):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50), lancamento(50, alvo="padaria"))
    runner.chat(uid_pro, "gastei 50 no mercado " + "9" * digitos, platform="whatsapp")   # não levanta
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]   # o 50 escrito ancora
    runner.chat(uid_pro, "gastei 50 na padaria", platform="whatsapp")                    # não ficou travada
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}] * 2


def test_mil_derrubado_pelo_modelo_nao_grava_mil_vezes_menos(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(2), lancamento(2000))
    runner.chat(uid_pro, "gastei 2 mil", platform="whatsapp")   # modelo manda 2: recusado
    assert lancamentos(uid_pro) == []
    runner.chat(uid_pro, "gastei 2 mil", platform="whatsapp")   # modelo manda 2000: grava
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 2000.0}]


def _duas(primeiro, segundo):
    return com_tools(chamada("add_launch", {"tipo": "despesa", "valor": primeiro, "alvo": "uber"}, "c1"),
                     chamada("add_launch", {"tipo": "despesa", "valor": segundo, "alvo": "uber"}, "c2"))


@pytest.mark.parametrize("ordem", [(77, 30), (30, 77)])
def test_n7_duas_chamadas_uma_sem_ancora(uid_pro, monkeypatch, ordem):  # B2
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _duas(*ordem))
    r = runner.chat(uid_pro, "gastei 30 no uber", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.0}]
    assert "Não anotei nada" not in r and "Não anotei um dos itens" in r and "30" in r, r


def test_n7_todas_sem_ancora_segue_terminal(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _duas(77, 88))
    r = runner.chat(uid_pro, "gastei 30 no uber", platform="whatsapp")
    assert RECUSA in r and "um dos itens" not in r and lancamentos(uid_pro) == [], r


def _conta_variavel(uid):
    from datetime import date
    import db.bills as B
    import db.recurring as R
    rec = R.create_recurring_expense(uid, "Luz", None, "conta", 10, "account",
                                     payment_mode="manual", variable_amount=True)
    B.ensure_bill_instance(rec["id"], uid, date.today(), 0)
    return B.list_bills(uid, include_paid=False)[0]


def _paga_luz(valor):
    return com_tools(chamada("mark_bill_paid", {"name": "luz", "amount": valor}))


def test_n5_conta_variavel_amount_inventado_vira_pergunta(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    conta = _conta_variavel(uid_pro)
    cli = openai_falso(monkeypatch, _paga_luz(150))
    r = runner.chat(uid_pro, "paguei a luz", platform="whatsapp")
    _usa_ia(cli)
    assert "Quanto veio" in r, r
    assert lancamentos(uid_pro) == []
    assert db.bills.get_bill(uid_pro, conta["id"])["status"] == "pending"
    assert db.get_pending_action(uid_pro)["action_type"] == "bill_amount_expected"


def _boletos(uid) -> int:
    return len(db.bills.list_bills(uid, include_paid=False))


def test_n8_boleto_daqui_10_dias_nao_grava_10_reais(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 10, "days": 10})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "days": 10})))
    r = runner.chat(uid_pro, "anota um boleto da internet para daqui 10 dias", platform="whatsapp")   # o 10 são dias
    _usa_ia(cli)
    assert RECUSA in r and _boletos(uid_pro) == 0, r
    runner.chat(uid_pro, "boleto da internet de 150 para daqui 10 dias", platform="whatsapp")          # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n9b_boleto_para_data_iso_nao_grava_o_ano(uid_pro, monkeypatch):   # Codex: o 2026 de "2026-10-20"
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 2026, "due_date": "20/10"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "due_date": "20/10"})))
    r = runner.chat(uid_pro, "anota o boleto da internet para 2026-10-20", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _boletos(uid_pro) == 0, r
    runner.chat(uid_pro, "boleto da internet de 150 para 2026-10-20", platform="whatsapp")   # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n9_boleto_para_12_de_outubro_nao_grava_12_reais(uid_pro, monkeypatch):   # P1 do Codex: o 12 é o dia
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 12, "due_date": "12/10"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "due_date": "12/10"})))
    r = runner.chat(uid_pro, "anota o boleto da internet para 12 de outubro", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _boletos(uid_pro) == 0, r
    runner.chat(uid_pro, "boleto de 150 para 12 de outubro", platform="whatsapp")   # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n9c_boleto_para_15_out_2026_sem_de_nao_grava_dia_nem_ano(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 2026, "due_date": "15/10"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 15, "due_date": "15/10"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "due_date": "15/10"})))
    r = runner.chat(uid_pro, "anota o boleto da internet para 15 outubro 2026", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _boletos(uid_pro) == 0, r
    runner.chat(uid_pro, "anota o boleto da internet para 15 outubro 2026", platform="whatsapp")   # o 15 também recusa
    assert _boletos(uid_pro) == 0
    runner.chat(uid_pro, "boleto de 150 para 15 out 2026", platform="whatsapp")   # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n10_boleto_vencimento_dia_dois_pontos_nao_grava_o_dia(uid_pro, monkeypatch):   # o 18 de "dia: 18" é o dia
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 18, "due_date": "18"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "due_date": "18"})))
    r = runner.chat(uid_pro, "anota o boleto da internet, vencimento dia: 18", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _boletos(uid_pro) == 0, r
    runner.chat(uid_pro, "boleto de 150, vencimento dia: 18", platform="whatsapp")   # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n11_cinquenta_centavos_colado_grava_meio_real_e_nao_50(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50), lancamento(0.5))
    r = runner.chat(uid_pro, "gastei 50centavos", platform="dashboard")   # R$ 50 inventado: recusa
    assert RECUSA in r and lancamentos(uid_pro) == [], r
    runner.chat(uid_pro, "gastei 50centavos", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 0.5}]


# ── (3) P: o caminho legítimo continua gravando ─────────────────────────────

def test_p1_gastei_50_no_mercado(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(50))
    diga(uid_pro, "piggy gastei 50 no mercado")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}]


def test_p2_app_com_banco_pergunta_a_forma_e_dinheiro_grava(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    _connect_fake_bank(uid_pro)
    openai_falso(monkeypatch, lancamento(500), lancamento(500, forma_pagamento="dinheiro"))
    r = runner.chat(uid_pro, "gastei 500", platform="dashboard")
    assert "dinheiro vivo" in r and lancamentos(uid_pro) == [], r   # _PERGUNTE_A_FORMA: nada gravado
    runner.chat(uid_pro, "dinheiro", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 500.0}]


@pytest.mark.parametrize("frase,valor", [("1,2 mil de aluguel", 1200), ("gastei 2 mil", 2000)])
def test_p3_multiplicador_em_digito(uid_pro, monkeypatch, frase, valor):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(valor))
    runner.chat(uid_pro, frase, platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": float(valor)}]


def test_p6_n_centavos_grava_meio_real_e_nao_50_reais(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(0.5), lancamento(50), lancamento(0.5))
    runner.chat(uid_pro, "gastei 50 centavos", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 0.5}]
    r = runner.chat(uid_pro, "gastei 50 centavos", platform="dashboard")   # R$ 50 inventado: recusa
    assert RECUSA in r and lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 0.5}], r


def test_p12_ponto_decimal_grava_49_90_e_nao_4990(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(4990), lancamento(49.9))
    r = runner.chat(uid_pro, "gastei 49.90", platform="dashboard")   # R$ 4.990 inventado: recusa
    assert RECUSA in r and lancamentos(uid_pro) == [], r
    runner.chat(uid_pro, "gastei 49.90", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 49.9}]


def test_p13_orcamento_de_milhoes(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, com_tools(chamada("set_budget", {"categoria": "Lazer", "budget": 2000000})),
                 com_tools(chamada("set_budget", {"categoria": "Lazer", "budget": 2000000})))
    r = runner.chat(uid_pro, "meu orçamento de lazer é dois milhões", platform="dashboard")   # extenso: recusa
    assert RECUSA in r and db.get_budget(uid_pro, "Lazer") is None, r
    runner.chat(uid_pro, "meu orçamento de lazer é 2 milhões", platform="dashboard")          # dígito: grava
    assert db.get_budget(uid_pro, "Lazer")["budget"] == 2000000


def test_p8_cinquenta_centavos_por_extenso_nao_ancora(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(0.5), lancamento(50))
    for _ in range(2):   # nem 0,50 nem R$ 50: o extenso não é lido
        r = runner.chat(uid_pro, "gastei cinquenta centavos", platform="dashboard")
        assert RECUSA in r and lancamentos(uid_pro) == [], r


def test_p9_artigo_um_nao_ancora_valor_1(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(1))   # "uma tv": o 1 é inventado
    r = runner.chat(uid_pro, "comprei uma tv no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r


def test_p10_duas_tvs_nao_ancora_valor_2(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(2))   # "duas tvs": o 2 é contagem, inventado como valor
    r = runner.chat(uid_pro, "comprei duas tvs no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r


def test_p11_reais_e_centavos(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(30.5), lancamento(30.5))
    r = runner.chat(uid_pro, "paguei trinta reais e cinquenta centavos", platform="dashboard")   # extenso: recusa
    assert RECUSA in r and lancamentos(uid_pro) == [], r
    runner.chat(uid_pro, "paguei 30 reais e 50 centavos", platform="dashboard")                  # dígito: grava o total
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.5}]


def test_p11b_reais_e_centavos_modelo_que_derruba_os_centavos_recusa(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, lancamento(30), lancamento(30.5))
    r = runner.chat(uid_pro, "paguei 30 reais e 50 centavos no mercado", platform="dashboard")   # valor=30: recusa
    assert RECUSA in r and lancamentos(uid_pro) == [], r
    runner.chat(uid_pro, "paguei 30 reais e 50 centavos no mercado", platform="dashboard")        # 30,5: grava
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.5}]


def _mercado_e_uber():
    return com_tools(chamada("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"}, "c1"),
                     chamada("add_launch", {"tipo": "despesa", "valor": 20, "alvo": "uber"}, "c2"))


def test_p7_dois_valores_na_mesma_frase_gravam_os_dois_e_a_soma_nao(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _mercado_e_uber(), _mercado_e_uber())
    r = runner.chat(uid_pro, "gastei cinquenta no mercado e vinte no uber", platform="dashboard")   # extenso: recusa
    assert RECUSA in r and lancamentos(uid_pro) == [], r
    runner.chat(uid_pro, "gastei 50 no mercado e 20 no uber", platform="dashboard")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 50.0}, {"tipo": "despesa", "valor": 20.0}]
    assert _ancora(uid_pro, "add_launch", {"valor": 70}, "gastei 50 no mercado e 20 no uber")[1] == "recusa"


def test_p4_cartao_12x_de_50_grava(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    openai_falso(monkeypatch, _compra_cartao(600, parcelas=12))
    runner.chat(uid_pro, "geladeira 12x de 50 no nubank", platform="whatsapp")
    assert _credito(uid_pro) == 12


# ── contagem de parcelas: o valor está no texto, a contagem tem que estar também ──

def _cartao_diz(uid, monkeypatch, fala, valor, parcelas=None):
    desliga_flag(monkeypatch)
    db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(valor, parcelas=parcelas))
    r = runner.chat(uid, fala, platform="whatsapp")
    _usa_ia(cli)
    return r


def test_q1_valor_no_texto_mas_parcelas_inventadas_recusa(uid_pro, monkeypatch):
    r = _cartao_diz(uid_pro, monkeypatch, "comprei 50 no nubank", 50, parcelas=60)
    assert RECUSA_PARCELAS in r and RECUSA not in r and _credito(uid_pro) == 0, r
    _historico_sem(uid_pro, 60)  # o `tool` do histórico não repete a contagem inventada


def test_q1b_recusa_de_parcelas_com_outra_chamada_que_gravou_anexa_o_parcial(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    openai_falso(monkeypatch, com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 30, "alvo": "uber"}, "c1"),
        chamada("add_credit_purchase", {"valor": 50, "parcelas": 60, "card_name": "Nubank", "descricao": "tv"}, "c2")))
    r = runner.chat(uid_pro, "gastei 30 no uber e comprei 50 no nubank", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.0}] and _credito(uid_pro) == 0
    assert "Não anotei um dos itens: em quantas parcelas foi? Ex.: 12x." in r, r
    assert "não achei o valor" not in r and RECUSA_PARCELAS not in r, r


def test_q1c_valor_inventado_com_outra_chamada_que_gravou_anexa_o_parcial_de_valor(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    openai_falso(monkeypatch, com_tools(
        chamada("add_launch", {"tipo": "despesa", "valor": 30, "alvo": "uber"}, "c1"),
        chamada("add_credit_purchase", {"valor": 77, "card_name": "Nubank", "descricao": "tv"}, "c2")))
    r = runner.chat(uid_pro, "gastei 30 no uber e comprei uma tv no nubank", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 30.0}] and _credito(uid_pro) == 0
    assert "Não anotei um dos itens: não achei o valor dele" in r and "parcelas" not in r, r


def test_q2_sem_parcelas_grava_uma(uid_pro, monkeypatch):
    _cartao_diz(uid_pro, monkeypatch, "comprei 50 no nubank", 50)
    assert _credito(uid_pro) == 1


def test_q3_12x_de_50_grava_12(uid_pro, monkeypatch):
    _cartao_diz(uid_pro, monkeypatch, "geladeira 12x de 50 no nubank", 600, parcelas=12)
    assert _credito(uid_pro) == 12


def test_q4_total_derivado_de_3x_de_100_grava_3(uid_pro, monkeypatch):
    _cartao_diz(uid_pro, monkeypatch, "comprei em 3x de 100 no nubank", 300, parcelas=3)
    assert _credito(uid_pro) == 3


def test_q4b_valor_da_parcela_como_total_recusa_e_nao_grava(uid_pro, monkeypatch):  # P1 do Codex: 12 × 50 viraria R$ 50
    r = _cartao_diz(uid_pro, monkeypatch, "geladeira 12x de 50 no nubank", 50, parcelas=12)
    assert RECUSA in r and _credito(uid_pro) == 0, r   # texto de "valor": a contagem está escrita, o total não


@pytest.mark.parametrize("fala,valor,parcelas,ancora", [
    ("12x de 50", 50, 12, False),            # 50 é a parcela
    ("12x de 50", 600, 12, True),            # 12 × 50
    ("12 x de R$ 50", 50, 12, False),
    ("12 vezes de 50", 50, 12, False),
    ("em 12x de 50,90", 50.9, 12, False),
    ("em 12x de 50,90", 610.8, 12, True),
    ("12x de R$ 1.234,56", 1234.56, 12, False),
    ("10x de 2 mil", 2000, 10, False),
    ("10x de 2 mil", 20000, 10, True),
    ("comprei 600 em 12x", 600, 12, True),   # total explícito
    ("600 em 12x de 50", 600, 12, True),     # total e parcela juntos: os dois ancoram
    ("600 em 12x de 50", 50, 12, False),
    ("12x de 50 total 600", 600, 12, True),
    ("12x de 50", 50, 1, True),              # sem parcelas>1 não há o que desfazer
    ("12x de 50", 50, None, True),
    ("geladeira 12x R$ 50", 50, 12, False),  # P1 do Codex: sem "de" também é parcela
    ("geladeira 12x R$ 50", 600, 12, True),
    ("12 parcelas a 50", 50, 12, False),
    ("12 parcelas a 50", 600, 12, True),
    ("12x de R$50", 600, 12, True),
    ("12x 50", 50, 12, True),                # solto (sem de/a/R$) é ambíguo: segue como total
    ("12x 50", 600, 12, False),
])
def test_q8_valor_de_parcela_nao_ancora_o_total(uid_pro, fala, valor, parcelas, ancora):
    args = {"valor": valor} | ({"parcelas": parcelas} if parcelas else {})
    assert (_ancora(uid_pro, "add_credit_purchase", args, fala)[1] is None) is ancora


def test_q8b_tool_sem_campo_parcelas_nao_e_afetada(uid_pro):
    assert _ancora(uid_pro, "add_launch", {"valor": 50, "parcelas": 12}, "12x de 50")[1] is None


# Classe "valor por parcela × total": `valor` é o TOTAL. Só `parcelas × valor-de-parcela escrito em DÍGITO` o deriva;
# a contagem pode ser por extenso, o valor não (extenso não ancora: recusa os dois). (ancora = a âncora aceita o `valor`.)
@pytest.mark.parametrize("fala,valor,parcelas,ancora", [
    ("comprei por 50 em 12x", 50, 12, True),        # o 50 era o total
    ("comprei por 50 em 12x", 600, 12, False),      # 12 × 50 inventado: 50 não é valor de parcela
    ("parcelei 50 em 12", 600, 12, False),
    ("comprei 600 em 12x", 600, 12, True),
    ("600 em 12x de 50", 600, 12, True),
    ("600 em 12x de 50", 50, 12, False),
    ("doze vezes de cinquenta", 600, 12, False),    # valor por extenso: nada em dígito que derive
    ("doze vezes de cinquenta", 50, 12, False),
    ("comprei tv em doze vezes de 50", 50, 12, False),   # era R1 caso 4 (aceito); agora recusado
    ("comprei tv em doze vezes de 50", 600, 12, True),
    ("12 vezes de cinquenta", 600, 12, False),      # contagem em dígito, valor por extenso
    ("12 vezes de cinquenta", 50, 12, False),
    ("doze vezes de 50", 600, 12, True),            # contagem por extenso, valor em dígito
    ("em doze parcelas de cinquenta e cinco", 660, 12, False),
    ("em doze parcelas de cinquenta e cinco", 55, 12, False),
    ("10x de dois mil", 20000, 10, False),
    ("10x de dois mil", 2000, 10, False),
    ("dez vezes de dois milhões", 20000000, 10, False),
    ("dez vezes de dois milhões", 2000000, 10, False),
    ("12x de 50.", 600, 12, True),                  # ponto final de frase
    ("12x de 50.", 50, 12, False),
    ("12x de 50 reais", 50, 12, False),
    # contagem nua ("em 12") não é R$ 12; o total escrito ao lado segue ancorando
    ("comprei a TV em 12", 12, 12, False),
    ("comprei a TV de 1200 em 12", 12, 12, False),
    ("comprei a TV de 1200 em 12", 1200, 12, True),
    ("parcelei 600 em 12", 600, 12, True),
    ("comprei 12 em 12", 12, 12, True),             # o 1º 12 é o total; só o "em 12" é contagem
    ("comprei a TV em 12,50", 12.5, 12, True),      # "em 12,50" é valor, não contagem
    # valor de parcela em centavos: "12x de 50 centavos" são 12 × R$ 0,50 = R$ 6
    ("12x de 50 centavos", 6, 12, True),
    ("12x de 50 centavos", 600, 12, False),
    ("12x de 50 centavos", 0.5, 12, False),
    # reais e centavos na parcela: "12x de 30 reais e 50 centavos" = 12 × 30,50 = 366; o 30 e o 50 soltos não são a parcela
    ("12x de 30 reais e 50 centavos", 366, 12, True),
    ("12x de 30 reais e 50 centavos", 360, 12, False),
    ("12x de 30 reais e 50 centavos", 600, 12, False),
    ("12x de 30 reais e 50 centavos", 30.5, 1, True),    # sem parcelas, o valor lido ancora como antes
    ("12x de 30 reais e 50 centavos", 30.5, 12, False),
    ("12x de 30,50", 366, 12, True),
    ("12x de 30,50", 30.5, 12, False),
    # "em 12/10" é data, não contagem; contagem/duração/extenso seguem valendo
    ("comprei a TV por 600 em 12/10", 600, 12, False),
    ("comprei a TV por 600 em 12/10", 600, 1, True),
    ("comprei a TV por 600 dia 12/10 em 3x", 600, 3, True),
    ("comprei a TV por 600 em 12-10", 600, 12, False),
    ("comprei a TV por 600 em 12 de outubro", 600, 12, False),
    ("comprei a TV por 600 em 12x", 600, 12, True),
    ("em 12 vezes comprei a TV por 600", 600, 12, True),
    ("comprei a TV por 600 em doze vezes", 600, 12, True),
    ("em 12 meses comprei a TV por 600", 600, 12, True),
    # a contagem nua sai sem deixar "600 vezes/meses/parcelas" colado (quantidade, que o 600 não ancoraria)
    ("comprei a TV por 600 em 12 vezes", 600, 12, True),
    ("comprei a TV por 600 em 12 vezes", 12, 12, False),
    ("comprei por 600 em 12 meses", 600, 12, True),
    ("comprei por 600 em 12 parcelas", 600, 12, True),
    ("TV de 1200 em 12", 1200, 12, True),
    ("TV de 1200 em 12", 12, 12, False),
    ("comprei por 50 em 12x", 50, 12, True),
    ("comprei por 50 em 12x", 600, 12, False),
    # centavos colado e "1 real e N centavos" na parcela
    ("12x de 50centavos", 6, 12, True),
    ("12x de 50centavos", 600, 12, False),
    ("12x de 1 real e 50 centavos", 18, 12, True),
    ("12x de 1 real e 50 centavos", 600, 12, False),
    # "prestação(ões)" é unidade de parcela, com ou sem acento
    ("comprei a geladeira em 12 prestações de R$ 50", 50, 12, False),
    ("comprei a geladeira em 12 prestações de R$ 50", 600, 12, True),
    ("em 12 prestacoes a 50", 600, 12, True),
    ("em 12 prestações R$ 50", 600, 12, True),
    ("em 12 prestações R$ 50", 50, 12, False),
    ("em 1 prestação de 50", 50, 1, True),
    # duração depois de "em" não é contagem de parcelas ("meses" segue sendo)
    ("comprei a TV por 600, entrega em 12 dias", 600, 12, False),
    ("comprei a TV por 600, garantia em 12 semanas", 600, 12, False),
    ("comprei a TV por 600, entrega em 12 horas", 600, 12, False),
    ("comprei a TV por 600, garantia de 12 anos, em 12 anos", 600, 12, False),
    ("comprei a TV por 600, entrega em 12 dias", 600, 1, True),
    ("comprei a TV por 600 em 12 meses, entrega em 3 dias", 600, 12, True),
    ("comprei a TV por 600 em 12x, entrega em 12 dias", 600, 12, True),
    ("comprei a TV por 600, garantia em 12 meses", 600, 12, True),   # LIMITE conhecido (ponytail): "meses" é parcelamento legítimo
    # valor de parcela rotulado ("no valor de ... cada"): o 50 é a parcela, o total é 10 × 50
    ("comprei em 10 parcelas no valor de R$ 50 cada", 50, 10, False),
    ("comprei em 10 parcelas no valor de R$ 50 cada", 500, 10, True),
    ("10 vezes, R$ 50 cada", 50, 10, False),
    ("10 vezes, R$ 50 cada", 500, 10, True),
    ("10 parcelas, 50 cada", 50, 10, False),
    ("10 parcelas, 50 cada", 500, 10, True),
    ("10 vezes, 50 reais cada", 50, 10, False),
    ("10 vezes, 50 reais cada", 500, 10, True),
    ("10x de R$ 50 cada", 50, 10, False),
    ("10x de R$ 50 cada", 500, 10, True),
    ("10 parcelas com valor de 50", 50, 10, False),
    ("10 parcelas com valor de 50", 500, 10, True),
    ("10 parcelas valor: 50", 500, 10, True),
    ("10 parcelas, 2 mil cada", 20000, 10, True),
    ("10 parcelas, 2 mil cada", 2000, 10, False),
    ("10 parcelas, 30 reais e 50 centavos cada", 305, 10, True),
    ("10 parcelas, 30 reais e 50 centavos cada", 30.5, 10, False),
    # sem rótulo continua como era: total
    ("50 em 10 vezes", 50, 10, True),
    ("50 em 10 vezes", 500, 10, False),
    ("comprei por 600 em 12 vezes, entrega amanhã", 600, 12, True),
])
def test_q9_valor_de_parcela_so_deriva_o_total(uid_pro, fala, valor, parcelas, ancora):
    args = {"valor": valor, "parcelas": parcelas}
    assert (_ancora(uid_pro, "add_credit_purchase", args, fala)[1] is None) is ancora


# Cada "Nx de V" só deriva o total com a SUA contagem: dois produtos (10x de 50, 12x de 60) não cruzam (60 × 10 = 600 nunca foi dito).
_DOIS = "TV 10x de 50 e fogão 12x de 60"


@pytest.mark.parametrize("fala,valor,parcelas,ancora", [
    (_DOIS, 500, 10, True), (_DOIS, 720, 12, True),
    (_DOIS, 600, 10, False), (_DOIS, 600, 12, False), (_DOIS, 500, 12, False), (_DOIS, 720, 10, False),
    (_DOIS, 50, 10, False), (_DOIS, 60, 12, False),
    ("12x de 50", 600, 12, True),                                # regressão
    ("doze vezes de 50", 600, 12, True),                         # contagem por extenso
    ("vinte e quatro vezes de 50", 1200, 24, True),
    ("TV dez vezes de 50 e fogão doze vezes de 60", 720, 12, True),
    ("TV dez vezes de 50 e fogão doze vezes de 60", 600, 10, False),
    ("12x de 30 reais e 50 centavos", 366, 12, True),
    ("2 TVs: 10x de 50 e 10x de 60", 500, 10, True),             # mesma contagem, valores diferentes: os dois gravam
    ("2 TVs: 10x de 50 e 10x de 60", 600, 10, True),
    ("2 TVs: 10x de 50 e 10x de 60", 550, 10, False),
])
def test_q20_cada_captura_so_multiplica_a_propria_contagem(uid_pro, fala, valor, parcelas, ancora):
    args = {"valor": valor, "parcelas": parcelas}
    assert (_ancora(uid_pro, "add_credit_purchase", args, fala)[1] is None) is ancora


@pytest.mark.parametrize("valor,parcelas,ancora", [(500, 10, True), (720, 12, True), (600, 10, False), (600, 12, False), (500, 12, False)])
def test_q20b_mensagens_separadas_mesma_regra(uid_pro, valor, parcelas, ancora):
    db.ai_append_message(uid_pro, "user", "TV 10x de 50")
    db.ai_append_message(uid_pro, "user", "fogão 12x de 60")
    r = _ancora(uid_pro, "add_credit_purchase", {"valor": valor, "parcelas": parcelas}, "no nubank")[1]
    assert (r is None) is ancora


def test_q20c_conversa_total_cruzado_nao_grava(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(600, parcelas=10))
    r = runner.chat(uid_pro, _DOIS + " no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r


@pytest.mark.parametrize("fala,inventado,escrito", [
    ("comprei por 50 em 12x no nubank", 600, 50),          # 50 é o total
    ("geladeira doze vezes de 50 no nubank", 50, 600),   # 50 é a parcela: o total é 12 × 50 (contagem por extenso, valor em dígito)
])
def test_q9b_conversa_total_ou_parcela(uid_pro, monkeypatch, fala, inventado, escrito):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(inventado, parcelas=12), _compra_cartao(escrito, parcelas=12))
    r = runner.chat(uid_pro, fala, platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r
    db.ai_append_message(uid_pro, "system", runner._ATENDIDA_FORA)   # a janela da conversa não ancora o 2º envio
    runner.chat(uid_pro, fala, platform="whatsapp")
    assert _credito(uid_pro) == 12


@pytest.mark.parametrize("fala,inventado,escrito", [
    ("comprei a TV de 1200 em 12 no nubank", 12, 1200),   # o 12 é a contagem, não R$ 12
    ("compra de 12x de 50 centavos no nubank", 600, 6),   # 50 centavos × 12 = R$ 6, não R$ 600
    ("compra de 12x de 30 reais e 50 centavos no nubank", 360, 366),   # 12 × 30,50 = 366, não 12 × 30
])
def test_q10_conversa_contagem_nua_e_parcela_em_centavos(uid_pro, monkeypatch, fala, inventado, escrito):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(inventado, parcelas=12), _compra_cartao(escrito, parcelas=12))
    r = runner.chat(uid_pro, fala, platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r
    db.ai_append_message(uid_pro, "system", runner._ATENDIDA_FORA)
    runner.chat(uid_pro, fala, platform="whatsapp")
    assert _credito(uid_pro) == 12


def test_q12_conversa_prestacoes(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    fala = "comprei a geladeira em 12 prestações de R$ 50 no nubank"
    cli = openai_falso(monkeypatch, _compra_cartao(50, parcelas=12), _compra_cartao(600, parcelas=12))
    r = runner.chat(uid_pro, fala, platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r   # 50 é a parcela, não o total
    db.ai_append_message(uid_pro, "system", runner._ATENDIDA_FORA)
    runner.chat(uid_pro, fala, platform="whatsapp")
    assert _credito(uid_pro) == 12


def test_q10b_conversa_total_antes_de_em_12_vezes(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    cli = openai_falso(monkeypatch, _compra_cartao(600, parcelas=12))
    runner.chat(uid_pro, "comprei a TV por 600 em 12 vezes no nubank", platform="whatsapp")
    _usa_ia(cli)
    assert _credito(uid_pro) == 12


def test_q13_conversa_duracao_nao_e_contagem(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    fala = "comprei a TV por 600, entrega em 12 dias no nubank"
    cli = openai_falso(monkeypatch, _compra_cartao(600, parcelas=12), _compra_cartao(600))
    r = runner.chat(uid_pro, fala, platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA_PARCELAS in r and _credito(uid_pro) == 0, r   # o 12 é a entrega: parcelas inventadas
    db.ai_append_message(uid_pro, "system", runner._ATENDIDA_FORA)
    runner.chat(uid_pro, fala, platform="whatsapp")
    assert _credito(uid_pro) == 1


def test_q13b_conversa_em_12_meses_grava_12(uid_pro, monkeypatch):
    _cartao_diz(uid_pro, monkeypatch, "comprei a TV por 600 em 12 meses no nubank", 600, parcelas=12)
    assert _credito(uid_pro) == 12


def test_q14_conversa_parcela_rotulada_cada(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    fala = "comprei a TV em 10 parcelas no valor de R$ 50 cada no nubank"
    cli = openai_falso(monkeypatch, _compra_cartao(50, parcelas=10), _compra_cartao(500, parcelas=10))
    r = runner.chat(uid_pro, fala, platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA in r and _credito(uid_pro) == 0, r   # 50 é a parcela, não o total
    db.ai_append_message(uid_pro, "system", runner._ATENDIDA_FORA)
    runner.chat(uid_pro, fala, platform="whatsapp")
    assert _credito(uid_pro) == 10


def test_q11_conversa_data_nao_e_contagem(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    db.create_card(uid_pro, "Nubank", closing_day=10, due_day=17)
    fala = "comprei a TV por 600 em 12/10 no nubank"
    cli = openai_falso(monkeypatch, _compra_cartao(600, parcelas=12), _compra_cartao(600))
    r = runner.chat(uid_pro, fala, platform="whatsapp")
    _usa_ia(cli)
    assert RECUSA_PARCELAS in r and _credito(uid_pro) == 0, r   # o 12 é o dia: parcelas inventadas
    db.ai_append_message(uid_pro, "system", runner._ATENDIDA_FORA)
    runner.chat(uid_pro, fala, platform="whatsapp")
    assert _credito(uid_pro) == 1                                # sem parcelas, o 600 grava


def test_q5_contagem_diferente_da_escrita_recusa(uid_pro, monkeypatch):
    r = _cartao_diz(uid_pro, monkeypatch, "comprei 50 em 12x no nubank", 50, parcelas=6)
    assert RECUSA_PARCELAS in r and _credito(uid_pro) == 0, r


@pytest.mark.parametrize("lixo", [float("inf"), float("nan"), "abc"])
def test_q6_parcelas_lixo_no_cartao_nao_estoura(uid_pro, monkeypatch, lixo):
    r = _cartao_diz(uid_pro, monkeypatch, "comprei 50 no nubank", 50, parcelas=lixo)
    assert RECUSA not in r  # lido como 1 pela guarda; quem recusa o lixo é o execute


@pytest.mark.parametrize("valor,parcelas,fala", [
    (600, 12, "parcelei 600 em 12"),
    (1200, 10, "tv 1200 parcelado em 10"),
    (600, 12, "comprei tv em doze vezes de 50"),   # (50 aqui era aceito; o limite caiu: ver test_q9)
])
def test_r1_contagem_escrita_sem_unidade_ou_por_extenso_ancora(uid_pro, valor, parcelas, fala):  # B1 do Manager
    assert _ancora(uid_pro, "add_credit_purchase", {"valor": valor, "parcelas": parcelas}, fala)[1] is None


def test_r2_contagem_nao_escrita_continua_recusada(uid_pro):
    a = lambda args, fala: _ancora(uid_pro, "add_credit_purchase", args, fala)[1]
    assert a({"valor": 3000, "parcelas": 60}, "comprei 50 no nubank") == "recusa_parcelas"
    assert a({"valor": 600, "parcelas": 12}, "parcelei 600 em 6") == "recusa_parcelas"
    assert a({"valor": 600, "parcelas": 12}, "comprei tv em dez vezes de 60") == "recusa_parcelas"


def test_q7_regra_das_parcelas_no_unitario(uid_pro):
    a = lambda args, fala: _ancora(uid_pro, "add_credit_purchase", args, fala)[1]
    assert a({"valor": 50, "parcelas": 60}, "comprei 50") == "recusa_parcelas"
    assert a({"valor": 50, "parcelas": 1}, "comprei 50 à vista") is None
    assert a({"valor": 50, "parcelas": 12.0}, "comprei 50 em 12x") is None   # float é 12
    assert a({"valor": 50, "parcelas": 6}, "comprei 50 em 12x") == "recusa_parcelas"
    assert a({"valor": 600, "parcelas": 12}, "12 parcelas de 50") is None
    # tool sem `parcelas` no schema ignora o campo, então não recusa por ele
    assert _ancora(uid_pro, "add_launch", {"valor": 50, "parcelas": 6}, "gastei 50")[1] is None


def _conta_fixa(uid):
    from datetime import date
    import db.bills as B
    import db.recurring as R
    rec = R.create_recurring_expense(uid, "Luz", 120, "conta", 10, "account", payment_mode="manual")
    B.ensure_bill_instance(rec["id"], uid, date.today(), 120)
    return B.list_bills(uid, include_paid=False)[0]


def test_n12_conta_paga_valor_escrito_diverge_recusa(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    conta = _conta_fixa(uid_pro)
    cli = openai_falso(monkeypatch, _paga_luz(500))
    r = runner.chat(uid_pro, "paguei 50 da luz", platform="whatsapp")   # escreveu 50, o modelo mandou 500
    _usa_ia(cli)
    assert RECUSA in r and lancamentos(uid_pro) == [], r
    assert db.bills.get_bill(uid_pro, conta["id"])["status"] == "pending"


def test_n12b_conta_paga_sem_valor_escrito_descarta_o_do_modelo_e_paga_o_cadastrado(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    conta = _conta_fixa(uid_pro)
    openai_falso(monkeypatch, _paga_luz(500))
    runner.chat(uid_pro, "paguei a luz", platform="whatsapp")   # nenhum valor escrito: o 500 é descartado
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 120.0}]
    assert db.bills.get_bill(uid_pro, conta["id"])["status"] == "paid"


def test_n13_boleto_as_18h30_nao_grava_hora_como_valor(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 30, "due_date": "20/10"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 18, "due_date": "20/10"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "due_date": "20/10"})))
    for _ in range(2):
        r = runner.chat(uid_pro, "anota o boleto da internet para amanhã às 18h30", platform="whatsapp")
        assert RECUSA in r and _boletos(uid_pro) == 0, r
    _usa_ia(cli)
    runner.chat(uid_pro, "boleto da internet de 150 às 18h30", platform="whatsapp")   # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n14_boleto_para_o_dia_1o_nao_grava_o_dia_como_valor(uid_pro, monkeypatch):   # P1 do Codex
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 30, "due_date": "01/11"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 1, "due_date": "01/11"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "due_date": "01/11"})))
    for _ in range(2):
        r = runner.chat(uid_pro, "anota o boleto da internet para o dia 1º", platform="whatsapp")
        assert RECUSA in r and _boletos(uid_pro) == 0, r
    _usa_ia(cli)
    runner.chat(uid_pro, "boleto de 150 para o dia 1º", platform="whatsapp")   # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n15_boleto_as_18_sem_h_nao_grava_hora_como_valor(uid_pro, monkeypatch):   # P1 do Codex
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 18, "due_date": "20/10"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "due_date": "20/10"})))
    r = runner.chat(uid_pro, "anota o boleto da internet para amanhã às 18", platform="whatsapp")
    assert RECUSA in r and _boletos(uid_pro) == 0, r
    _usa_ia(cli)
    runner.chat(uid_pro, "boleto de 150 às 18", platform="whatsapp")   # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n16_boleto_amanha_18hs_nao_grava_hora_como_valor(uid_pro, monkeypatch):   # P1 do Codex
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("add_boleto", {"name": "internet", "amount": 18, "due_date": "20/10"})),
                       com_tools(chamada("add_boleto", {"name": "internet", "amount": 150, "due_date": "20/10"})))
    r = runner.chat(uid_pro, "anota o boleto da internet para amanhã 18hs", platform="whatsapp")
    assert RECUSA in r and _boletos(uid_pro) == 0, r
    _usa_ia(cli)
    runner.chat(uid_pro, "boleto de 150 para amanhã 18hs", platform="whatsapp")   # 150 escrito: grava
    assert _boletos(uid_pro) == 1


def test_n17_dois_multiplicadores_na_mesma_frase_gravam_os_dois(uid_pro, monkeypatch):   # P2 do Codex
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, _duas(2000, 3000), lancamento(30))
    runner.chat(uid_pro, "gastei 2 mil no mercado e 3 mil no uber", platform="whatsapp")
    assert sorted(l["valor"] for l in lancamentos(uid_pro)) == [2000.0, 3000.0]
    runner.chat(uid_pro, "gastei 2 mil no mercado e 3 mil no uber", platform="whatsapp")   # 30 não está escrito: recusa
    assert len(lancamentos(uid_pro)) == 2


def test_p5_conta_variavel_com_valor_dito_paga(uid_pro, monkeypatch):
    desliga_flag(monkeypatch)
    conta = _conta_variavel(uid_pro)
    openai_falso(monkeypatch, _paga_luz(132))
    runner.chat(uid_pro, "paguei a luz, deu 132", platform="whatsapp")
    assert lancamentos(uid_pro) == [{"tipo": "despesa", "valor": 132.0}]
    assert db.bills.get_bill(uid_pro, conta["id"])["status"] == "paid"


# create_meta (auto-executa, mas `confirmar_se=_confirmar_criar`): o alvo que o modelo manda só grava direto se a FRASE
# traz o mesmo valor; senão arma o "Só confirmando" (nada gravado até o "sim"). Codex: "alvo inventado grava direto".
@pytest.mark.parametrize("frase,alvo", [
    ("cria uma meta Viagem", 5000),            # imperativa sem valor: o 5000 é do modelo
    ("cria uma meta Viagem de 3000", 5000),    # valor na frase diverge do alvo
])
def test_meta_alvo_inventado_nao_grava_direto_pergunta(chave, monkeypatch, frase, alvo):
    u = usuario_pagante(plan="pro_max")
    chave(u)
    desliga_flag(monkeypatch)
    cli = openai_falso(monkeypatch, com_tools(chamada("create_meta", {"nome": "Viagem", "tipo": "meta", "alvo": alvo})),
                       texto_ia("🐷 Confirma?"))
    r = runner.chat(u, frase, platform="whatsapp")
    _usa_ia(cli)
    assert metas_gravadas(u) == [], r
    p = db.ai_get_pending_action(u)
    assert p and p["tool_name"] == "create_meta" and "R$ 5.000,00" in p["summary"], (p, r)   # o valor aparece na pergunta


def test_meta_alvo_igual_ao_da_frase_grava_direto(chave, monkeypatch):
    u = usuario_pagante(plan="pro_max")
    chave(u)
    desliga_flag(monkeypatch)
    openai_falso(monkeypatch, com_tools(chamada("create_meta", {"nome": "Viagem", "tipo": "meta", "alvo": 3000})))
    runner.chat(u, "cria uma meta Viagem de 3000", platform="whatsapp")
    assert [m["alvo"] for m in metas_gravadas(u)] == [3000] and db.ai_get_pending_action(u) is None
