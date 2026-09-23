from __future__ import annotations

import unittest

from _harness_lote import rodar_lote


def _responses(*texts: str) -> list[str]:
    lote = rodar_lote("core", texts)
    for payload in lote:
        if payload["exit"] != 0:
            raise AssertionError(payload)
    return [payload["response"] for payload in lote]


class HelpAliasesTests(unittest.TestCase):
    def test_painel_e_link_recebem_ajuda_do_bot(self) -> None:
        painel, comando, nao_consigo, nao_funciona = _responses(
            "como usar o painel?",
            "como usar o comando link?",
            "não consigo usar o link",
            "meu link não funciona",
        )
        self.assertIn("dashboard", painel.lower())
        self.assertIn("link 123456", comando.lower())
        self.assertIn("link 123456", nao_consigo.lower())
        self.assertIn("link 123456", nao_funciona.lower())

    def test_assuntos_externos_com_as_mesmas_palavras_continuam_fora(self) -> None:
        texts = (
            "como usar o painel do carro?",
            "como usar o link do meu site?",
            "não consigo usar o link do meu site",
            "como vejo o histórico do navegador?",
        )
        for text, response in zip(texts, _responses(*texts)):
            with self.subTest(text=text):
                self.assertIn("Só consigo ajudar com finanças pessoais", response)

    def test_ajuda_para_historico_e_registros_de_lancamentos(self) -> None:
        cases = (
            ("como vejo meu histórico?", "listar lançamentos"),
            ("como registro o que recebi?", "recebi 1000 salario"),
            ("como registro o que gastei?", "gastei 50 mercado"),
        )
        for (text, expected), response in zip(cases, _responses(*(t for t, _ in cases))):
            with self.subTest(text=text):
                self.assertIn(expected, response.lower())

    def test_ajuda_para_boletos_e_contas_a_pagar(self) -> None:
        texts = ("como vejo meus boletos?", "como usar contas a pagar?")
        for text, response in zip(texts, _responses(*texts)):
            with self.subTest(text=text):
                response = response.lower()
                self.assertIn("contas a pagar", response)
                self.assertIn("vencem", response)

    def test_ajuda_para_limite_e_regras(self) -> None:
        limite, regras = _responses("como vejo meu limite?", "como vejo minhas regras?")
        self.assertIn("cartões, crédito", limite.lower())
        self.assertIn("regras de categoria", regras.lower())

    def test_qual_comando_usar_para_ver_investimentos(self) -> None:
        texts = (
            "que comando devo usar para listar investimentos?",
            "qual comando devo usar para ver meu CDB?",
        )
        for text, response in zip(texts, _responses(*texts)):
            with self.subTest(text=text):
                response = response.lower()
                self.assertIn("investimentos", response)
                self.assertNotIn("não posso comprar, vender", response)

    def test_limite_e_regras_externos_continuam_fora(self) -> None:
        texts = ("como vejo o limite de velocidade?", "como vejo as regras do jogo?")
        for text, response in zip(texts, _responses(*texts)):
            with self.subTest(text=text):
                self.assertIn("Só consigo ajudar com finanças pessoais", response)


if __name__ == "__main__":
    unittest.main()
