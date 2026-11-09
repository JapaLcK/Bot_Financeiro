"""
db/affiliate_reviews.py — pendência de reembolso do programa de afiliados.

Quem protege o dinheiro é a pendência no banco, não o aviso: enquanto ela está `open`, a comissão
fica fora de saque novo e o saque que a contém não é pago. A linha é também a notificação pendente
(`notified_at` nulo), gravada na mesma transação do rótulo, então aviso que falha não perde nada.

Estados: `no_commission` (fatura sem comissão, ainda), `open` (admin decide), `recovery` (dinheiro
já saiu e falta recuperar), `resolved`. `tipo` nunca rebaixa, e linha `total` nunca reabre.

Ordem de travas: nas ações do admin, saque, comissões, pendência; no webhook, fatura, (indicado),
comissão, pendência, sem saque. Sem ciclo. Nada aqui guarda dado do indicado; referência do comprovante e
justificativa moram só em `affiliate_refund_review_events`, nunca em log, aviso ou nota do saque.
"""
from .affiliate_payments import _rotular
from .connection import get_conn

# Comissão em revisão e saque bloqueado são a MESMA pendência, vista de dois lados. Fonte única
# (`{c}` e `{saque}` são expressões SQL de quem usa).
PENDENCIA_ABERTA_SQL = """exists (select 1 from affiliate_refund_reviews v
                                   where v.commission_id = {c}.id and v.status = 'open')"""
# A comissão do saque, ou o vínculo que ficou quando ela saiu com o afiliado (saque órfão).
SAQUE_BLOQUEADO_SQL = """exists (select 1 from affiliate_refund_reviews v
                                   left join affiliate_commissions vc on vc.id = v.commission_id
                                  where v.status = 'open'
                                    and (vc.payout_id = {saque} or v.orphan_payout_id = {saque}))"""


class PendenciaMudou(Exception):
    """A pendência não está mais como o admin a viu: a rota responde 409."""


def trava_fatura(cur, fatura: str) -> None:
    """Serializa reembolso e nascimento da comissão da mesma fatura: sem ela, o reembolso não vê
    a comissão, a comissão não vê o reembolso, e nenhum dos dois a segura."""
    cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"afiliado_fatura:{fatura}",))


def trava_indicado(cur, referred_user_id: int) -> None:
    """Serializa a 1ª cobrança do mesmo cliente (Pix e cartão). Só depois da `trava_fatura`: sem ciclo."""
    cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"afiliado_indicado:{int(referred_user_id)}",))


