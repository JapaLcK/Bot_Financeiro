"""db/reconquista.py — fila do e-mail de reconquista de trial vencido sem pagamento.
Toda query filtra por user_id; o lote só reivindica linha vencida (skip locked)."""
from __future__ import annotations

from .connection import get_conn


def agendar(user_id: int, trial_end) -> None:
    """Uma linha por usuário, nunca reagendada: a 2ª chamada não muda nada."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into reconquista_trial (user_id, trial_end, enviar_em)"
            " values (%s, %s, %s + interval '2 days') on conflict (user_id) do nothing",
            (int(user_id), trial_end, trial_end),
        )
        conn.commit()


def reivindicar_vencidos(limite: int = 20) -> list[dict]:
    """pendente vencida -> enviando, atomicamente; devolve (user_id, trial_end)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update reconquista_trial set status = 'enviando' where user_id in ("
            " select user_id from reconquista_trial where status = 'pendente' and enviar_em <= now()"
            " order by enviar_em for update skip locked limit %s)"
            " and status = 'pendente' returning user_id, trial_end",
            (int(limite),),
        )
        rows = cur.fetchall()
        conn.commit()
    return [dict(r) for r in rows]


def fechar(user_id: int, status: str, motivo: str | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update reconquista_trial set status = %s, motivo = %s,"
            " enviado_em = case when %s = 'enviado' then now() else enviado_em end"
            " where user_id = %s",
            (status, motivo, status, int(user_id)),
        )
        conn.commit()


def saindo(user_id: int) -> bool:
    """Exclusão de conta agendada ou em curso (mesma expressão de `remarketing.estado_fresco`)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select 1 from auth_accounts where user_id = %s and deletion_status in ('scheduled', 'processing')"
            " and deletion_scheduled_for is not null limit 1",
            (int(user_id),),
        )
        r = cur.fetchone()
        conn.commit()
    return r is not None
