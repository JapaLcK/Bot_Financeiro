from __future__ import annotations

from pathlib import Path
from typing import Any

from harness_support.safe_runtime import SafetyGuards


def run_policy_case(text: str) -> dict[str, Any]:
    """Executa somente a política determinística, sob as mesmas guardas."""
    guards = SafetyGuards(allowed_write_root=Path.cwd())
    guards.install()
    try:
        from core.intent_router import investment_action_refusal

        response = investment_action_refusal(text)
        return {
            "refused": response is not None,
            "response": response or "",
            "blocked": guards.events,
        }
    finally:
        guards.close()
