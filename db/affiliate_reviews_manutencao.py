"""
db/affiliate_reviews_manutencao.py — o que roda fora do fluxo do reembolso: a exclusão do afiliado,
a limpeza de `no_commission` e a fila da conferência na Stripe.

Separado de `db/affiliate_reviews.py` pelo portão de 350 linhas; as regras da pendência moram lá.
"""
from .affiliate_reviews import registrar_evento
from .connection import get_conn


def desvincular_afiliado(cur, user_id: int) -> None:
    """Afiliado que exclui a conta (A3, D8 = B): pendências, histórico e saques ficam, desvinculados do
    usuário, com os dados financeiros retidos. Chave Pix e nota do saque saem. A comissão paga (N1) e a
    de saque aberto ("guardar vínculo") deixam na linha da fatura o valor e o saque, para o reembolso
    que chegar depois seguir a regra de afiliado ativo; a pendência `open` delas segue aberta. Só a
    `open` de comissão solta fecha: nunca paga nem em saque, não há o que bloquear nem recuperar.
    Roda na transação de `delete_user_data`, antes de apagar `affiliates`, que leva as comissões.

    Ordem global entre exclusões (#1414): roda para todo usuário, afiliado ou não. Primeiro trava, uma
    linha por vez e em ordem de id, os afiliados ligados ao usuário: o dele FOR UPDATE, e os que o
    indicaram ou têm comissão dele FOR KEY SHARE. Toda linha-filho que esta transação apaga (cascata) ou
    anula (set null) pertence a um afiliado travado aqui, e as duas forças conflitam: duas exclusões
    ligadas se encontram em `affiliates`, em ordem total, e nunca nas referrals e comissões. KEY SHARE
    convive com o `no key update` do saque e do percentual e com a FK de comissão nova. Subir a força
    de uma trava já pega seria o deadlock clássico; por isso uma por vez. Indicação nova entre a leitura e
    a trava não espera nada, mas `record_referral` só roda no cadastro: exige excluir a conta no instante
    do próprio cadastro (resíduo aceito). Comissão nova sai de afiliado já no conjunto.

    Depois, na ordem do `request_payout` (afiliado, depois comissões), com os saques no meio: afiliado,
    saques, comissões, pendências. Comissões antes do afiliado fariam ciclo com o pedido de saque."""
    cur.execute(
        """
        select a.id, a.user_id = %(u)s as proprio from affiliates a
         where a.user_id = %(u)s
            or a.id in (select affiliate_id from affiliate_referrals where referred_user_id = %(u)s
                        union select affiliate_id from affiliate_commissions where referred_user_id = %(u)s)
         order by a.id
        """,
        {"u": int(user_id)},
    )
    for a in cur.fetchall():
        modo = "for update" if a["proprio"] else "for key share"
        cur.execute(f"select id from affiliates where id = %s {modo}", (a["id"],))
    cur.execute("select id, code from affiliates where user_id = %s for update", (int(user_id),))
    afiliado = cur.fetchone()
    if afiliado is None:
        return
    cur.execute("select id from affiliate_payouts where affiliate_id = %s order by id for update", (afiliado["id"],))
    cur.execute("select id from affiliate_commissions where affiliate_id = %s order by id for update", (afiliado["id"],))
    # Comissão paga ou em saque sem linha da fatura ganha uma, `resolved`: o reembolso que chegar depois
    # tem de achar o que foi pago ou o saque que bloquear. O evento guarda o saque, que sobrevive (D8).
    cur.execute(
        """
        select c.id, c.stripe_invoice_id, c.amount_cents, c.payout_id from affiliate_commissions c
         where c.affiliate_id = %s and (c.status = 'paid' or (c.status = 'pending' and c.payout_id is not null))
           and not exists (select 1 from affiliate_refund_reviews r where r.stripe_invoice_id = c.stripe_invoice_id)
        """,
        (afiliado["id"],),
    )
    for c in cur.fetchall():
        cur.execute("insert into affiliate_refund_reviews(stripe_invoice_id, commission_id, tipo, refunded_cents,"
                    " amount_cents, status) values (%s, %s, 'partial', 0, %s, 'resolved') returning id",
                    (c["stripe_invoice_id"], c["id"], c["amount_cents"]))
        registrar_evento(cur, cur.fetchone()["id"], "affiliate_deleted", "sistema", saque=c["payout_id"])
    cur.execute(
        """
        update affiliate_refund_reviews r
           set affiliate_code = %s,
               paid_cents = case when c.status = 'paid' then c.amount_cents end,
               commission_cents = case when c.status = 'pending' and c.payout_id is not null then c.amount_cents end,
               orphan_payout_id = case when c.status = 'pending' then c.payout_id end
          from affiliate_commissions c
         where c.id = r.commission_id and c.affiliate_id = %s
        returning r.id, r.status, c.status = 'pending' and c.payout_id is null as solta
        """,
        (afiliado["code"], afiliado["id"]),
    )
    for r in cur.fetchall():
        if r["status"] == "open" and r["solta"]:
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


ESPERA_CONFERENCIA_S = 3600  # a adiada (fatura que ainda não confirma) volta à fila do worker depois disto


def comissoes_a_conferir(limite: int = 20) -> list[dict]:
    """Primeiro as nunca tentadas, por id; depois a tentativa mais antiga, passada a espera (#1497). Assim
    as que nunca confirmam não seguram as seguintes. A espera é medida pelo `now()` do banco."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select id, stripe_invoice_id from affiliate_commissions
             where payment_check_pending
               and (payment_check_tried_at is null
                    or payment_check_tried_at < now() - make_interval(secs => %(espera)s))
             order by payment_check_tried_at nulls first, id
             limit %(limite)s
            """,
            {"espera": ESPERA_CONFERENCIA_S, "limite": int(limite)},
        )
        return cur.fetchall()


def adiar_conferencia(commission_id: int, motivo: str) -> None:
    """Só o worker: a fatura ainda não confirma. Grava a hora e o motivo, depois do HTTP e numa transação
    curta; a marca, e com ela o bloqueio financeiro, fica."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update affiliate_commissions set payment_check_tried_at = now(), payment_check_reason = %s"
                    " where id = %s and payment_check_pending", (motivo, int(commission_id)))
        conn.commit()


def marcar_conferencia(commission_ids: list[int]) -> None:
    """Fica para o worker conferir: disputa aberta (D10) ou Stripe fora no "Pagamento já realizado"."""
    if not commission_ids:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update affiliate_commissions set payment_check_pending = true where id = any(%s)",
                    ([int(i) for i in commission_ids],))
        conn.commit()


def liberar_conferencia(commission_id: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update affiliate_commissions set payment_check_pending = false, payment_check_tried_at = null,"
                    " payment_check_reason = null where id = %s", (int(commission_id),))
        conn.commit()


def resumo_da_conferencia() -> dict:
    """Para o admin notar queda longa da Stripe: quantas aguardam e desde quando."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as count, min(created_at) as oldest from affiliate_commissions"
                    " where payment_check_pending")
        return cur.fetchone()
