"""Escrita e leitura da tabela `system_event_logs` — a metade "banco" do que era
`core/observability.py`.

Separado porque o arquivo original ia estourar o teto de 350 linhas do
`tests/test_max_lines_python.py` ao ganhar o `statement_timeout` (CLAUDE.md
§0.5). A direção do import é ÚNICA: `core.observability` importa daqui, e este
módulo não pode importar nada de lá.

**Nada de `logging` neste arquivo, e não é estilo.** O desfecho de falha aqui é
`print` no stderr, de propósito: um `logging.warning()` deste módulo é captado
pelo `_DashboardHandler` (que fica no ROOT logger, nível WARNING) e volta para
`log_system_event_sync`, que é a função que acabou de falhar. Preso por
`tests/test_log_falha_traceback.py`.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from config.env import load_app_env
from core.pg_text import limpa_para_pg


load_app_env()

# Teto de EXECUÇÃO das duas conexões deste módulo. O `connect_timeout` do libpq
# limita só o handshake: com a `system_event_logs` travada (`access exclusive`
# de um `alter table`/`vacuum full`), o INSERT esperava o lock inteiro — medido
# nesta sessão, 3,00s de espera para um lock de 3s, sem teto nenhum. Como o
# `_DashboardHandler` está no root logger, essa espera é paga por QUALQUER
# `warning()`/`error()` do processo, inclusive dentro do event loop.
# Com `-c statement_timeout=1500ms` e lock de 8s: cancelado em 1,50s,
# `QueryCanceled` sqlstate 57014; com a tabela livre, 0,004s.
#
# O QUE ESTE TETO NÃO COBRE, e são furos NOMEADOS, não esquecimento:
#   (a) o COMMIT. Não há knob por query no libpq — é o mesmo teto que o
#       `_CursorComTeto` de `db/open_finance.py:592-600` já registra.
#   (b) servidor que aceita o socket e nunca responde. O `statement_timeout` é
#       aplicado PELO SERVIDOR: se ele não processa, não há quem cancele.
#       Fechar isso exige `conn.cancel()` num watchdog — outro PR, como
#       `db/open_finance.py:609-610` já registra para o rollback.
_TETO_PADRAO_MS = 2000
_PISO_MS = 100

# Guarda de reentrância do ciclo handler → log → handler. Ver `_reentrou`.
_local = threading.local()


def _statement_timeout_options() -> str:
    """`options` do connect, com config sem sentido voltando ao default.

    Piso no molde de `_prazo_reconexao_ms` (`frontend/routes/open_finance.py`),
    e pelo mesmo motivo elevado a um grau: aqui `0` não é só "sem sentido", é
    INVERSÃO — no Postgres `statement_timeout=0` significa SEM LIMITE, então
    obedecer a env desligaria exatamente o que ela configura. Negativo o
    servidor recusa (o connect inteiro falharia), e abaixo de 100ms não cabe
    nem o INSERT em tabela livre.

    Construído POR CHAMADA, não como constante de módulo, para a conversão
    ficar dentro do `try` das duas funções: uma env inválida não pode virar
    exceção no import de `core.observability`, que meio repositório importa.

    ponytail: `options=` SOBRESCREVE um `options` que viesse na `DATABASE_URL`
    (mesmo teto de `db/open_finance_state.py:704-707`). Hoje a nossa URL não
    traz nenhum; se um dia trouxer, este parâmetro o apaga em silêncio.
    """
    try:
        ms = int(os.getenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", str(_TETO_PADRAO_MS)))
    except (TypeError, ValueError):
        ms = _TETO_PADRAO_MS
    return f"-c statement_timeout={ms if ms >= _PISO_MS else _TETO_PADRAO_MS}ms"


def _reentrou(o_que: str) -> bool:
    """True se esta thread já está dentro de uma escrita/leitura deste módulo.

    Fecha o ciclo que o teto acima torna mais provável: `psycopg` tem
    `logging.getLogger("psycopg")` e emite `logger.warning("error ignored in
    rollback on %s: %s", …)` no `Connection.__exit__` (psycopg 3.3.5,
    `connection.py:170`) quando o `with` sai com exceção E o rollback também
    falha. Esse record sobe ao root, o `_DashboardHandler` o pega (o único
    filtro que existe lá, `_sem_ratelimit_no_banco`, só exclui o `slowapi`) e
    chama `log_system_event_sync` de novo — connect novo na MESMA tabela
    travada, cada volta custando um TCP connect mais o teto inteiro.

    A guarda fica no ponto de estrangulamento (as duas funções deste módulo)
    e não num filtro por `record.name == "psycopg"`: toda volta do ciclo passa
    obrigatoriamente por aqui, então isto fecha a CLASSE e não a instância
    (CLAUDE.md §2). Thread-local e não global de propósito: WARNING legítimo de
    OUTRA thread não pode ser descartado porque esta está logando.

    A reentrada devolve o mesmo que o `except` das duas já devolvia (`None` /
    `False`), e imprime — perda de log não pode ser muda.
    """
    if getattr(_local, "dentro", False):
        print(f"[observability] reentrada ignorada em {o_que}", file=sys.stderr)
        return True
    return False


def _desfecho(exc: Exception) -> str:
    """`teto` (o `statement_timeout` acima cortou) vs `defeito` (todo o resto).

    Discriminado por `sqlstate` 57014 via `QueryCanceled`, nunca por `str(exc)`:
    "canceling statement due to statement timeout" é traduzida pelo
    `lc_messages` do servidor; o sqlstate não.
    """
    rotulo = "teto" if isinstance(exc, psycopg.errors.QueryCanceled) else "defeito"
    return f"{rotulo} {type(exc).__name__} sqlstate={getattr(exc, 'sqlstate', None)}: {exc}"


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
    if _reentrou(f"record {event_type}"):
        return

    _local.dentro = True
    try:
        # `connect_timeout=2`: este INSERT é síncrono e bloqueante, e com banco
        # inalcançável o connect ficava preso (medido: >30s) — travando a thread
        # do caller, que pode ser o event loop. 2s é o MÍNIMO que o libpq aceita
        # (valor menor é promovido a 2). Perder um log é melhor que travar a
        # requisição: a falha já cai no `except` abaixo, que só imprime no stderr.
        # `options`: o `connect_timeout` NÃO cobre a query nem a espera de lock —
        # o teto de execução é o `statement_timeout`, aplicado pelo SERVIDOR.
        with psycopg.connect(database_url, connect_timeout=2,
                             options=_statement_timeout_options()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO system_event_logs (level, event_type, message, source, user_id, details)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (limpa_para_pg(level), limpa_para_pg(event_type),
                     limpa_para_pg(message[:1000]), limpa_para_pg(source),
                     user_id, Jsonb(limpa_para_pg(details or {}))),
                )
            conn.commit()
    except Exception as exc:
        # Desde o saneamento com `limpa_para_pg` na tupla acima, NUL e surrogate
        # solitário não derrubam mais este INSERT (issue #357): das duas causas
        # conhecidas de perda silenciosa aqui, sobra só a de baixo.
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
        # A partir do teto, o `QueryCanceled` (sqlstate 57014) também cai aqui:
        # perder o log é o desfecho ESCOLHIDO, e o `_desfecho` o rotula para que
        # "banco travado" não se leia como "bug no INSERT".
        print(f"[observability] failed to record {event_type}: {_desfecho(exc)}",
              file=sys.stderr)
    finally:
        _local.dentro = False


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
    if _reentrou(f"check {event_type}"):
        return False

    _local.dentro = True
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
        # O `options` é o que fecha o "consulta lenta continua sendo esperada":
        # o SELECT e a espera de lock agora têm teto, e o desfecho do corte é
        # `False`, exatamente o que o `except` abaixo já devolvia.
        with psycopg.connect(database_url, connect_timeout=2,
                             options=_statement_timeout_options()) as conn:
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
        print(f"[observability] failed to check {event_type}: {_desfecho(exc)}",
              file=sys.stderr)
        return False
    finally:
        _local.dentro = False
