from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness_support.safety_guards import SafetyGuards, SafetyViolation


_MISSING = object()


@dataclass
class ModuleOverrides:
    originals: dict[str, Any] = field(default_factory=dict)

    def remember(self, name: str) -> None:
        self.originals.setdefault(name, sys.modules.get(name, _MISSING))

    def install(self, name: str, **members: Any) -> types.ModuleType:
        self.remember(name)
        module = types.ModuleType(name)
        for member_name, value in members.items():
            setattr(module, member_name, value)
        sys.modules[name] = module
        return module

    def close(self) -> None:
        for name, original in reversed(tuple(self.originals.items())):
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def install_runtime_boundaries(
    modules: ModuleOverrides,
    replies: list[dict[str, str]],
    *,
    safety_events: list[str],
    handler_behavior: str = "reply",
    event_log_error: bool = False,
    send_behavior: str = "reply",
    user_lookup_error: bool = False,
) -> None:
    """Substitui somente I/O; o parser e o fluxo do adaptador continuam reais."""
    send_attempts = 0

    def deny_boundary(event: str) -> None:
        safety_events.append(event)
        raise SafetyViolation(event)

    def send_text(*, to: str, body: str, **_: Any) -> dict[str, Any]:
        nonlocal send_attempts
        send_attempts += 1
        if send_behavior == "error" or (
            send_behavior == "error-once" and send_attempts == 1
        ):
            raise TimeoutError("falha sintética de envio")
        replies.append({"to": to, "body": body})
        return {"contacts": [{"wa_id": to}], "messages": [{"id": "harness-out-1"}]}

    modules.install(
        "adapters.whatsapp.wa_client",
        download_media=lambda *_a, **_k: deny_boundary("media:download"),
        send_interactive_buttons=lambda **_k: None,
        send_interactive_list=lambda **_k: None,
        send_text=send_text,
        send_typing_indicator=lambda *_a, **_k: None,
    )
    modules.install(
        "adapters.whatsapp.wa_tutorial",
        get_tutorial_button_id=lambda _raw: None,
        handle_tutorial_button=lambda *_a, **_k: None,
        send_welcome=lambda *_a, **_k: None,
    )
    modules.install(
        "adapters.whatsapp.wa_help_menu",
        get_help_menu_id=lambda _raw: None,
        send_help_menu=lambda *_a, **_k: None,
        send_help_section=lambda *_a, **_k: None,
    )
    modules.install(
        "adapters.whatsapp.wa_commands_menu",
        get_commands_menu_id=lambda _raw: None,
        send_commands_menu=lambda *_a, **_k: None,
        send_commands_section=lambda *_a, **_k: None,
    )
    modules.install("core.help_text", HELP_TRIGGERS=set())
    modules.install("core.intent_router", abandona_pergunta_de_valor=lambda _text: False)
    def log_system_event_sync(*_args: Any, **_kwargs: Any) -> None:
        if event_log_error:
            raise RuntimeError("falha sintética da observabilidade")

    modules.install("core.observability", log_system_event_sync=log_system_event_sync)
    modules.install(
        "core.handlers.report",
        disable=lambda _uid: "",
        disable_weekly=lambda _uid: "",
        disable_monthly=lambda _uid: "",
    )
    modules.install("core.handlers", report=sys.modules["core.handlers.report"])
    modules.install("core.services.commands_intent", is_commands_intent=lambda _text: False)

    from core.types import OutgoingMessage

    def handle_incoming(incoming: Any, **_kwargs: Any) -> list[OutgoingMessage]:
        if handler_behavior == "unsafe-env":
            Path(".env").read_text(encoding="utf-8")
        if handler_behavior == "error":
            raise RuntimeError("falha sintética do núcleo")
        if handler_behavior == "empty-output":
            return [OutgoingMessage(text="")]
        return [OutgoingMessage(text=f"harness recebeu: {incoming.text}")]

    modules.install("core.handle_incoming", handle_incoming=handle_incoming)

    class RestorePending:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: Any) -> bool:
            return False

    def canonical_user(*_args: Any, **_kwargs: Any) -> int:
        if user_lookup_error:
            raise RuntimeError("falha sintética antes do núcleo")
        return 7

    modules.install(
        "db",
        attempt_whatsapp_phone_link=lambda _wa_id, current_user_id=None: {
            "status": "already_linked",
            "user_id": current_user_id,
        },
        claim_pending_action=lambda *_a, **_k: False,
        consume_pending_action=lambda *_a, **_k: False,
        get_conn=lambda: deny_boundary("database:get_conn"),
        get_or_create_canonical_user=canonical_user,
        get_pending_action=lambda _uid: None,
        restore_pending_on_error=lambda *_a, **_k: RestorePending(),
        set_pending_action=lambda *_a, **_k: None,
        set_whatsapp_updates_opt_out=lambda *_a, **_k: None,
        update_launch_category=lambda *_a, **_k: None,
    )


def whatsapp_payload(text: str, *, attachment: bool = False) -> dict[str, Any]:
    inbound = {
        "id": "harness-in-1",
        "from": "5511999999999",
        "timestamp": "1710000000",
        "type": "document" if attachment else "text",
    }
    if attachment:
        inbound["document"] = {"id": "harness-media-1", "filename": "teste.pdf"}
    else:
        inbound["text"] = {"body": text}
    return {
        "entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": "5511999999999"}],
            "messages": [inbound],
        }}]}]
    }


def run_adapter_case(
    text: str,
    *,
    attachment: bool = False,
    handler_behavior: str = "reply",
    event_log_error: bool = False,
    send_behavior: str = "reply",
    user_lookup_error: bool = False,
) -> dict[str, Any]:
    replies: list[dict[str, str]] = []
    guards = SafetyGuards(allowed_write_root=Path.cwd())
    modules = ModuleOverrides()
    guards.install()
    try:
        install_runtime_boundaries(
            modules,
            replies,
            safety_events=guards.events,
            handler_behavior=handler_behavior,
            event_log_error=event_log_error,
            send_behavior=send_behavior,
            user_lookup_error=user_lookup_error,
        )
        modules.remember("adapters.whatsapp.wa_runtime")
        from adapters.whatsapp.wa_runtime import process_payload

        processed = process_payload(whatsapp_payload(text, attachment=attachment))
        blocked = list(guards.events)
        delivered = bool(replies) and not blocked
        outcome = (
            "safety_violation" if blocked else "delivered" if delivered else "delivery_failed"
        )
        return {
            "extracted": processed,
            "delivered": delivered,
            "outcome": outcome,
            "replies": replies,
            "blocked": blocked,
        }
    finally:
        modules.close()
        guards.close()
