"""frontend/brand/icon.png é logo de ~40 px espalhado pelo frontend
(quem referencia: `grep -rln "brand/icon.png" frontend/`): tem teto de peso.

Já foi um PNG de 542.919 B (1584×1682) baixado em toda primeira visita.
Se alguém repuser o original por cima, este teste avisa.
"""
import pathlib

ICON = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "brand" / "icon.png"


def test_icon_png_dentro_do_teto():
    assert ICON.stat().st_size <= 20_480, f"icon.png com {ICON.stat().st_size} B (teto: 20 KiB)"
