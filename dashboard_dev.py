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
import socket
import threading
import time

import uvicorn

from config.env import load_app_env

_BANNER = """
✅ DASHBOARD PRONTO PARA USO!
🌐 Dashboard: http://localhost:{port}/
🔧 Admin:     http://localhost:{port}/admin
"""


def _port_accepts_connections(port: int) -> bool:
    for host in ("127.0.0.1", "localhost"):
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except OSError:
            pass
    return False


def _wait_and_notify(port: int, timeout: int = 20) -> None:
    """
    Tenta conectar na porta a cada 0.5s.
    Imprime o banner apenas quando a porta de fato aceitar conexões —
    ou seja, quando o uvicorn terminou de fazer o socket binding e o
    servidor está realmente acessível.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_accepts_connections(port):
            print(_BANNER.format(port=port), flush=True)
            return
        time.sleep(0.3)

    print(
        "[dashboard_dev] A porta ainda não respondeu ao teste local. "
        f"Se o navegador abriu, use http://localhost:{port}/admin; "
        "caso contrário, verifique se a porta está ocupada ou se o startup travou.",
        flush=True,
    )


def main() -> None:
    app_env = load_app_env()
    host    = os.environ.get("HOST", "0.0.0.0")
    port    = int(os.environ.get("PORT", "8000"))
    reload  = os.environ.get("UVICORN_RELOAD", "0") == "1"

    print(f"[dashboard_dev] Ambiente ativo: {app_env}", flush=True)
    print(f"[dashboard_dev] Subindo app em http://127.0.0.1:{port}/admin", flush=True)
    os.environ.setdefault("RUN_BACKGROUND_TASKS", "0")

    if _port_accepts_connections(port):
        print(
            f"[dashboard_dev] A porta {port} já está em uso. "
            f"Provavelmente já existe um dashboard rodando em http://localhost:{port}/admin. "
            "Encerre o processo antigo ou escolha outra porta com PORT=8001.",
            flush=True,
        )
        raise SystemExit(1)

    # Thread daemon: imprime o banner assim que a porta aceitar conexões.
    # Funciona tanto no modo normal quanto no modo reload.
    t = threading.Thread(target=_wait_and_notify, args=(port,), daemon=True)
    t.start()

    try:
        uvicorn.run(
            "frontend.finance_bot_websocket_custom:app",
            host=host,
            port=port,
            reload=reload,
            log_level="warning",
        )
    except KeyboardInterrupt:
        print("\n[dashboard_dev] Servidor encerrado.")


if __name__ == "__main__":
    main()
