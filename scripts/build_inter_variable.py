#!/usr/bin/env python3
"""Gera o WOFF2 variável da Inter usado pelo frontend.

Entrada reproduzível: ``web/InterVariable.woff2`` da release oficial Inter 4.1
(https://github.com/rsms/inter/releases/tag/v4.1). O arquivo tem os eixos de
peso e tamanho óptico; fixamos o tamanho óptico em 14 para manter o desenho das
fontes web estáticas anteriores e preservamos apenas o eixo de peso.

O subset reutiliza exatamente os caracteres do gerador legado, mantém as
features necessárias ao produto e carimba no brand.css o hash do WOFF2. A rota
da fonte usa cache imutável de um ano, portanto esse cache-buster é obrigatório.

Uso:
    .venv/bin/python scripts/build_inter_variable.py /caminho/InterVariable.woff2
"""
from __future__ import annotations

import hashlib
import io
import pathlib
import re
import sys

from fontTools.subset import Options, Subsetter, parse_unicodes
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from build_inter_subset import RAIZ, UNICODES

DESTINO = RAIZ / "frontend" / "fonts" / "Inter-Variable.woff2"
BRAND_CSS = RAIZ / "frontend" / "brand.css"
FEATURES = ["calt", "ccmp", "cpsp", "kern", "locl", "mark", "mkmk", "pnum", "tnum"]
GPOS_OBRIGATORIAS = {"cpsp", "kern", "mark", "mkmk"}


def gerar(origem: pathlib.Path) -> str:
    # Não carimbar a hora da execução em `head.modified`: o hash também é a
    # versão pública do asset, então a mesma entrada precisa gerar o mesmo URL.
    fonte = TTFont(origem, recalcTimestamp=False)
    eixos = {eixo.axisTag for eixo in fonte["fvar"].axes}
    if eixos != {"opsz", "wght"}:
        raise SystemExit(f"esperava os eixos opsz/wght da Inter 4.1, achei {sorted(eixos)}")

    instantiateVariableFont(fonte, {"opsz": 14}, inplace=True)
    # O instancer deixa a gvar com acesso preguiçoso à ordem antiga dos glifos.
    # Serializar e reabrir materializa a instância antes de o subset removê-los.
    intermediaria = io.BytesIO()
    fonte.flavor = "woff2"
    fonte.save(intermediaria)
    fonte.close()
    fonte = TTFont(io.BytesIO(intermediaria.getvalue()), lazy=False, recalcTimestamp=False)
    opcoes = Options()
    opcoes.layout_features = FEATURES
    opcoes.flavor = "woff2"
    recorte = Subsetter(opcoes)
    recorte.populate(unicodes=parse_unicodes(UNICODES))
    recorte.subset(fonte)
    fonte.flavor = "woff2"
    fonte.save(DESTINO)
    fonte.close()

    gerada = TTFont(DESTINO, recalcTimestamp=False)
    eixos_gerados = {eixo.axisTag for eixo in gerada["fvar"].axes}
    gsub = {item.FeatureTag for item in gerada["GSUB"].table.FeatureList.FeatureRecord}
    gpos = {item.FeatureTag for item in gerada["GPOS"].table.FeatureList.FeatureRecord}
    gerada.close()
    if eixos_gerados != {"wght"}:
        raise SystemExit(f"fonte gerada com eixos inesperados: {sorted(eixos_gerados)}")
    if "tnum" not in gsub:
        raise SystemExit("fonte gerada perdeu a feature tnum")
    if not GPOS_OBRIGATORIAS <= gpos:
        raise SystemExit(f"fonte gerada perdeu features GPOS: {sorted(GPOS_OBRIGATORIAS - gpos)}")

    return hashlib.blake2b(DESTINO.read_bytes(), digest_size=6).hexdigest()


def versionar_css(versao: str) -> None:
    css = BRAND_CSS.read_text(encoding="utf-8")
    novo, trocas = re.subn(
        r'(/fonts/Inter-Variable\.woff2\?v=)[^"\)]+',
        rf"\g<1>{versao}",
        css,
        count=1,
    )
    if trocas != 1:
        raise SystemExit("URL da Inter variável não encontrada em brand.css")
    BRAND_CSS.write_text(novo, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    versao = gerar(pathlib.Path(sys.argv[1]))
    versionar_css(versao)
    print(f"{DESTINO.relative_to(RAIZ)}: {DESTINO.stat().st_size} bytes; v={versao}")


if __name__ == "__main__":
    main()