def registrar_evento(cur, review_id: int, action: str, actor: str, *, antes=None, depois=None,
                     saque=None, em=None, referencia=None, justificativa=None) -> None:
    cur.execute(
        """
        insert into affiliate_refund_review_events(review_id, action, actor, before_cents, after_cents,
                                                   payout_id, event_on, receipt_ref, justification)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (review_id, action, actor, antes, depois, saque, em, referencia, justificativa),
    )


def _gravar(cur, fatura, antes, c, tipo, devolvido, quando, status, ator, recuperacao=None) -> dict:
    """Cria ou reabre a linha. Fato novo zera aprovação e aviso; `amount_cents` nunca é reescrito."""
    if antes is None:
        cur.execute(
            """
            insert into affiliate_refund_reviews(stripe_invoice_id, commission_id, tipo, refunded_cents,
                                                 amount_cents, status, refunded_at, recovery_cents)
            values (%s, %s, %s, %s, %s, %s, %s, %s) returning *
            """,
            (fatura, c and c["id"], tipo, devolvido, c and c["amount_cents"], status, quando, recuperacao),
        )
        return cur.fetchone()
    cur.execute(
        """
        update affiliate_refund_reviews
           set tipo = %s, refunded_cents = %s, refunded_at = %s, status = %s, recovery_cents = %s,
               approved_cents = null, notified_at = null, notify_claimed_until = null,
               commission_id = coalesce(%s, commission_id), amount_cents = coalesce(amount_cents, %s)
         where id = %s returning *
        """,
        (tipo, devolvido, quando, status, recuperacao, c and c["id"], c and c["amount_cents"], antes["id"]),
    )
    linha = cur.fetchone()
    if antes["status"] in ("resolved", "recovery") and status in ("open", "recovery"):
        registrar_evento(cur, linha["id"], "reopened", ator, antes=antes["refunded_cents"], depois=devolvido)
    return linha


def _aplicar(cur, fatura, a, c, tipo, devolvido, quando, ator) -> dict | None:
    """A máquina de estados: comissão (`c`, travada) contra o reembolso. `a` é a linha que já existe."""
    if c is None and a:
        c = _comissao_que_saiu(cur, a)
    if c is None:  # pendência cuja comissão saiu com o afiliado (A3) fica como está: não vira `no_commission`
        if a and a["status"] != "no_commission":
            return a
        return _gravar(cur, fatura, a, None, tipo, devolvido, quando, "no_commission", ator)
    if c["status"] == "reversed":
        if not (a and a["status"] == "open" and tipo == "total"):
            return a
        linha = _gravar(cur, fatura, a, c, tipo, devolvido, quando, "resolved", ator)
        registrar_evento(cur, linha["id"], "auto_reversed", ator)
        return linha
    if c["status"] == "paid":
        if tipo == "partial":
            return _gravar(cur, fatura, a, c, tipo, devolvido, quando, "open", ator)
        falta = max(0, c["amount_cents"] - (a["recovery_closed_cents"] if a else 0))
        status = "recovery" if falta else "resolved"
        linha = _gravar(cur, fatura, a, c, tipo, devolvido, quando, status, ator, falta or None)
        # O saque guarda valor e data e sobrevive à exclusão do afiliado (D8): o evento aponta para ele.
        registrar_evento(cur, linha["id"], "paid_before_refund", ator, depois=c["amount_cents"], saque=c["payout_id"])
        return linha
    if tipo == "total" and c["payout_id"] is None:  # solta: D9, estorna sozinha
        cur.execute("update affiliate_commissions set status = 'reversed' where id = %s", (c["id"],))
        linha = _gravar(cur, fatura, a, c, tipo, devolvido, quando, "resolved", ator)
        registrar_evento(cur, linha["id"], "auto_reversed", ator, antes=c["amount_cents"], depois=0)
        return linha
    return _gravar(cur, fatura, a, c, tipo, devolvido, quando, "open", ator)


def _comissao_que_saiu(cur, a: dict) -> dict | None:
    """A comissão que saiu com o afiliado, refeita da linha da fatura: mesmo comportamento de afiliado
    ativo. Paga (N1) segue a regra da paga; num saque órfão ainda aberto, a da comissão em saque, que
    bloqueia. Órfão rejeitado: a comissão nunca foi paga, e a linha fica como está."""
    if a["paid_cents"] is not None:
        return {"id": None, "status": "paid", "amount_cents": a["paid_cents"], "payout_id": a["orphan_payout_id"]}
    if a["orphan_payout_id"] is not None:
        cur.execute("select status from affiliate_payouts where id = %s", (a["orphan_payout_id"],))
        saque = cur.fetchone()
        if saque and saque["status"] == "requested":
            return {"id": None, "status": "pending", "amount_cents": a["commission_cents"], "payout_id": a["orphan_payout_id"]}
    return None


def registrar_reembolso(fatura: str, tipo: str, devolvido_cents: int, quando, ator: str = "sistema") -> dict | None:
    """Rótulo e pendência numa transação só. Decide sob as travas, com o estado de agora: quem chama
    (webhook ou script) não classifica nada. Reentrega e repetição sem fato novo não mudam nada."""
    with get_conn() as conn, conn.cursor() as cur:
        trava_fatura(cur, fatura)
        cur.execute("select id, status, payout_id, amount_cents from affiliate_commissions"
                    " where stripe_invoice_id = %s for update", (fatura,))
        c = cur.fetchone()
        _rotular(cur, "stripe", fatura, tipo, quando)
        cur.execute("select * from affiliate_refund_reviews where stripe_invoice_id = %s for update", (fatura,))
        a = cur.fetchone()
        if a is None or (a["tipo"] != "total" and devolvido_cents > a["refunded_cents"]):
            a = _aplicar(cur, fatura, a, c, tipo, devolvido_cents, quando, ator)
        conn.commit()
    return a


def promover(cur, comissao: dict) -> bool:
    """Reembolso que chegou antes do `invoice.paid`: a comissão recém-criada passa pela mesma máquina.
    Total nasce `reversed`, parcial nasce em revisão. Quem chama já tem a `trava_fatura`.
    False = não havia linha da fatura: quem decide é a conferência na Stripe (A5)."""
    cur.execute("select * from affiliate_refund_reviews where stripe_invoice_id = %s"
                " and status = 'no_commission' for update", (comissao["stripe_invoice_id"],))
    a = cur.fetchone()
    if a:
        _aplicar(cur, comissao["stripe_invoice_id"], a, comissao, a["tipo"], a["refunded_cents"],
                 a["refunded_at"], "sistema")
    return a is not None


def abertas_do_saque(cur, payout_id: int) -> list[dict]:
    """Pendências abertas das comissões do saque, travadas. Quem chama já travou saque e comissões."""
    cur.execute(
        """
        select r.* from affiliate_refund_reviews r
          left join affiliate_commissions c on c.id = r.commission_id
         where r.status = 'open' and (c.payout_id = %s or r.orphan_payout_id = %s)
         order by r.id for update of r
        """,
        (int(payout_id), int(payout_id)),
    )
    return cur.fetchall()


def faturas_do_saque(payout_id: int) -> list[dict]:
    """As faturas a conferir antes de pagar: as das comissões do saque e, no saque órfão, as que ficaram nas
    linhas gravadas na exclusão. Sem trava: o conjunto só encolhe, e quem decide é a trava de quem paga."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select c.id as commission_id, c.stripe_invoice_id as fatura from affiliate_commissions c where c.payout_id = %s
            union all
            select null, r.stripe_invoice_id from affiliate_refund_reviews r
             where r.orphan_payout_id = %s and r.commission_id is null
            """,
            (int(payout_id), int(payout_id)),
        )
        return cur.fetchall()


def saque_bloqueado(cur, payout_id: int) -> bool:
    """Comando próprio, depois da trava das comissões: em READ COMMITTED é ele que enxerga o commit
    do webhook que a trava esperou. Aviso entregue não libera."""
    cur.execute(f"select {SAQUE_BLOQUEADO_SQL.format(saque='%(saque)s')} as bloqueado", {"saque": int(payout_id)})
    return cur.fetchone()["bloqueado"]


def resolver_parcial(review_id: int, revisor: str, aprovado_cents: int, nota: str, visto_cents: int) -> bool:
    """Fecha a revisão parcial com o valor aprovado. False = pendência inexistente.

    `PendenciaMudou` se ela não está mais como o admin viu; valor fora da faixa é `ValueError`.
    Comissão paga não muda (D6): a diferença vira recuperação, atribuída e nunca somada, então
    reprocessar não duplica, e o que já foi encerrado não é cobrado de novo."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select r.commission_id, coalesce(c.payout_id, r.orphan_payout_id) as payout_id"
                    " from affiliate_refund_reviews r left join affiliate_commissions c on c.id = r.commission_id"
                    " where r.id = %s", (int(review_id),))
        lido = cur.fetchone()
        if lido is None:
            return False
        saque = None
        if lido["payout_id"]:
            cur.execute("select id, amount_cents from affiliate_payouts where id = %s and status = 'requested'"
                        " for update", (lido["payout_id"],))
            saque = cur.fetchone()
        cur.execute("select id, status, payout_id, amount_cents from affiliate_commissions where id = %s for update",
                    (lido["commission_id"],))
        c = cur.fetchone()
        cur.execute("select * from affiliate_refund_reviews where id = %s for update", (int(review_id),))
        r = cur.fetchone()
        c = c or _comissao_que_saiu(cur, r)  # saiu com o afiliado: paga, ou num saque órfão aberto
        if c is None or (r["status"] != "open" or r["tipo"] != "partial" or r["refunded_cents"] != visto_cents
                or c["payout_id"] != lido["payout_id"]):
            raise PendenciaMudou("A pendência mudou. Recarregue antes de decidir.")
        atual = c["amount_cents"]
        if not 0 <= aprovado_cents <= atual:
            raise ValueError("Valor aprovado fora da faixa.")
        diferenca, status, falta = atual - aprovado_cents, "resolved", None
        if c["status"] == "paid":
            falta = max(0, diferenca - r["recovery_closed_cents"]) or None
            status = "recovery" if falta else "resolved"
        elif diferenca:
            if c["id"] is None:  # saque órfão: o valor da comissão mora na linha
                cur.execute("update affiliate_refund_reviews set commission_cents = %s where id = %s",
                            (aprovado_cents, r["id"]))
            elif aprovado_cents == 0:  # equivale a estorno: `amount_cents` fica como estava
                cur.execute("update affiliate_commissions set status = 'reversed', payout_id = null where id = %s",
                            (c["id"],))
            else:
                cur.execute("update affiliate_commissions set amount_cents = %s where id = %s",
                            (aprovado_cents, c["id"]))
            if saque:
                _encolher_saque(cur, saque, diferenca)
        cur.execute("update affiliate_refund_reviews set status = %s, approved_cents = %s, recovery_cents = %s"
                    " where id = %s", (status, aprovado_cents, falta, r["id"]))
        registrar_evento(cur, r["id"], "partial_approved", revisor, antes=atual, depois=aprovado_cents,
                         saque=c["payout_id"], justificativa=nota)
        conn.commit()
    return True


