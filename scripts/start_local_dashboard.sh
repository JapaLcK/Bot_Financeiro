#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  echo "Ambiente virtual .venv nao encontrado em: $ROOT_DIR"
  echo "Crie a venv antes de rodar este script."
  exit 1
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
APP_MODULE="${APP_MODULE:-frontend.finance_bot_websocket_custom:app}"
RELOAD_FLAG="${UVICORN_RELOAD:-0}"

echo "Subindo painel local em http://127.0.0.1:${PORT}/admin"
echo "App: ${APP_MODULE}"

if [[ "$RELOAD_FLAG" == "1" ]]; then
  echo "Modo reload ativado."
  exec python -m uvicorn "$APP_MODULE" --host "$HOST" --port "$PORT" --reload
fi

exec python -m uvicorn "$APP_MODULE" --host "$HOST" --port "$PORT"
