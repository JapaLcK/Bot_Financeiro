#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _exit_code(layer: str, result: dict) -> int:
    if result["blocked"]:
        return 70
    if layer == "core":
        return 0 if result["answered"] else 2
    if layer == "policy":
        return 0
    return 0 if result["delivered"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Harness hermético do WhatsApp")
    # Repetível só em core/policy: um processo roda N frases, uma linha JSON por frase.
    parser.add_argument("--text", action="append", default=None)
    parser.add_argument("--layer", choices=("adapter", "core", "policy"), default="adapter")
    parser.add_argument("--probe", choices=("env", "network", "write"))
    parser.add_argument(
        "--handler-behavior",
        choices=("reply", "empty-output", "error", "unsafe-env"),
        default="reply",
    )
    parser.add_argument("--event-log-error", action="store_true")
    parser.add_argument("--send-error", action="store_true")
    parser.add_argument("--send-error-once", action="store_true")
    parser.add_argument("--attachment", action="store_true")
    parser.add_argument("--core-error", action="store_true")
    parser.add_argument("--core-unsafe-env", action="store_true")
    parser.add_argument("--user-lookup-error", action="store_true")
    args = parser.parse_args()
    texts = args.text or ["qual é meu saldo?"]
    if len(texts) > 1 and (args.layer == "adapter" or args.probe):
        parser.error("mais de um --text só vale com --layer core ou policy")

    try:
        with tempfile.TemporaryDirectory(prefix="pigbank-wa-harness-") as temp_dir:
            os.chdir(temp_dir)
            safe_environment = {
                "PATH": os.environ.get("PATH", ""),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            os.environ.clear()
            os.environ.update(safe_environment)
            # urllib3 testa IPv6 com bind local durante o import. Carregue a
            # dependência confiável antes de instalar a guarda de código exercitado.
            import urllib3.util.connection  # noqa: F401
            from harness_support.safe_runtime import (
                SafetyGuards,
                SafetyViolation,
                run_adapter_case,
            )
            from harness_support.core_runtime import run_core_case
            from harness_support.policy_runtime import run_policy_case

            if args.probe:
                guards = SafetyGuards(allowed_write_root=Path(temp_dir))
                guards.install()
                try:
                    if args.probe == "env":
                        Path(".env").read_text(encoding="utf-8")
                    elif args.probe == "network":
                        socket.create_connection(("example.invalid", 443))
                    else:
                        (REPO / ".harness-forbidden-write").write_text(
                            "não deve existir", encoding="utf-8"
                        )
                finally:
                    guards.close()
            else:
                exit_code = 0
                for text in texts:
                    if args.layer == "core":
                        result = run_core_case(
                            text,
                            force_internal_error=args.core_error,
                            force_safety_violation=args.core_unsafe_env,
                        )
                    elif args.layer == "policy":
                        result = run_policy_case(text)
                    else:
                        result = run_adapter_case(
                            text,
                            attachment=args.attachment,
                            handler_behavior=args.handler_behavior,
                            event_log_error=args.event_log_error,
                            send_behavior=(
                                "error"
                                if args.send_error
                                else "error-once" if args.send_error_once else "reply"
                            ),
                            user_lookup_error=args.user_lookup_error,
                        )
                    result["cwd_is_temporary"] = Path.cwd() != REPO
                    result["environment_sanitized"] = not any(
                        name in os.environ
                        for name in (
                            "DATABASE_URL",
                            "OPENAI_API_KEY",
                            "RESEND_API_KEY",
                            "WA_ACCESS_TOKEN",
                        )
                    )
                    result["text"] = text
                    result["exit"] = _exit_code(args.layer, result)
                    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                    exit_code = max(exit_code, result["exit"])
                    if result["blocked"]:
                        break  # depois de uma guarda disparar, o estado do processo é desconhecido
                return exit_code
    except SafetyViolation as exc:
        print(json.dumps({"error": "SAFETY_VIOLATION", "detail": str(exc)}))
        return 70
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
