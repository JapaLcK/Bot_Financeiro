"""O gerador de leituras para de materializar o produto cartesiano (#323, P1).

`_leituras_da_resposta` gerava toda fatia `tokens[i:fim]` com `i` limitado pelo
allowlist de prefixo e `fim` pela cortesia final. Os dois laços aninhados são o
PRODUTO dos dois: medido neste branch, com `"a " * p + "nubank" + " pf" * c`
(o pior caso por caractere), o gerador antigo dava

    chars    leituras       heap      tempo
      256       2.709      0,6 MB     0,004 s
      756      23.749     12,6 MB     0,062 s
    2.006     167.500    183,4 MB     0,843 s
    4.095     698.368  1.459,5 MB     6,384 s   (RSS de pico 1,8 GB)

4.095 é o limite de uma mensagem do WhatsApp, e `pay_bill_choice` fica armada
com um "quais faturas?" — qualquer usuário alcança isso. REMEDIR antes de
reusar os números (§2): o script está no relato do #323.

O corte é o maior nome de cartão do usuário, EM TOKENS, e não é heurística:
`_card_name_da_resposta` casa por IGUALDADE, então leitura com mais tokens que
o maior nome guardado NUNCA pode casar. Por isso o conjunto de leituras que
casam é idêntico ao de antes — é o que `test_o_limite_nao_muda_nenhum_casamento`
mede, contra a implementação sem limite escrita aqui.

CONTROLE NEGATIVO Y — desligue o limite em `core/handlers/credit.py`
(`for i in range(0, min(podavel, fim - 1) + 1)`). VERMELHOS:

    test_resposta_longa_nao_materializa_o_produto_cartesiano[256]
    test_resposta_longa_nao_materializa_o_produto_cartesiano[756]
    test_resposta_longa_nao_materializa_o_produto_cartesiano[2006]
    test_resposta_longa_nao_materializa_o_produto_cartesiano[4095]
    test_leitura_nunca_tem_mais_tokens_que_o_maior_nome[256]
    test_leitura_nunca_tem_mais_tokens_que_o_maior_nome[4095]

`test_o_limite_nao_muda_nenhum_casamento` segue VERDE com a injeção, e é o
ponto: ele mede EQUIVALÊNCIA, não o limite. Se ele ficasse vermelho, o limite
estaria mudando casamento — que é a hipótese que este PR precisa excluir.

CONTROLE NEGATIVO Z — tire o curto-circuito de zero cartões
(`if not por_nome: return None` em `_card_name_da_resposta`). VERMELHO:

    test_usuario_sem_cartao_nenhum_nao_gera_leitura

O POSITIVO do grupo é o `casaram > 0` dentro do
`test_o_limite_nao_muda_nenhum_casamento`: sem ele a igualdade de conjuntos
seria vazio == vazio e o teste passaria num gerador que devolve nada.

Rodar:  .venv/bin/python -m pytest tests/test_pendencia_credito_leituras_limite.py -q
"""

import string
import time

import pytest

import core.handlers.credit as credit
from _pendencia_credito_helpers import novo_uid
from core.handlers.credit import (_CORTESIA_FINAL, _PODAVEL_NO_PREFIXO,
                                  _card_name_da_resposta,
                                  _leituras_da_resposta)
from db.cards import MAX_CARD_NAME_LEN
from db.connection import get_conn
from utils_text import normalize_text

_TETO = (MAX_CARD_NAME_LEN + 1) // 2   # o mesmo cálculo de `_card_name_da_resposta`


def _sem_limite(alvo: str) -> set[str]:
    """O gerador ANTES do corte, cópia literal do laço. É a referência da
    equivalência — sem ela o teste só afirmaria o que o código faz."""
    tokens = alvo.split()
    sem_cortesia = len(tokens)
    while sem_cortesia > 1 and tokens[sem_cortesia - 1] in _CORTESIA_FINAL:
        sem_cortesia -= 1
    podavel = 0
    while podavel < len(tokens) and tokens[podavel] in _PODAVEL_NO_PREFIXO:
        podavel += 1
    return {" ".join(tokens[i:fim])
            for fim in range(len(tokens), sem_cortesia - 1, -1)
            for i in range(min(podavel, fim - 1) + 1)}


