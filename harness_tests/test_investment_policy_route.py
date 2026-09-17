from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "whatsapp_harness_safe.py"


class InvestmentPolicyRouteTests(unittest.TestCase):
    def test_pedidos_sem_ativo_e_imperativos_recebem_recusa_antes_da_ia(self) -> None:
        for text in (
            "Piggy, onde devo investir meu dinheiro?",
            "Piggy, em que devo investir?",
            "Piggy, investe 100 reais em CDB para mim",
            "Piggy, aplica 100 reais em CDB para mim",
        ):
            with self.subTest(text=text):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "--layer", "core", "--text", text],
                    cwd=REPO,
                    env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO)},
                    text=True, capture_output=True, timeout=15, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["blocked"], [])
                self.assertIn("não posso comprar", payload["response"].lower())
