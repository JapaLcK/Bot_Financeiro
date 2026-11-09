"""
db/affiliate_reviews_manutencao.py — o que roda fora do fluxo do reembolso: a exclusão do afiliado,
a limpeza de `no_commission` e a fila da conferência na Stripe.

Separado de `db/affiliate_reviews.py` pelo portão de 350 linhas; as regras da pendência moram lá.
"""
from .affiliate_reviews import registrar_evento
from .connection import get_conn


def desvincular_afiliado(cur, user_id: int) -> None:
    """Afiliado que exclui a conta (A3, D8 = B): pendências, histórico e saques ficam, desvinculados do
    usuário, com os dados financeiros retidos. Chave Pix e nota do saque saem; pendência `open` fecha,
    porque sem comissão não há o que bloquear nem o que o admin decidir. Roda na transação de
    `delete_user_data`, antes de apagar `affiliates`, que leva as comissões em cascata.

    Travas na ordem do `request_payout` (afiliado, depois comissões), com os saques no meio: afiliado,
    saques, comissões, pendências. Comissões antes do afiliado fariam ciclo com o pedido de saque."""
    cur.execute("select id, code from affiliates where user_id = %s for update", (int(user_id),))
    afiliado = cur.fetchone()
    if afiliado is None:
        return
    cur.execute("select id from affiliate_payouts where affiliate_id = %s order by id for update", (afiliado["id"],))
    cur.execute("select id from affiliate_commissions where affiliate_id = %s order by id for update", (afiliado["id"],))
    cur.execute(
        """
        update affiliate_refund_reviews r set affiliate_code = %s
          from affiliate_commissions c
         where c.id = r.commission_id and c.affiliate_id = %s
        returning r.id, r.status
        """,
        (afiliado["code"], afiliado["id"]),
    )
    for r in cur.fetchall():
        if r["status"] == "open":
            cur.execute("update affiliate_refund_reviews set status = 'resolved' where id = %s", (r["id"],))
            registrar_evento(cur, r["id"], "affiliate_deleted", "sistema")
    cur.execute("update affiliate_payouts set affiliate_code = %s, pix_key_enc = null, note = null"
                " where affiliate_id = %s", (afiliado["code"], afiliado["id"]))


def limpar_sem_comissao(dias: int = 30, limite: int = 500) -> int:
    """Apaga `no_commission` antiga: reembolso de fatura que nunca virou comissão. Escolha operacional,
    não garantia contra `invoice.paid` atrasado; quem cobre o atraso é a conferência na Stripe.

    O `status` repetido fora da subconsulta é o que segura a corrida com `promover`: o `delete` espera
    a linha travada e reavalia a condição dela depois do commit."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            delete from affiliate_refund_reviews r
             where r.status = 'no_commission' and r.commission_id is null
               and r.id in (select v.id from affiliate_refund_reviews v
                             where v.status = 'no_commission' and v.commission_id is null
                               and v.created_at < now() - make_interval(days => %s)
                               and not exists (select 1 from affiliate_refund_review_events e where e.review_id = v.id)
                             order by v.id limit %s)
            """,
            (int(dias), int(limite)),
        )
        apagadas = cur.rowcount
        conn.commit()
    return apagadas


def comissoes_a_conferir(limite: int = 20) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id, stripe_invoice_id from affiliate_commissions where payment_check_pending"
                    " order by id limit %s", (int(limite),))
        return cur.fetchall()


def liberar_conferencia(commission_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update affiliate_commissions set payment_check_pending = false where id = %s", (int(commission_id),))
        conn.commit()


def resumo_da_conferencia() -> dict:
    """Para o admin notar queda longa da Stripe: quantas aguardam e desde quando."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as count, min(created_at) as oldest from affiliate_commissions"
                    " where payment_check_pending")
        return cur.fetchone()
