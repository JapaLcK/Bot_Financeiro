"""Varredura de trial vencido no Open Finance (planos v2).

O contrato Pluggy cobra R$ 2,50 por conexão ATIVA/mês (teto de 500) — banco
conectado de trial que morreu sem virar assinatura ocupa slot pago à toa. Esta
varredura acha usuários no tier free (trial expirado, sem assinatura — via
plan_service.get_plan_tier) que ainda têm conexão Pluggy ativa e:

  1. deleta o item na Pluggy (libera o slot pago; idempotente — 404 conta como ok);
  2. marca a conexão como PAUSED localmente.

Decisão de produto: os dados já importados FICAM (launches, faturas, saldo
histórico continuam visíveis) — só o sync para; "reative seu banco" vira CTA de
upgrade. Por isso NÃO se usa disconnect_open_finance_connection (que reverte tudo).

Inerte com PLANS_V2_ENABLED off — o flag é relido a cada tick (liga/desliga sem
redeploy, como o resto do plan_service). Trabalho bloqueante (httpx + DB): o loop
do lifespan chama via asyncio.to_thread.
"""

from __future__ import annotations

from collections import defaultdict

from core.services import plan_service
from core.services.pluggy import create_pluggy_api_key, delete_pluggy_item
from db import list_pluggy_connections_for_trial_sweep, pause_open_finance_connection


def pause_expired_trial_connections() -> dict:
    """Um tick da varredura. Best-effort por conexão: se o delete na Pluggy falhar,
    a conexão NÃO é pausada (fica pro próximo tick — nunca marca PAUSED com o item
    ainda vivo lá, senão o slot pago vaza pra sempre)."""
    if not plan_service.plans_v2_enabled():
        return {"ok": True, "disabled": True, "checked_users": 0, "paused": 0, "errors": 0}

    by_user: dict[int, list[dict]] = defaultdict(list)
    for conn in list_pluggy_connections_for_trial_sweep():
        by_user[int(conn["user_id"])].append(conn)

    checked = 0
    paused = 0
    errors = 0
    api_key: str | None = None

    for user_id, conns in by_user.items():
        # Contas de admin/teste ficam de fora (é onde o Lucas valida o sandbox).
        if user_id in plan_service._ACCESS_ALLOWLIST:
            continue
        checked += 1
        try:
            tier = plan_service.get_plan_tier(user_id)
        except Exception:
            errors += 1
            continue
        if tier != "free":
            continue

        for conn in conns:
            try:
                if api_key is None:
                    api_key = create_pluggy_api_key()
                delete_pluggy_item(conn["provider_item_id"], api_key)
            except Exception:
                errors += 1
                continue
            pause_open_finance_connection(int(conn["id"]))
            paused += 1

    return {"ok": True, "disabled": False, "checked_users": checked, "paused": paused, "errors": errors}
