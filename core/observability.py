from __future__ import annotations

import logging
import os
import sys
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from config.env import load_app_env


load_app_env()

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
    Contido em dois pontos, não resolvido: (1) `connect_timeout=2` nas DUAS
    funções que abrem conexão aqui (`log_system_event_sync` e
    `recent_event_exists`), limitando o travamento a 2s com banco inalcançável
    (era >30s, medido); (2) os 5 call sites das 4 rotas destrutivas `async`
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


# ── DB event log ──────────────────────────────────────────────────────────────

def _database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def log_system_event_sync(
    level: str,
    event_type: str,
    message: str,
    *,
    source: str | None = None,
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    database_url = _database_url()
    if not database_url:
        return

    try:
        # `connect_timeout=2`: este INSERT é síncrono e bloqueante, e com banco
        # inalcançável o connect ficava preso (medido: >30s) — travando a thread
        # do caller, que pode ser o event loop. 2s é o MÍNIMO que o libpq aceita
        # (valor menor é promovido a 2). Perder um log é melhor que travar a
        # requisição: a falha já cai no `except` abaixo, que só imprime no stderr.
        with psycopg.connect(database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_event_logs (level, event_type, message, source, user_id, details)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (level, event_type, message[:1000], source, user_id, Jsonb(details or {})),
                )
            conn.commit()
    except Exception as exc:
        # ponytail: teto conhecido — `user_id` fora de `users` derruba o INSERT
        # INTEIRO pela `system_event_logs_user_id_fkey` e o evento se PERDE; antes
        # deste PR ele ficava gravado com a coluna NULL. Caminho medido: token de
        # dashboard LEGADO (sem `jti`) de conta já apagada: o ramo
        # `frontend/routes/shared.py:538-543` só invalida via
        # `get_password_changed_at` (`:542`), que numa conta apagada não devolve
        # nada, e a rota roda com um `user_id` sem linha em `users`. Token COM
        # `jti` cai no `:533-536`, onde `auth_sessions` já foi apagado junto com
        # a conta (`db/privacy.py:429`) e vira 401 ANTES da rota.
        # Quem ainda emite token de dashboard SEM `jti` HOJE, já depois do
        # rollout: `POST /auth/dashboard-token` e `POST /auth/dashboard-link`
        # (`frontend/finance_bot_websocket_custom.py:3440` e `:3453`), que leem
        # `request.state.session_jti` com `getattr(…, None)` (`:3443`, `:3467`)
        # — e esse atributo só é setado DENTRO do ramo `if jti:` (`shared.py:537`
        # e `finance_bot_websocket_custom.py:2325`), nunca no ramo legado de
        # `_get_current_user` (`finance_bot_websocket_custom.py:2328-2335`).
        # Então a janela é a UNIÃO de dois conjuntos, não só a dos tokens
        # pré-rollout: (a) token de dashboard pré-rollout, teto de 12h
        # (`DASHBOARD_SESSION_HOURS`, `finance_bot_websocket_custom.py:298`); e (b)
        # token de dashboard novo mintado a partir de um JWT de auth LEGADO.
        # Esse JWT vive 15 MINUTOS (`frontend/routes/shared.py:472`, que é o
        # único lugar que minta `"type": "auth"`; espelhado em
        # `AUTH_COOKIE_MAX_AGE`, `finance_bot_websocket_custom.py:2169`), e JWT
        # legado novo não nasce — todo `_make_jwt` de produção passa `jti` real
        # (`:2190`, `:2829`, `:4969`). Nem estica: rotacionar exige
        # `session_jti` (`core/refresh_tokens.py:51`, coluna `not null` em
        # `db/schema.py:1485`), que um JWT sem `jti` não tem. Logo (b) só é
        # MINTADO nos 15 min seguintes ao rollout, e o último token dele morre
        # 12h depois: 15min + 12h — a união fecha em ~12h15. Os dois prazos
        # vieram do `timedelta`/`max_age`, não de comentário que fale deles.
        # A perda é DECISÃO REGISTRADA, não esquecimento: retry com `user_id=None`
        # gravaria linha órfã com o id do titular no texto, nascida DEPOIS da
        # exclusão de conta e fora do alcance de qualquer `delete` — a mesma forma
        # de bug que este PR fecha (#220). O rastro que sobra é o `print` abaixo,
        # no stderr, e é de propósito.
        print(f"[observability] failed to record {event_type}: {exc}", file=sys.stderr)


def recent_event_exists(event_type: str, user_id: int, within_days: float = 7.0) -> bool:
    """
    True se existe um system_event_logs com (event_type, user_id) nos últimos
    `within_days`. Usado pra dedup de emails transacionais que podem ser
    disparados por múltiplas fontes (webhook + scheduler).
    Falha silenciosa retorna False — melhor mandar duplicado que perder.
    """
    database_url = _database_url()
    if not database_url:
        return False
    try:
        # `connect_timeout=2` pelo mesmo motivo do `log_system_event_sync` acima.
        # O timeout do libpq limita o ESTABELECIMENTO da conexão, não a query
        # (medido: `connect_timeout=2` + `pg_sleep(5)` devolveu o resultado em
        # 5,00s; connect real 3–6 ms), então consulta lenta continua sendo
        # esperada. Com banco inalcançável o retorno vira `False` em ~2s em vez
        # de >30s — e `False` já é o que o `except` abaixo devolve. A dedup só
        # muda numa janela estreita: banco VIVO cujo connect demore mais de 2s
        # passa a devolver `False` e o e-mail sai duplicado — que é a política
        # já declarada no docstring ("melhor mandar duplicado que perder").
        with psycopg.connect(database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM system_event_logs
                    WHERE event_type = %s
                      AND user_id = %s
                      AND created_at > now() - %s::interval
                    LIMIT 1
                    """,
                    (event_type, int(user_id), f"{within_days} days"),
                )
                return cur.fetchone() is not None
    except Exception as exc:
        print(f"[observability] failed to check {event_type}: {exc}", file=sys.stderr)
        return False
