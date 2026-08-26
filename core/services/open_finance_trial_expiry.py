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


def enforce_of_bank_limits() -> dict:
    """Um tick da varredura: pausa conexões ACIMA do teto do plano vigente.

    Antes isto só tratava o trial vencido (`tier == "free"`). Hoje trata toda
    queda de plano, e o caso antigo virou o caso `of_banks_max == 0` — quem cai
    pro Grátis perde todas, quem cai de Pro (5) para Essencial (1) perde 4. Sem
    isto, o teto só barrava conexão NOVA: quem tinha 5 bancos e descia para o
    Essencial seguia com os 5 sincronizando, ocupando slot pago na Pluggy.

    CAI O MAIS RECENTE. Ordena por `id` crescente e pausa do fim para o começo:
    o banco principal costuma ser o primeiro conectado, e o que veio depois foi
    o que o plano maior habilitou. Determinístico e explicável ao usuário.

    Best-effort por conexão: se o delete na Pluggy falhar, a conexão NÃO é
    pausada (fica pro próximo tick — nunca marca PAUSED com o item ainda vivo
    lá, senão o slot pago vaza pra sempre).

    ponytail: o arquivo e o teste ainda se chamam `*_trial_expiry`. Renomear
    custa mexer em ~8 referências e engorda o diff de revisão; se outro caso de
    queda de plano entrar aqui, aí vale `git mv` para `plan_downgrade.py`.
    """
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
            limit = plan_service.get_user_limits(user_id).get("of_banks_max")
        except Exception:
            errors += 1
            continue
        if limit is None:
            continue                      # ilimitado (Premium futuro)

        # `[limit:]` já cobre o caso do Grátis: com teto 0, sobra a lista toda.
        excedentes = sorted(conns, key=lambda c: int(c["id"]))[int(limit):]
        for conn in excedentes:
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
