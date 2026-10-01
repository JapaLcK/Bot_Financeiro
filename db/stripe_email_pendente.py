"""db/stripe_email_pendente.py — o e-mail novo que falta levar ao cliente do Stripe.

Uma linha por conta. A PATCH /settings/{uid}/security/contact grava (ou sobe a
`versao`) na mesma transação da troca; o job `core/services/stripe_email_sync.py`
varre (`abertas`), pega a linha com um claim que expira (`reivindicar`) e apaga
(`fechar`). Mesmo padrão de `db/ebook_entregas.py`; nenhuma transação fica
aberta durante o Stripe.
"""

from __future__ import annotations

from .connection import get_conn


def abertas() -> list[int]:
    """A varredura do job — a ÚNICA query sem `user_id`, de propósito (o mesmo
    caso de `ebook_entregas.abertas`): devolve só as chaves, e toda operação
    seguinte filtra por `user_id`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select user_id from stripe_email_pendente"
            " where reivindicada_ate is null or reivindicada_ate < now()"
            " order by criada_em"
        )
        return [int(r["user_id"]) for r in cur.fetchall()]


def reivindicar(user_id: int) -> dict | None:
    """Claim atômico que já devolve o e-mail ATUAL da conta. O claim dobra a cada
    tentativa (10, 20, 40… min, teto de 1 dia) — a mesma régua de
    `ebook_entregas.reivindicar`. Invariante: o claim mínimo (10 min) é maior que
    a pior chamada do Stripe (stripe 15.6.1: max_network_retries=2 e timeout de
    80 s por tentativa, ou seja 3 × 80 s = 240 s, mais até 5 s de espera entre
    tentativas; medido no .venv em 2026-10-01, remeça ao subir a lib), então
    duas rodadas nunca enviam a mesma linha ao mesmo tempo."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update stripe_email_pendente p set tentativas = p.tentativas + 1,"
            " reivindicada_ate = now() + make_interval(mins => least(10 << least(p.tentativas, 8), 1440))"
            " from auth_accounts a where p.user_id = %s and a.user_id = p.user_id"
            " and (p.reivindicada_ate is null or p.reivindicada_ate < now())"
            " returning p.versao, a.stripe_customer_id, a.email, a.email_enc",
            (int(user_id),),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def fechar(user_id: int, versao: int) -> bool:
    """Apaga só se ninguém trocou o e-mail durante o envio. Se trocou (a `versao`
    subiu), solta o claim para a próxima rodada mandar o e-mail atual."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from stripe_email_pendente where user_id = %s and versao = %s",
                    (int(user_id), int(versao)))
        if cur.rowcount == 1:
            return True
        cur.execute("update stripe_email_pendente set reivindicada_ate = null where user_id = %s",
                    (int(user_id),))
        return False
