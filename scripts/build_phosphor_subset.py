#!/usr/bin/env python3
"""Regera frontend/phosphor.css com apenas os ícones que o frontend usa.

O CSS completo do @phosphor-icons/web traz 1530 ícones; usamos ~157. Servir os
1530 em toda página é peso morto. Este script varre o frontend, monta o conjunto
usado e reescreve o CSS com esse subset.

Uso (precisa do CSS completo do pacote upstream):

    npm pack @phosphor-icons/web@2.1.1 && tar xzf phosphor-icons-web-2.1.1.tgz
    python3 scripts/build_phosphor_subset.py package/src/regular/style.css

O conjunto usado sai de três fontes, nesta ordem:
  1. qualquer token `ph-<nome>` literal em frontend/**.{html,js,css};
  2. os valores dos mapas fechados EMOJI_TO_PH (dashboard.js), ACTIVITY_ICONS e
     CAT_ICONS (settings.html), que alimentam as interpolações `ph-${...}`;
  3. os fallbacks literais desses três helpers: tag, circle, trend-down.

Se algum nome referenciado não existir no CSS de origem, o script ABORTA em vez
de gerar um subset com buraco — ícone faltando não dá erro no browser, some
calado. O teste tests/test_phosphor_subset.py cobre o caminho inverso: garante
que todo ícone referenciado está no CSS servido.
"""
from __future__ import annotations

import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = RAIZ / "frontend"
DESTINO = FRONTEND / "phosphor.css"

# mapas fechados que alimentam as interpolações `ph-${...}`
MAPAS = [("dashboard.js", "EMOJI_TO_PH"), ("settings.html", "ACTIVITY_ICONS"),
         ("settings.html", "CAT_ICONS")]
# fallbacks literais de phIcon(), catIcon() e do render de atividade
FALLBACKS = {"tag", "circle", "trend-down"}

CABECALHO = """/* Phosphor Icons — peso Regular, self-hosted (MIT). SUBSET GERADO — {n} de {total} ícones.
   Fonte: @phosphor-icons/web@2.1.1, filtrado por scripts/build_phosphor_subset.py.
   Nao editar a mao nem colar o pacote inteiro por cima: rode o script.
   Ícone novo no HTML/JS sem regerar = ícone invisível (tests/test_phosphor_subset.py pega).
   Uso: <i class="ph ph-wallet"></i>  ·  cor via currentColor, tamanho via font-size. */
"""


def icones_usados() -> set[str]:
    usados = set()
    for f in FRONTEND.rglob("*"):
        if f.suffix not in (".html", ".js", ".css") or f == DESTINO:
            continue
        usados |= set(re.findall(r"\bph-([a-z0-9-]+)", f.read_text(encoding="utf-8", errors="ignore")))
    for arquivo, var in MAPAS:
        texto = (FRONTEND / arquivo).read_text(encoding="utf-8")
        bloco = re.search(rf"const\s+{var}\s*=\s*{{(.*?)}};", texto, re.S)
        if not bloco:
            sys.exit(f"mapa {var} não encontrado em {arquivo} — renomearam? ajuste MAPAS.")
        usados |= set(re.findall(r':\s*"([a-z0-9-]+)"', bloco.group(1)))
    return usados | FALLBACKS


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    origem = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    regras = re.findall(r"(\.ph\.ph-([a-z0-9-]+):before\s*\{[^}]*\}\n?)", origem)
    disponiveis = {nome for _, nome in regras}
    if not disponiveis:
        sys.exit(f"{sys.argv[1]} não parece o CSS do Phosphor (nenhuma regra .ph.ph-*)")

    usados = icones_usados()
    faltando = sorted(usados - disponiveis)
    if faltando:
        sys.exit("ícones referenciados que não existem no CSS de origem: " + ", ".join(faltando))

    # Remove as regras não usadas em vez de remontar a partir do cabeçalho: assim
    # qualquer outra coisa no arquivo sobrevive. O upstream põe
    # `.ph{vertical-align:-0.125em}` DEPOIS das regras de ícone, e remontar pelo
    # cabeçalho a descartava calada — todo ícone desalinhava 0.125em.
    saida = origem
    for regra, nome in regras:
        if nome not in usados:
            saida = saida.replace(regra, "", 1)
    # troca só o comentário de topo do upstream pelo nosso, preservando o resto
    if saida.lstrip().startswith("/*"):
        saida = saida[saida.index("*/") + 2:].lstrip("\n")
    saida = CABECALHO.format(n=len(usados), total=len(disponiveis)) + saida
    DESTINO.write_text(saida, encoding="utf-8")
    print(f"{DESTINO.relative_to(RAIZ)}: {len(usados)} ícones de {len(disponiveis)} disponíveis")


if __name__ == "__main__":
    main()
