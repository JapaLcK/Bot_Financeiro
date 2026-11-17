"""
db/affiliate_eventos.py — trilha de auditoria do programa de afiliados: `affiliate_refund_review_events`.

Só inserção, nunca purgada, e é a fonte do "Histórico" do admin. Âncora: a pendência (`review_id`), o saque
ou a comissão (decisão na conferência, #1538). Nada do indicado: justificativa e referência moram só aqui,
nunca em log. `codigo` é o do afiliado no ato, para o evento seguir legível depois da exclusão (D8).
"""
from psycopg.types.json import Jsonb


def registrar_evento(cur, review_id: int | None, action: str, actor: str, *, antes=None, depois=None,
                     saque=None, em=None, referencia=None, justificativa=None, comissao=None, codigo=None,
                     estado_antes: dict | None = None, estado_depois: dict | None = None) -> None:
    cur.execute(
        """
        insert into affiliate_refund_review_events(review_id, action, actor, before_cents, after_cents,
                                                   payout_id, event_on, receipt_ref, justification,
                                                   commission_id, affiliate_code, state_before, state_after)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (review_id, action, actor, antes, depois, saque, em, referencia, justificativa, comissao, codigo,
         None if estado_antes is None else Jsonb(estado_antes),
         None if estado_depois is None else Jsonb(estado_depois)),
    )
