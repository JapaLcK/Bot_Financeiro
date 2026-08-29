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

    # ── O FUSO: uma escrita só, já com o valor final ─────────────────────────
    #
    # ISTO NÃO RODA SÓ NO BOOT. `adapters/whatsapp/wa_app.py:39` chama
    # `load_app_env` no import, e esse import é tardio de propósito
    # (`frontend/finance_bot_websocket_custom.py:1952-1963`, 1ª requisição;
    # `:1632-1656`, 1 s depois do startup). Então isto executa com event loop,
    # threadpool e WebSockets ativos — e a glibc relê `TZ` a cada `localtime()`.
    # Consequência dura: **todo valor intermediário de `TZ` é observável por uma
    # thread concorrente chamando `date.today()`**, e um valor intermediário
    # errado é o bug da #178 de volta por microssegundos.
    #
    # Três versões desta função já falharam por escrever `TZ` mais de uma vez:
    # `pop` (janela SEM `TZ`), e depois `os.environ["TZ"] = merged["TZ"]`
    # (janela com o `TZ` do arquivo quando o `REPORT_TIMEZONE`, de precedência
    # MAIOR, é quem manda). Por isso agora o valor efetivo é RESOLVIDO ANTES e
    # escrito UMA VEZ.
    #
    # A máquina inteira, enumerada — `R` = REPORT_TIMEZONE, `T` = TZ,
    # `env` = ambiente real (`_TZ_ENV_ORIGINAL` para o `T`), `f` = vindo do
    # `.env`. Precedência: R ganha de T; dentro de cada um, ambiente real ganha
    # do arquivo. `efetivo` é o que as TRÊS pontas (processo, app e sessão do
    # Postgres) têm de valer, do começo ao fim:
    #
    #   R_env  R_f  T_env  T_f   efetivo        por quê
    #   ────────────────────────────────────────────────────────────────────
    #     •     -     -     -    R_env          R ganha, ambiente real
    #     •     •     -     -    R_env          ambiente real ganha do arquivo
    #     •     -     •     -    R_env          R ganha de T
    #     •     •     •     •    R_env          idem, todos presentes
    #     -     •     -     -    R_f            R do arquivo, sem R real
    #     -     •     •     -    R_f            R ganha de T mesmo vindo de arquivo
    #     -     •     -     •    R_f            idem
    #     -     •     •     •    R_f            idem
    #     -     -     •     -    T_env          sem R, T do ambiente real
    #     -     -     •     •    T_env          ambiente real ganha do arquivo
    #     -     -     -     •    T_f            sem R e sem T real: o arquivo vale
    #     -     -     -     -    default        America/Sao_Paulo
    #
    # (São 16 combinações; as 12 acima cobrem todas — as 4 que faltam repetem
    # linha por o `R_env` tornar o resto irrelevante. `tests/test_fuso_do_app.py`
    # parametriza as 16 e afirma as três pontas em cada uma.)
    #
    # `_TZ_ENV_ORIGINAL` é o `TZ` que o ambiente REAL trazia, capturado no import
    # de `utils_date` ANTES de a nossa própria escrita existir — sem ele não há
    # como distinguir "o operador setou `TZ`" de "nós setamos `TZ`".
    efetivo = (os.environ.get("REPORT_TIMEZONE")
               or merged.get("REPORT_TIMEZONE")
               or utils_date._TZ_ENV_ORIGINAL
               or merged.get("TZ"))

    # `TZ` e `REPORT_TIMEZONE` saem do `setdefault` genérico: a precedência
    # delas é a da tabela acima, não a de "quem já está no ambiente ganha" — o
    # import já escreveu `TZ`, então o `setdefault` o veria como valor do
    # operador e o `TZ` do `.env` (documentado em `.env.example`) seria no-op.
    for key, value in merged.items():
        if key not in ("TZ", "REPORT_TIMEZONE"):
            os.environ.setdefault(key, value)

    # `REPORT_TIMEZONE` precisa estar no ambiente para o `_tz()` enxergá-lo.
    if "REPORT_TIMEZONE" not in os.environ and "REPORT_TIMEZONE" in merged:
        os.environ["REPORT_TIMEZONE"] = merged["REPORT_TIMEZONE"]

    # A ÚNICA escrita de `TZ` aqui, e já com o valor final: nenhum instante
    # observável tem `TZ` ausente nem `TZ` diferente do efetivo.
    if efetivo:
        os.environ["TZ"] = efetivo

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
