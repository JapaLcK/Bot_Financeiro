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
            "como vejo o histórico do navegador?",
        ):
            with self.subTest(text=text):
                self.assertIn("Só consigo ajudar com finanças pessoais", _response(text))

    def test_ajuda_para_historico_e_registros_de_lancamentos(self) -> None:
        for text, expected in (
            ("como vejo meu histórico?", "listar lançamentos"),
            ("como registro o que recebi?", "recebi 1000 salario"),
            ("como registro o que gastei?", "gastei 50 mercado"),
        ):
            with self.subTest(text=text):
                self.assertIn(expected, _response(text).lower())

    def test_ajuda_para_boletos_e_contas_a_pagar(self) -> None:
        for text in ("como vejo meus boletos?", "como usar contas a pagar?"):
            with self.subTest(text=text):
                response = _response(text).lower()
                self.assertIn("contas a pagar", response)
                self.assertIn("vencem", response)

    def test_ajuda_para_limite_e_regras(self) -> None:
        self.assertIn("cartões, crédito", _response("como vejo meu limite?").lower())
        self.assertIn("regras de categoria", _response("como vejo minhas regras?").lower())

    def test_qual_comando_usar_para_ver_investimentos(self) -> None:
        for text in (
            "que comando devo usar para listar investimentos?",
            "qual comando devo usar para ver meu CDB?",
        ):
            with self.subTest(text=text):
                response = _response(text).lower()
                self.assertIn("investimentos", response)
                self.assertNotIn("não posso comprar, vender", response)

    def test_limite_e_regras_externos_continuam_fora(self) -> None:
        for text in ("como vejo o limite de velocidade?", "como vejo as regras do jogo?"):
            with self.subTest(text=text):
                self.assertIn("Só consigo ajudar com finanças pessoais", _response(text))


if __name__ == "__main__":
    unittest.main()
