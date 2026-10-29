"""Rotas de analytics (Sprint 6) + insights/padrões via LLM (Sprint 7).

Etapa 2 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento — cada rota
autoriza, delega pro db/analytics ou core/ai_patterns e serializa.

5 endpoints de analytics separados (em vez de 1 unificado) porque o painel
personalizável vai deixar o user escolher quais widgets ver — assim cada
widget pode buscar só seu dado.
"""

import asyncio
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from core.observability import get_logger
from core.pg_text import recusa_veneno
from core.services.insights_contrato import CHAVE_MAX, GRAVIDADE
from frontend.routes import shared

router = APIRouter()
logger = get_logger(__name__)
FORA_DO_HISTORICO = "Sem comparação: o período anterior está fora do histórico do seu plano."


@router.get("/analytics/{user_id}/kpis")
async def analytics_kpis_route(
    request: Request,
    user_id: int,
    months: int = 6,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
):
    """KPIs do período: receita, despesa, líquido, taxa de poupança. Default = últimos 6 meses.

    Plus+ recebe `delta_pct` (income/expense), `delta_escolha` e `delta_meses` (o N usado) da regra única de comparação
    (`comparar_mes` com N meses, P1-B: os N meses até o corrente, inteiros, contra os N antes deles; a mesma
    janela dos totais desta rota; cartão pela fatura). O gate vem antes da leitura do anterior; o Essencial não recebe nem lê.
    Janela livre (`from` E `to`, a regra de `resolve_window`) fica sem delta: a regra só existe para meses
    terminando no corrente. Falha da comparação não derruba os KPIs: o delta sai nulo."""
    shared.authorize_dashboard_access(request, user_id)
    from db import compute_kpis
    fd, td = shared.resolve_analytics_window(months, from_, to)
    result = await asyncio.to_thread(compute_kpis, user_id, fd, td, False)
    from core.services.plan_service import plan_gate_ok
    if not await asyncio.to_thread(plan_gate_ok, user_id, "financial_comparison"):
        return {"ok": True, "kpis": result}
    pct = {"income": None, "expense": None}
    result["delta_pct"], result["delta_escolha"], result["delta_meses"] = pct, None, None
    if not (from_ and to):
        from core.services.comparacao_periodos import comparar_mes
        from utils_date import now_tz
        agora = now_tz()
        n = (td.year * 12 + td.month) - (fd.year * 12 + fd.month)  # os meses de `resolve_window`
        try:
            r = await asyncio.to_thread(comparar_mes, user_id, agora.year, agora.month, agora, n)
        except Exception as exc:  # o delta é extra: os totais saem
            logger.warning("analytics_kpis: comparação indisponível (%s)", type(exc).__name__)
            r = None
        if r is not None:
            result["delta_meses"] = n  # o rótulo diz "vs os N meses anteriores" com este N, não com o do seletor
            fora = r["motivo"] == "inicio_do_historico"
            result["delta_escolha"] = f"{FORA_DO_HISTORICO} {r['escolha']}" if fora else r["escolha"]
            for chave, linha in (("income", r["receita"]), ("expense", r["despesa"])):
                pct[chave] = float(linha["pct"]) if linha and linha["pct"] is not None else None
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
    await asyncio.to_thread(shared.require_plan_feature, user_id, "financial_comparison")
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
    await asyncio.to_thread(shared.require_plan_feature, user_id, "financial_comparison")
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
@shared.limiter.limit("20/minute")
async def insights_current_route(request: Request, user_id: int):
    """Insights acionáveis do Piggy, sem LLM (D12 do PL-06).

    Contrato v1 (`core/services/insights_contrato.py`): os fatos e o texto vêm dos
    detectores (`compute_active_insights`); até 6 itens por severidade, cache de até 6h (ou até a meia-noite) por
    user. `impacto`/`acoes` só no Pro (`insights_acoes`). O dispensado que não piorou
    (`PUT .../estado`) sai antes do corte em 6. Lista vazia traz `motivo`
    (`sem_dados`/`sem_destaques`/`dispensados`), ou nulo se um detector falhou.

    Segurança: cada cálculo roda as consultas dos detectores. Rate-limit por IP +
    o `force` (bypass de cache) NÃO é exposto ao cliente — senão dava pra chamar
    `?force=true` em loop e martelar o banco.
    """
    shared.authorize_dashboard_access(request, user_id)
    from core.ai_patterns import generate_ai_insights, motivo_vazio
    await asyncio.to_thread(shared.require_plan_feature, user_id, "insights")
    result = await asyncio.to_thread(generate_ai_insights, user_id, force=False)
    motivo = None if result else await asyncio.to_thread(motivo_vazio, user_id)
    return {"ok": True, "insights": result or [], "motivo": motivo}


class InsightEstadoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chave: str = Field(min_length=1, max_length=CHAVE_MAX)
    dispensado: StrictBool | None = None
    util: StrictBool | None = None
    severidade: str | None = None  # a que o usuário VIU; obrigatória com `dispensado: true`
    valor: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)  # o visto, só no OF

    @model_validator(mode="after")
    def _um_dos_dois(self):
        if self.dispensado is None and self.util is None:
            raise ValueError("informe dispensado ou util")
        if self.dispensado and self.severidade not in GRAVIDADE:
            raise ValueError("dispensar exige a severidade vista")
        return recusa_veneno(self)  # NUL na chave daria 500 no execute


@router.put("/insights/{user_id}/estado")
@shared.limiter.limit("30/minute")
async def insights_estado_route(request: Request, user_id: int, body: InsightEstadoBody):
    """"Dispensar"/"útil" de um insight (PL-06 A2). Estado explícito e idempotente: `dispensado: false` desfaz;
    o campo ausente fica como está. `dispensado: true` leva a `severity` (e, no OF, o `valor` de
    `explicacao.atual`, obrigatório: sem ele, 422 só para a chave do dono) que o usuário viu: piora gravada depois disso continua visível. "Útil" é só métrica (D3).
    404 igual para chave inexistente e de outro usuário."""
    shared.authorize_dashboard_access(request, user_id)
    from db.insight_estado import ValorObrigatorio, marcar
    await asyncio.to_thread(shared.require_plan_feature, user_id, "insights")
    try:
        estado = await asyncio.to_thread(marcar, user_id, body.chave, dispensado=body.dispensado, util=body.util,
                                         severidade=body.severidade, valor=body.valor)
    except ValorObrigatorio:
        raise HTTPException(status_code=422, detail="Dispensar este aviso exige o valor visto.") from None
    if estado is None:
        raise HTTPException(status_code=404, detail="Insight não encontrado.")
    return {"ok": True, "chave": body.chave, **estado}


@router.get("/analytics/{user_id}/patterns")
@shared.limiter.limit("20/minute")
async def analytics_patterns_route(
    request: Request,
    user_id: int,
):
    """Padrões comportamentais via LLM. Cache 24h.

    O LLM recebe métricas agregadas (gastos por hora, weekend split, salary
    burn, top merchants, top categorias) e devolve narrativas descobertas
    dinamicamente (variam por user). Retorna lista de items `{icon, title,
    subtitle, tone}` no campo `patterns`.

    Fallback: se LLM indisponível, retorna lista vazia (frontend mostra
    empty state). NÃO retorna a agregação bruta — só formato narrativo.

    Segurança: idem /insights — rate-limit por IP e `force` não exposto ao
    cliente (cada chamada é uma completion paga; evita queimar tokens em loop).
    """
    shared.authorize_dashboard_access(request, user_id)
    from core.ai_patterns import generate_ai_patterns
    await asyncio.to_thread(shared.require_plan_feature, user_id, "insights")
    result = await asyncio.to_thread(generate_ai_patterns, user_id, force=False)
    return {"ok": True, "patterns": result or []}
