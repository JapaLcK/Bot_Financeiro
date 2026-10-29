"""Unidade do `core/pg_text.py` — o que o Postgres não consegue armazenar.

Separado de `test_corpo_json_string_venenosa.py` de propósito: lá é a fronteira
HTTP (o webhook e o `note` do saque respondendo 200/404 em vez de 500), aqui é
o contrato da função. Os dois quebram por motivos diferentes e um não deve
esconder o outro.
"""
import json
import signal
from types import SimpleNamespace

import pytest

import core.pg_text as pg_text
from core.pg_text import limpa_para_pg, quadros_da_cadeia


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


# ── quadros_da_cadeia: a pilha da cadeia inteira, sem a mensagem de nenhum elo ──
# A mensagem dos elos é `_DETALHE`, por NOME no `raise`: o quadro mostra o código-fonte,
# então o literal no `raise` apareceria e o "segredo" daria falso vermelho.

_DETALHE = "DETAIL: Key (id)=(42) segredo"


class _ComSqlstate(Exception):
    def __init__(self, msg, sqlstate):
        super().__init__(msg)
        self.sqlstate = sqlstate


def _banco(sqlstate="23505"):
    raise _ComSqlstate(_DETALHE, sqlstate)


def _capturada(f) -> BaseException:
    try:
        f()
    except Exception as e:  # noqa: BLE001
        return e
    raise AssertionError("não levantou")


def _implicito():
    try:
        _banco()
    except Exception:
        raise RuntimeError(_DETALHE)  # sem `from`: o caso mais comum, `__context__`


def _from_none():
    try:
        _banco()
    except Exception:
        raise RuntimeError(_DETALHE) from None


def _dois_sqlstates():
    try:
        _banco("23505")
    except Exception as e:
        raise _ComSqlstate(_DETALHE, "40001") from e


def _causa_explicita():
    causa = _capturada(_banco)
    try:
        raise KeyError(_DETALHE)
    except KeyError:
        raise RuntimeError(_DETALHE) from causa  # `__cause__` (o banco) != `__context__` (o KeyError)


@pytest.mark.parametrize("fonte,quadro_do_banco,sqlstate", [
    (_implicito, True, "23505"),        # o contexto implícito é seguido
    (_from_none, False, None),          # `from None` para a cadeia
    (_dois_sqlstates, True, "40001"),   # vale o sqlstate do PRIMEIRO elo (o de fora)
    (_causa_explicita, True, "23505"),  # `__cause__` antes de `__context__`, como o Python
])
def test_quadros_da_cadeia(fonte, quadro_do_banco, sqlstate):
    quadros, achado = quadros_da_cadeia(_capturada(fonte))
    assert ("in _banco" in quadros) == quadro_do_banco and achado == sqlstate, (quadros, achado)
    assert f"in {fonte.__name__}" in quadros, quadros
    assert "segredo" not in quadros and "DETAIL" not in quadros, quadros


@pytest.mark.skipif(
    not hasattr(signal, "SIGALRM"), reason="SIGALRM é POSIX-only (não existe no Windows)"
)
def test_quadros_da_cadeia_termina_em_ciclo():
    """Sem a guarda de vistos o laço não termina: o alarme vira o travamento em erro (o
    `except` interno o engole e devolve vazio, e o assert abaixo acusa)."""
    def _estourou(*_):
        raise AssertionError("quadros_da_cadeia não retornou: laço infinito")

    a, b = ValueError(_DETALHE), KeyError(_DETALHE)
    a.__context__, b.__context__ = b, a
    anterior = signal.signal(signal.SIGALRM, _estourou)
    signal.setitimer(signal.ITIMER_REAL, 1)
    try:
        quadros, sqlstate = quadros_da_cadeia(a)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, anterior)
    assert quadros == "\nCausado por KeyError:\n" and sqlstate is None, quadros


def test_quadros_da_cadeia_nunca_levanta(monkeypatch):
    def quebra(_tb):
        raise RuntimeError(_DETALHE)
    monkeypatch.setattr(pg_text, "traceback", SimpleNamespace(format_tb=quebra))  # só o de pg_text
    assert quadros_da_cadeia(_capturada(_implicito)) == ("", None)
