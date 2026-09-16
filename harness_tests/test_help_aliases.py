from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "whatsapp_harness_safe.py"


def _response(text: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--layer", "core", "--text", text],
        cwd=REPO,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(result.stdout)["response"]


class HelpAliasesTests(unittest.TestCase):
    def test_painel_e_link_recebem_ajuda_do_bot(self) -> None:
        self.assertIn("dashboard", _response("como usar o painel?").lower())
        self.assertIn("link 123456", _response("como usar o comando link?").lower())

    def test_assuntos_externos_com_as_mesmas_palavras_continuam_fora(self) -> None:
        for text in (
            "como usar o painel do carro?",
            "como usar o link do meu site?",
        ):
            with self.subTest(text=text):
                self.assertIn("Só consigo ajudar com finanças pessoais", _response(text))


if __name__ == "__main__":
    unittest.main()
