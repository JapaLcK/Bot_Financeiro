# core/ai_rate_limiter.py
"""
Rate limiting por usuário para chamadas à API da OpenAI.

Usa sliding window em memória: conta quantas chamadas à IA cada usuário
fez nos últimos WINDOW_SEC segundos. Se ultrapassar MAX_CALLS, bloqueia.

Limites padrão (sobrescrevíveis por variável de ambiente):
  AI_RATE_LIMIT_MAX_CALLS  → padrão 10 chamadas
  AI_RATE_LIMIT_WINDOW_SEC → padrão 60 segundos
"""
from __future__ import annotations

import os
import time
from collections import deque

# ---------------------------------------------------------------------------
# Configuração (lida uma vez no import)
# ---------------------------------------------------------------------------

MAX_CALLS: int = int(os.getenv("AI_RATE_LIMIT_MAX_CALLS", "10"))
WINDOW_SEC: float = float(os.getenv("AI_RATE_LIMIT_WINDOW_SEC", "60"))

# Dicionário: user_id → deque de timestamps (monotonic)
_windows: dict[int, deque[float]] = {}


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def is_allowed(user_id: int) -> bool:
    """
    Retorna True se o usuário ainda tem chamadas disponíveis na janela atual
    e registra a chamada. Retorna False se o limite foi atingido (sem registrar).
    """
    now = time.monotonic()
    window = _windows.setdefault(user_id, deque())

    # Remove timestamps fora da janela deslizante
    while window and now - window[0] > WINDOW_SEC:
        window.popleft()

    if len(window) >= MAX_CALLS:
        return False

    window.append(now)
    return True


def remaining(user_id: int) -> int:
    """Retorna quantas chamadas o usuário ainda tem disponíveis na janela atual."""
    now = time.monotonic()
    window = _windows.get(user_id, deque())
    active = sum(1 for ts in window if now - ts <= WINDOW_SEC)
    return max(0, MAX_CALLS - active)


def reset(user_id: int) -> None:
    """Limpa o histórico de um usuário (útil para testes)."""
    _windows.pop(user_id, None)