def _pior_caso(chars: int) -> str:
    """Maximiza podáveis × cortesias dentro de `chars`: `a` custa 2 caracteres
    por token e `pf` custa 3, que é o mais barato de cada ponta."""
    melhor, alvo = -1, ""
    for p in range(1, chars // 2):
        c = (chars - 2 * p - 7) // 3
        if c < 1:
            break
        if p * c > melhor:
            melhor, alvo = p * c, "a " * p + "nubank" + " pf" * c
    return alvo


# Respostas reais e ataques das outras suítes de crédito, com os conjuntos de
# cartões em que cada uma discrimina. `Nubank`/`Nubank PF` está aqui porque é
# onde a ordem mais-longa-primeiro decide dinheiro.
_CORPUS = [
    "nubank", "o nubank", "a do nubank", "a fatura do nubank",
    "minha fatura do nubank", "essa do nubank", "quero o nubank",
    "pode ser o nubank", "escolho o nubank", "nubank por favor",
    "nubank obrigado", "nubank pf", "a do nubank pf", "a do nubank por favor",
    "a fatura do nubank por favor", "nubank pf por favor",
    "mercado pago", "o mercado pago", "a do mercado pago",
    "banco do brasil", "a fatura do banco do brasil",
    "a conta", "minha conta", "a minha conta", "vai", "o vai", "quero o vai",
    "vai por favor", "pode", "quero o pode",
    "excluir cartao nubank", "nubank excluir", "quanto gastei no nubank",
    "somei 50 no nubank", "depositei 50 no nubank", "investi 50 no nubank",
    "saquei 50 no nubank", "transferi 50 no nubank", "coloquei 50 no nubank",
    "debitei 50 no cartao nubank", "nubank fatura", "nubank saldo",
    "excluir cartao vai", "vai excluir", "quanto tem na minha conta",
    "obrigado", "1", "setembro",
]
_CONJUNTOS = [
    ("Nubank",), ("Nubank", "Nubank PF"), ("Mercado Pago",),
    ("Banco do Brasil",), ("Conta",), ("Vai",), ("Pode",), ("Minha Conta",),
    ("Nubank", "Mercado Pago", "Banco do Brasil", "Vai", "Minha Conta"),
    ("Cartao Do Banco Do Brasil S A",),  # nome mais longo que a resposta
]


@pytest.mark.parametrize("nomes", _CONJUNTOS, ids=[n[0] for n in _CONJUNTOS])
def test_o_limite_nao_muda_nenhum_casamento(nomes):
    """O que casa, e QUAL casa primeiro, é idêntico com e sem o corte."""
    por_nome = {normalize_text(n): n for n in nomes}
    maior = max(len(n.split()) for n in por_nome)
    casaram = 0
    for resposta in _CORPUS:
        alvo = normalize_text(resposta)
        com = _leituras_da_resposta(alvo, maior)
        sem = sorted(_sem_limite(alvo), key=len, reverse=True)
        assert ({l for l in com if l in por_nome}
                == {l for l in sem if l in por_nome}), (nomes, resposta)
        primeiro = next((l for l in com if l in por_nome), None)
        assert primeiro == next((l for l in sem if l in por_nome), None), \
            (nomes, resposta)
        casaram += primeiro is not None
    # O POSITIVO do grupo: sem isto a igualdade acima é vazio == vazio, e o
    # teste passaria num gerador que devolve lista vazia — pior que o bug.
    # O único conjunto sem resposta no corpus é o do nome mais longo que a
    # resposta, que está aqui justamente por não poder casar nada.
    assert casaram > 0 or nomes == ("Cartao Do Banco Do Brasil S A",), casaram


@pytest.mark.parametrize("chars", [256, 756, 2006, 4095])
def test_resposta_longa_nao_materializa_o_produto_cartesiano(chars):
    """O gerador antigo dava 698.368 leituras e 1,4 GB em 4.095 caracteres."""
    alvo = _pior_caso(chars)
    t0 = time.perf_counter()
    leituras = _leituras_da_resposta(alvo, 3)
    decorrido = time.perf_counter() - t0
    assert len(leituras) <= 4 * (3 + 1), len(leituras)
    assert decorrido < 0.5, decorrido
    assert len(_sem_limite(alvo)) > 2000, "o pior caso parou de ser pior caso"


@pytest.mark.parametrize("chars", [256, 4095])
def test_leitura_nunca_tem_mais_tokens_que_o_maior_nome(chars):
    """A invariante que o corte instala, e a razão de ela não perder cobertura:
    o casamento é por igualdade."""
    for leitura in _leituras_da_resposta(_pior_caso(chars), 2):
        assert len(leitura.split()) <= 2, leitura


def test_usuario_sem_cartao_nenhum_nao_gera_leitura():
    """O limite viraria 0. Curto-circuita antes, com usuário real e sem mock."""
    assert _card_name_da_resposta(novo_uid(), "a do nubank por favor") is None


# ---------------------------------------------------------------------------
# O teto ABSOLUTO (segunda rodada do mesmo P1)
# ---------------------------------------------------------------------------
# O corte pelo maior nome guardado era controlado pelo atacante: `name` é
# `text` sem restrição, então bastava guardar um cartão de mil tokens para o
# produto cartesiano voltar. `validate_card_name` fecha a porta para o dado
# NOVO; estas linhas cobrem o dado VELHO, que validação nenhuma alcança.
#
# Medido neste branch (remedir antes de reusar, §2), nome guardado de 1.000
# tokens e a resposta de pior caso:
#
#     chars   sem teto   memória   tempo      com teto (40)
#     2.506    260.347    341,7 MB  1,578 s    820 leituras / 0,2 MB / 0,003 s
#     4.095    450.097    729,3 MB  3,315 s    820 leituras / 0,2 MB / 0,005 s
#
# CONTROLE NEGATIVO W — tire o `min(..., (MAX_CARD_NAME_LEN + 1) // 2)` de
# `_card_name_da_resposta`. VERMELHOS:
#
#     test_teto_absoluto_vale_mesmo_com_nome_gigante_ja_guardado


def _cartao_de_nome_longo(uid: int, tokens: int) -> str:
    """Linha ANTIGA: escreve direto na tabela, sem passar pela fronteira.

    Tokens de UM caractere porque `unique(user_id, name)` é um btree e recusa
    valor acima de ~2.704 bytes (`ProgramLimitExceeded`) — esse é o teto que o
    banco já tinha, e ele deixa passar 1.352 tokens.
    """
    nome = " ".join(string.ascii_lowercase[i % 26] for i in range(tokens))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into credit_cards (user_id, name, closing_day, due_day) "
                "values (%s, %s, 1, 8)",
                (uid, nome),
            )
        conn.commit()
    return nome


