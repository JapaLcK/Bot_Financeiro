from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

# Módulo, não `from ... import`: `_TZ_ENV_ORIGINAL` é lido aqui e escrito lá, e
# a direção é acíclica (`utils_date` não importa nada do projeto).
import utils_date


ROOT_DIR = Path(__file__).resolve().parent.parent

# Aviso de fuso divergente: uma vez por processo (ver o fim de `load_app_env`).
_AVISO_FUSO_EMITIDO = False


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

    # O `APP_TZ` de `frontend/dashboard.js` é LITERAL e vale `utils_date.TZ_PADRAO`.
    # A guarda 19.1 de `tests/test_fuso_do_app.py` prova MENOS desse par do que o
    # nome sugere: que existe UMA const chamada `APP_TZ` com o valor certo e que
    # três substrings de uso continuam no arquivo. Ela lê TEXTO — não executa o JS.
    # Sombrear o nome (`function appTzWallClockToISO(localStr, APP_TZ)` mais
    # `appTzWallClockToISO(dataVal, Intl.DateTimeFormat().resolvedOptions().timeZone)`
    # no chamador) a deixa VERDE com o dashboard já seguindo o APARELHO — medido.
    # Quem prova o USO é `tests/frontend/edit_launch_patch_body.test.mjs`, caso
    # "navegador em Asia/Tokyo": ele roda o `dashboard.js` de verdade num Chromium
    # noutro fuso e confere o `criado_em` do PATCH.
    #
    # As duas juntas protegem a CONSTANTE e UM caminho de escrita, não a classe:
    # um segundo formulário chamando `new Date(v).toISOString()` passaria verde nas
    # duas. Então um fuso efetivo diferente do padrão quebra o par JS↔servidor: o
    # `appTzWallClockToISO` do dashboard continua lendo o datetime-local como hora
    # de parede em São Paulo e grava o lançamento em DIA errado para quem estiver
    # perto da meia-noite.
    #
    # AVISO, não `exit(1)`, por duas razões medidas:
    #   1. `load_app_env` não roda só no boot — `adapters/whatsapp/wa_app.py:39` o
    #      chama NO IMPORT, e esse import é tardio de propósito (primeira
    #      requisição). Sair aqui derrubaria um processo JÁ SERVINDO.
    #   2. fuso divergente é configuração SUPORTADA e testada: 21 casos de
    #      `tests/test_fuso_do_app.py` chamam `load_app_env` com fuso efetivo ≠
    #      padrão, e a técnica `REPORT_TIMEZONE=<zona> pytest` (documentada no
    #      cabeçalho daquele arquivo e no de `tests/test_virada_de_mes.py`)
    #      morreria junto.
    # UMA VEZ POR PROCESSO. `load_app_env` é chamado no IMPORT de vários módulos do
    # mesmo processo: medido, `import core.observability, frontend.routes.shared,
    # adapters.whatsapp.wa_app` imprimia 3 linhas idênticas e agora imprime 1. Um
    # boot web importa mais que isso.
    #
    # A SUÍTE não muda: continua em 22 avisos, todos de `tests/test_fuso_do_app.py`
    # (medido: sem esse arquivo, 0). É de propósito — a fixture `_restaura_fuso`
    # zera a flag ANTES de cada caso, senão `test_fuso_divergente_...avisa` ficaria
    # verde-por-engano ao rodar depois de outro caso que já avisou (medido: sem o
    # reset, ele passa sozinho e falha em `test_report_timezone_...` + ele).
    #
    # ponytail: comparação por NOME, não por offset. Um ALIAS do mesmo fuso —
    # `Brazil/East`, mesmo TZif de `America/Sao_Paulo`, offset medido idêntico
    # (−03:00) — dispara aviso falso. Aceito: alias é raro, o aviso não bloqueia
    # nada, e resolver por identidade de zona custa mais código do que o alarme
    # cosmético vale. Se incomodar, compare `ZoneInfo(a).utcoffset(now)`.
    global _AVISO_FUSO_EMITIDO
    if not _AVISO_FUSO_EMITIDO and utils_date.tz_name() != utils_date.TZ_PADRAO:
        _AVISO_FUSO_EMITIDO = True
        print(
            f"WARNING: fuso do servidor ({utils_date.tz_name()}) diverge do APP_TZ do "
            f"dashboard ({utils_date.TZ_PADRAO}), que é literal no JS. Lançamento "
            f"criado pelo dashboard perto da meia-noite vai para o DIA errado. "
            f"Operar noutro fuso exige mudar os três de PRODUÇÃO juntos — o `APP_TZ` "
            f"de frontend/dashboard.js, o `TZ_PADRAO` de utils_date.py e o ambiente "
            f"(REPORT_TIMEZONE/TZ) — mais os controles que cravam São Paulo em "
            f"tests/test_fuso_do_app.py (`_SP` e `_SP_DEFAULT`). Mexer só no ambiente "
            f"é o que este aviso está vendo agora.",
            file=sys.stderr,
        )

    return app_env
