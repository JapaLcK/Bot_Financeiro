"""Rotas de analytics (Sprint 6) + insights/padrões via LLM (Sprint 7).

Etapa 2 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento — cada rota
autoriza, delega pro db/analytics ou core/ai_patterns e serializa.

5 endpoints de analytics separados (em vez de 1 unificado) porque o painel
personalizável vai deixar o user escolher quais widgets ver — assim cada
widget pode buscar só seu dado.
"""

import asyncio

from fastapi import APIRouter, Query, Request

from frontend.routes import shared

router = APIRouter()


@router.get("/analytics/{user_id}/kpis")
async def analytics_kpis_route(
    request: Request,
    user_id: int,
    months: int = 6,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
):
    """KPIs do período: receita, despesa, líquido, taxa de poupança + comparativo
    com período anterior. Default = últimos 6 meses."""
    shared.authorize_dashboard_access(request, user_id)
    from db import compute_kpis
    fd, td = shared.resolve_analytics_window(months, from_, to)
    result = await asyncio.to_thread(compute_kpis, user_id, fd, td)
    return {"ok": True, "kpis": result}


@router.get("/analytics/{user_id}/evolution")
async def analytics_evolution_route(
    request: Request,
    user_id: int,
    months: int = 6,
):
    """Evolução mensal de receita/despesa/líquido nos últimos N meses (sempre
    buckets mensais terminando no mês atual)."""
    shared.authorize_dashboard_access(request, user_id)
    from db import compute_evolution
    n = max(1, min(int(months or 6), 36))
    result = await asyncio.to_thread(compute_evolution, user_id, n)
    return {"ok": True, "evolution": result, "months": n}


@router.get("/analytics/{user_id}/categories")
async def analytics_categories_route(
    request: Request,
    user_id: int,
    months: int = 6,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 10,
):
    """Distribuição de despesas por categoria no período. Inclui pct, count,
    e emoji/color quando user customizou via user_categories."""
    shared.authorize_dashboard_access(request, user_id)
    from db import compute_categories
    fd, td = shared.resolve_analytics_window(months, from_, to)
    lim = max(1, min(int(limit or 10), 50))
    result = await asyncio.to_thread(compute_categories, user_id, fd, td, lim)
    return {"ok": True, "categories": result, "window": {"from": fd.isoformat(), "to": td.isoformat()}}


@router.get("/analytics/{user_id}/weekday-pattern")
async def analytics_weekday_route(
    request: Request,
    user_id: int,
    months: int = 6,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
):
    """Padrão de gasto por dia da semana (seg→dom). Total, count e média
    diária por DOW no período. Inclui credit_transactions por purchased_at
    (não bill.period_end — aqui interessa o dia da compra real)."""
    shared.authorize_dashboard_access(request, user_id)
    from db import compute_weekday_pattern
    fd, td = shared.resolve_analytics_window(months, from_, to)
    result = await asyncio.to_thread(compute_weekday_pattern, user_id, fd, td)
    return {"ok": True, "weekdays": result, "window": {"from": fd.isoformat(), "to": td.isoformat()}}


@router.get("/analytics/{user_id}/top-merchants")
async def analytics_top_merchants_route(
    request: Request,
    user_id: int,
    months: int = 6,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 10,
):
    """Top estabelecimentos no período. Junta launches (alvo/nota) +
    credit_transactions (nota). Agrupa por chave normalizada (lower/trim).
    Retorna sources = {debito, credito} pra desbobinar na UI se quiser."""
    shared.authorize_dashboard_access(request, user_id)
    from db import compute_top_merchants
    fd, td = shared.resolve_analytics_window(months, from_, to)
    lim = max(1, min(int(limit or 10), 50))
    result = await asyncio.to_thread(compute_top_merchants, user_id, fd, td, lim)
    return {"ok": True, "merchants": result, "window": {"from": fd.isoformat(), "to": td.isoformat()}}


@router.get("/insights/{user_id}/current")
async def insights_current_route(request: Request, user_id: int, force: bool = False):
    """Insights acionáveis do Piggy, gerados via LLM (gpt-4o-mini).

    Recebe estado financeiro ATUAL (orçamentos, recorrentes, metas, KPIs) e
    devolve 3-5 insights priorizados por severidade. Cache 6h por user.

    Fallback: se OPENAI_API_KEY ausente ou LLM falha, usa heurística antiga
    (`compute_active_insights`) pra não deixar o card vazio.

    `?force=true` ignora cache (útil só pra debug).
    """
    shared.authorize_dashboard_access(request, user_id)
    from core.ai_patterns import generate_ai_insights
    result = await asyncio.to_thread(generate_ai_insights, user_id, force=force)
    return {"ok": True, "insights": result or []}


@router.get("/analytics/{user_id}/patterns")
async def analytics_patterns_route(
    request: Request,
    user_id: int,
    force: bool = False,
):
    """Padrões comportamentais via LLM (gpt-4o-mini). Cache 24h.

    O LLM recebe métricas agregadas (gastos por hora, weekend split, salary
    burn, top merchants, top categorias) e devolve narrativas descobertas
    dinamicamente (variam por user). Retorna lista de items `{icon, title,
    subtitle, tone}` no campo `patterns`.

    Fallback: se LLM indisponível, retorna lista vazia (frontend mostra
    empty state). NÃO retorna a agregação bruta — só formato narrativo.

    `?force=true` ignora cache (útil só pra debug).
    """
    shared.authorize_dashboard_access(request, user_id)
    from core.ai_patterns import generate_ai_patterns
    result = await asyncio.to_thread(generate_ai_patterns, user_id, force=force)
    return {"ok": True, "patterns": result or []}