def test_teto_absoluto_vale_mesmo_com_nome_gigante_ja_guardado(monkeypatch):
    """Espiona o `max_tokens` REAL que sai do caminho alterado: sem o teto ele
    é 1.000 (o nome guardado) e a resposta de 4.095 vira 450 mil leituras."""
    uid = novo_uid()
    nome = _cartao_de_nome_longo(uid, 1000)
    assert len(nome.split()) == 1000

    vistos = []
    real = credit._leituras_da_resposta

    def espiao(alvo, max_tokens):
        vistos.append(max_tokens)
        return real(alvo, max_tokens)

    monkeypatch.setattr(credit, "_leituras_da_resposta", espiao)
    t0 = time.perf_counter()
    assert credit._card_name_da_resposta(uid, _pior_caso(4095)) is None
    decorrido = time.perf_counter() - t0

    assert vistos == [_TETO], vistos
    assert decorrido < 0.5, decorrido
    # O pior caso continua sendo pior caso: medido barato (756 caracteres, ~13
    # MB) para não pagar os 729 MB do de 4.095 dentro da suíte.
    assert len(_leituras_da_resposta(_pior_caso(756), 1000)) > 2000


def test_o_teto_em_tokens_cobre_todo_nome_que_a_fronteira_aceita():
    """A coerência entre as duas pernas, enumerada em vez de afirmada: nome
    dentro do limite de CARACTERES nunca passa do teto de TOKENS, porque cada
    token custa ao menos 1 caractere e cada token além do primeiro custa também
    o separador. O mais fatiado que cabe é `a a a ...`."""
    mais_fatiado = ("a " * _TETO).strip()
    assert len(mais_fatiado) <= MAX_CARD_NAME_LEN
    assert len(mais_fatiado.split()) == _TETO
    assert len(mais_fatiado + " a") > MAX_CARD_NAME_LEN   # um token a mais não cabe