def _encolher_saque(cur, saque: dict, diferenca: int) -> None:
    """Saque que ficaria sem valor vira `rejected`, com o `amount_cents` histórico."""
    novo = saque["amount_cents"] - diferenca
    if novo > 0:
        cur.execute("update affiliate_payouts set amount_cents = %s where id = %s", (novo, saque["id"]))
    else:
        cur.execute("update affiliate_payouts set status = 'rejected', note = 'Comissão ajustada na revisão.'"
                    " where id = %s", (saque["id"],))


def refunded_at_da_pendencia(review_id: int):
    """A data mínima do comprovante de recuperação."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select refunded_at from affiliate_refund_reviews where id = %s", (int(review_id),))
        linha = cur.fetchone()
    return linha and linha["refunded_at"]


def encerrar_recuperacao(review_id: int, resultado: str, ator: str, justificativa: str,
                         data=None, referencia: str | None = None) -> bool:
    """Admin encerra a recuperação como `recovered` ou `waived`. False = inexistente.

    Sem efeito em saldo: nada desconta de saque futuro. Repetir é `PendenciaMudou`, de propósito:
    dois admins com resultados diferentes não podem parecer ambos atendidos."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from affiliate_refund_reviews where id = %s for update", (int(review_id),))
        r = cur.fetchone()
        if r is None:
            return False
        if r["status"] != "recovery":
            raise PendenciaMudou("Esta recuperação já foi encerrada.")
        cur.execute(
            """
            update affiliate_refund_reviews
               set status = 'resolved', recovery_cents = null,
                   recovery_closed_cents = recovery_closed_cents + coalesce(recovery_cents, 0)
             where id = %s
            """,
            (r["id"],),
        )
        registrar_evento(cur, r["id"], f"recovery_{resultado}", ator, antes=r["recovery_cents"], em=data,
                         referencia=referencia, justificativa=justificativa)
        conn.commit()
    return True


