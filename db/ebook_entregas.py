"""db/ebook_entregas.py — a pendência do e-book comprado na /assinar.

Uma linha por compra (`user_id`, `session_id`). O webhook do checkout grava
(`registrar`), o job `core/services/ebook_entrega.py` varre (`abertas`), pega a
linha com um claim que expira (`reivindicar`) e fecha (`fechar`). Nenhuma
transação fica aberta durante o Stripe e o Resend.
"""

from __future__ import annotations

from .connection import get_conn


def registrar(user_id: int, session_id: str, ebook_price: str, ebook_url: str | None) -> None:
    """Idempotente (reentrega do webhook). Levanta em erro de banco: o webhook
    precisa do 5xx para a Stripe reentregar."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into ebook_entregas (user_id, session_id, ebook_price, ebook_url)"
            " values (%s, %s, %s, %s) on conflict (user_id, session_id) do nothing",
            (int(user_id), session_id, ebook_price, ebook_url),
        )


def abertas() -> list[tuple[int, str]]:
    """A varredura do job — a ÚNICA query sem `user_id`, de propósito (o mesmo
    caso de `eventos_pendentes` do Pix): devolve só as chaves, e toda operação
    seguinte filtra por `user_id AND session_id`."""
    # ponytail: sem `limit` — o volume é o de compras ainda sem senha, e um
    # `limit` faria as linhas antigas (que nunca criam senha) bloquearem as
    # novas. Se crescer, filtrar a credencial no SQL.
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id, session_id from ebook_entregas"
            " where fechada_em is null and ebook_url is not null"
            " and (reivindicada_ate is null or reivindicada_ate < now())"
            " order by criada_em"
        )
        return [(int(r["user_id"]), r["session_id"]) for r in cur.fetchall()]


def reivindicar(user_id: int, session_id: str) -> dict | None:
    """Claim atômico: duas rodadas simultâneas não pegam a mesma linha. Se o job
    cair antes do `fechar`, o claim expira e a linha volta (pelo menos uma vez).
    O claim dobra a cada tentativa (10, 20, 40… min, teto de 1 dia): a linha que
    falha sempre espaça sozinha em vez de logar a cada 5 min, e nunca fecha."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update ebook_entregas set tentativas = tentativas + 1, reivindicada_ate = now()"
            " + make_interval(mins => least(10 << least(tentativas, 8), 1440))"
            " where user_id = %s and session_id = %s and fechada_em is null"
            " and (reivindicada_ate is null or reivindicada_ate < now())"
            " returning ebook_price, ebook_url",
            (int(user_id), session_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fechar(user_id: int, session_id: str, resultado: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update ebook_entregas set fechada_em = now(), resultado = %s"
            " where user_id = %s and session_id = %s and fechada_em is null",
            (resultado, int(user_id), session_id),
        )
        return cur.rowcount == 1
