from __future__ import annotations

import builtins
import os
import socket
import subprocess
import sys
import threading
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class SafetyViolation(RuntimeError):
    """Uma borda proibida foi alcançada durante uma execução do harness."""


@dataclass
class SafetyGuards:
    allowed_write_root: Path = field(default_factory=Path.cwd)
    events: list[str] = field(default_factory=list)
    _restore: list[Callable[[], None]] = field(default_factory=list, init=False)

    def _replace(self, owner: Any, name: str, replacement: Any) -> None:
        original = getattr(owner, name)
        setattr(owner, name, replacement)
        self._restore.append(lambda: setattr(owner, name, original))

    def _deny(self, kind: str, detail: str) -> None:
        event = f"{kind}:{detail}"
        self.events.append(event)
        raise SafetyViolation(event)

    @staticmethod
    def _is_env_path(value: Any) -> bool:
        try:
            name = Path(value).name
        except TypeError:
            return False
        return name == ".env" or name.startswith(".env.")

    def install(self) -> None:
        original_open = builtins.open

        def guarded_open(file: Any, *args: Any, **kwargs: Any):
            if self._is_env_path(file):
                self._deny("env", str(file))
            mode = str(args[0] if args else kwargs.get("mode", "r"))
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                self._guard_write(file)
            return original_open(file, *args, **kwargs)

        original_path_open = Path.open

        def guarded_path_open(path: Path, *args: Any, **kwargs: Any):
            if self._is_env_path(path):
                self._deny("env", str(path))
            mode = str(args[0] if args else kwargs.get("mode", "r"))
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                self._guard_write(path)
            return original_path_open(path, *args, **kwargs)

        original_os_open = os.open

        def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any):
            if self._is_env_path(path):
                self._deny("env", str(path))
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
            if flags & write_flags:
                self._guard_write(path)
            return original_os_open(path, flags, *args, **kwargs)

        def deny_network(sock: socket.socket, address: Any) -> None:
            self._deny("network", repr(address))

        def deny_create_connection(address: Any, *args: Any, **kwargs: Any) -> None:
            self._deny("network", repr(address))

        def deny_process(*args: Any, **kwargs: Any) -> None:
            command = args[0] if args else kwargs.get("args", "unknown")
            self._deny("process", repr(command))

        def deny_thread(thread: threading.Thread, *args: Any, **kwargs: Any) -> None:
            self._deny("thread", thread.name)

        self._replace(builtins, "open", guarded_open)
        self._replace(Path, "open", guarded_path_open)
        self._replace(os, "open", guarded_os_open)
        self._replace(socket.socket, "connect", deny_network)
        self._replace(socket.socket, "connect_ex", deny_network)
        self._replace(socket, "create_connection", deny_create_connection)
        self._replace(subprocess, "Popen", deny_process)
        self._replace(threading.Thread, "start", deny_thread)

    def _guard_write(self, value: Any) -> None:
        if isinstance(value, int):
            return
        target = Path(value).resolve(strict=False)
        root = self.allowed_write_root.resolve(strict=False)
        if target != root and root not in target.parents:
            self._deny("write", str(target))

    def close(self) -> None:
        while self._restore:
            self._restore.pop()()


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
    handler_behavior: str = "reply",
    event_log_error: bool = False,
    send_behavior: str = "reply",
) -> None:
    """Substitui somente I/O; o parser e o fluxo do adaptador continuam reais."""
    send_attempts = 0

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
        download_media=lambda *_a, **_k: (_ for _ in ()).throw(
            SafetyViolation("media:download")
        ),
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

    modules.install(
        "db",
        attempt_whatsapp_phone_link=lambda _wa_id, current_user_id=None: {
            "status": "already_linked",
            "user_id": current_user_id,
        },
        claim_pending_action=lambda *_a, **_k: False,
        consume_pending_action=lambda *_a, **_k: False,
        get_conn=lambda: (_ for _ in ()).throw(SafetyViolation("database:get_conn")),
        get_or_create_canonical_user=lambda _provider, _external_id: 7,
        get_pending_action=lambda _uid: None,
        restore_pending_on_error=lambda *_a, **_k: RestorePending(),
        set_pending_action=lambda *_a, **_k: None,
        set_whatsapp_updates_opt_out=lambda *_a, **_k: None,
        update_launch_category=lambda *_a, **_k: None,
    )


def whatsapp_payload(text: str) -> dict[str, Any]:
    return {
        "entry": [{"changes": [{"value": {
            "contacts": [{"wa_id": "5511999999999"}],
            "messages": [{
                "id": "harness-in-1",
                "from": "5511999999999",
                "timestamp": "1710000000",
                "type": "text",
                "text": {"body": text},
            }],
        }}]}]
    }


def run_adapter_case(
    text: str,
    *,
    handler_behavior: str = "reply",
    event_log_error: bool = False,
    send_behavior: str = "reply",
) -> dict[str, Any]:
    replies: list[dict[str, str]] = []
    guards = SafetyGuards(allowed_write_root=Path.cwd())
    modules = ModuleOverrides()
    guards.install()
    try:
        install_runtime_boundaries(
            modules,
            replies,
            handler_behavior=handler_behavior,
            event_log_error=event_log_error,
            send_behavior=send_behavior,
        )
        modules.remember("adapters.whatsapp.wa_runtime")
        from adapters.whatsapp.wa_runtime import process_payload

        processed = process_payload(whatsapp_payload(text))
        delivered = bool(replies)
        return {
            "extracted": processed,
            "delivered": delivered,
            "outcome": "delivered" if delivered else "delivery_failed",
            "replies": replies,
            "blocked": guards.events,
        }
    finally:
        modules.close()
        guards.close()
