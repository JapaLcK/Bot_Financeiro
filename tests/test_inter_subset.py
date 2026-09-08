"""frontend/fonts/Inter-*.woff2 são subset (latin + latin-1 + pontuação).

O risco do subset é silencioso: glifo ausente não dá erro, vira tofu. Estes
testes garantem que os glifos de texto pt-BR real existem em TODOS os pesos
servidos — inclusive os acentuados, que é onde um subset "latin only" quebraria.

Gerador: scripts/build_inter_subset.py.
"""
from __future__ import annotations

import pathlib

from fontTools.ttLib import TTFont

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTES = sorted((RAIZ / "frontend" / "fonts").glob("Inter-*.woff2"))

# Texto pt-BR real: acentos dos dois casos, ç, moeda, número, travessão, aspas.
TEXTO_PT_BR = "Ações à çedilha — R$ 1.234,56 até ô, José já viu: “ótimo”, né? Saúde ü í ú Â Ê Õ…"
# Glifos de UI que o frontend usa em texto/CSS (CTA "→", toast "✓", "⤓ Sacar
# tudo", content:"✓"…) — os 17 que um subset só-texto deixou cair (MÉDIA-4).
GLIFOS_UI = "→←✓≥≤≈↓↑●▲▼◀▶↻↔⤓↪"


def _faltando(path: pathlib.Path, texto: str) -> list[str]:
    cmap = TTFont(path).getBestCmap()
    return sorted({ch for ch in texto if ch != " " and ord(ch) not in cmap})


def test_ha_seis_pesos_de_inter():
    assert len(FONTES) == 6, f"esperava 6 pesos, achei {[f.name for f in FONTES]}"


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
