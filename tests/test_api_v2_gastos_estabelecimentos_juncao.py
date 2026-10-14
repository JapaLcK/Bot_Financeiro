"""`db/estabelecimentos.ler` sem banco (cursor falso): a junção por rótulo nunca repete nome (P1 b).

Defeito provado pelo Tester: o nome da união era recontado sobre as linhas de TODOS os grupos, então uma
descrição solta ("B") vencia e a tela mostrava dois "B". Controle negativo medido à mão: com
`_rotulo(sorted(linhas))` no lugar do filtro por `rotulos` em `ler`, os dois testes ficam vermelhos.
"""
import random
from decimal import Decimal

import pytest

from db import estabelecimentos

CNPJS = ("11111111000191", "22222222000191", "33333333000191", "44444444000191")
DESCRICOES = ("B", "b", "X", "x!", "***", "---", "Uber Trip", "UBER *TRIP", "Pix enviado", None, "", "🍕",
              "Sem descrição")


class _Cur:
    def __init__(self, raw):
        self.raw = raw  # {id: (cnpj, recebedor)}

    def execute(self, sql, params):
        pass

    def fetchall(self):
        return [{"tabela": "l", "ref": i, "cnpj": c, "recebedor": r} for i, (c, r) in self.raw.items()]


def _ler(linhas):
    """`linhas`: [(descricao, valor, cnpj, recebedor)] na ordem do `listar` (a mais recente primeiro)."""
    saidas = [{"tabela": "l", "id": i, "descricao": d, "valor": Decimal(v)} for i, (d, v, _, _) in enumerate(linhas, 1)]
    raw = {i: (c, r) for i, (_, _, c, r) in enumerate(linhas, 1) if c or r}
    return estabelecimentos.ler(_Cur(raw), 1, saidas)


def test_descricao_solta_nao_rouba_o_nome_da_uniao_nem_repete_nome():
    # CNPJ1: X x3 + B x2; CNPJ2: x! x3 + B x2; mais um "B" sem raw. "X" e "x!" juntam (chave `x`); o "B" sem raw é
    # outro estabelecimento (chave `b`, sem CNPJ). Nome da união: só entre {X, x!}, empate -> o da linha mais recente.
    c1, c2 = CNPJS[:2]
    linhas = ([("X", 1, c1, None)] * 3 + [("x!", 1, c2, None)] * 3 + [("B", 1, c1, None)] * 2
              + [("B", 1, c2, None)] * 2 + [("B", 1, None, None)])
    r = _ler(linhas)
    assert [(e["nome"], e["valor"], e["quantidade"]) for e in r] == [("X", 10, 10), ("B", 1, 1)]
    # positivo: sem a descrição solta o resultado da união é o mesmo (a junção continua funcionando)
    assert [(e["nome"], e["quantidade"]) for e in _ler(linhas[:-1])] == [("X", 10)]


def test_regras_antigas_continuam():
    assert len(_ler([("***", 1, None, None), ("---", 1, None, None)])) == 2
    assert [(e["nome"], e["quantidade"]) for e in _ler([("***", 1, None, None)] * 2)] == [("***", 2)]
    assert [(e["nome"], e["quantidade"]) for e in _ler([(d, 1, None, None) for d in (None, "", "  ")])] == [("Sem descrição", 3)]


def _caso(rnd):
    linhas = []
    for _ in range(rnd.randint(0, 30)):
        cnpj = rnd.choice(CNPJS + (None,) * 3)
        rec = rnd.choice(("Ana", "Bruno", None, None))
        linhas.append((rnd.choice(DESCRICOES), rnd.randint(1, 5), cnpj, rec))
    return linhas


@pytest.mark.parametrize("semente", [42])
def test_propriedade_nome_unico_e_soma_preservada(semente, monkeypatch):
    monkeypatch.setattr(estabelecimentos, "TOP", 10**6)  # sem o corte do top 5, a soma fecha
    rnd = random.Random(semente)
    for _ in range(500):
        linhas = _caso(rnd)
        r = _ler(linhas)
        nomes = [e["nome"] for e in r]
        assert len(nomes) == len(set(nomes)), (linhas, r)
        assert all(nomes) and all(e["quantidade"] > 0 for e in r), (linhas, r)
        assert sum(e["valor"] for e in r) == sum(Decimal(v) for _, v, _, _ in linhas), (linhas, r)
        assert sum(e["quantidade"] for e in r) == len(linhas), (linhas, r)
        assert r == sorted(r, key=lambda e: (-e["valor"], -e["quantidade"], e["nome"])), (linhas, r)
        vistas = {(d or "").strip() or "Sem descrição" for d, _, _, _ in linhas}
        assert set(nomes) <= vistas, (linhas, r)  # o nome é sempre uma descrição que existe
