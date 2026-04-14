"""
dashboard_dev.py — ponto de entrada local para o painel/web app.

Uso:
  python dashboard_dev.py

Opções por ambiente:
  HOST=127.0.0.1 PORT=8000 python dashboard_dev.py
  UVICORN_RELOAD=1 python dashboard_dev.py
"""
from __future__ import annotations

import os

import uvicorn

from config.env import load_app_env


def main() -> None:
    app_env = load_app_env()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    reload_enabled = os.environ.get("UVICORN_RELOAD", "0") == "1"

    print(f"[dashboard_dev] Ambiente ativo: {app_env}")
    print(f"[dashboard_dev] Subindo app em http://127.0.0.1:{port}/admin")

    uvicorn.run(
        "frontend.finance_bot_websocket_custom:app",
        host=host,
        port=port,
        reload=reload_enabled,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
