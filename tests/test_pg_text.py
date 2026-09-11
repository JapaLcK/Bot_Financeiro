"""Unidade do `core/pg_text.py` — o que o Postgres não consegue armazenar.

Separado de `test_corpo_json_string_venenosa.py` de propósito: lá é a fronteira
HTTP (o webhook e o `note` do saque respondendo 200/404 em vez de 500), aqui é
o contrato da função. Os dois quebram por motivos diferentes e um não deve
esconder o outro.
"""
import json
import signal

import pytest

import core.pg_text as pg_text
from core.pg_text import limpa_para_pg


@pytest.mark.parametrize("entrada,esperado", [
    ("", ""),
    ("pão à vista 😀", "pão à vista 😀"),
    ("a\x00b", "a�b"),
    ("\x00", "�"),
    # 3 U+FFFD por surrogate: são os 3 bytes do `surrogatepass`. Teto conhecido
    # e cosmético — o que importa é que a string deixa de casar com id real.
    ("a\ud800b", "a���b"),
    ("a\udc00b", "a���b"),
    ("a😀b", "a😀b"),  # par legítimo NÃO é surrogate solitário
    (42, 42),
    (None, None),
    (True, True),
    ([], []),
    ({}, {}),
], ids=["vazio", "acento_emoji", "nul_meio", "so_nul", "surr_alto", "surr_baixo",
        "par_valido", "int", "none", "true", "lista_vazia", "dict_vazio"])
def test_limpa_para_pg_tabela(entrada, esperado):
    assert limpa_para_pg(entrada) == esperado


def test_limpa_para_pg_chave_valor_array_e_aninhamento():
    """Chave, valor, array, aninhamento e passagem de não-string numa asserção
    só — as cinco coisas que a caminhada tem de acertar juntas."""
    assert limpa_para_pg({"a\x00b": ["x\ud800", {"y\x00": "z\x00"}, 1, None]}) == {
        "a�b": ["x���", {"y�": "z�"}, 1, None],
    }


def test_limpa_para_pg_e_iterativo():
    """A regressão mais provável do PR: 5000 níveis estouram o limite de frames
    do Python (1000) e uma caminhada recursiva morre aqui. 5000 e não 20000
    porque o próprio `json.loads` levanta RecursionError em 20000 (teto
    pré-existente do scanner, medido). A comparação desce por índice: o `==` de
    listas aninhadas também recursaria.
    """
    n = 5_000
    fundo = json.loads("[" * n + '"a\\u0000b"' + "]" * n)
    saneado = limpa_para_pg(fundo)
    for _ in range(n):
        saneado = saneado[0]
    assert saneado == "a�b"


def test_limpa_para_pg_muta_no_lugar_e_devolve_a_mesma_raiz():
    """Contrato usado pelo webhook: `event = limpa_para_pg(json.loads(...))`
    funciona tanto por retorno quanto por mutação. Se um dia virar cópia, o
    chamador que ignorar o retorno passa a gravar veneno em silêncio."""
    corpo = {"k": "a\x00b"}
    assert limpa_para_pg(corpo) is corpo
    assert corpo == {"k": "a�b"}


# Mesma guarda do irmão `tests/test_pool_async_entre_loops.py`: `signal.SIGALRM`
# é POSIX-only e `docs/readme.md:18` lista Windows como suportado. A COLETA não
# quebra (o uso está no corpo, não no import); a HIPÓTESE é que lá o caso daria
# `AttributeError` em vez de rodar — não medido, não há Windows aqui. No CI
# (ubuntu) e no macOS o skip nunca dispara.
@pytest.mark.skipif(
    not hasattr(signal, "SIGALRM"), reason="SIGALRM é POSIX-only (não existe no Windows)"
)
def test_limpa_para_pg_termina_em_estrutura_ciclica():
    """Sem o memo de visitados isto NÃO fica vermelho: fica PENDURADO — a pilha
    nunca esvazia (CPU pura, não OOM; num worker FastAPI é uma thread perdida em
    definitivo). O alarme existe só para transformar o travamento em vermelho,
    porque suíte pendurada não avisa ninguém.

    `json.loads` não produz ciclo, então não é bug vivo hoje: é o contrato do
    arquivo, que se vende como a fonte única de "o que o Postgres aceita".
    """
    def _estourou(*_):
        raise AssertionError("limpa_para_pg não retornou: laço infinito")

    a = {}
    a["eu"] = a
    a["v"] = "p\x00q"
    anterior = signal.signal(signal.SIGALRM, _estourou)
    signal.setitimer(signal.ITIMER_REAL, 5)
    try:
        assert limpa_para_pg(a) is a
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, anterior)
    assert a["v"] == "p\ufffdq"
    assert a["eu"] is a


def test_limpa_para_pg_nao_reprocessa_o_mesmo_no(monkeypatch):
    """O irmão do ciclo: o mesmo objeto alcançável por dois caminhos. Sem memo
    são 2^n visitas — MEDIDO na árvore sem ele: 18 níveis 0,176s, 22 níveis
    2,871s, 24 níveis 10,949s.

    Conta chamadas em vez de cronometrar: relógio em máquina carregada vira
    flake, e a contagem é a grandeza que de fato explode (38 → 786.430, medido
    com o memo removido).
    """
    chamadas = []
    real = pg_text._limpa_str
    monkeypatch.setattr(pg_text, "_limpa_str", lambda s: chamadas.append(s) or real(s))

    no = "p\x00q"
    for _ in range(18):
        no = {"a": no, "b": no}  # o MESMO filho duas vezes por nível
    limpa_para_pg(no)

    # 2 chaves × 18 dicts distintos + as 2 folhas do dict mais fundo.
    assert len(chamadas) == 38, len(chamadas)
