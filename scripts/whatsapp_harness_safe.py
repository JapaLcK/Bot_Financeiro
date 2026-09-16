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


def main() -> int:
    parser = argparse.ArgumentParser(description="Harness hermético do WhatsApp")
    parser.add_argument("--text", default="qual é meu saldo?")
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
    args = parser.parse_args()

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
                if args.layer == "core":
                    result = run_core_case(
                        args.text,
                        force_internal_error=args.core_error,
                    )
                elif args.layer == "policy":
                    result = run_policy_case(args.text)
                else:
                    result = run_adapter_case(
                        args.text,
                        attachment=args.attachment,
                        handler_behavior=args.handler_behavior,
                        event_log_error=args.event_log_error,
                        send_behavior=(
                            "error"
                            if args.send_error
                            else "error-once" if args.send_error_once else "reply"
                        ),
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
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                if args.layer == "core":
                    return 0 if result["answered"] else 2
                if args.layer == "policy":
                    return 0
                if result["blocked"]:
                    return 70
                return 0 if result["delivered"] else 2
    except SafetyViolation as exc:
        print(json.dumps({"error": "SAFETY_VIOLATION", "detail": str(exc)}))
        return 70
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
