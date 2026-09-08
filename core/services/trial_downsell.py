"""
Downsell do fim do trial (escada v2).

Trial de 15 dias venceu sem virar assinatura → um único e-mail oferecendo o
Essencial (R$ 9,90) antes do churn. Roda junto do tick de expiração de trial
(mesmo loop do of_trial_expiry). Inerte com PLANS_V2_ENABLED off.

Regras:
  - 1 e-mail por conta, na vida (trial_downsell_sent_at).
  - Janela de 7 dias pós-vencimento — flag ligado meses depois não dispara
    e-mail atrasado pra trial antigo.
  - Respeita engagement_opt_out (no SQL) e a allowlist de admin/teste.
  - Marca como enviado ANTES do envio custar caro? Não — marca após tentar;
    send_email nunca lança (Resend off = False) e aí NÃO marca, fica pro
    próximo tick.
"""

from __future__ import annotations

import os
import sys

from core.services import plan_service


def send_trial_downsell_emails() -> dict:
    if not plan_service.plans_v2_enabled():
        return {"ok": True, "disabled": True, "checked": 0, "sent": 0}

    from db import get_auth_user
    from db.plans import list_trial_downsell_candidates, mark_trial_downsell_sent
    from core.services.email_service import send_trial_downsell_email

    dashboard_url = os.getenv("DASHBOARD_URL", "https://pigbankai.com")
    checked = 0
    sent = 0
    for user_id in list_trial_downsell_candidates():
        if user_id in plan_service._ACCESS_ALLOWLIST:
            continue
        checked += 1
        try:
            # Só quem ficou de fato no Grátis (assinou = não é downsell).
            if plan_service.get_plan_tier(user_id) != "free":
                mark_trial_downsell_sent(user_id)  # nunca mais considerar
                continue
            email = (get_auth_user(user_id) or {}).get("email")
            if not email:
                mark_trial_downsell_sent(user_id)
                continue
            if send_trial_downsell_email(email, dashboard_url):
                mark_trial_downsell_sent(user_id)
                sent += 1
        except Exception as exc:
            print(f"[trial_downsell] user={user_id}: {exc}", file=sys.stderr)

    return {"ok": True, "disabled": False, "checked": checked, "sent": sent}
