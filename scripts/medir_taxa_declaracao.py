#!/usr/bin/env python3
"""Taxa de recusa da declaração "pelo banco" no corpus de frases legítimas (ADR 0006, DP-10; issue #1310).

Uso (de qualquer diretório; a árvore medida pode ser a `main` ou um branch):
    .venv/bin/python scripts/medir_taxa_declaracao.py <árvore> [corpus.json]

`corpus.json` padrão: `tests/fixtures/declaracao_frases_legitimas.json` da árvore deste script, para as duas colunas
(main × branch) usarem o MESMO corpus. Por categoria: recusadas / total, "grava errado" (não recusou e o parser grava
valores diferentes dos ditos) e "classificador" (não recusou e o parser não lê a frase: quem grava é o valor do
classificador/LLM, fora desta medição). O "grava" simula o caminho de texto do `add` com o parser real da árvore
(`parse_receita_despesa_natural`, categoria trocada por "outros": sem banco nem rede).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

arvore = Path(sys.argv[1]).resolve()
corpus_json = sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "tests" / "fixtures" / \
    "declaracao_frases_legitimas.json"
sys.path.insert(0, str(arvore))

import parsers  # noqa: E402

parsers.infer_category = lambda **k: SimpleNamespace(category="outros", reason="t")
from core.handlers import forma_pagamento as fp  # noqa: E402
from core.handlers.declaracao_valor import recusa  # noqa: E402
from core.intent_classifier import is_comparative_question  # noqa: E402


def grava(frase):
    t = fp.limpar(frase, banco=True)
    partes = [p for p in parsers.split_financial_transactions(t) if not is_comparative_question(p)]
    if len(partes) > 1:
        vals = [r["valor"] for p in partes if (r := parsers.parse_receita_despesa_natural(1, p, aprender=False))]
        if vals:
            return sorted(round(v, 2) for v in vals)
    r = parsers.parse_receita_despesa_natural(1, t, aprender=False)
    return [round(r["valor"], 2)] if r else None


assert parsers.__file__.startswith(str(arvore)), parsers.__file__  # mede a árvore pedida, não a do cwd
corpus = json.loads(Path(corpus_json).read_text())
por = defaultdict(lambda: [0, 0, 0, 0])
linhas = []
for c in corpus:
    x = por[c["categoria"]]
    x[1] += 1
    if recusa(c["frase"]):
        x[0] += 1
        linhas.append("recusa  " + c["frase"])
        continue
    g = grava(c["frase"])
    if g is None:
        x[3] += 1
    elif c["valores"] and g != sorted(round(v, 2) for v in c["valores"]):
        x[2] += 1
        linhas.append(f"ERRADO  {c['frase']} -> {g}")
tot = [sum(x[i] for x in por.values()) for i in range(4)]
for cat, (n, t, e, f) in list(por.items()) + [("TOTAL", tot)]:
    print(f"{cat:22} recusa {n:3}/{t:<3} grava errado {e:2}  classificador {f}")
print("\n".join(linhas))
