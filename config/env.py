from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values


ROOT_DIR = Path(__file__).resolve().parent.parent


def load_app_env() -> str:
    """
    Load environment variables from `.env` plus `.env.<APP_ENV>`.

    Precedence:
      1. Real environment variables already present in the process
      2. `.env.<APP_ENV>`
      3. `.env`
    """
    app_env = (os.getenv("APP_ENV") or "dev").strip().lower()

    merged: dict[str, str] = {}
    base_file = ROOT_DIR / ".env"
    env_file = ROOT_DIR / f".env.{app_env}"

    if base_file.exists():
        merged.update({k: v for k, v in dotenv_values(base_file).items() if v is not None})

    if env_file.exists():
        merged.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})

    for key, value in merged.items():
        os.environ.setdefault(key, value)

    os.environ.setdefault("APP_ENV", app_env)
    return app_env
