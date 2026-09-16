from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "whatsapp_harness_safe.py"


class DeliveryFailureTests(unittest.TestCase):
    def test_falha_apos_processamento_nao_manda_repetir_escrita(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--send-error-once"],
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
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["outcome"], "delivered")
        self.assertIn("pode ter sido concluída", payload["replies"][0]["body"])
        self.assertNotIn("tente novamente", payload["replies"][0]["body"].lower())

    def test_falha_ao_confirmar_anexo_antes_do_nucleo_pede_reenvio(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--attachment", "--send-error-once"],
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
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["outcome"], "delivered")
        self.assertEqual(payload["blocked"], [])
        self.assertIn("Tente novamente", payload["replies"][0]["body"])
        self.assertNotIn("pode ter sido concluída", payload["replies"][0]["body"])


if __name__ == "__main__":
    unittest.main()
