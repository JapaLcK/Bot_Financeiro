"""db/pix_extras.py — a cobrança Pix lida pelo job que entrega os cadernos extras.

Filtra por `user_id` E `external_reference` (CLAUDE.md §0): a linha de
`ebook_entregas` traz os dois, e uma linha de A com a referência da cobrança de B
não acha nada — o job fecha `nao_comprou` sem consultar o Asaas.
"""

from __future__ import annotations

from .connection import get_conn


def cobranca_do_dono(user_id: int, external_reference: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select status, asaas_payment_id, extras from pix_charges"
            " where user_id = %s and external_reference = %s",
            (int(user_id), external_reference),
        )
        row = cur.fetchone()
    return dict(row) if row else None
