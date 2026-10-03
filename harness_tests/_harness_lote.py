from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "whatsapp_harness_safe.py"


def executar(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        },
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def rodar_lote(layer: str, textos: list[str] | tuple[str, ...], *extra: str) -> list[dict]:
    """Um processo para N frases; devolve uma linha JSON por frase, na ordem."""
    args = ["--layer", layer, *extra]
    for texto in textos:
        args += ["--text", texto]
    result = executar(*args)
    linhas = [json.loads(linha) for linha in result.stdout.splitlines()]
    if len(linhas) < len(textos):
        ultima = linhas[-1] if linhas else {}
        raise AssertionError(f"parou em {ultima.get('text')}: {ultima or result.stderr}")
    if [linha.get("text") for linha in linhas] != list(textos):
        raise AssertionError(f"ordem das linhas difere da entrada: {result.stdout}")
    return linhas
