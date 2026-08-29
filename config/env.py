from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

# Módulo, não `from ... import`: `_TZ_ENV_ORIGINAL` é lido aqui e escrito lá, e
# a direção é acíclica (`utils_date` não importa nada do projeto).
import utils_date


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
        merged.update({k: v for k, v in dotenv_values(base_file, interpolate=False).items() if v is not None})

    if env_file.exists():
        merged.update({k: v for k, v in dotenv_values(env_file, interpolate=False).items() if v is not None})

    # `utils_date` é importado ANTES de o `.env` ser lido e já escreveu `TZ` no
    # ambiente (`align_process_tz`). Se o operador NÃO tinha `TZ` no ambiente
    # real, esse `TZ` é nosso, não dele — e o `setdefault` de baixo o veria como
    # valor já existente, transformando `TZ` no `.env` (documentado em
    # `.env.example`) num no-op. Esta linha devolve ao arquivo a vez dele; `TZ`
    # vindo do ambiente REAL não é tocado e continua ganhando.
    #
    # É ATRIBUIÇÃO, e nunca `pop`, porque isto NÃO roda só no boot: o import de
    # `adapters/whatsapp/wa_app.py` (que chama `load_app_env` na linha 39) é
    # tardio de propósito — `frontend/finance_bot_websocket_custom.py:1952-1963`
    # o faz na 1ª requisição e `:1632-1656` 1 s depois do startup, com event
    # loop, threadpool e WebSockets já ativos. A glibc relê `TZ` a cada
    # `localtime()`, então um `pop` abriria uma janela (até o `align_process_tz`
    # do fim) em que uma thread concorrente chamando `date.today()` cairia no
    # fuso do contêiner — UTC no Railway, que é o bug deste PR de volta por
    # microssegundos. Quem prova a ausência da janela é o caso 14 de
    # `tests/test_fuso_do_app.py`.
    if utils_date._TZ_ENV_ORIGINAL is None and "TZ" in merged:
        os.environ["TZ"] = merged["TZ"]

    for key, value in merged.items():
        os.environ.setdefault(key, value)

    os.environ.setdefault("APP_ENV", app_env)

    # Realinha DEPOIS do `.env`: sem isto, um `REPORT_TIMEZONE` vindo de ARQUIVO
    # faz o app e a sessão do Postgres seguirem o arquivo enquanto o PROCESSO
    # fica no fuso do sistema — exatamente a divergência que `align_process_tz`
    # existe para fechar, reintroduzida por um canal suportado (e é a variável
    # que a mensagem de erro logo abaixo ensina o operador a usar).
    #
    # Fuso inválido tem de matar o processo AQUI, no boot, e não na primeira
    # query: o nome vai para `PGTZ`, e um nome inválido derruba a conexão DEPOIS
    # de o health check passar — o pool pendura até `PoolTimeout` e o deploy é
    # dado como bom. Sem fallback de propósito: a intenção é falhar, só que cedo.
    try:
        utils_date.align_process_tz()
    except Exception as exc:
        print(
            f"ERROR: REPORT_TIMEZONE/TZ inválido ({exc}). Use um nome IANA, ex.: America/Sao_Paulo.",
            file=sys.stderr,
        )
        sys.exit(1)

    return app_env
