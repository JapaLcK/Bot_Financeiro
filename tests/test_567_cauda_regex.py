"""#567, Codex no #709 (P1 de desempenho, thread 4159812354): a cauda da resposta a
"de qual…?" não pode travar o worker do webhook, e a forma de valor continua a mesma.

A regex antiga da forma de valor (`[(+\\-\\s]*` duas vezes, `[\\d.,\\s]+`, `[)+\\-\\s]*` duas
vezes, todas comendo o mesmo espaço) backtrackava de forma exponencial: "tesouro, " +
"- " * 200 + "1x" levava ~4 s e 100 espaços passavam de 5 s. Com um worker só, uma mensagem
montada trava a fila de todo mundo. A varredura por preposição e por vírgula também era
quadrática (", " * 2000 levava 1,7 s; na `main`, 6 ms).

O CONSERTO tem três peças, e cada uma tem o seu controle:
  - `_forma_de_valor`, em passos de uma classe cada: mesma linguagem (`test_equivale_*`);
  - `_RESPOSTA_MAX`/`_CAUDA_MAX`: tetos antes de qualquer regex (`test_*_tempo`).

CONTROLE NEGATIVO (medido em 2026-10-01 sobre 00c0ed4b): com a regex antiga de volta em
`_forma_de_valor`, `test_tempo_da_cauda_no_pior_caso` fica VERMELHO (o subprocesso passa
do teto e é morto; um `re` não se interrompe por dentro, por isso o processo à parte).
Sem o `_RESPOSTA_MAX`, o mesmo teste fica vermelho pela varredura por preposição. O teto
SOZINHO não basta: a regex antiga, com 63 espaços (dentro do `_CAUDA_MAX`), levava 0,56 s.
CONTROLE POSITIVO: `test_equivale_a_regex_antiga` (a regex nova aceita o que a antiga
aceitava, "(80)", "80-", "R$ -80", "( 80 )"; e recusa "80-90") e
`test_resposta_longa_nao_e_quantia_explicita` (o teto é só para o que não é resposta).
"""
from __future__ import annotations

import itertools
import random
import re
import subprocess
import sys
import textwrap

import pytest

from core.handlers import bills as h_bills
from core.intent_router import _forma_de_valor, _funde_a_resposta
from utils_text import limpa_pontuacao_final

TETO_S = 2.0

# A regex ANTIGA, copiada: é o oráculo. Não importar do código de produção.
_P, _E = r"[(+\-\s]*", r"[)+\-\s]*"
_ANTIGA = re.compile(
    rf"{_P}(?:r\$\s*)?{_P}[\d.,\s]+{_E}(?:\s*{h_bills._UNIDADE})?{_E}", re.I)


def test_equivale_a_regex_antiga():
    """Exaustivo até 5 símbolos e 100 mil cadeias de pedaços reais, tamanho ≤ 12."""
    for t in range(6):
        for tup in itertools.product(" ()+-1.,r$s", repeat=t):
            s = "".join(tup)
            assert _forma_de_valor(s) == bool(_ANTIGA.fullmatch(s)), repr(s)
    rnd = random.Random(567)
    pedacos = [" ", "  ", "(", ")", "+", "-", "80", "1,5", "1.000", ".", ",", "R$", "r$ ", "reais",
               "real", "rs", "pila", "conto", "mangos", " reais", "x", "−", "٣", " "]
    for _ in range(100_000):
        s = "".join(rnd.choice(pedacos) for _ in range(rnd.randint(0, 12)))
        assert _forma_de_valor(s) == bool(_ANTIGA.fullmatch(s)), repr(s)
    for ok in ("(80)", "( 80 )", "(80,00)", "(-80)", "80-", "80 -", "R$ (80)", "+80", "-R$ 80", "132 50"):
        assert _forma_de_valor(ok), ok
    for fora in ("80-90", "2029-12", "a de 2027", "dia 15", "80%"):
        assert not _forma_de_valor(fora), fora
    assert _forma_de_valor("1" * 64) and not _forma_de_valor("1" * 65)   # `_CAUDA_MAX`


_CODIGO = textwrap.dedent('''
    import time
    from core.intent_router import _forma_de_valor, _funde_a_resposta, _quantia_explicita
    from utils_text import limpa_pontuacao_final
    n = 20000
    CAUDAS = {"- 1x": "- " * n + "1x", "- 1": "- " * n + "1", "(": "(" * n, "+ 1x": "+ " * n + "1x",
              "espacos": " " * n + "x", ".,": ".," * n, "1 1": "1 " * n, "-": "-" * n,
              "1 espacos": "1" + " " * n, "-(1": "-(" * n + "1x", ")": "1" + ")-" * n}
    RESPOSTAS = {k: "tesouro, " + v for k, v in CAUDAS.items()}
    RESPOSTAS.update({", ": "tesouro" + ", " * n, " de ": "tesouro " + "de " * n, ", 1": "tesouro" + ", 1" * n})
    def tempo(fn):
        t = time.perf_counter(); fn(); return time.perf_counter() - t
    for k, v in CAUDAS.items():
        print("forma", k, tempo(lambda: _forma_de_valor(v)))
    for k, r in RESPOSTAS.items():
        print("quantia", k, tempo(lambda: _quantia_explicita(r, r, ["Tesouro"])))
        print("funde", k, tempo(lambda: _funde_a_resposta(
            "funds.withdraw", {"amount": 50.0, "want_all": False},
            limpa_pontuacao_final(r), ["Tesouro"], pede_nome=True, crua=r)))
''')


def test_tempo_da_cauda_no_pior_caso():
    """Cada entrada, com 20 mil repetições, em menos de 2 s. Num PROCESSO à parte: uma
    regex exponencial não se interrompe por dentro, então o vermelho morre no timeout."""
    try:
        r = subprocess.run([sys.executable, "-c", _CODIGO], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail("a cauda travou o processo por mais de 60 s (regex exponencial ou varredura quadrática)")
    assert r.returncode == 0, r.stderr[-400:]
    lentas = [linha for linha in r.stdout.splitlines() if float(linha.rsplit(" ", 1)[1]) > TETO_S]
    assert not lentas, lentas
    assert len(r.stdout.splitlines()) == 11 + 14 * 2   # 11 caudas + 14 respostas x 2 funções


def test_resposta_longa_nao_e_quantia_explicita():
    """Limite declarado: acima de 200 caracteres a resposta não troca o valor guardado;
    a curta ("tesouro, 80") segue lendo 80."""
    def amount(r):
        ents, _ = _funde_a_resposta("funds.withdraw", {"amount": 50.0, "want_all": False},
                                    limpa_pontuacao_final(r), ["Tesouro"], pede_nome=True, crua=r)
        return ents.get("amount")
    assert amount("tesouro, 80") == 80.0
    assert amount("tesouro, " + " " * 300 + "80") == 50.0
