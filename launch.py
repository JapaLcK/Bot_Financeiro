"""
launch.py — Ponto de entrada do Procfile e do ambiente local.

Carrega o ambiente e se substitui (execv) pelo uvicorn do dashboard FastAPI,
que escuta em $PORT. Sem processo pai no meio, o SIGTERM chega direto ao uvicorn.

O Railway não passa por aqui: o start command do serviço `Dashboard/whatsapp`
sobe o uvicorn direto e vale no lugar do Procfile.
"""
import os
import sys

from config.env import load_app_env


def main():
    app_env = load_app_env()
    port = os.environ.get("PORT", "8000")
    print(f"[launch] Ambiente ativo: {app_env}")
    print(f"[launch] Iniciando dashboard na porta {port}...", flush=True)
    os.execv(
        sys.executable,
        [
            sys.executable, "-m", "uvicorn",
            "frontend.finance_bot_websocket_custom:app",
            "--host", "0.0.0.0",
            "--port", str(port),
            "--log-level", "warning",
        ],
    )


if __name__ == "__main__":
    main()
