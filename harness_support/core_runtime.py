from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from harness_support.safe_runtime import SafetyGuards


def _empty_balance() -> dict[str, Any]:
    return {
        "manual": 0,
        "open_finance_bank": 0,
        "consolidated": 0,
        "of_bank_count": 0,
        "bank_movements": {"pending_count": 0},
    }


def _is_internal_failure_response(response: str) -> bool:
    normalized = response.strip().casefold()
    return (
        normalized.startswith(("❌ erro ", "erro ao ", "⚠️ erro ao "))
        or "ocorreu um erro interno" in normalized
        or "database_url" in normalized
        or "deu erro técnico" in normalized
        or normalized.startswith("deu erro ao ")
        or normalized.startswith("⚠️ não consegui gerar seu link do dashboard agora")
    )


def run_core_case(
    text: str, *, force_internal_error: bool = False, force_safety_violation: bool = False,
) -> dict[str, Any]:
    """Executa o núcleo real com bordas externas e leituras persistentes locais."""
    guards = SafetyGuards(allowed_write_root=Path.cwd())
    guards.install()
    try:
        from core.handle_incoming import handle_incoming
        from core.intent_classifier import classify
        from core.types import IncomingMessage

        billing_side_effect: Any = (
            RuntimeError("falha interna simulada") if force_internal_error else None
        )
        if force_safety_violation:
            def unsafe_billing(*_args: Any, **_kwargs: Any) -> None:
                Path(".env").read_text(encoding="utf-8")

            billing_side_effect = unsafe_billing

        with (
            patch("core.handle_incoming._paywall_gate", return_value=None),
            patch("core.handle_incoming.db.ensure_user", return_value=None),
            patch("core.handle_incoming.db.update_last_activity", return_value=None),
            patch("core.handle_incoming.db.get_pending_action", return_value=None),
            patch(
                "core.handle_incoming.handle_open_finance_whatsapp_command",
                return_value=None,
            ),
            patch(
                "core.services.billing_commands.handle_billing_command",
                return_value=None,
                side_effect=billing_side_effect,
            ),
            patch(
                "core.services.ai_chat_commands.handle_ai_chat_command",
                return_value=None,
            ),
            patch("core.services.plan_service.is_pro", return_value=False),
            patch("core.services.plan_service.ai_chat_allowed", return_value=False),
            patch(
                "core.services.plan_service.consolidated_balance_enabled",
                return_value=False,
            ),
            patch("core.intent_router.db.get_pending_action", return_value=None),
            patch(
                "core.intent_router.h_pending.get_pending_clarification",
                return_value=None,
            ),
            patch(
                "core.handlers.balance.db.get_consolidated_balance",
                side_effect=lambda _uid: _empty_balance(),
            ),
            patch("core.handlers.balance.db.get_launches_by_period", return_value=[]),
            patch(
                "core.handlers.balance.db.get_summary_by_period",
                return_value={"despesa": 0.0},
            ),
            patch("core.handlers.balance.db.list_cards", return_value=[]),
            patch("core.handle_incoming.logger.error") as error_log,
        ):
            intent = classify(text, allow_ai=False)
            responses = handle_incoming(
                IncomingMessage(platform="whatsapp", user_id=7, text=text),
            )
        response = responses[0].text if responses else ""
        internal_error = _is_internal_failure_response(response) or any(
            call.args
            and isinstance(call.args[0], str)
            and call.args[0].startswith("handle_incoming FAILED")
            for call in error_log.call_args_list
        )
        blocked = list(guards.events)
        answered = bool(response.strip()) and not internal_error and not blocked
        return {
            "intent": intent.intent,
            "confidence": intent.confidence,
            "response": response,
            "answered": answered,
            "internal_error": internal_error,
            "outcome": (
                "safety_violation"
                if blocked
                else "internal_error" if internal_error
                else "answered" if answered else "no_answer"
            ),
            "blocked": blocked,
        }
    finally:
        guards.close()
