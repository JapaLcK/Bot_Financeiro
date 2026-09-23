from __future__ import annotations

import unittest
from unittest.mock import patch

from _harness_lote import rodar_lote


class InvestmentPolicyRouteTests(unittest.TestCase):
    def test_venda_de_ativo_responde_a_descricao_pendente(self) -> None:
        from core.intent_classifier import IntentResult
        from core.intent_router import route
        from core.types import IncomingMessage

        pending = {"payload": {"intent": "launches.add", "entities": {"valor": 100}}}
        with (
            patch("core.intent_router.db.get_pending_action", return_value=None),
            patch("core.intent_router.h_pending.get_pending_clarification", return_value=pending),
            patch("core.intent_router._resolve_clarification", return_value="descrição aceita") as resolve,
        ):
            answer = route(
                IntentResult(intent="out_of_scope", confidence=0),
                IncomingMessage(platform="whatsapp", user_id=7, text="venda de PETR4"),
            )
        self.assertEqual(answer, "descrição aceita")
        resolve.assert_called_once()

    def test_pedidos_sem_ativo_e_imperativos_recebem_recusa_antes_da_ia(self) -> None:
        texts = (
            "Piggy, onde devo investir meu dinheiro?",
            "Piggy, em que devo investir?",
            "Piggy, investe 100 reais em CDB para mim",
            "Piggy, aplica 100 reais em CDB para mim",
            "Piggy, qual CDB você compraria?",
            "Piggy, qual CDB você escolheria?",
        )
        for text, payload in zip(texts, rodar_lote("core", texts)):
            with self.subTest(text=text):
                self.assertEqual(payload["exit"], 0, payload)
                self.assertEqual(payload["blocked"], [])
                self.assertIn("não posso comprar", payload["response"].lower())