def reivindicar(review_id: int | None = None) -> dict | None:
    """Claim da próxima notificação pendente, numa transação curta: o HTTP sai depois do commit, sem
    prender conexão nem a linha que o webhook precisa. Processo que morre: o prazo vence e a linha
    volta. Repetição possível, perda não. `skip locked` só no `select` interno."""
    filtro, args = ("and id = %s", (int(review_id),)) if review_id else ("", ())
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            update affiliate_refund_reviews r
               set notify_claimed_until = now() + interval '2 minutes'
             where r.id = (select id from affiliate_refund_reviews
                            where status in ('open', 'recovery') and notified_at is null
                              and (notify_claimed_until is null or notify_claimed_until < now())
                              {filtro}
                            order by id limit 1 for update skip locked)
            returning r.*,
                      (select c.status from affiliate_commissions c where c.id = r.commission_id) as commission_status,
                      coalesce((select c.payout_id from affiliate_commissions c where c.id = r.commission_id),
                               r.orphan_payout_id) as payout_id,
                      coalesce((select c.amount_cents from affiliate_commissions c where c.id = r.commission_id),
                               r.commission_cents, r.paid_cents) as commission_cents
            """,
            args,
        )
        linha = cur.fetchone()
        conn.commit()
    return linha


def marcar_avisada(review_id: int, refunded_cents: int, status: str) -> None:
    """Só depois de `_send` True, e só se a linha segue como foi avisada: fato novo (`refunded_cents`)
    ou mudança de estado (`status`, como o "Pagamento já realizado") durante o envio segue pendente."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update affiliate_refund_reviews set notified_at = now(), notify_claimed_until = null"
                    " where id = %s and refunded_cents = %s and status = %s",
                    (int(review_id), int(refunded_cents), status))
        conn.commit()
