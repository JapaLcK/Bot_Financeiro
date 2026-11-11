"""core/services/afiliado_reconciliacao.py — reembolso perdido de comissão já paga (#1501).

O `charge.refunded` ou o `charge.dispute.closed` de uma comissão paga pode se perder, e ninguém
reconfere uma comissão paga: a recuperação nunca abriria. Uma vez por dia (`CADENCIA_S`) e por até
`JANELA_DIAS` depois do pagamento, cada fatura paga é reconferida na Stripe pela regra da conferência
(`devolvido_na_stripe`) e, com reembolso, entra pela mesma máquina do webhook (`registrar_reembolso`).

Uma vez só, por construção: `registrar_reembolso` só age com fato novo e atribui a recuperação, nunca
soma. Repetir, reiniciar ou duas instâncias na mesma volta não duplicam valor, evento nem aviso; a
tabela `affiliate_paid_refund_checks` serve só para custo e cadência. A recuperação segue manual: nada
desconta de saque. Fora da janela, só o `scripts/backfill_afiliado_pagamentos.py`.
"""
import os
from datetime import datetime, timezone

from core.observability import log_system_event_sync
from core.services.afiliado_reembolso import SemConfirmacao, devolvido_na_stripe
from db.affiliate_reconciliacao import faturas_pagas_a_conferir, limpar_conferidas, marcar_conferida
from db.affiliate_reviews import registrar_reembolso

JANELA_DIAS = 540  # D1: o prazo mais longo de disputa de algumas bandeiras
CADENCIA_S = 86_400  # D2: diária
LOTE = 20  # por volta do worker (5 min): até 5.760 conferências por dia


def reconciliar_pagas(limite: int = LOTE) -> int:
    """Uma passada do `_afiliado_alerta_worker`. Devolve quantas faturas foram conferidas.

    HTTP fora de transação; cada escrita numa transação curta, depois dele. Erro da própria fatura
    (4xx: apagada, sem acesso) é dela: grava a consulta e segue, senão ela travaria a fila na frente.
    Erro que vale para todas (conexão, prazo de 8 s, chave, limite, 5xx) para a passada sem gravar:
    a mesma fatura abre a próxima volta."""
    import stripe  # noqa: PLC0415

    chave = os.getenv("STRIPE_SECRET_KEY")
    if not chave:
        return 0
    limpar_conferidas(JANELA_DIAS)
    conferidas = 0
    for fatura in faturas_pagas_a_conferir(limite, JANELA_DIAS, CADENCIA_S):
        try:
            tipo, devolvido, _ = devolvido_na_stripe(fatura, chave)
        except SemConfirmacao as exc:  # disputa aberta, `payment_record`, sem cobrança: amanhã de novo
            log_system_event_sync("info", "affiliate_paid_check_pending", "Reconciliacao de comissao paga adiada.",
                                  source="affiliates", details={"motivo": str(exc)})
        except Exception as exc:  # noqa: BLE001 — o tipo e o status bastam; o texto pode trazer a fatura
            da_fatura = isinstance(exc, (stripe.InvalidRequestError, stripe.PermissionError))
            log_system_event_sync("warning", "affiliate_paid_check_failed", "Reconciliacao de comissao paga falhou.",
                                  source="affiliates", details={"erro": type(exc).__name__, "da_fatura": da_fatura,
                                                                "http_status": getattr(exc, "http_status", None)})
            if not da_fatura:
                break
        else:
            if tipo:
                registrar_reembolso(fatura, tipo, devolvido, datetime.now(timezone.utc), ator="sistema")
        marcar_conferida(fatura)
        conferidas += 1
    return conferidas
