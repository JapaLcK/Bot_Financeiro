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
            "Piggy, compra um CDB para mim",
            "Piggy, faça uma compra de CDB para mim",
            "realize a compra de um fundo para mim",
            "execute a compra de 3 ativos",
            "compra um fundo para mim",
            "Piggy, compra petr4 para mim",
            "Piggy, você compra PETR4 para mim agora?",
            "Piggy, você vende PETR4 para mim agora?",
            "Piggy, qual investimento rende mais?",
            "Piggy, qual CDB tem maior rentabilidade?",
            "compre petr4 para mim",
            "qual PETR4 devo comprar?",
            "Piggy, compre Ethereum para mim",
            "Piggy, compre BTC para mim",
            "compre ETH para mim",
            "venda Solana",
            "compre SOL",
            "Piggy, compre aapl para mim",
            "Piggy, compre btc para mim",
            "venda eth para mim",
            "Piggy, eu deveria investir em CDB?",
            "eu deveria comprar PETR4?",
            "qual ativo eu deveria comprar?",
            "qual ação devo comprar?",
            "Piggy, venda todos os meus ativos",
            "Piggy, venda todos os meus ativos agora",
            "venda 3 ativos amanhã",
            "venda os meus fundos",
            "venda 3 ativos",
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
            "compre sol para o jardim",
            "compre uma meta de vendas",
            "qual wifi6 devo comprar?",
            "compre wifi6 para mim",
            "recebi 100 da compra de ações",
            "recebi 100 pela compra de um fundo",
            "compra de CDB",
            "compra de ações",
            "qual ação devo tomar?",
            "recebi 100 de um bom investimento",
            "recebi bons rendimentos do CDB",
            "recebi 100 pela venda de todos os meus ativos",
            "todos os meus ativos estão registrados?",
            "qual CDB eu deveria registrar na minha carteira?",
            "qual dos meus investimentos rende mais?",
        ):
            with self.subTest(text=text):
                self.assertFalse(_policy(text)["refused"])

    def test_consulta_de_ticker_proprio_nao_e_recomendacao(self) -> None:
        for text in (
            "meu AAPL é bom?",
            "PETR4F da minha carteira é boa?",
            "meu BTC é bom?",
            "meu btc é bom?",
            "Ethereum da minha carteira é bom?",
        ):
            with self.subTest(text=text):
                self.assertFalse(_policy(text)["refused"])

    def test_investimento_ja_feito_e_consulta_de_carteira(self) -> None:
        for text in (
            "qual é o melhor investimento que eu fiz?",
            "qual foi o melhor fundo que eu comprei?",
        ):
            with self.subTest(text=text):
                self.assertFalse(_policy(text)["refused"])


if __name__ == "__main__":
    unittest.main()
