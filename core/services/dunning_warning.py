"""
core/services/dunning_warning.py — o aviso da VÉSPERA do corte por inadimplência.

Mora em módulo próprio, e não dentro do `engagement_scheduler`, por dois
motivos: o assunto é cobrança (não engajamento) e o scheduler já batia no teto
de 350 linhas do `tests/test_max_lines_python.py` (§0.5). Mesmo desenho do
`trial_downsell.py`: a regra vive aqui, o tick de 24 h só a chama.

Chamado por `engagement_scheduler.run_engagement_loop`, no mesmo molde
isolado do `_check_trial_ending` e do `_check_free_upgrade_nudge` — falha aqui
não afeta os outros e-mails.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from core.crypto import PiiAccessContext, decrypt_pii_optional
from core.observability import log_system_event_sync

logger = logging.getLogger(__name__)


async def check_dunning_warning() -> None:
    """Avisa quem é cortado AMANHÃ por cartão em atraso (7 dias de carência).

    INERTE quando `dunning_block_enabled()` é False: avisar "amanhã você é
    cortado" sem o corte ligado é mentir para o usuário.

    Janela de 1 dia no SQL (`db.plans.list_dunning_warning_candidates`), no
    desenho do `_check_trial_ending`: o tick roda a cada 24 h, então cada conta
    entra na janela uma vez por ciclo de inadimplência.

    Dedupe por `system_event_logs` (`recent_event_exists`), e não por coluna
    como o `trial_downsell_sent_at`: inadimplência RECORRE, e uma coluna
    precisaria ser zerada no desbloqueio — virando uma quarta coisa para
    esquecer. Custo declarado: `system_event_logs` é purgável, então um "Limpar"
    no painel pode fazer o aviso sair repetido. Mesmo trade-off que o
    `trial_ending_email_sent` já aceita.

    Pula allowlist e quem tem grant `pix`/`admin` vigente — senão avisa de um
    corte que não vai acontecer (é a MESMA guarda 5 do gate).

    E-MAIL é o caminho garantido; o WhatsApp é melhoria. Ver `_wa_dunning_warning`.
    """
    from core.observability import recent_event_exists
    from core.services import plan_service
    from core.services.billing_dunning import (
        DUNNING_GRACE_DAYS,
        dunning_block_enabled,
    )

    if not dunning_block_enabled():
        return

    from core.services.email_service import send_dunning_warning_email
    # Reuso, não cópia (§0.1): a minimização de PII em log já existe lá.
    from core.services.engagement_scheduler import _mask_email
    from db.plans import list_dunning_warning_candidates

    loop = asyncio.get_event_loop()
    dashboard_url = os.getenv("DASHBOARD_URL", "https://pigbankai.com")

    try:
        rows = await loop.run_in_executor(
            None, list_dunning_warning_candidates, DUNNING_GRACE_DAYS)
    except Exception as exc:
        logger.error("[dunning] Falha ao buscar candidatos: %s", exc, exc_info=True)
        return

    for row in rows:
        user_id = int(row["user_id"])
        if user_id in plan_service._ACCESS_ALLOWLIST:
            continue
        if row.get("email_enc"):
            email = decrypt_pii_optional(
                row["email_enc"],
                ctx=PiiAccessContext(
                    purpose="send_dunning_warning_email",
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
            None, recent_event_exists, "dunning_warning_sent", user_id, 6.0
        ):
            continue
        # Grant pix/admin vigente resgata: a conta não vai ser bloqueada, então
        # não há véspera para avisar. É a guarda 5 do gate, reusada pelo
        # PREDICADO (`grant_vigente`) e não por `bloqueado_por_inadimplencia`:
        # aquela função só devolve True DEPOIS da carência, e o aviso é
        # justamente de ANTES — chamá-la aqui zeraria o aviso sempre. Medido:
        # com ela, o caso de 6,5 dias não mandava e-mail nenhum.
        try:
            if await loop.run_in_executor(None, _resgatado_por_grant, user_id):
                continue
        except Exception as exc:
            logger.error("[dunning] checagem de grant falhou user_id=%s: %s", user_id, exc)
            continue
        try:
            ok = await loop.run_in_executor(
                None, send_dunning_warning_email, email, dashboard_url)
            if not ok:
                continue
            logger.info("[dunning] e-mail enviado → user_id=%s (%s)", user_id, _mask_email(email))
            wa = await loop.run_in_executor(None, _wa_dunning_warning, user_id)
            log_system_event_sync(
                "info",
                "dunning_warning_sent",
                "Aviso da vespera do corte por inadimplencia enviado.",
                source="engagement_scheduler",
                user_id=user_id,
                details={"email": True, "whatsapp": wa},
            )
        except Exception as exc:
            logger.error("[dunning] falha enviando user_id=%s: %s", user_id, exc)


def _resgatado_por_grant(user_id: int) -> bool:
    """True se um grant `pix` ou `admin` vigente impede o bloqueio desta conta.

    Mesmo conjunto ('pix', 'admin') da guarda 5 de
    `billing_dunning.bloqueado_por_inadimplencia` — `legacy` NÃO resgata.
    """
    from core.services.billing_access import grant_vigente
    from db.plan_grants import list_grants
    return grant_vigente(list_grants(user_id), datetime.now(timezone.utc),
                         sources=("pix", "admin"))


def _wa_dunning_warning(user_id: int) -> bool:
    """Manda o aviso por WhatsApp. True se saiu, False se não.

    Mensagem proativa fora da janela de 24 h exige TEMPLATE APROVADO NA META,
    e o nome do template vive em `WA_TEMPLATE_DUNNING_WARNING`, VAZIO por
    padrão — sem a env este caminho é dormente e nem importa o `wa_client`
    (mesmo desenho do `open_finance_proactive._template_cfg`).

    Nunca levanta: o e-mail já saiu quando isto roda, e derrubar o tick por
    causa de um template não aprovado transformaria a melhoria em regressão.
    """
    nome = (os.getenv("WA_TEMPLATE_DUNNING_WARNING") or "").strip()
    if not nome:
        return False
    try:
        from adapters.whatsapp.wa_app import _dedupe_whatsapp_targets
        from adapters.whatsapp.wa_client import send_template
        from db import list_identities_by_user
        idioma = (os.getenv("WA_TEMPLATE_DUNNING_WARNING_LANGUAGE") or "pt_BR").strip()
        enviado = False
        for to in _dedupe_whatsapp_targets(list_identities_by_user(user_id)):
            send_template(to, nome, language_code=idioma)
            enviado = True
        return enviado
    except Exception as exc:
        logger.warning("[dunning] WhatsApp não enviado user_id=%s: %s", user_id, exc)
        return False
