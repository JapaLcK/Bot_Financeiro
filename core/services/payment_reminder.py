"""
core/services/payment_reminder.py — o lembrete de pagamento do 6º dia de atraso.

Quem está com o cartão falhado há 6 dias recebe UM lembrete de que a cobrança
continua pendente. **Nada é bloqueado, pausado ou removido** — nem aqui nem em
nenhum outro lugar deste PR. A regra de acesso por inadimplência é assunto de
outro trabalho; até ela existir, nenhuma mensagem daqui pode prometer perda de
acesso (foi o erro que reprovou a versão anterior deste código, que mandava
"amanhã eu pauso" sem nada pausar depois).

Mora em módulo próprio, e não dentro do `engagement_scheduler`, por dois
motivos: o assunto é cobrança (não engajamento) e o scheduler já batia no teto
de 350 linhas do `tests/test_max_lines_python.py` (§0.5). Mesmo desenho do
`trial_downsell.py`: a regra vive aqui, o tick de 24 h só a chama.

Chamado por `engagement_scheduler.run_engagement_loop`, no mesmo molde
isolado do `_check_trial_ending` e do `_check_free_upgrade_nudge` — falha aqui
não afeta os outros e-mails.

FREIO PRÓPRIO, `PAYMENT_REMINDER_ENABLED`, **default DESLIGADO**: ver
`payment_reminder_enabled()` abaixo. Ele NÃO é a `DUNNING_BLOCK_ENABLED` de
antes com outro nome — aquela ligava o corte, e o lembrete pegava carona no
`if` dela. Quando o corte saiu, a carona sumiu com ele e este caminho ficou
sem interruptor por acidente; a env nova repõe a propriedade que o desenho já
tinha (mesma convenção de `PAYWALL_ENABLED` e `PLANS_V2_ENABLED`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from core.crypto import PiiAccessContext, decrypt_pii_optional
from core.observability import log_system_event_sync

logger = logging.getLogger(__name__)


def payment_reminder_enabled() -> bool:
    """Lembrete de pagamento ligado? Default DESLIGADO, lido dinamicamente do
    ambiente a cada tick pra ligar/desligar SEM REDEPLOY — mesmo formato de
    `plan_service.paywall_enabled`. Liga com `PAYMENT_REMINDER_ENABLED=true`.

    Sem cache de módulo de propósito: este caminho manda e-mail (e, quando o
    template existir, WhatsApp) para cliente real, e a única forma de parar um
    envio errado não pode ser um deploy.

    Mora aqui, junto do consumidor, e NÃO em `billing_dunning` — aquele arquivo
    é a casa da lista de status e está com ZERO import de propósito (ele entra
    no caminho do webhook); pôr um `os.getenv` lá estragaria isso por nada.
    """
    return (os.getenv("PAYMENT_REMINDER_ENABLED") or "").strip().lower() in (
        "1", "true", "yes", "on"
    )


async def check_payment_reminder() -> None:
    """Lembra quem está com o cartão em atraso há 6 dias de que a cobrança
    continua pendente.

    INERTE sem `PAYMENT_REMINDER_ENABLED`, e a guarda é a PRIMEIRA linha: nem a
    query do funil roda com a flag desligada.

    Janela no SQL (`db.dunning.list_payment_reminder_candidates`), abrindo no
    6º dia e com `PAYMENT_REMINDER_WINDOW_DAYS` de largura. Ela NÃO é de 1 dia,
    e a razão está na constante: o tick não é de 24 h exatos (o `sleep` de
    `run_engagement_loop` vem depois do trabalho) e restart/deploy atrasam
    muito mais — com 24 h, um tick perdido sumia com o único lembrete do ciclo.
    Quem impede o segundo lembrete é a dedupe abaixo, e a invariante que amarra
    a largura ao tamanho dela está em `core/services/billing_dunning`.

    Dedupe por `system_event_logs` (`recent_event_exists`,
    `PAYMENT_REMINDER_DEDUPE_DAYS`), e não por coluna
    como o `trial_downsell_sent_at`: inadimplência RECORRE, e uma coluna
    precisaria ser zerada quando o pagamento entra — virando uma quarta coisa
    para esquecer. Custo declarado: `system_event_logs` é purgável, então um
    "Limpar" no painel pode fazer o lembrete sair repetido. Mesmo trade-off que
    o `trial_ending_email_sent` já aceita.

    Pula allowlist e quem tem grant `pix`/`admin` vigente — nesses a cobrança
    do cartão não é o que sustenta o acesso, e lembrar de pagar algo que já
    está pago por outro caminho é ruído.

    E-MAIL é o caminho garantido; o WhatsApp é melhoria. Ver `_wa_lembrete`.
    """
    if not payment_reminder_enabled():
        return

    from core.observability import recent_event_exists
    from core.services import plan_service
    from core.services.billing_dunning import (
        DUNNING_GRACE_DAYS,
        PAYMENT_REMINDER_DEDUPE_DAYS,
    )
    from core.services.email_service import send_payment_reminder_email
    # Reuso, não cópia (§0.1): a minimização de PII em log já existe lá.
    from core.services.engagement_scheduler import _mask_email
    from db.dunning import list_payment_reminder_candidates

    loop = asyncio.get_event_loop()
    dashboard_url = os.getenv("DASHBOARD_URL", "https://pigbankai.com")

    try:
        rows = await loop.run_in_executor(
            None, list_payment_reminder_candidates, DUNNING_GRACE_DAYS)
    except Exception as exc:
        logger.error("[cobranca] Falha ao buscar candidatos: %s", exc, exc_info=True)
        return

    for row in rows:
        user_id = int(row["user_id"])
        if user_id in plan_service._ACCESS_ALLOWLIST:
            continue
        if row.get("email_enc"):
            email = decrypt_pii_optional(
                row["email_enc"],
                ctx=PiiAccessContext(
                    purpose="send_payment_reminder_email",
                    actor="system:engagement",
                    subject_user_id=user_id,
                    field="email",
                ),
            )
        else:
            email = row["email"]
        if not email:
            continue
        if await loop.run_in_executor(
            None, recent_event_exists, "payment_reminder_sent", user_id,
            PAYMENT_REMINDER_DEDUPE_DAYS,
        ):
            continue
        # Grant pix/admin vigente: o acesso desta conta não depende do cartão,
        # então o lembrete não tem assunto. Reusa o PREDICADO (`grant_vigente`)
        # em vez de uma segunda lista de sources para divergir.
        try:
            if await loop.run_in_executor(None, _pago_por_outro_caminho, user_id):
                continue
        except Exception as exc:
            logger.error("[cobranca] checagem de grant falhou user_id=%s: %s", user_id, exc)
            continue
        try:
            ok = await loop.run_in_executor(
                None, send_payment_reminder_email, email, dashboard_url)
            if not ok:
                continue
            logger.info("[cobranca] lembrete enviado → user_id=%s (%s)",
                        user_id, _mask_email(email))
            wa = await loop.run_in_executor(None, _wa_lembrete, user_id)
            log_system_event_sync(
                "info",
                "payment_reminder_sent",
                "Lembrete de pagamento (cartao em atraso) enviado.",
                source="engagement_scheduler",
                user_id=user_id,
                details={"email": True, "whatsapp": wa},
            )
        except Exception as exc:
            logger.error("[cobranca] falha enviando user_id=%s: %s", user_id, exc)


def _pago_por_outro_caminho(user_id: int) -> bool:
    """True se um grant `pix` ou `admin` vigente sustenta o acesso desta conta
    independentemente do cartão. `legacy` NÃO conta — ele é a reconstrução do
    MESMO acesso de cartão feita pelo backfill de grants."""
    from core.services.billing_access import grant_vigente
    from db.plan_grants import list_grants
    return grant_vigente(list_grants(user_id), datetime.now(timezone.utc),
                         sources=("pix", "admin"))


def _wa_lembrete(user_id: int) -> bool:
    """Manda o lembrete por WhatsApp. True se saiu, False se não.

    Mensagem proativa fora da janela de 24 h exige TEMPLATE APROVADO NA META,
    e o nome do template vive em `WA_TEMPLATE_PAYMENT_REMINDER`, VAZIO por
    padrão — sem a env este caminho é dormente e nem importa o `wa_client`
    (mesmo desenho do `open_finance_proactive._template_cfg`). O texto do
    template é escrito na Meta, fora deste repositório: quem o aprovar tem de
    manter a mesma regra da copy do e-mail e NÃO prometer perda de acesso.

    Nunca levanta: o e-mail já saiu quando isto roda, e derrubar o tick por
    causa de um template não aprovado transformaria a melhoria em regressão.
    """
    nome = (os.getenv("WA_TEMPLATE_PAYMENT_REMINDER") or "").strip()
    if not nome:
        return False
    try:
        from adapters.whatsapp.wa_app import _dedupe_whatsapp_targets
        from adapters.whatsapp.wa_client import send_template
        from db import list_identities_by_user
        idioma = (os.getenv("WA_TEMPLATE_PAYMENT_REMINDER_LANGUAGE") or "pt_BR").strip()
        enviado = False
        for to in _dedupe_whatsapp_targets(list_identities_by_user(user_id)):
            send_template(to, nome, language_code=idioma)
            enviado = True
        return enviado
    except Exception as exc:
        logger.warning("[cobranca] WhatsApp não enviado user_id=%s: %s", user_id, exc)
        return False
