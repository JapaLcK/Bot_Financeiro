from __future__ import annotations

import logging
import sys
from typing import Any

# Reexportação de FACHADA, de propósito — não é import morto nem sobra de
# refatoração. A tabela `system_event_logs` mudou de arquivo (CLAUDE.md §0.5: o
# teto de 350 linhas do `tests/test_max_lines_python.py`), mas os call sites
# importam os dois nomes DAQUI, e vários testes fazem
# `monkeypatch.setattr(observability, "log_system_event_sync", …)`. Reescrevê-los
# violaria §0.3 e quebraria os patches — e o `_DashboardHandler.emit` abaixo
# resolve o nome NESTE módulo, que é o que faz o monkeypatch continuar valendo.
#   grep -rn "from core.observability import" --include="*.py" --exclude-dir=.venv .
#   grep -rn "setattr(observability" --include="*.py" --exclude-dir=.venv .
# A direção é ÚNICA: `core.system_event_log` NÃO importa nada daqui (e não pode
# — ele é o módulo sem `logging`, justamente para não reentrar no handler).
# `load_app_env()` sai junto: quem o chama agora é o módulo importado abaixo,
# então o efeito colateral de import continua acontecendo.
from core.system_event_log import log_system_event_sync, recent_event_exists

# ── Logger centralizado ───────────────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_root_configured = False


