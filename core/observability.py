from __future__ import annotations

import logging
import os
import sys
import time
import traceback
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
from core.pg_text import causa_segura, quadros_da_cadeia, redigir_failing_row
from core.system_event_log import log_system_event_sync, recent_event_exists
from core import log_fila

# ── Logger centralizado ───────────────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_root_configured = False


class _DashboardHandler(logging.Handler):
    """
    Handler que espelha WARNING e ERROR no dashboard (tabela system_event_logs).
    Só grava se DATABASE_URL estiver configurado.

    ponytail: `emit` chama `log_system_event_sync` direto — um psycopg.connect()
    + INSERT POR REGISTRO, sem pool. Não fica no root: lá está a `FilaDoPainel`
    de `core/log_fila.py`, e este `emit` roda na thread do ouvinte dela. Quem
    loga paga só o `prepare` da fila (o grupo 1 de `tests/test_log_fila_painel.py`
    mede o loop com a tabela travada; com este handler no root ele parava). Teto:
    um connect por registro, em série, no ouvinte (2,1–3,7 ms em localhost; com
    banco remoto, mais o RTT, não medido daqui); um laço quente logando enche a
    fila e o excedente é descartado com aviso no stderr. Por isso os contornos de
    VOLUME ficam: `frontend/routes/shared.py` loga a queda da página de erro uma
    vez por TRANSIÇÃO (`_error_degraded`), e `_sem_ratelimit_no_banco` tira o
    slowapi. `connect_timeout=2` e `statement_timeout` (`core/system_event_log.py`;
    o gravador async `log_system_event` de `core/admin_dashboard.py` usa o mesmo
    helper, #429) limitam quanto o ouvinte espera por registro com o banco
    inalcançável ou a tabela travada.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        level = record.levelname.lower()      # "warning" | "error" | "critical"
        source = record.name                   # nome do módulo (ex: adapters.discord.discord_bot)
        # #1331: a pilha mora só em `details.traceback` (o admin a mostra; a exportação a omite,
        # A9). `self.format` anexava `exc_text` aqui. Não mexer no record: o stderr depende dele.
        message = record.getMessage()

        # inclui traceback no campo details se disponível
        details: dict[str, Any] = {"logger": record.name}
        if record.exc_info:
            # `[-2000:]` é o mesmo teto do `admin_error_logging_middleware`
            # (`core/admin_dashboard.py:1383`), o caminho que este PR restaura;
            # sem ele um traceback fundo cresce sem limite dentro do JSONB.
            # `"".join(...)`: a lista virava `"['Traceback…', …]"` no
            # `esc(row.details.traceback)` do `admin-dashboard.html:1308`.
            details["traceback"] = "".join(traceback.format_exception(*record.exc_info))[-2000:]
        elif record.exc_text:  # o filtro `_sem_failing_row` já redigiu e zerou o exc_info
            details["traceback"] = record.exc_text[-2000:]

        # roda na thread do ouvinte de `core/log_fila.py`, não na de quem logou
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
    privacidade: o `_DashboardHandler` (acima) persiste o traceback em
    `system_event_logs.details["traceback"]` (desde a #1331, não mais na
    `message`); e o traceback de um erro do psycopg carrega
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


_PILHA_A_CADA = 3600.0  # s: a mesma assinatura volta a trazer a pilha depois disso
_ultima_pilha: dict[str, tuple[tuple, float]] = {}  # tag -> (assinatura, monotonic da última pilha)
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep  # o quadro "nosso" mora aqui
_DE_FORA = ("site-packages", "dist-packages")  # ...salvo o `.venv` dentro da raiz


def _origem(exc: BaseException) -> tuple | None:
    """Os quadros NOSSOS (arquivo, linha), na ordem, de toda a cadeia `__cause__`/`__context__`, a
    mesma que `quadros_da_cadeia` imprime. Sem quadro nosso, o mais interno de `exc`; sem traceback,
    `None`. Nunca levanta (roda no `except` de um topo): na dúvida, `None`."""
    nossos, ultimo, vistos, e = [], None, set(), exc
    try:
        while e is not None and id(e) not in vistos:
            vistos.add(id(e))
            tb = e.__traceback__
            while tb is not None:
                quadro = (tb.tb_frame.f_code.co_filename, tb.tb_lineno)
                if e is exc:
                    ultimo = quadro
                if quadro[0].startswith(_RAIZ) and not any(p in quadro[0] for p in _DE_FORA):
                    nossos.append(quadro)
                tb = tb.tb_next
            e = e.__cause__ if e.__cause__ is not None else (None if e.__suppress_context__ else e.__context__)
    except Exception:  # noqa: BLE001
        return None
    return tuple(dict.fromkeys(nossos)) or ultimo  # sem repetição: relançar o mesmo objeto acrescenta quadros


def falha_de_tarefa(tag: str, exc: BaseException) -> None:
    """Topo de tarefa de fundo (P3 b da #657): tipo, sqlstate e status, e os quadros da pilha sem a linha final.

    Pilha só na 1ª falha seguida (P11, P12): a linha `motivo=` sai sempre; os quadros, só quando a
    assinatura da tag muda ou passou `_PILHA_A_CADA` desde a última pilha dela. A assinatura é a causa
    (`causa_segura`: tipo, sqlstate e status) e a `_origem`: TODOS os quadros nossos (sob a raiz do
    repositório e fora de `site-packages`/`dist-packages`), na ordem, com a cadeia de causas. A pilha
    só é omitida quando sairia idêntica no que é nosso: o psycopg é o mais interno num erro de banco,
    e um helper nosso comum é a linha nossa mais interna de dois caminhos, e nos dois casos o dreno e a
    purga do `[pix]` dividiriam a pilha. Com o banco fora, o worker de exclusão falha a cada ~10 s, e
    a pilha em toda volta inundava o log. Os laços não avisam quando voltam a funcionar, então
    "voltou ao normal" é aproximado por assinatura nova ou tempo.

    Só a ÚLTIMA assinatura da tag conta: falhas alternando (A, B, A, B) mostram a pilha toda vez. A
    `tag` tem de ser literal (a guarda D4 reprova outra coisa, `TAG_VIVA`): cada tag distinta é uma
    entrada no estado, que nunca é podado. Todo chamador roda na thread do loop, por isso não há trava."""
    causa, agora = causa_segura(exc), time.monotonic()
    assinatura = (causa, _origem(exc))
    anterior = _ultima_pilha.get(tag)
    com_pilha = anterior is None or anterior[0] != assinatura or agora - anterior[1] >= _PILHA_A_CADA
    if com_pilha:
        _ultima_pilha[tag] = (assinatura, agora)
    print(f"{tag} erro: {causa}\n{quadros_da_cadeia(exc)[0] if com_pilha else ''}", file=sys.stderr, end="")


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


def _sem_failing_row(record: logging.LogRecord) -> bool:
    """Tira o DETAIL `Failing row contains (...)` do Postgres (linha inteira do usuário) do
    texto e do traceback do log. Um CHECK violado (`finito_*`, db/finito_checks.py) o põe no
    `str(exc)` e no traceback.

    COBRE: records que passam por todo handler do root (os dois que `_configure_root_logger` instala e os que já existiam)
    (stderr, se o root estava sem handler, e a `FilaDoPainel`), com `logger.*(..., exc)` ou `exc_info`. A pilha fica: o traceback vira `exc_text` já
    redigido (o Formatter imprime `exc_text`; o `exc_info` é zerado para ninguém reformatá-lo).
    2ª linha de defesa: a 1ª é `psycopg.Error.__str__` redigido em `db/connection.py`, que já tira
    a linha de `print(exc)`, f-string, log e traceback de qualquer conexão. Este filtro cobre texto
    cru que não passou por ele. NÃO COBRE: logger com `propagate=False` (uvicorn), `print` de
    texto cru e handler fora do root. As gravações diretas em `system_event_logs` são cobertas à parte, pelos
    gravadores (`core.pg_text.redigir_failing_row`, a mesma regra).

    Nunca levanta: filtro roda fora do `handleError`, e uma chamada de log com args que não
    batem ou `__str__` que falha não pode virar exceção no chamador.
    """
    try:
        ei = record.exc_info
        tb = record.exc_text or ("".join(traceback.format_exception(*ei)) if ei and ei[1] is not None else "")
        msg = record.getMessage()
        if "Failing row contains" in msg or "Failing row contains" in tb:
            record.msg, record.args = redigir_failing_row(msg), None
            record.exc_text, record.exc_info = redigir_failing_row(tb) or None, None
    except Exception:  # noqa: BLE001
        pass
    return True


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

    # espelho do dashboard atrás da fila do processo: grava fora da thread de quem loga
    if not any(isinstance(h, log_fila.FilaDoPainel) for h in root.handlers):
        dash_handler = _DashboardHandler()
        dash_handler.setLevel(logging.WARNING)
        fila = log_fila.instala(dash_handler)
        fila.setLevel(logging.WARNING)
        if _sem_ratelimit_no_banco not in fila.filters:
            fila.addFilter(_sem_ratelimit_no_banco)
        root.addHandler(fila)

    # todo handler do root, inclusive os que o processo instalou antes deste módulo
    for h in root.handlers:
        if _sem_failing_row not in h.filters:
            h.addFilter(_sem_failing_row)

    _root_configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Retorna um logger configurado. Use no topo de cada módulo:
        logger = get_logger(__name__)

    WARNING e ERROR aparecem automaticamente no dashboard admin.
    """
    _configure_root_logger()
    return logging.getLogger(name)
