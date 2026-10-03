from __future__ import annotations

import json
import unittest

from _harness_lote import executar, rodar_lote

CASOS = {
    "core": [
        "saldo",
        "faturas",
        "compre PETR4 para mim",
        "conte uma piada",
        "como faço para criar uma caixinha",
    ],
    "policy": [
        "compre PETR4 para mim",
        "recebi 100 da venda de ações",
        "qual fundo de tela devo comprar?",
    ],
}


class HarnessLoteTests(unittest.TestCase):
    def test_lote_em_qualquer_ordem_equivale_a_execucao_isolada(self) -> None:
        for layer, textos in CASOS.items():
            with self.subTest(layer=layer):
                isolado = [rodar_lote(layer, [texto])[0] for texto in textos]
                self.assertEqual(rodar_lote(layer, textos), isolado)
                self.assertEqual(rodar_lote(layer, textos[::-1]), isolado[::-1])

    def test_exit_por_linha_e_o_do_processo_e_o_maior(self) -> None:
        result = executar("--layer", "core", "--text", "faturas", "--text", "saldo")
        linhas = [json.loads(linha) for linha in result.stdout.splitlines()]
        self.assertEqual([linha["exit"] for linha in linhas], [2, 0], result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_lote_para_na_primeira_violacao(self) -> None:
        result = executar(
            "--layer", "core", "--core-unsafe-env", "--text", "saldo", "--text", "faturas"
        )
        self.assertEqual(result.returncode, 70, result.stderr or result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout)

    def test_adapter_recusa_mais_de_uma_frase(self) -> None:
        result = executar("--text", "a", "--text", "b")
        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