class _DashboardHandler(logging.Handler):
    """
    Handler que espelha WARNING e ERROR no dashboard (tabela system_event_logs).
    Só grava se DATABASE_URL estiver configurado.

    ponytail: `emit` chama `log_system_event_sync` direto — um psycopg.connect()
    + INSERT bloqueante POR REGISTRO, sem pool e sem fila. Teto: como este handler
    fica no ROOT logger, qualquer `warning()`/`error()` de qualquer módulo paga a
    conta, e no processo web (uvicorn) ela é paga DENTRO do event loop, travando
    todas as conexões enquanto dura. Medido em localhost: 11,3 ms na 1ª chamada e
    2,1–3,7 ms por chamada em série, contra 0,02 ms sem o handler; com banco remoto
    cada connect ainda paga o RTT (não medido daqui — produção inacessível). Foi
    barato até aqui porque warning em produção é raro; o que assusta é o caso em
    que ele deixa de ser — um laço quente logando por requisição. Já obrigou
    contorno em pelo menos um ponto: `frontend/routes/shared.py` loga a queda da
    página de erro uma vez por TRANSIÇÃO (flag `_error_degraded`) em vez de por
    requisição, senão um bot varrendo URL vira um INSERT por 404.
    Contido em três pontos, não resolvido: (1) `connect_timeout=2` nas DUAS
    funções que abrem conexão (`log_system_event_sync` e `recent_event_exists`,
    hoje em `core/system_event_log.py`), limitando o travamento a 2s com banco
    inalcançável (era >30s, medido); (1b) `statement_timeout` nas mesmas duas —
    e, desde a issue #429, também no gravador async `log_system_event` de
    `core/admin_dashboard.py`, que passou a abrir conexão própria com o MESMO
    helper (`statement_timeout_options`) em vez de ir pelo `db_connect` do
    painel. É o que limita a ESPERA DE LOCK e a execução: sem ele a tabela
    travada pendurava o caller pelo lock inteiro (medido: 3,00s para um lock de
    3s). O `db_connect` do painel continua sem teto, de propósito — 11
    chamadores, entre eles DDL de boot, agregações e a retenção diária;
    (2) os 5 call sites das 4 rotas destrutivas `async`
    (`frontend/routes/cards.py`, `frontend/finance_bot_websocket_custom.py` —
    incluindo o ramo WARNING da `/launches`) chamam o `_log_falha` por
    `asyncio.to_thread`, tirando o INSERT do event loop NAQUELE call site. Todo
    o resto do processo web ainda paga dentro do loop.
    Upgrade: `logging.handlers.QueueHandler` + `QueueListener` (stdlib) tira o
    INSERT do caller em TODOS os call sites de uma vez, e aposenta os dois
    contornos acima. Transversal (mexe no logger de todo o processo) — não é o
    PR da página de erro nem o do delete.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        level = record.levelname.lower()      # "warning" | "error" | "critical"
        source = record.name                   # nome do módulo (ex: adapters.discord.discord_bot)
        message = self.format(record)

        # inclui traceback no campo details se disponível
        details: dict[str, Any] = {"logger": record.name}
        if record.exc_info:
            import traceback as _tb
            # `[-2000:]` é o mesmo teto do `admin_error_logging_middleware`
            # (`core/admin_dashboard.py:1383`), o caminho que este PR restaura;
            # sem ele um traceback fundo cresce sem limite dentro do JSONB.
            # `"".join(...)`: a lista virava `"['Traceback…', …]"` no
            # `esc(row.details.traceback)` do `admin-dashboard.html:1308`.
            details["traceback"] = "".join(_tb.format_exception(*record.exc_info))[-2000:]

        # chama de forma síncrona — handler roda em thread do bot
        log_system_event_sync(
            level,
            event_type=f"logger.{level}",
            message=message[:1000],
            source=source,
            user_id=getattr(record, "user_id", None),
            details=details,
        )


# ── Falha de operação: causa no log, nunca na mensagem ───────────────────────

_logger = logging.getLogger(__name__)


def _log_falha(op: str, user_id: int, e: Exception, *,
               nivel: int = logging.ERROR, com_traceback: bool = False,
               **extra) -> None:
    """Causa no log, nunca na mensagem do usuário: `str(e)` do psycopg pode
    trazer o valor e a descrição da linha (`DETAIL: Key (…)=(…)`). Nome do tipo +
    sqlstate já separam conexão (08006), deadlock (40P01), permissão (42501) e
    bug de código.

    O traceback (`exc_info`) só vai ao log quando o CALL SITE pede
    `com_traceback=True`. Quem decide é a PORTA, não o nível, e o critério é
    privacidade: o `_DashboardHandler` (acima) persiste em `system_event_logs`
    tanto o `self.format(record)` — que anexa o traceback à `message` — quanto
    `details["traceback"]`; e o traceback de um erro do psycopg carrega
    `DETAIL: Key (…)=(…)`, com valor e descrição da linha do cliente.

    O critério NÃO é "é rota HTTP", é medido por rota: o
    `admin_error_logging_middleware` (`core/admin_dashboard.py`) só gravava
    `http_unhandled_exception` (com traceback) onde a `main` deixava a exceção
    SUBIR crua — ele faz `except HTTPException: raise`, então rota que já
    levantava `HTTPException` ele nunca viu.

      - COM traceback — só as duas rotas de `frontend/routes/cards.py`
        (`delete_card_route`, `installment_delete_route`), ramo TÉCNICO. Na
        `main` elas não tinham `try/except` algum: a pilha JÁ era gravada
        naquele mesmo `system_event_logs.details`, e o `HTTPException(500)`
        que traduz a falha em frase de produto é justamente o que o middleware
        DEIXA PASSAR. Aqui `exc_info` mantém o rastro que existia; omitir troca
        um rastro melhor por um pior (medido em três colunas).
      - SEM traceback — todo o resto, incluindo as rotas HTTP
        `delete_launch_route` e `delete_credit_transaction_route`
        (`frontend/finance_bot_websocket_custom.py`) e as portas de conversa
        (`core/handlers/pending.py`, `core/handlers/credit.py`,
        `core/services/ai_chat/tools/launches.py`). Nas duas rotas a `main` já
        levantava `HTTPException(500, f"Erro ao apagar…")` e nas portas de
        conversa não passa middleware nenhum: em nenhuma delas houve pilha
        gravada. Medido com o banco estourando `DETAIL: Key (…)`: `main`
        gravava ZERO evento nessas rotas. Ligar `com_traceback=True` ali não
        restaura nada — CRIA persistência nova de dado do cliente. O
        diagnóstico é o que a `main` já tinha: tipo da exceção, sqlstate e os
        ids do `**extra`.

    Quem pode ligar está preso por `tests/test_log_falha_traceback.py`, que
    varre o repositório com `ast` e cobra a allowlist (arquivo, função) — não é
    lista por nome de arquivo, porta nova em `core/handlers/` também é pega.

    O default é `False` de propósito (fail-safe de privacidade): call site novo
    que esquecer do parâmetro não vaza. Sanitizar o traceback do psycopg não é
    alternativa — é frágil e deixa passar `DETAIL`, valor de coluna e dado
    financeiro.

    Helper ÚNICO de todas as portas destrutivas (`core/handlers/pending.py`,
    `core/handlers/credit.py`, `core/services/ai_chat/tools/launches.py`,
    `frontend/finance_bot_websocket_custom.py` e `frontend/routes/cards.py`):
    duas cópias com níveis diferentes faziam a MESMA condição contar como erro
    numa porta e não na outra. Mora aqui, e não em `core/handlers/pending.py`,
    porque `frontend/` não importa nada de `core.handlers` — e o dono da
    política de nível que a docstring cita é este módulo. O nível importa fora
    do log: o `_DashboardHandler` (acima) espelha WARNING e ERROR em
    `system_event_logs` com `level=levelname.lower()`, e
    `core/admin_dashboard.py` conta `backend_errors_24h WHERE level='error'`.

    `nivel` segue a MESMA distinção dos `except` das portas, não outra:
      - condição de domínio ESPERADA (`LaunchNoEffects`,
        `InvestmentLotHasWithdrawal`, `LaunchUnsafeRollback`) → `logging.WARNING`.
        Inflar o contador de erros do admin com aporte que teve resgate é
        ruído, não incidente.
      - falha técnica/inesperada (`except Exception`, `ValueError` sem código
        conhecido) → `logging.ERROR`, que é o default: quem esquecer de
        classificar erra para o lado barulhento, não para o lado silencioso.

    `nivel` e `com_traceback` são keyword-only por isso não colidem com
    `**extra`; um campo extra com um desses nomes seria engolido (nenhum call
    site usa).
    """
    _logger.log(
        nivel,
        "%s: falha user_id=%s%s causa=%s sqlstate=%s",
        op, user_id, "".join(f" {k}={v}" for k, v in extra.items()),
        type(e).__name__, getattr(e, "sqlstate", None),
        exc_info=e if com_traceback else None,
        extra={"user_id": user_id},
    )


def _sem_ratelimit_no_banco(record: logging.LogRecord) -> bool:
    """WARNING do slowapi NÃO vira linha em `system_event_logs` (segue no stderr).

    É o "laço quente logando por requisição" que o docstring do
    `_DashboardHandler` nomeia como o que assusta: o slowapi loga um
    `warning("ratelimit ... exceeded")` por requisição BARRADA, e cada record
    vira um `psycopg.connect()` + INSERT bloqueante. MEDIDO: 40 GETs anônimos em
    `/d/{code}` com teto de 30/min → 10 × 429 e **10 linhas** em
    `system_event_logs`. Ou seja, o teto trocava 200 DELETEs baratos por uma
    inundação de log pior que a do #321 — e vale para os tetos que já existiam
    (um brute-force em `/auth/login` gravava uma linha por tentativa barrada).

    Só WARNING: `logger.error` do slowapi (limite mal configurado, storage
    morto) continua indo para o banco — é incidente, não tráfego.
    """
    return not (record.name == "slowapi" and record.levelno == logging.WARNING)


def _configure_root_logger() -> None:
    global _root_configured
    if _root_configured:
        return

    root = logging.getLogger()
    if not root.handlers:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT))
        root.setLevel(logging.INFO)
        root.addHandler(stderr_handler)

    # adiciona handler do dashboard se ainda não estiver presente
    if not any(isinstance(h, _DashboardHandler) for h in root.handlers):
        dash_handler = _DashboardHandler()
        dash_handler.setLevel(logging.WARNING)
        dash_handler.addFilter(_sem_ratelimit_no_banco)
        root.addHandler(dash_handler)

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Retorna um logger configurado. Use no topo de cada módulo:
        logger = get_logger(__name__)

    WARNING e ERROR aparecem automaticamente no dashboard admin.
    """
    _configure_root_logger()
    return logging.getLogger(name)
