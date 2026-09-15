"""As fontes Inter ativa e legadas são subsets com os glifos do produto.

O risco do subset é silencioso: glifo ausente não dá erro, vira tofu. Estes
testes garantem que os glifos de texto pt-BR real existem em TODOS os pesos
servidos — inclusive os acentuados, que é onde um subset "latin only" quebraria.

Geradores: scripts/build_inter_variable.py e scripts/build_inter_subset.py.
"""
from __future__ import annotations

import hashlib
import pathlib

from fontTools.ttLib import TTFont

from scripts import build_inter_subset as gerador_legado

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTE_VARIAVEL = RAIZ / "frontend" / "fonts" / "Inter-Variable.woff2"
FONTES_LEGADAS = sorted(
    path for path in (RAIZ / "frontend" / "fonts").glob("Inter-*.woff2")
    if path != FONTE_VARIAVEL
)
FONTES = [FONTE_VARIAVEL, *FONTES_LEGADAS]

# Texto pt-BR real: acentos dos dois casos, ç, moeda, número, travessão, aspas.
TEXTO_PT_BR = "Ações à çedilha — R$ 1.234,56 até ô, José já viu: “ótimo”, né? Saúde ü í ú Â Ê Õ…"
# Glifos de UI que o frontend usa em texto/CSS (CTA "→", toast "✓", "⤓ Sacar
# tudo", content:"✓"…) — os 17 que um subset só-texto deixou cair (MÉDIA-4).
GLIFOS_UI = "→←✓≥≤≈↓↑●▲▼◀▶↻↔⤓↪"


def _faltando(path: pathlib.Path, texto: str) -> list[str]:
    cmap = TTFont(path).getBestCmap()
    return sorted({ch for ch in texto if ch != " " and ord(ch) not in cmap})


def test_ha_seis_pesos_legados_de_inter():
    assert len(FONTES_LEGADAS) == 6, (
        f"esperava 6 pesos legados, achei {[f.name for f in FONTES_LEGADAS]}"
    )
    assert gerador_legado.fontes_legadas() == FONTES_LEGADAS


def test_fonte_ativa_e_variavel_e_substitui_os_seis_downloads():
    font = TTFont(FONTE_VARIAVEL)
    eixos = {eixo.axisTag for eixo in font["fvar"].axes}
    assert eixos == {"wght"}
    css = (RAIZ / "frontend" / "brand.css").read_text(encoding="utf-8")
    versao = hashlib.blake2b(FONTE_VARIAVEL.read_bytes(), digest_size=6).hexdigest()
    assert f'/fonts/Inter-Variable.woff2?v={versao}' in css
    for path in FONTES_LEGADAS:
        assert f'/fonts/{path.name}' not in css


def test_todo_peso_tem_os_glifos_de_pt_br():
    for path in FONTES:
        faltando = _faltando(path, TEXTO_PT_BR)
        assert not faltando, (
            f"{path.name} sem glifos pt-BR {faltando} — texto viraria tofu. "
            "Rode scripts/build_inter_subset.py a partir da fonte completa."
        )


def test_todo_peso_tem_os_glifos_de_ui():
    for path in FONTES:
        faltando = _faltando(path, GLIFOS_UI)
        assert not faltando, (
            f"{path.name} sem glifos de UI {faltando} — seta/check viraria "
            "fallback do sistema. Rode scripts/build_inter_subset.py a partir "
            "da fonte completa."
        )


def test_woff2_e_subset_nao_a_fonte_completa():
    """A Inter completa tem ~112 KiB/peso; o subset, ~40. Se alguém repuser a
    completa por cima, o peso da primeira visita volta sem aviso."""
    for path in FONTES:
        n = path.stat().st_size
        assert n < 80_000, f"{path.name} tem {n} B — parece a fonte completa, não o subset."


def test_tnum_sobreviveu_ao_subset():
    """dashboard.css usa font-variant-numeric: tabular-nums; o default do
    pyftsubset descarta a feature `tnum` e os números desalinham sem erro."""
    for path in FONTES:
        font = TTFont(path)
        feats = {rec.FeatureTag for rec in font["GSUB"].table.FeatureList.FeatureRecord}
        assert "tnum" in feats, f"{path.name} perdeu a feature tnum"


def test_fonte_variavel_preserva_kerning_e_marcas_combinantes():
    font = TTFont(FONTE_VARIAVEL)
    feats = {rec.FeatureTag for rec in font["GPOS"].table.FeatureList.FeatureRecord}
    esperadas = {"cpsp", "kern", "mark", "mkmk"}
    assert esperadas <= feats, f"Inter variável sem features GPOS: {sorted(esperadas - feats)}"
