#!/usr/bin/env python3
"""Regera frontend/phosphor.css com apenas os ícones que o frontend usa.

O CSS completo do @phosphor-icons/web traz 1530 ícones; usamos ~157. Servir os
1530 em toda página é peso morto. Este script varre o frontend, monta o conjunto
usado e reescreve o CSS com esse subset.

Uso (precisa do CSS completo do pacote upstream):

    npm pack @phosphor-icons/web@2.1.1 && tar xzf phosphor-icons-web-2.1.1.tgz
    python3 scripts/build_phosphor_subset.py \
        package/src/regular/style.css package/src/regular/Phosphor.woff2

O conjunto usado sai de três fontes, nesta ordem:
  1. qualquer token `ph-<nome>` literal em frontend/**.{html,js,css} e em
     webapp/src/**.{js,jsx,css} — a FONTE da ilha React, e não o bundle
     minificado que ela produz (ver ARTEFATOS abaixo);
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
DESTINO_FONTE = FRONTEND / "fonts" / "Phosphor.woff2"
# Artefato de build, não código-fonte. Fica fora da varredura nos DOIS sentidos:
# um `ph-algo` que o minificador emita por acidente viraria ícone FANTASMA (e
# vermelho no test_phosphor_subset.py por um nome que ninguém escreveu), e um
# ícone de verdade que o componente use está na fonte (`webapp/src`), que a
# varredura lê. Hoje o bundle tem ZERO ocorrências de `ph-`; é do dia em que
# tiver que esta linha trata.
ARTEFATOS = {FRONTEND / "precos-app.js", FRONTEND / "precos-app.css"}
# A ilha React: fonte de frontend que NÃO mora em frontend/.
WEBAPP_SRC = RAIZ / "webapp" / "src"

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

FONTE_CSS = """@font-face {
  font-family: "Phosphor";
  src: url("/fonts/Phosphor.woff2") format("woff2");
  font-weight: normal;
  font-style: normal;
  font-display: block;
}"""


def icones_usados() -> set[str]:
    usados = set()
    for f in (*FRONTEND.rglob("*"), *WEBAPP_SRC.rglob("*")):
        if f.suffix not in (".html", ".js", ".jsx", ".css") or f == DESTINO:
            continue
        if f in ARTEFATOS:
            continue
        usados |= set(re.findall(r"\bph-([a-z0-9-]+)", f.read_text(encoding="utf-8", errors="ignore")))
    for arquivo, var in MAPAS:
        texto = (FRONTEND / arquivo).read_text(encoding="utf-8")
        bloco = re.search(rf"const\s+{var}\s*=\s*{{(.*?)}};", texto, re.S)
        if not bloco:
            sys.exit(f"mapa {var} não encontrado em {arquivo} — renomearam? ajuste MAPAS.")
        usados |= set(re.findall(r':\s*"([a-z0-9-]+)"', bloco.group(1)))
    return usados | FALLBACKS


def _unicode_da_regra(regra: str, nome: str) -> int:
    valor = re.search(r'content:\s*"\\([0-9a-fA-F]+)"', regra)
    if not valor:
        sys.exit(f"regra do ícone {nome!r} não tem um codepoint hexadecimal")
    return int(valor.group(1), 16)


def gerar_fonte_subset(origem: pathlib.Path, unicodes: set[int]) -> None:
    """Mantém no WOFF2 somente os codepoints presentes no CSS gerado."""
    try:
        from fontTools import subset
    except ImportError:
        sys.exit("fontTools ausente — instale `fonttools[woff]` para regerar a fonte")

    opcoes = subset.Options()
    opcoes.flavor = "woff2"
    opcoes.ignore_missing_unicodes = False
    fonte = subset.load_font(str(origem), opcoes)
    recorte = subset.Subsetter(options=opcoes)
    recorte.populate(unicodes=unicodes)
    recorte.subset(fonte)
    subset.save_font(fonte, str(DESTINO_FONTE), opcoes)

    # Verifica o artefato gravado, não apenas o objeto em memória: uma opção
    # incorreta de serialização não pode deixar o CSS apontando para glifos ausentes.
    fonte_gerada = subset.load_font(str(DESTINO_FONTE), opcoes)
    cmap = fonte_gerada.getBestCmap() or {}
    ausentes = unicodes - set(cmap)
    fonte_gerada.close()
    if ausentes:
        codigos = ", ".join(f"U+{codigo:04X}" for codigo in sorted(ausentes))
        sys.exit(f"fonte gerada sem os codepoints: {codigos}")


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    origem = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    fonte_origem = pathlib.Path(sys.argv[2])
    regras = re.findall(r"(\.ph\.ph-([a-z0-9-]+):before\s*\{[^}]*\}\n?)", origem)
    disponiveis = {nome for _, nome in regras}
    if not disponiveis:
        sys.exit(f"{sys.argv[1]} não parece o CSS do Phosphor (nenhuma regra .ph.ph-*)")

    usados = icones_usados()
    faltando = sorted(usados - disponiveis)
    if faltando:
        sys.exit("ícones referenciados que não existem no CSS de origem: " + ", ".join(faltando))

    unicodes = {_unicode_da_regra(regra, nome) for regra, nome in regras if nome in usados}

    # Remove as regras não usadas em vez de remontar a partir do cabeçalho: assim
    # qualquer outra regra base do pacote sobrevive.
    saida = origem
    for regra, nome in regras:
        if nome not in usados:
            saida = saida.replace(regra, "", 1)
    # troca só o comentário de topo do upstream pelo nosso, preservando o resto
    if saida.lstrip().startswith("/*"):
        saida = saida[saida.index("*/") + 2:].lstrip("\n")
    # O pacote aponta para ./Phosphor.{woff2,woff,ttf,svg}; nosso servidor expõe
    # somente /fonts/Phosphor.woff2. Normalizar aqui impede uma regeneração de
    # criar quatro 404 silenciosos nas páginas.
    saida, trocas = re.subn(r"@font-face\s*\{.*?\}", FONTE_CSS, saida, count=1, flags=re.S)
    if trocas != 1:
        sys.exit("@font-face do Phosphor não encontrado no CSS de origem")
    # Ajuste visual próprio do PigBank, deliberadamente fora do upstream.
    if "vertical-align:-0.125em" not in saida:
        saida = saida.rstrip() + "\n\n.ph{vertical-align:-0.125em}\n"
    saida = CABECALHO.format(n=len(usados), total=len(disponiveis)) + saida
    DESTINO.write_text(saida, encoding="utf-8")
    gerar_fonte_subset(fonte_origem, unicodes)
    print(
        f"{DESTINO.relative_to(RAIZ)}: {len(usados)} ícones de {len(disponiveis)}; "
        f"fonte com {DESTINO_FONTE.stat().st_size} bytes"
    )


if __name__ == "__main__":
    main()
