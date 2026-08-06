"""Agentes do Piggy — API do dashboard (prateleira, ativação, feed).

Gate de plano: Free ativa 1 agente; Plus/Pro (v2) ou Pro (v1) ativam todos.
Espelha o padrão dos outros routers: shared.authorize_dashboard_access por
atributo de módulo + db lazy + asyncio.to_thread.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from frontend.routes import shared

router = APIRouter()

# Catálogo da prateleira. "disponivel": False = card "Em breve" (aparece
# desabilitado — gate visível, nunca escondido). A arte (SVG) vive no front.
AGENT_CATALOG: list[dict] = [
    {
        "kind": "xerife", "nome": "Xerife", "emoji": "🤠",
        "freq": "Diário · a cada sync",
        "desc": "Alerta quando um gasto foge do seu padrão ou uma categoria estoura o limite",
        "disponivel": True,
    },
    {
        "kind": "reporter", "nome": "Repórter", "emoji": "🎤",
        "freq": "Mensal",
        "desc": "Traz a manchete do seu mês: o que entrou, o que saiu e quanto sobrou",
        "disponivel": True,
    },
    {
        "kind": "carteiro", "nome": "Carteiro", "emoji": "📬",
        "freq": "Contínuo",
        "desc": "Não deixa boleto vencer: avisa antes, direto no seu feed",
        "disponivel": True,
    },
    {
        "kind": "detetive", "nome": "Detetive", "emoji": "🔍",
        "freq": "Semanal",
        "desc": "Fareja cobranças repetidas que parecem assinatura esquecida",
        "disponivel": False,
    },
    {
        "kind": "cofre", "nome": "Cofre", "emoji": "🎯",
        "freq": "Dia do salário",
        "desc": "Acompanha suas caixinhas e sugere o aporte que adianta a meta",
        "disponivel": False,
    },
    {
        "kind": "barao", "nome": "Barão", "emoji": "🎩",
        "freq": "Diário · Open Finance",
        "desc": "Fareja dinheiro parado rendendo nada e acompanha seus investimentos",
        "disponivel": False,
    },
    {
        "kind": "aviador", "nome": "Aviador", "emoji": "✈️",
        "freq": "Dados externos",
        "desc": "Caça passagem em promoção pros destinos que você marcar",
        "disponivel": False,
    },
]


def _plan_allows_multiple(user_id: int) -> bool:
    from core.services.plan_service import is_pro
    # plans_v2_enabled/require_min_tier só existem quando a escada v2 está no
    # código (feature ainda atrás de flag, não shipada aqui). Sem ela, o gate
    # binário (is_pro) É o comportamento certo — e é exatamente o que
    # require_min_tier faz com v2 off. Import defensivo pra a view não morrer
    # com ImportError enquanto o plans-v2 não pousa.
    try:
        from core.services.plan_service import plans_v2_enabled, require_min_tier
    except ImportError:
        return is_pro(user_id)
    if plans_v2_enabled():
        # Escada v2: agentes ilimitados são corte do Plus (ver _FEATURE_MIN_TIER_V2).
        return require_min_tier(user_id, "plus")
    return is_pro(user_id)


def _v2_agents_gate(user_id: int, kind: str | None = None) -> tuple[bool, bool]:
    """(v2_ativo, permitido). Com a escada ligada, o direito é por tier e por
    kind (fonte única: AGENT_KIND_MIN_TIER em plan_limits): Grátis/Essencial =
    nenhum agente; Plus = detectores da Fase A; Pro+ = todos. Com v2 off
    devolve (False, True) — gate legado decide."""
    try:
        from core.services.plan_service import (
            plans_v2_enabled, require_min_tier, agent_kind_allowed,
        )
    except ImportError:
        return False, True
    if not plans_v2_enabled():
        return False, True
    if kind is None:
        return True, require_min_tier(user_id, "plus")
    return True, agent_kind_allowed(user_id, kind)


class ActivateBody(BaseModel):
    config: dict | None = None


@router.get("/agents/{user_id}")
async def agents_shelf_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    from db import agents_summary, list_agents

    mine, summary, multi = await asyncio.gather(
        asyncio.to_thread(list_agents, user_id),
        asyncio.to_thread(agents_summary, user_id),
        asyncio.to_thread(_plan_allows_multiple, user_id),
    )
    by_kind = {a["kind"]: a for a in mine}
    catalog = []
    for card in AGENT_CATALOG:
        mine_a = by_kind.get(card["kind"])
        catalog.append({
            **card,
            "status": (mine_a or {}).get("status"),
            "config": (mine_a or {}).get("config") or {},
            "fired_30d": int((mine_a or {}).get("fired_30d") or 0),
            "saved_365d": float((mine_a or {}).get("saved_365d") or 0),
        })
    # v2: Grátis/Essencial veem a prateleira mas não ativam (gates visíveis →
    # descoberta/conversão); can_activate deixa o front desenhar o cadeado.
    v2_on, v2_allowed = await asyncio.to_thread(_v2_agents_gate, user_id, None)
    can_activate = v2_allowed if v2_on else True
    return {"ok": True, "summary": summary, "catalog": catalog,
            "multi_allowed": multi, "can_activate": can_activate}


@router.post("/agents/{user_id}/{kind}/activate")
async def agents_activate_route(request: Request, user_id: int, kind: str, body: ActivateBody | None = None):
    shared.authorize_dashboard_access(request, user_id)
    from db import AGENT_KINDS, activate_agent, get_agent

    if kind not in AGENT_KINDS:
        available = {c["kind"] for c in AGENT_CATALOG if c["disponivel"]}
        detail = "Esse agente ainda não está disponível." if kind in {c["kind"] for c in AGENT_CATALOG} - available \
            else "Agente desconhecido."
        raise HTTPException(status_code=400, detail=detail)

    # Escada v2: gate por tier e por kind ANTES de qualquer coisa — Grátis e
    # Essencial não ativam agente nenhum (0 na escada); Plus ativa os 3 da
    # Fase A; kinds avançados (Fase B) exigirão Pro.
    v2_on, v2_allowed = await asyncio.to_thread(_v2_agents_gate, user_id, kind)
    if v2_on and not v2_allowed:
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "agents"})

    existing = await asyncio.to_thread(get_agent, user_id, kind)
    already_active = bool(existing and existing["status"] == "active")
    # Reativar o MESMO kind não adiciona agente → não passa pelo teto. Só uma
    # ativação de kind novo precisa do direito a múltiplos. O teto é aplicado
    # DENTRO de activate_agent (mesma transação) pra fechar o TOCTOU.
    # No v2 o teto é por kind/tier (checado acima) — múltiplos liberados.
    if v2_on:
        allow_multiple = True
    else:
        allow_multiple = already_active or await asyncio.to_thread(_plan_allows_multiple, user_id)

    config = (body.config if body else None) or {}
    agent = await asyncio.to_thread(activate_agent, user_id, kind, config, allow_multiple)
    if agent is None:
        # Mesmo payload de 403 do resto do app → frontend abre o modal de upgrade.
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "agents"})
    return {"ok": True, "agent": {
        "kind": agent["kind"], "status": agent["status"], "config": agent["config"],
    }}


@router.post("/agents/{user_id}/{kind}/pause")
async def agents_pause_route(request: Request, user_id: int, kind: str):
    shared.authorize_dashboard_access(request, user_id)
    from db import pause_agent

    changed = await asyncio.to_thread(pause_agent, user_id, kind)
    if not changed:
        raise HTTPException(status_code=404, detail="Agente não encontrado.")
    return {"ok": True}


@router.get("/agents/{user_id}/feed")
async def agents_feed_route(request: Request, user_id: int, limit: int = 20):
    shared.authorize_dashboard_access(request, user_id)
    from db import list_agent_events

    limit = max(1, min(int(limit), 50))
    events = await asyncio.to_thread(list_agent_events, user_id, limit)
    return {"ok": True, "events": events}


@router.post("/agents/{user_id}/feed/seen")
async def agents_feed_seen_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    from db import mark_agent_events_seen

    n = await asyncio.to_thread(mark_agent_events_seen, user_id)
    return {"ok": True, "marked": n}
