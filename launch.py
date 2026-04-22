"""
launch.py — Ponto de entrada único para Railway.

Sobe dois processos em paralelo:
  1. FastAPI dashboard (uvicorn) → escuta em $PORT (Railway expõe como URL pública)
  2. Discord bot (bot.py)        → conecta ao Discord via WebSocket

Railway precisa de um processo `web` que escute em $PORT.
O dashboard cumpre esse papel; o bot roda em paralelo.
"""
import os
import signal
import subprocess
import sys
import time

from config.env import load_app_env


def main():
    app_env = load_app_env()
    port = os.environ.get("PORT", "8000")
    print(f"[launch] Ambiente ativo: {app_env}")
    print(f"[launch] Iniciando dashboard na porta {port}...")
    dashboard = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "frontend.finance_bot_websocket_custom:app",
            "--host", "0.0.0.0",
            "--port", str(port),
            "--log-level", "warning",
        ]
    )

    print("[launch] Iniciando bot do Discord...")
    bot = subprocess.Popen([sys.executable, "bot.py"])

    def _shutdown(signum=None, frame=None):
        print("[launch] Encerrando processos...")
        for proc in (dashboard, bot):
            try:
                proc.terminate()
            except Exception:
                pass

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Monitora: se qualquer processo morrer, encerra o outro também
    while True:
        time.sleep(2)
        d_rc = dashboard.poll()
        b_rc = bot.poll()

        if d_rc is not None:
            print(f"[launch] Dashboard encerrou (rc={d_rc}). Encerrando bot...")
            bot.terminate()
            sys.exit(d_rc)

        if b_rc is not None:
            print(f"[launch] Bot encerrou (rc={b_rc}). Encerrando dashboard...")
            dashboard.terminate()
            sys.exit(b_rc)


if __name__ == "__main__":
    main()
