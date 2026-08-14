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
        "freq": "Contínuo · a cada sync",
        "desc": "Fareja cobranças repetidas que parecem assinatura esquecida",
        "disponivel": True,
    },
    {
        # Renomeado (era "Cofre" → "Guardião do Tesouro" → "Banqueiro"); o kind interno segue "cofre".
        "kind": "cofre", "nome": "Banqueiro", "emoji": "🎯",
        "freq": "Contínuo · a cada sync",
        "desc": "Detecta aportes na sua caixinha e acompanha quanto falta pra meta",
        "disponivel": True,
    },
    {
        "kind": "barao", "nome": "Barão", "emoji": "🎩",
        "freq": "Mensal · Open Finance",
        "desc": "Fareja dinheiro parado rendendo nada e mostra quanto renderia no CDI",
        "disponivel": True,
    },
    {
        "kind": "faria_limer", "nome": "Faria Limer", "emoji": "📈",
        "freq": "Mensal · Open Finance",
        "desc": "Acompanha sua renda variável (ações e FIIs): o retrato do mês e a concentração da carteira — só fatos, nunca recomendação",
        "disponivel": True,
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
    """(v2_ativo, tem_direito_a_agentes). Modelo de energia: todos os agentes
    ficam liberados em Plus e Pro (sem trava por kind); Grátis/Essencial não têm
    energia (orçamento 0) → nenhum agente. Este gate só responde "o tier permite
    agentes?"; QUANTOS cabem é a energia, checada na ativação. Com v2 off devolve
    (False, True) — gate legado decide."""
    try:
        from core.services.plan_service import plans_v2_enabled, agents_energy_budget
    except ImportError:
        return False, True
    if not plans_v2_enabled():
        return False, True
    return True, agents_energy_budget(user_id) > 0


class ActivateBody(BaseModel):
    config: dict | None = None


def _require_agents_beta(user_id: int) -> None:
    """Gate beta: fora do allowlist, a feature de Agentes 'não existe' (404).
    A nav do dashboard também some via /auth/me. Abrir geral = AGENTS_UI_ENABLED=1."""
    from core.services.plan_service import agents_ui_enabled
    if not agents_ui_enabled(user_id):
        raise HTTPException(status_code=404, detail="Feature indisponível.")


@router.get("/agents/{user_id}")
async def agents_shelf_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    _require_agents_beta(user_id)
    from db import agents_summary, list_agents
    from core.services.plan_limits import agent_energy_cost
    from core.services.plan_service import agents_energy_budget, plans_v2_enabled

    # Modelo de energia SÓ vale com a escada v2 ligada. Com v2 off (freio de
    # emergência), o gate legado decide (Free 1 agente / pago todos) e a UI não
    # deve mostrar medidor nem travar por energia — senão trava botões que o
    # backend legado aceitaria.
    energy_enabled = plans_v2_enabled()
    mine, summary, multi = await asyncio.gather(
        asyncio.to_thread(list_agents, user_id),
        asyncio.to_thread(agents_summary, user_id),
        asyncio.to_thread(_plan_allows_multiple, user_id),
    )
    energy_budget = await asyncio.to_thread(agents_energy_budget, user_id) if energy_enabled else 0
    by_kind = {a["kind"]: a for a in mine}
    catalog = []
    energy_used = 0
    for card in AGENT_CATALOG:
        mine_a = by_kind.get(card["kind"])
        status = (mine_a or {}).get("status")
        cost = agent_energy_cost(card["kind"]) if (energy_enabled and card.get("disponivel")) else 0
        if status == "active":
            energy_used += cost
        catalog.append({
            **card,
            "status": status,
            "config": (mine_a or {}).get("config") or {},
            "fired_30d": int((mine_a or {}).get("fired_30d") or 0),
            "saved_365d": float((mine_a or {}).get("saved_365d") or 0),
            "energy_cost": cost,
        })
    # v2: Grátis/Essencial veem a prateleira mas não ativam (orçamento 0 → cadeado
    # que abre o upgrade). Com v2 off, o gate legado libera (can_activate True).
    can_activate = (energy_budget > 0) if energy_enabled else True
    return {"ok": True, "summary": summary, "catalog": catalog,
            "multi_allowed": multi, "can_activate": can_activate,
            "energy_enabled": energy_enabled,
            "energy_budget": int(energy_budget), "energy_used": int(energy_used)}


@router.post("/agents/{user_id}/{kind}/activate")
async def agents_activate_route(request: Request, user_id: int, kind: str, body: ActivateBody | None = None):
    shared.authorize_dashboard_access(request, user_id)
    _require_agents_beta(user_id)
    from db import AGENT_KINDS, activate_agent, get_agent

    if kind not in AGENT_KINDS:
        available = {c["kind"] for c in AGENT_CATALOG if c["disponivel"]}
        detail = "Esse agente ainda não está disponível." if kind in {c["kind"] for c in AGENT_CATALOG} - available \
            else "Agente desconhecido."
        raise HTTPException(status_code=400, detail=detail)

    # Modelo de energia: Grátis/Essencial não têm energia (orçamento 0) → nenhum
    # agente. Plus/Pro têm todos os agentes liberados; QUANTOS cabem é a energia,
    # enforçada dentro de activate_agent (mesma transação, fecha o TOCTOU).
    v2_on, v2_allowed = await asyncio.to_thread(_v2_agents_gate, user_id, kind)
    if v2_on and not v2_allowed:
        # Tier sem energia p/ agentes (Grátis/Essencial) → modal de upgrade.
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "agents"})

    config = (body.config if body else None) or {}
    if v2_on:
        from core.services.plan_limits import AGENT_ENERGY_COST, agent_energy_cost
        from core.services.plan_service import agents_energy_budget
        budget = await asyncio.to_thread(agents_energy_budget, user_id)
        agent = await asyncio.to_thread(
            activate_agent, user_id, kind, config, True, budget, AGENT_ENERGY_COST,
        )
        if agent is None:
            # Tem plano, mas o orçamento não comporta mais esse agente → o front
            # mostra "sem energia" (pausar um agente ou subir pro Pro), não upgrade.
            raise HTTPException(status_code=403, detail={
                "error": "no_energy", "feature": "agents",
                "cost": agent_energy_cost(kind), "budget": int(budget),
            })
    else:
        existing = await asyncio.to_thread(get_agent, user_id, kind)
        already_active = bool(existing and existing["status"] == "active")
        # Reativar o MESMO kind não adiciona agente → não passa pelo teto legado.
        allow_multiple = already_active or await asyncio.to_thread(_plan_allows_multiple, user_id)
        agent = await asyncio.to_thread(activate_agent, user_id, kind, config, allow_multiple)
        if agent is None:
            raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "agents"})
    return {"ok": True, "agent": {
        "kind": agent["kind"], "status": agent["status"], "config": agent["config"],
    }}


