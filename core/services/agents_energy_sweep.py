"""
core/services/agents_energy_sweep.py — pausa agentes acima do orçamento de
energia do plano vigente.

O `agents_energy_budget` só era cobrado na ATIVAÇÃO. Quem descia de plano
mantinha tudo ligado: um Pro (orçamento 14, cabem os 7 agentes) que cai para o
Plus (orçamento 4) seguia com os 7 disparando.

A queda para um tier SEM energia (Grátis e Essencial, orçamento 0) já era
tratada — os runners filtram por `agent_kind_allowed`, que é
`agents_energy_budget(uid) > 0`. O buraco era só de pago para pago, hoje um
caso só na escada: Pro (14) → Plus (4).

Pausar, e não apagar: `pause_agent` deixa a linha e o histórico de disparos de
pé, então voltar ao plano maior é reativar num clique. Mesma decisão de produto
da conexão de Open Finance pausada.

CAI O MAIS RECENTE, igual à varredura de OF: ordena por `id` crescente e pausa
do fim para o começo até caber. Determinístico e explicável ("os que você
ativou por último saíram").
"""
from __future__ import annotations

from collections import defaultdict

from core.services import plan_service
from core.services.plan_limits import agent_energy_cost
from db import list_users_with_active_agents, pause_agent


def enforce_agents_energy_budget() -> dict:
    """Um tick da varredura. Devolve o resumo, no formato das outras."""
    if not plan_service.plans_v2_enabled():
        return {"ok": True, "disabled": True, "checked_users": 0, "paused": 0, "errors": 0}

    by_user: dict[int, list[dict]] = defaultdict(list)
    for row in list_users_with_active_agents():
        by_user[int(row["user_id"])].append(row)

    checked = 0
    paused = 0
    errors = 0

    for user_id, ativos in by_user.items():
        # Testers do beta usam qualquer agente independente do tier (é o que o
        # `agent_kind_allowed` já faz nos runners). Sem esta saída, a varredura
        # desligaria justamente os agentes de quem está testando o beta.
        try:
            if plan_service.agents_beta_tester(user_id):
                continue
            budget = plan_service.agents_energy_budget(user_id)
        except Exception:
            errors += 1
            continue

        checked += 1
        usada = sum(agent_energy_cost(a["kind"]) for a in ativos)
        if usada <= budget:
            continue

        # Do mais novo para o mais velho, até caber.
        for a in sorted(ativos, key=lambda r: int(r["agent_id"]), reverse=True):
            if usada <= budget:
                break
            try:
                if pause_agent(user_id, a["kind"]):
                    usada -= agent_energy_cost(a["kind"])
                    paused += 1
            except Exception:
                errors += 1
                break                      # não insiste no mesmo usuário neste tick

    return {"ok": True, "disabled": False, "checked_users": checked,
            "paused": paused, "errors": errors}
