"""As quatro TTFs do app (`app/assets/fonts/`) são espelho das woff2 do site
(`frontend/fonts/`), convertidas com fontTools — não uma fonte baixada à parte.

O app carrega fonte local (`require()`, ver `app/app/_layout.tsx`), não a
mesma URL do site; sem este teste, as duas árvores podem divergir em silêncio
(peso errado, subset diferente, versão velha) e só apareceria no aparelho.

Compara estrutura (glyph order, cmap, versão) em vez de bytes crus: o TTF não
é woff2 recomprimido, é o MESMO font grafo salvo sem a flag `flavor`.
"""
from __future__ import annotations

import pathlib

from fontTools.ttLib import TTFont

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTES_APP = RAIZ / "app" / "assets" / "fonts"
FONTES_SITE = RAIZ / "frontend" / "fonts"

PESOS = ["Regular", "Medium", "SemiBold", "Bold"]

# `Texto.tsx` liga `fontVariant: ["tabular-nums"]` em valor numérico — sem a
# feature `tnum` o número desalinha sem erro (mesmo risco do
# `test_inter_subset.py` para o site). U+2212 é o menos tipográfico que
# `Money.tsx` (PR B) usa para saída negativa, nunca o hífen `-`; U+2022 é o
# ponto da máscara de valor oculto ("R$ ••••").
GLIFOS_OBRIGATORIOS = {0x2212: "− (U+2212)", 0x2022: "• (U+2022)"}


def _par(peso: str) -> tuple[pathlib.Path, pathlib.Path]:
    return (FONTES_APP / f"Inter-{peso}.ttf", FONTES_SITE / f"Inter-{peso}.woff2")


def test_os_quatro_pesos_existem_dos_dois_lados():
    for peso in PESOS:
        ttf, woff2 = _par(peso)
        assert ttf.is_file(), f"falta {ttf}"
        assert woff2.is_file(), f"falta a origem {woff2}"


def test_ttf_e_o_ttf_puro_do_mesmo_grafo_da_woff2():
    """Mesmo desenho, formato de arquivo diferente: TTF não pode carregar a
    compressão woff2 (Metro/Expo não entendem woff2 em `require()`)."""
    for peso in PESOS:
        ttf_path, woff2_path = _par(peso)
        ttf = TTFont(ttf_path)
        woff2 = TTFont(woff2_path)
        assert ttf.flavor is None, f"{ttf_path.name} não é TTF puro (flavor={ttf.flavor})"
        assert woff2.flavor == "woff2", f"{woff2_path.name} não é a woff2 de origem"
        assert ttf.getGlyphOrder() == woff2.getGlyphOrder(), (
            f"{ttf_path.name} tem glyph order diferente da origem — não é a mesma conversão"
        )
        assert ttf.getBestCmap() == woff2.getBestCmap(), (
            f"{ttf_path.name} tem cmap diferente da origem"
        )
        assert ttf["head"].fontRevision == woff2["head"].fontRevision, (
            f"{ttf_path.name} é de uma versão da Inter diferente da woff2 servida ao site"
        )
        # `usWeightClass` e `hmtx` pegam o defeito que glyph order/cmap/versão
        # não pegam: um peso copiado por cima de outro (Bold sobre Regular)
        # tem o MESMO grafo (mesmo alfabeto, mesmo cmap) e só denuncia pelo
        # peso declarado e pela métrica horizontal de cada glifo.
        assert ttf["OS/2"].usWeightClass == woff2["OS/2"].usWeightClass, (
            f"{ttf_path.name} tem usWeightClass diferente da woff2 — peso trocado na conversão"
        )
        assert ttf["hmtx"].metrics == woff2["hmtx"].metrics, (
            f"{ttf_path.name} tem métricas horizontais diferentes da woff2 — peso trocado na conversão"
        )


def test_ttf_preserva_tabular_nums():
    for peso in PESOS:
        ttf_path, _ = _par(peso)
        font = TTFont(ttf_path)
        feats = {rec.FeatureTag for rec in font["GSUB"].table.FeatureList.FeatureRecord}
        assert "tnum" in feats, f"{ttf_path.name} perdeu a feature tnum na conversão"


def test_ttf_tem_os_glifos_que_o_app_usa_fora_do_alfabeto_comum():
    for peso in PESOS:
        ttf_path, _ = _par(peso)
        cmap = TTFont(ttf_path).getBestCmap()
        faltando = [nome for codepoint, nome in GLIFOS_OBRIGATORIOS.items() if codepoint not in cmap]
        assert not faltando, f"{ttf_path.name} sem glifo(s) {faltando}"
