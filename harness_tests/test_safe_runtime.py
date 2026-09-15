from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "whatsapp_harness_safe.py"
LIVE_SCRIPT = REPO / "scripts" / "whatsapp_qa_vault_harness.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    safe_env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        env=safe_env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


class SafeRuntimeTests(unittest.TestCase):
    def test_harness_ao_vivo_exige_autorizacao_explicita(self) -> None:
        result = subprocess.run(
            [sys.executable, str(LIVE_SCRIPT)],
            cwd=REPO,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(result.returncode, 64, result.stderr or result.stdout)
        self.assertIn("bloqueada antes de ler .env", result.stdout)

    def test_executa_payload_real_do_adaptador_sem_tocar_bordas(self) -> None:
        result = _run("--text", "qual é meu saldo?")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["extracted"], 1)
        self.assertTrue(payload["delivered"])
        self.assertEqual(payload["outcome"], "delivered")
        self.assertEqual(payload["blocked"], [])
        self.assertTrue(payload["cwd_is_temporary"])
        self.assertTrue(payload["environment_sanitized"])
        self.assertEqual(
            payload["replies"],
            [{"to": "5511999999999", "body": "harness recebeu: qual é meu saldo?"}],
        )

    def test_saida_vazia_do_nucleo_recebe_fallback(self) -> None:
        result = _run("--handler-behavior", "empty-output")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["replies"]), 1)
        self.assertIn("não consegui", payload["replies"][0]["body"].lower())

    def test_fora_do_escopo_recebe_limite_claro(self) -> None:
        for text in (
            "qual é a previsão do tempo amanhã?",
            "conte uma piada",
            "banana radioativo do espaco",
        ):
            with self.subTest(text=text):
                result = _run("--layer", "core", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["intent"], "out_of_scope")
                self.assertEqual(payload["blocked"], [])
                self.assertTrue(payload["answered"])
                self.assertEqual(payload["outcome"], "answered")
                self.assertIn("só consigo ajudar com finanças", payload["response"].lower())

    def test_pedido_para_operar_investimento_recebe_recusa_explicita(self) -> None:
        cases = (
            "compre ações da Petrobras para mim",
            "me indique uma ação para investir",
            "me indica uma ação boa",
            "você recomenda qual ação?",
            "você indica qual fundo?",
            "recomende um ETF para mim",
            "qual investimento devo fazer?",
            "qual FII devo comprar?",
            "compre PETR4 para mim",
            "compre petr4 para mim",
            "piggy, recomende um ETF",
            "qual é o melhor investimento para mim?",
            "qual ação é boa?",
            "qual é o melhor investimento para minha aposentadoria?",
            "qual ação é boa para meus filhos?",
            "qual seria meu melhor investimento?",
            "qual deve ser meu melhor investimento?",
            "vale a pena investir em CDB?",
            "Piggy, vale a pena investir em CDB?",
            "investir em CDB vale a pena?",
            "qual CDB é bom comparado ao da minha carteira?",
            "quais ETFs você recomenda?",
            "vale a pena investir em CDBs?",
        )
        for text in cases:
            with self.subTest(text=text):
                result = _run("--layer", "core", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["intent"], "out_of_scope")
                self.assertEqual(payload["blocked"], [])
                self.assertIn("não posso comprar", payload["response"].lower())
                self.assertIn("por você", payload["response"].lower())

    def test_receita_de_venda_de_ativo_nao_e_ordem_de_venda(self) -> None:
        for text in (
            "recebi 100 da venda de ações",
            "quanto eu receberia se vendesse PETR4?",
            "se eu vendesse PETR4 ontem, qual seria meu lucro?",
        ):
            with self.subTest(text=text):
                result = _run("--layer", "policy", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertFalse(payload["refused"])
                self.assertEqual(payload["blocked"], [])

    def test_ordem_explicita_de_venda_continua_recusada(self) -> None:
        for text in (
            "venda PETR4",
            "por favor venda minhas ações",
            "por gentileza, venda PETR4",
            "me vende PETR4",
            "Piggy, me vende PETR4",
            "quero que você venda PETR4",
            "vende PETR4 para mim",
            "gostaria que você vendesse PETR4",
            "faça a venda das minhas ações",
        ):
            with self.subTest(text=text):
                result = _run("--layer", "policy", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertTrue(payload["refused"])

    def test_recusa_de_investimento_antecede_fallback_de_ia(self) -> None:
        source = (REPO / "core" / "handle_incoming.py").read_text(encoding="utf-8")

        refusal_position = source.index("policy_refusal = investment_action_refusal(text)")
        explicit_ai_position = source.index("ai_reply = handle_ai_chat_command")
        ai_position = source.index("should_try_ai_fallback =")
        self.assertLess(refusal_position, explicit_ai_position)
        self.assertLess(refusal_position, ai_position)

    def test_consulta_da_carteira_nao_e_tratada_como_recomendacao(self) -> None:
        for text in (
            "qual foi meu melhor investimento?",
            "qual meu melhor investimento?",
            "qual dos meus fundos foi o melhor?",
            "dos meus investimentos, qual foi o melhor?",
            "meu investimento é bom?",
            "poderia mostrar meu melhor investimento?",
            "como estão meus investimentos?",
            "qual o rendimento do meu FII?",
            "qual CDB da minha carteira foi o melhor?",
            "qual das ações da minha carteira é boa?",
            "qual dos CDBs da minha carteira foi o melhor?",
            "qual criptomoeda da minha carteira foi a melhor?",
            "qual renda fixa da minha carteira foi a melhor?",
            "qual PETR4 da minha carteira foi o melhor?",
            "PETR4 da minha carteira é boa?",
            "como registrar no PigBank que comecei a investir em CDB?",
        ):
            with self.subTest(text=text):
                result = _run("--layer", "core", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertNotIn("não posso comprar", payload["response"].lower())

    def test_erro_interno_nao_e_contabilizado_como_resposta(self) -> None:
        result = _run("--layer", "core", "--core-error", "--text", "saldo")

        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["response"])
        self.assertFalse(payload["answered"])
        self.assertTrue(payload["internal_error"])
        self.assertEqual(payload["outcome"], "internal_error")

    def test_ajuda_financeira_classificada_fora_do_escopo_continua_util(self) -> None:
        cases = {
            "como faço para criar uma caixinha": "para criar uma caixinha",
            "como faço para importar um extrato OFX": "para importar um ofx",
            "como faço um lançamento": "para fazer um lançamento",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = _run("--layer", "core", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["intent"], "out_of_scope")
                self.assertEqual(payload["blocked"], [])
                self.assertIn(expected, payload["response"].lower())

    def test_pergunta_financeira_nao_reconhecida_recebe_ajuda_contextual(self) -> None:
        cases = {
            "não entendi meu extrato": "importar um extrato ofx",
            "como funciona minha caixinha?": "caixinhas",
            "quero saber de investimentos": "investimentos",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = _run("--layer", "core", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["intent"], "out_of_scope")
                self.assertTrue(payload["answered"])
                self.assertEqual(payload["outcome"], "answered")
                self.assertIn(expected, payload["response"].lower())

    def test_palavra_ambigua_sem_contexto_financeiro_fica_fora_do_escopo(self) -> None:
        for text in (
            "qual é o limite de velocidade?",
            "quando vence minha habilitação?",
            "como importar uma biblioteca Python?",
            "como usar o limite de velocidade?",
            "como usar importar em Python?",
            "como chamar uma pessoa baixinha?",
            "PigBank, conte uma piada",
            "vale a pena comprar um carro?",
            "vale a pena investir em um curso?",
        ):
            with self.subTest(text=text):
                result = _run("--layer", "core", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertIn("só consigo ajudar com finanças", payload["response"].lower())

    def test_topicos_financeiros_secundarios_mantem_ajuda(self) -> None:
        cases = {
            "não consigo vincular minha conta do WhatsApp": "vinculação",
            "não entendi o report diário": "report diário",
            "não entendi as regras de categoria": "categorias",
            "como usar o report diário?": "report diário",
            "como usar a vinculação da conta?": "vincular",
            "como faço para categorizar meu gasto?": "categorias",
            "como faço para categorizar meus gastos?": "categorias",
            "como usar o report diário no PigBank?": "report diário",
            "como usar o report diário de gastos?": "report diário",
            "como faço para vincular minhas contas do WhatsApp ao PigBank?": "vincular",
            "como usar o dashboard?": "dashboard",
            "caxinha banana cosmica": "caixinhas",
            "categoria marciana aleatoria": "categorias",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                result = _run("--layer", "core", "--text", text)

                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                payload = json.loads(result.stdout)
                self.assertIn(expected, payload["response"].lower())

    def test_excecao_do_nucleo_recebe_fallback(self) -> None:
        result = _run("--handler-behavior", "error")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["replies"]), 1)
        self.assertIn("não consegui", payload["replies"][0]["body"].lower())

    def test_falha_da_observabilidade_nao_impede_fallback(self) -> None:
        result = _run("--handler-behavior", "error", "--event-log-error")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(len(payload["replies"]), 1)
        self.assertIn("não consegui", payload["replies"][0]["body"].lower())

    def test_falha_de_envio_nao_e_registrada_como_entrega(self) -> None:
        result = _run("--send-error")

        self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["extracted"], 1)
        self.assertFalse(payload["delivered"])
        self.assertEqual(payload["outcome"], "delivery_failed")
        self.assertEqual(payload["replies"], [])

    def test_controle_positivo_bloqueia_leitura_de_env(self) -> None:
        result = _run("--probe", "env")

        self.assertEqual(result.returncode, 70, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"], "SAFETY_VIOLATION")
        self.assertTrue(payload["detail"].startswith("env:"))

    def test_controle_positivo_bloqueia_rede(self) -> None:
        result = _run("--probe", "network")

        self.assertEqual(result.returncode, 70, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"], "SAFETY_VIOLATION")
        self.assertTrue(payload["detail"].startswith("network:"))

    def test_controle_positivo_bloqueia_escrita_fora_do_temporario(self) -> None:
        result = _run("--probe", "write")

        self.assertEqual(result.returncode, 70, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["error"], "SAFETY_VIOLATION")
        self.assertTrue(payload["detail"].startswith("write:"))


if __name__ == "__main__":
    unittest.main()
