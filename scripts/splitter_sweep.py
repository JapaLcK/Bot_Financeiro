"""Varredura do separador de multiplos lancamentos.

Enumera POR SCRIPT as duas formas de frase que quebraram o separador, uma para
cada palavra de `CATEGORY_KEYWORDS` (364 de token unico), e conta quantas
dividem quando NAO deviam (e quantas deixam de dividir quando deviam).

Existe porque a rodada anterior validou o separador contra 11 frases escolhidas
a mao, passou verde, e frases banais derrubaram tudo depois. 240.100 frases em
~80s, sem banco e sem rede.

Uso:  PYTHONPATH=. python3 scripts/splitter_sweep.py [--exemplos N]
"""
import sys, time, collections
from utils_text import CATEGORY_KEYWORDS, guess_category
from parsers import split_financial_transactions as S  # trocavel: sweep.S = outra_impl

PALAVRAS = [w for ws in CATEGORY_KEYWORDS.values() for w in ws if " " not in w and "-" not in w]
CAT = {w: guess_category(w) for w in PALAVRAS}
PALAVRAS = [w for w in PALAVRAS if CAT[w] != "outros"]

# --- Forma 1: numero de referencia / quantidade no meio ----------------------
# "gastei 80 na corrida 99887 do uber" — o 99887 e' referencia, nao dinheiro.
REFS = [("na corrida", "99887", "do"), ("no pedido", "1234", "do"),
        ("boleto", "12345", "da"), ("na nota", "5567", "do"),
        ("apartamento", "302", "do"), ("na mesa", "12", "do")]
# Mesma coisa, mas o destino vem em LOCATIVO ("... 99887 no uber"). Foi esta
# variante que derrubou a rodada 3: o "do/da" era o unico freio, e trocar por
# "no/na" inventava R$99.887. So os prefixos que tambem sao locativos (o
# paralelismo dos dois lados era exigido), com tres numeros de referencia.
REFS_LOC = [p for p, _, _ in REFS if p.split()[0] in ("na", "no")]
REF_NUMS = ["99887", "1234", "5567"]
# "gastei 60 no ifood 2 pizzas" — o 2 e' quantidade; a palavra vem depois.
QTDS = ["2", "3", "12"]


def forma1():
    for w in PALAVRAS:
        for pre, num, prep in REFS:
            yield ("1-referencia", f"gastei 80 {pre} {num} {prep} {w}")
        for pre in REFS_LOC:
            for n2 in REF_NUMS:
                yield ("1-referencia-loc", f"gastei 80 {pre} {n2} no {w}")
        for q in QTDS:
            yield ("1-quantidade", f"gastei 60 no ifood {q} {w}")
            # sem preposicao nenhuma: os dois lados "paralelos" e os dois
            # numeros colados. Era 100% de divisao errada.
            yield ("1-quantidade-sem-prep", f"gastei 60 ifood {q} {w}")


# --- Forma 2: dois destinos ligados por " e " --------------------------------
def forma2():
    for a in PALAVRAS:
        for b in PALAVRAS:
            if CAT[a] == CAT[b]:
                continue  # mesma categoria nunca divide, por desenho
            yield ("2-sem-prep", f"gastei 45 no {a} e {b}")
            yield ("2-com-prep", f"gastei 45 no {a} e no {b}")
            # genitivo do 2o lado: "de X" descreve a compra, nao e' um segundo
            # lugar. Aceita-lo dobrava o valor.
            yield ("2-genitivo", f"gastei 45 no {a} e de {b}")


ESPERADO = {  # quantos pedacos a frase DEVE render
    "1-referencia": 1, "1-referencia-loc": 1,
    "1-quantidade": 1, "1-quantidade-sem-prep": 1,
    "2-sem-prep": 1, "2-com-prep": 2, "2-genitivo": 1,
}

def main():
    n_ex = 8
    if "--exemplos" in sys.argv:
        n_ex = int(sys.argv[sys.argv.index("--exemplos") + 1])
    tot = collections.Counter()
    ruim = collections.Counter()
    exemplos = collections.defaultdict(list)
    t0 = time.time()
    for grupo, frase in list(forma1()) + list(forma2()):
        tot[grupo] += 1
        n = len(S(frase))
        if (n > 1) != (ESPERADO[grupo] > 1):
            ruim[grupo] += 1
            if len(exemplos[grupo]) < n_ex:
                exemplos[grupo].append((frase, S(frase)))
    largura = max(len(g) for g in tot)
    print(f"{'grupo':<{largura}}  {'frases':>7} {'erradas':>8} {'%':>6}")
    for g in sorted(tot):
        pct = 100 * ruim[g] / tot[g]
        print(f"{g:<{largura}}  {tot[g]:>7} {ruim[g]:>8} {pct:>5.1f}%")
    print(f"{'TOTAL':<{largura}}  {sum(tot.values()):>7} {sum(ruim.values()):>8}")
    print(f"({time.time()-t0:.1f}s)")
    for g in sorted(exemplos):
        print(f"\n-- {g} --")
        for frase, saida in exemplos[g]:
            print(f"   {frase!r} -> {saida}")


if __name__ == "__main__":
    main()
