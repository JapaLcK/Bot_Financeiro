from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "whatsapp_harness_safe.py"


def _policy(text: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--layer", "policy", "--text", text],
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
    return json.loads(result.stdout)


class InvestmentPolicyMatrixTests(unittest.TestCase):
    def test_recusa_recomendacoes_e_ordens_com_ativos_ambiguos(self) -> None:
        for text in (
            "me indique um fundo",
            "recomende um fundo para mim",
            "me indica um ativo?",
            "qual fundo devo comprar?",
            "compre um fundo para mim",
            "venda meu fundo",
            "qual ouro você recomenda?",
            "qual moeda você recomenda?",
            "qual é o melhor fundo?",
            "qual é o melhor ativo?",
            "qual é o melhor ouro?",
            "qual é a melhor moeda?",
            "qual fundo vale a pena?",
            "ouro vale a pena?",
            "quais são os melhores fundos?",
            "quais fundos são bons?",
            "quais são os melhores ativos?",
            "você indica qual fundo?",
            "me recomenda algum fundo?",
            "pode me recomendar um fundo?",
            "qual fundo é o melhor?",
            "qual ativo é o melhor?",
            "qual moeda é a melhor?",
            "quais fundos são os melhores?",
            "qual ouro seria o melhor?",
            "qual fundo cambial você recomenda?",
            "qual fundo DI você recomenda?",
            "qual fundo de crédito privado você recomenda?",
            "você recomendaria algum fundo?",
            "aplique em um fundo",
            "Piggy, compre AAPL para mim",
            "Piggy, venda PETR4F",
            "compre MSFT para mim",
            "compre PETR4F",
        ):
            with self.subTest(text=text):
                self.assertTrue(_policy(text)["refused"])

    def test_preserva_usos_nao_financeiros(self) -> None:
        for text in (
            "qual fundo de tela devo comprar?",
            "compre um fundo azul para mim",
            "qual ativo do jogo devo comprar?",
            "qual moeda do jogo você recomenda?",
            "qual fundo da foto é melhor?",
            "qual moeda é melhor no jogo?",
            "qual medalha de ouro é melhor?",
            "qual fundo de maquiagem você recomenda?",
            "qual fundo musical você recomenda?",
            "compre um fundo transparente para mim",
            "qual ativo de software devo comprar?",
            "qual fundo de maquiagem devo aplicar?",
            "qual fundo de tela devo aplicar?",
            "qual ativo de software devo aplicar?",
            "qual fundo musical devo aplicar?",
            "compre BOLO para mim",
            "compre um livro para mim",
        ):
            with self.subTest(text=text):
                self.assertFalse(_policy(text)["refused"])

    def test_consulta_de_ticker_proprio_nao_e_recomendacao(self) -> None:
        for text in (
            "meu AAPL é bom?",
            "PETR4F da minha carteira é boa?",
        ):
            with self.subTest(text=text):
                self.assertFalse(_policy(text)["refused"])


if __name__ == "__main__":
    unittest.main()
