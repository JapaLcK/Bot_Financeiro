"""core/services/pix_pago_sem_qr.py — aviso de Pix pago em linha sem QR entregue (#819).

Cobrança `draft`/`creating` paga é o sinal de que o `pixQrCode` do Asaas falhou e o
cliente pagou pela fatura que o Asaas mandou por e-mail. O dreno concede o acesso
assim mesmo; este aviso existe porque, depois do conserto, já não sobra evento
travado para denunciar o QR quebrado.
"""

from __future__ import annotations

import logging

from core.observability import log_system_event_sync
from core.services import admin_notify


def avisar(cobranca) -> None:
    # Aviso nunca atrasa o grant: levantar aqui, depois do commit da transição,
    # deixaria a 1ª passada em `paid` com 0 grants (a concessão sairia 60 s depois).
    try:
        log_system_event_sync(
            "warning", "pix_pago_sem_qr",
            "Pix pago em cobranca que nunca mostrou o QR (draft/creating).",
            source="pix", user_id=cobranca["user_id"],
            details={"charge_id": cobranca["id"], "estado": cobranca["status"]},
        )
        admin_notify.notify_pix_alerta(
            f"⚠️ **Pix pago sem QR entregue** cobrança `{cobranca['id']}` "
            f"(`{cobranca['status']}`) — acesso concedido; o `pixQrCode` do Asaas pode estar "
            "falhando."
        )
    except Exception as exc:  # noqa: BLE001 — só aviso; sem PII no log
        logging.getLogger(__name__).warning("pix_pago_sem_qr: aviso falhou (%s)", type(exc).__name__)
