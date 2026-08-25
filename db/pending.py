"""
db/pending.py — Ações pendentes de confirmação (ex: "apagar lançamento?").
"""
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .connection import get_conn
from .users import ensure_user


def advance_pending_action(user_id: int, action_type: str,
                           old_payload: dict, new_payload: dict | None,
                           minutes: int = 10,
                           new_action_type: str | None = None) -> bool:
    """Avança (ou apaga) a pendência SÓ SE ela ainda for `old_payload`.

    Compare-and-swap. Duas respostas do mesmo usuário podem ser processadas em
    paralelo: `adapters/discord/discord_bot.py:122` é um `on_message` async sem
    lock, e o `launch.py` sobe o Discord num processo separado do uvicorn — um
    usuário com as duas plataformas ligadas é alcançado pelos dois ao mesmo
    tempo. (O webhook do WhatsApp, sozinho, NÃO corre: `wa_app.py` enfileira em
    `_queue` e um `_worker_loop` único consome um payload por vez.)
    Sem isso as duas leem a mesma fila, registram o MESMO item e o segundo valor
    some. Aqui a segunda escrita não pega: o Postgres serializa o UPDATE na
    linha, a condição `payload = <o que eu li>` já não vale, `rowcount` volta 0 e
    quem chamou relê a fila e reavalia.

    Sem lock de propósito. Um `pg_advisory_xact_lock` numa conexão dedicada
    segura uma conexão do pool durante todo o trabalho: com o pool em 8, oito
    usuários simultâneos consomem o pool só em locks e o bot inteiro para.

    Devolve True se gravou, False se outra thread já tinha avançado. Gravar
    renova o prazo (`minutes`), como o `set_pending_action` que ela substitui —
    senão uma fila longa expiraria 10 min depois da PRIMEIRA pergunta, não da
    última resposta.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            if new_payload is None:
                cur.execute(
                    "delete from pending_actions "
                    "where user_id = %s and action_type = %s and payload = %s",
                    (user_id, action_type, Jsonb(old_payload)),
                )
            else:
                cur.execute(
                    "update pending_actions "
                    "set action_type = %s, payload = %s, created_at = now(), "
                    "    expires_at = %s "
                    "where user_id = %s and action_type = %s and payload = %s",
                    (new_action_type or action_type,
                     Jsonb(new_payload),
                     datetime.now(timezone.utc) + timedelta(minutes=minutes),
                     user_id, action_type, Jsonb(old_payload)),
                )
            gravou = cur.rowcount == 1
        conn.commit()
    return gravou



def create_pending_action_if_absent(user_id: int, action_type: str, payload: dict,
                                    minutes: int = 10) -> bool:
    """Cria a pendência SÓ SE o usuário não tiver nenhuma. Devolve True se criou.

    Irmã do `advance_pending_action` para o caso "não havia linha". O
    `set_pending_action` faz upsert incondicional: duas devoluções simultâneas
    (dois itens reivindicados que estouraram, ex. os dois batendo o teto de
    plano) veem a fila vazia e cada uma grava a SUA — a última apaga a primeira
    e um item some. Aqui a segunda insere zero linhas, devolve False, e quem
    chamou relê e prepende na fila que a primeira acabou de criar.
    """
    ensure_user(user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pending_actions (user_id, action_type, payload, expires_at) "
                "values (%s, %s, %s, %s) on conflict (user_id) do nothing",
                (user_id, action_type, Jsonb(payload), expires_at),
            )
            criou = cur.rowcount == 1
        conn.commit()
    return criou


def set_pending_action(user_id: int, action_type: str, payload: dict, minutes: int = 10):
    """Cria/atualiza uma ação pendente de confirmação (persistente no Postgres)."""
    ensure_user(user_id)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                insert into pending_actions (user_id, action_type, payload, expires_at)
                values (%s, %s, %s, %s)
                on conflict (user_id)
                do update set action_type = excluded.action_type,
                              payload = excluded.payload,
                              created_at = now(),
                              expires_at = excluded.expires_at
                """,
                (user_id, action_type, Jsonb(payload), expires_at),
            )
        conn.commit()


def get_pending_action(user_id: int):
    """Retorna a ação pendente se existir e não estiver expirada. Senão None."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "select user_id, action_type, payload, created_at, expires_at "
                "from pending_actions where user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        return None

    if row["expires_at"] <= datetime.now(timezone.utc):
        clear_pending_action(user_id)
        return None

    return row


def clear_pending_action(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from pending_actions where user_id = %s", (user_id,))
        conn.commit()
