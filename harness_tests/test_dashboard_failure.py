from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "whatsapp_harness_safe.py"


class DashboardFailureTests(unittest.TestCase):
    def test_falha_do_dashboard_nao_conta_como_resposta(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--layer", "core", "--text", "dashboard"],
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
        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["intent"], "dashboard.open")
        self.assertIn("não consegui gerar seu link", payload["response"].lower())
        self.assertFalse(payload["answered"])
        self.assertTrue(payload["internal_error"])
        self.assertEqual(payload["outcome"], "internal_error")


if __name__ == "__main__":
    unittest.main()
