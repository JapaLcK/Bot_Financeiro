#!/usr/bin/env python3
"""Regera frontend/fonts/Inter-*.woff2 como subset latin + latin-1 + pontuação.

A Inter completa traz ~112–115 KiB por peso × 6 pesos (~680 KiB por primeira
visita). O produto é pt-BR: latin + latin-1 cobre todo o texto, e o bloco de
pontuação geral cobre – — “ ” • … ‹ › que o frontend usa. Emoji fica com o
sistema (não vem da Inter).

Idempotente: subset de subset com os mesmos unicodes é estável. Para atualizar
a versão da Inter: substitua os .woff2 completos e rode o script de novo.

Mantém TODAS as layout features (--layout-features='*'): o dashboard usa
`font-variant-numeric: tabular-nums` (dashboard.css), e o default do pyftsubset
descartaria a feature `tnum` — número deixaria de alinhar sem erro nenhum.

Uso: .venv/bin/python scripts/build_inter_subset.py
Paridade servida: tests/test_inter_subset.py
"""
from __future__ import annotations

import pathlib

from fontTools.subset import Options, Subsetter, parse_unicodes
from fontTools.ttLib import TTFont

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTS = RAIZ / "frontend" / "fonts"

# latin, latin-1, diacríticos combinantes (texto NFD não vira tofu), Δ,
# pontuação geral (– — “ ” • … ‹ ›), setas/símbolos de UI que o frontend USA
# (→ ← ↑ ↓ ↔ ↪ ↻ ⤓ ✓ ≥ ≤ ≈ ● ▲ ▼ ◀ ▶ — CTAs, toasts, content: do CSS),
# €, ₿, −, ＋ — levantado por varredura de frontend/**.{html,js,css} e
# conferido por pixel-diff (achado MÉDIA-4 do Tester: 17 glifos usados tinham
# ficado de fora).
UNICODES = (
    "0020-007E,00A0-00FF,0300-036F,0394,2000-206F,20AC,20BF,2212,FF0B,"
    "2190-2194,21AA,21BB,2248,2264-2265,2713,25B2,25B6,25BC,25C0,25CF,2913"
)


def subset_font(path: pathlib.Path) -> None:
    options = Options()
    options.layout_features = ["*"]  # tnum/lnum/etc. — ver docstring
    options.flavor = "woff2"
    font = TTFont(path)
    subsetter = Subsetter(options)
    subsetter.populate(unicodes=parse_unicodes(UNICODES))
    subsetter.subset(font)
    antes = path.stat().st_size
    font.save(path)
    print(f"  {path.name}: {antes/1024:.0f} KiB -> {path.stat().st_size/1024:.0f} KiB")


def main() -> None:
    alvos = sorted(FONTS.glob("Inter-*.woff2"))
    if not alvos:
        raise SystemExit(f"nenhuma Inter-*.woff2 em {FONTS}")
    for path in alvos:
        subset_font(path)


if __name__ == "__main__":
    main()