@router.post("/agents/{user_id}/{kind}/pause")
async def agents_pause_route(request: Request, user_id: int, kind: str):
    shared.authorize_dashboard_access(request, user_id)
    _require_agents_beta(user_id)
    from db import pause_agent

    changed = await asyncio.to_thread(pause_agent, user_id, kind)
    if not changed:
        raise HTTPException(status_code=404, detail="Agente não encontrado.")
    return {"ok": True}


class EmailPrefBody(BaseModel):
    enabled: bool


@router.post("/agents/{user_id}/{kind}/email")
async def agents_email_pref_route(request: Request, user_id: int, kind: str, body: EmailPrefBody):
    """Liga/desliga o e-mail proativo desse agente (o feed continua). Opt-out por
    agente — decidido na ativação ou depois, no card."""
    shared.authorize_dashboard_access(request, user_id)
    _require_agents_beta(user_id)
    from db import AGENT_KINDS, set_agent_email_enabled

    if kind not in AGENT_KINDS:
        raise HTTPException(status_code=400, detail="Agente desconhecido.")
    ok = await asyncio.to_thread(set_agent_email_enabled, user_id, kind, body.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail="Ative o agente antes de configurar o e-mail.")
    return {"ok": True, "email_enabled": body.enabled}


@router.get("/agents/{user_id}/feed")
async def agents_feed_route(request: Request, user_id: int, limit: int = 20):
    shared.authorize_dashboard_access(request, user_id)
    _require_agents_beta(user_id)
    from db import list_agent_events

    limit = max(1, min(int(limit), 50))
    events = await asyncio.to_thread(list_agent_events, user_id, limit)
    return {"ok": True, "events": events}


@router.post("/agents/{user_id}/feed/seen")
async def agents_feed_seen_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    _require_agents_beta(user_id)
    from db import mark_agent_events_seen

    n = await asyncio.to_thread(mark_agent_events_seen, user_id)
    return {"ok": True, "marked": n}
