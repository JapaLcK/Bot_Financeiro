"""db/ebook_entregas.py — a pendência dos produtos comprados na /assinar.

Uma linha por produto comprado (`user_id`, `session_id`, `ebook_price`). O
webhook do checkout grava (`registrar`), o job `core/services/ebook_entrega.py`
varre (`abertas`), pega a linha com um claim que expira (`reivindicar`) e fecha
(`fechar`). Nenhuma transação fica aberta durante o Stripe e o Resend.
"""

from __future__ import annotations

from .connection import get_conn


def registrar(user_id: int, session_id: str, itens: list[tuple[str, str | None]]) -> None:
    """`itens` = [(ebook_price, ebook_url | None)]. Um statement só: grava todos
    ou nenhum. Idempotente (reentrega do webhook). Levanta em erro de banco: o
    webhook precisa do 5xx para a Stripe reentregar."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into ebook_entregas (user_id, session_id, ebook_price, ebook_url)"
            " select %s, %s, p, u from unnest(%s::text[], %s::text[]) as t(p, u)"
            " on conflict (user_id, session_id, ebook_price) do nothing",
            (int(user_id), session_id, [p for p, _ in itens], [u for _, u in itens]),
        )


def abertas() -> list[tuple[int, str, str]]:
    """A varredura do job — a ÚNICA query sem `user_id`, de propósito (o mesmo
    caso de `eventos_pendentes` do Pix): devolve só as chaves, e toda operação
    seguinte filtra por `user_id AND session_id AND ebook_price`."""
    # ponytail: sem `limit` — o volume é o de compras ainda sem senha, e um
    # `limit` faria as linhas antigas (que nunca criam senha) bloquearem as
    # novas. Se crescer, filtrar a credencial no SQL.
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id, session_id, ebook_price from ebook_entregas"
            " where fechada_em is null and ebook_url is not null"
            " and (reivindicada_ate is null or reivindicada_ate < now())"
            " order by criada_em"
        )
        return [(int(r["user_id"]), r["session_id"], r["ebook_price"]) for r in cur.fetchall()]


def reivindicar(user_id: int, session_id: str, ebook_price: str) -> dict | None:
    """Claim atômico: duas rodadas simultâneas não pegam a mesma linha. Se o job
    cair antes do `fechar`, o claim expira e a linha volta (pelo menos uma vez).
    O claim dobra a cada tentativa (10, 20, 40… min, teto de 1 dia): a linha que
    falha sempre espaça sozinha em vez de logar a cada 5 min, e nunca fecha."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update ebook_entregas set tentativas = tentativas + 1, reivindicada_ate = now()"
            " + make_interval(mins => least(10 << least(tentativas, 8), 1440))"
            " where user_id = %s and session_id = %s and ebook_price = %s and fechada_em is null"
            " and (reivindicada_ate is null or reivindicada_ate < now())"
            " returning ebook_price, ebook_url",
            (int(user_id), session_id, ebook_price),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fechar(user_id: int, session_id: str, ebook_price: str, resultado: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update ebook_entregas set fechada_em = now(), resultado = %s"
            " where user_id = %s and session_id = %s and ebook_price = %s"
            " and fechada_em is null",
            (resultado, int(user_id), session_id, ebook_price),
        )
        return cur.rowcount == 1
