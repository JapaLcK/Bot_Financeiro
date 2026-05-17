"""
core/ai_patterns.py — Sprint 7. Insights e padrões proativos via LLM (OpenAI).

Estratégia: backend agrega dados estruturados, manda pra `gpt-4o-mini` com
prompt específico, recebe JSON validado, cacheia em `ai_proactive_cache`.

Cache TTL por kind:
- `insights`: 6h (estado financeiro muda rápido — orçamento, salário)
- `patterns`: 24h (padrões comportamentais são estáveis)

Fallback: se LLM falha (sem API key, erro de rede, JSON inválido), retorna
lista vazia (frontend mostra empty state). Para insights, usa heurística
de `db.insights.compute_active_insights` como segundo nível de fallback.

Por que LLM e não heurística:
- Padrões variam por user (descoberta dinâmica, não regras fixas)
- Permite dicas acionáveis customizadas baseadas no perfil real
- Tom Piggy (anti-fricção) consistente, geração natural

Privacidade: dados enviados ao LLM são SEMPRE agregados (totais, %, médias).
Nunca enviamos nomes nominais de pessoas, e-mails ou identificadores. Nomes
de estabelecimentos (merchants) e categorias livres vão como estão.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any


logger = logging.getLogger(__name__)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE = 0.4  # ligeiramente acima do chat IA pra ter variedade nas narrativas

INSIGHTS_CACHE_TTL_SECONDS = 6 * 3600     # 6h
PATTERNS_CACHE_TTL_SECONDS = 24 * 3600    # 24h


# ─────────────────────────────────────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────────────────────────────────────

def _get_cached(user_id: int, kind: str, ttl_seconds: int) -> list[dict] | None:
    """Retorna payload cacheado se ainda válido, senão None."""
    from db.connection import get_conn
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select payload
                    from ai_proactive_cache
                    where user_id = %s and kind = %s
                      and generated_at + (%s || ' seconds')::interval > now()
                    """,
                    (user_id, kind, str(ttl_seconds)),
                )
                row = cur.fetchone()
                if not row:
                    return None
                payload = row["payload"]
                # psycopg pode retornar jsonb como dict/list ou string
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if isinstance(payload, list):
                    return payload
                if isinstance(payload, dict) and "items" in payload:
                    return payload["items"]
                return None
    except Exception as e:
        logger.warning("ai_patterns: cache read falhou: %s", e)
        return None


def _save_cache(user_id: int, kind: str, items: list[dict]) -> None:
    """Persiste lista de narrativas no cache (upsert)."""
    from db.connection import get_conn
    try:
        payload = json.dumps({"items": items}, ensure_ascii=False)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into ai_proactive_cache (user_id, kind, payload, generated_at)
                    values (%s, %s, %s::jsonb, now())
                    on conflict (user_id, kind) do update
                      set payload = excluded.payload,
                          generated_at = excluded.generated_at
                    """,
                    (user_id, kind, payload),
                )
            conn.commit()
    except Exception as e:
        logger.warning("ai_patterns: cache write falhou: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Coleta de dados estruturados
# ─────────────────────────────────────────────────────────────────────────────

def _collect_patterns_data(user_id: int) -> dict[str, Any]:
    """Junta tudo que o LLM precisa pra descobrir padrões comportamentais.

    Retorna dict que vai serializado como JSON no prompt user-message.
    """
    from db import (
        compute_behavioral_patterns,
        compute_categories,
        resolve_window,
    )

    out: dict[str, Any] = {}

    try:
        behaviors = compute_behavioral_patterns(user_id, months=6)
        out["behaviors"] = {
            "window": behaviors.get("window"),
            "hour_buckets": behaviors.get("hour_buckets"),
            "weekend_split": behaviors.get("weekend_split"),
            "salary_burn": behaviors.get("salary_burn"),
            "top_merchants": behaviors.get("top_merchants"),
        }
    except Exception as e:
        logger.warning("ai_patterns: behaviors falhou: %s", e)
        out["behaviors"] = None

    try:
        fd, td = resolve_window(months=6)
        cats = compute_categories(user_id, fd, td)
        # Top 8 categorias por gasto pra LLM ter contexto sem explodir tokens
        out["top_categories"] = (cats or [])[:8]
    except Exception as e:
        logger.warning("ai_patterns: categories falhou: %s", e)
        out["top_categories"] = []

    return out


def _collect_insights_data(user_id: int) -> dict[str, Any]:
    """Junta estado financeiro ATUAL pro LLM gerar alertas/dicas acionáveis."""
    from db import (
        get_budgets_status_for_month,
        list_recurring_expenses,
        compute_kpis,
        resolve_window,
    )

    out: dict[str, Any] = {}

    # Orçamentos do mês corrente
    try:
        out["budgets"] = get_budgets_status_for_month(user_id)
    except Exception as e:
        logger.warning("ai_patterns: budgets falhou: %s", e)
        out["budgets"] = None

    # Recurring com reajustes recentes
    try:
        recurrings = list_recurring_expenses(user_id, include_inactive=False)
        out["recurrings"] = [
            {
                "name": r.get("name"),
                "amount": r.get("amount"),
                "last_amount": r.get("last_amount"),
                "last_amount_changed_at": r.get("last_amount_changed_at"),
                "category": r.get("category"),
                "is_essential": r.get("is_essential"),
                "due_day": r.get("due_day"),
            }
            for r in (recurrings or [])
        ]
    except Exception as e:
        logger.warning("ai_patterns: recurrings falhou: %s", e)
        out["recurrings"] = []

    # KPIs do mês corrente
    try:
        today = date.today()
        fd = date(today.year, today.month, 1)
        td = resolve_window(months=1)[1]
        out["month_kpis"] = compute_kpis(user_id, fd, td)
    except Exception as e:
        logger.warning("ai_patterns: kpis falhou: %s", e)
        out["month_kpis"] = None

    # Metas (goals) com status
    try:
        out["goals"] = _compact_goals_status(user_id)
    except Exception as e:
        logger.warning("ai_patterns: goals falhou: %s", e)
        out["goals"] = []

    return out


def _compact_goals_status(user_id: int) -> list[dict]:
    """Replica essencial de /goals/{uid}/status sem precisar do HTTP.

    Retorna só metas (target_amount NOT NULL) com pct_complete e indicator.
    """
    from db.pockets import list_pockets
    from db.connection import get_conn

    out: list[dict] = []
    today = date.today()
    pockets = list_pockets(user_id)
    for p in pockets:
        ta = p.get("target_amount")
        td = p.get("target_date")
        if ta is None or td is None:
            continue
        saved = float(p.get("balance") or 0)
        tgt = float(ta)
        if tgt <= 0:
            continue
        pct = (saved / tgt * 100.0)
        days_left = (td - today).days
        monthly_needed = None
        if days_left and days_left > 0:
            monthly_needed = (tgt - saved) / max(1, days_left / 30.0)

        # Ritmo dos últimos 90d
        monthly_pace = 0.0
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select
                          coalesce(sum(case when tipo='deposito_caixinha' then valor else 0 end), 0) -
                          coalesce(sum(case when tipo='saque_caixinha' then valor else 0 end), 0)
                          as net
                        from launches
                        where user_id=%s and alvo=%s
                          and criado_em >= now() - interval '90 days'
                        """,
                        (user_id, p["name"]),
                    )
                    r = cur.fetchone()
                    monthly_pace = float(r["net"] or 0) / 3.0
        except Exception:
            monthly_pace = 0.0

        indicator = "on_track"
        if pct >= 100:
            indicator = "achieved"
        elif days_left is not None and days_left < 0:
            indicator = "behind"
        elif monthly_needed and monthly_pace < monthly_needed * 0.5:
            indicator = "behind"
        elif monthly_needed and monthly_pace < monthly_needed * 0.9:
            indicator = "tight"

        out.append({
            "name": p["name"],
            "balance": saved,
            "target_amount": tgt,
            "target_date": td.isoformat(),
            "pct_complete": round(pct, 1),
            "days_left": days_left,
            "monthly_pace_current": round(monthly_pace, 2),
            "monthly_pace_needed": round(monthly_needed, 2) if monthly_needed else None,
            "indicator": indicator,
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

PATTERNS_SYSTEM_PROMPT = """\
Você é o Piggy 🐷, o assistente financeiro do PigBank AI. Sua tarefa: analisar
um JSON com métricas comportamentais agregadas de um usuário (gastos por
horário, dia da semana, top categorias, top estabelecimentos, ritmo de
queima do salário) e identificar 3 a 5 PADRÕES INTERESSANTES.

Tom: amigo curioso e observador, anti-fricção, sem julgar. PT-BR coloquial.

Cada padrão é uma DESCOBERTA específica e numérica sobre o comportamento,
não uma regra genérica. Exemplos do tom que queremos:
- "Você gasta 2,3x mais em iFood depois das 22h"
- "Sextas e sábados concentram 51% do seu lazer"
- "Você queima 80% do salário até o dia 12"
- "Suas assinaturas somam R$ 287/mês — mais que o gasto médio com transporte"
- "Dica do Piggy: cortar 1 jantar fora por semana economiza ~R$ 240/mês"

REGRAS:
1. Use SÓ números reais do JSON. NUNCA invente valores ou estabelecimentos.
2. Se uma seção do JSON está vazia ou null, NÃO faça pattern sobre ela.
3. Cada padrão deve ter um cálculo verificável no JSON (ex: "2,3x" precisa
   ser X / Y onde X e Y existem nos dados).
4. Evite redundância — não repita o mesmo padrão com palavras diferentes.
5. Pelo menos 1 padrão deve ser ACIONÁVEL (uma dica de economia/ajuste).
6. Formatação de R$: usar vírgula como decimal (R$ 1.234,56). Não escrever USD.
7. Use no máximo 1 emoji por padrão e só se fizer sentido contextual.

FORMATO DE SAÍDA — JSON ESTRITO, NADA ALÉM DISSO:
{
  "items": [
    {
      "icon": "🌙",                                 // 1 emoji
      "title": "Você gasta 2,3x mais em iFood depois das 22h",
      "subtitle": "média noturna R$ 67 vs R$ 29 nas outras refeições",
      "tone": "neutral"                            // "neutral" | "warn" | "tip"
    },
    ...
  ]
}

Se não houver dados suficientes pra padrões reais, retorne {"items": []}.
"""


INSIGHTS_SYSTEM_PROMPT = """\
Você é o Piggy 🐷, o assistente financeiro do PigBank AI. Sua tarefa: analisar
um JSON com o ESTADO FINANCEIRO ATUAL de um usuário (orçamentos do mês,
recorrentes, metas, KPIs) e gerar 3 a 5 INSIGHTS ACIONÁVEIS — alertas,
alertas iminentes, observações importantes.

Tom: amigo presente, anti-fricção, direto. PT-BR coloquial.

Diferença para padrões: insights são sobre o ESTADO ATUAL (orçamento
estourando AGORA, recorrente reajustou, meta atrasada). Padrões são
descobertas de comportamento ao longo do tempo.

REGRAS:
1. Use SÓ números reais do JSON. NUNCA invente.
2. Insights precisam ser SOBRE O ESTADO ATUAL — não invenções históricas.
3. Cada insight tem severidade: critical (urgente), warning (atenção), info
   (informativo). Distribuir conforme prioridade.
4. Se um budget está vermelho (≥100%), é crítico.
5. Se um recorrente reajustou recentemente, é warning.
6. Se uma meta tá behind, é warning.
7. Formatação de R$: vírgula decimal (R$ 1.234,56).
8. Use no máximo 1 emoji por insight (deixe o título limpo).
9. Se nada relevante, retorne {"items": []}.

FORMATO DE SAÍDA — JSON ESTRITO:
{
  "items": [
    {
      "icon": "🍔",
      "title": "Alimentação estourou o orçamento",
      "message": "R$ 487 de R$ 500 (97%) e faltam 19 dias no mês.",
      "severity": "critical",                   // critical | warning | info
      "action_label": "Ajustar",                // texto curto ou null
      "action_view": "budgets",                 // budgets|fixed|goals|analytics|null
      "key": "budget:alimentacao"               // identificador estável p/ dismiss
    },
    ...
  ]
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Chamada LLM
# ─────────────────────────────────────────────────────────────────────────────

def _call_llm(system_prompt: str, user_data: dict[str, Any]) -> list[dict] | None:
    """Chama OpenAI e retorna lista de items (JSON estruturado) ou None se falha."""
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("ai_patterns: OPENAI_API_KEY não configurada — pulando LLM")
        return None

    try:
        from openai import OpenAI
    except Exception as e:
        logger.error("ai_patterns: falha ao importar openai SDK: %s", e)
        return None

    try:
        client = OpenAI(api_key=api_key)
    except Exception as e:
        logger.error("ai_patterns: falha ao inicializar OpenAI: %s", e)
        return None

    user_msg = "Aqui está o JSON com os dados agregados. Gere o JSON de saída exatamente no formato pedido:\n\n```json\n" + json.dumps(user_data, ensure_ascii=False, default=str) + "\n```"

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
        )
    except Exception as e:
        logger.error("ai_patterns: erro na chamada OpenAI: %s", e)
        return None

    try:
        content = resp.choices[0].message.content or "{}"
        parsed = json.loads(content)
        items = parsed.get("items")
        if not isinstance(items, list):
            logger.warning("ai_patterns: resposta sem 'items': %s", content[:200])
            return None
        return items
    except Exception as e:
        logger.error("ai_patterns: falha ao parsear JSON da LLM: %s", e)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def generate_ai_patterns(user_id: int, *, force: bool = False) -> list[dict]:
    """Padrões comportamentais via LLM. Cache 24h.

    `force=True` ignora cache e regenera. Use só pra debug ou refresh manual.
    """
    if not force:
        cached = _get_cached(user_id, "patterns", PATTERNS_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached

    data = _collect_patterns_data(user_id)

    # Se não tem dado nenhum, evita queimar token
    behaviors = data.get("behaviors") or {}
    has_signal = bool(
        behaviors and (
            (behaviors.get("hour_buckets") or [{}])[0].get("total", 0) > 0
            or (behaviors.get("top_merchants") or [])
        )
    )
    if not has_signal:
        _save_cache(user_id, "patterns", [])
        return []

    items = _call_llm(PATTERNS_SYSTEM_PROMPT, data) or []
    items = _sanitize_pattern_items(items)
    _save_cache(user_id, "patterns", items)
    return items


def generate_ai_insights(user_id: int, *, force: bool = False) -> list[dict]:
    """Insights acionáveis via LLM. Cache 6h.

    Fallback: se LLM falha por qualquer motivo, usa heurística antiga
    (`db.insights.compute_active_insights`) pra não deixar o card vazio.
    """
    if not force:
        cached = _get_cached(user_id, "insights", INSIGHTS_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached

    data = _collect_insights_data(user_id)
    items = _call_llm(INSIGHTS_SYSTEM_PROMPT, data)
    if items is None:
        # Fallback heurístico
        try:
            from db import compute_active_insights
            items = compute_active_insights(user_id)
        except Exception as e:
            logger.warning("ai_patterns: fallback heurística falhou: %s", e)
            items = []

    items = _sanitize_insight_items(items)
    _save_cache(user_id, "insights", items)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Sanitização — proteção contra LLM teimoso com schema
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_TONES = {"neutral", "warn", "tip"}
_ALLOWED_SEVERITIES = {"critical", "warning", "info"}
_ALLOWED_VIEWS = {"budgets", "fixed", "goals", "analytics", "pockets", "recurring"}


def _sanitize_pattern_items(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "") or "").strip()
        if not title:
            continue
        out.append({
            "icon": str(it.get("icon") or "🐷")[:4],
            "title": title[:140],
            "subtitle": str(it.get("subtitle") or "")[:200],
            "tone": it.get("tone") if it.get("tone") in _ALLOWED_TONES else "neutral",
        })
        if len(out) >= 6:
            break
    return out


def _sanitize_insight_items(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for idx, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "") or "").strip()
        if not title:
            continue
        sev = it.get("severity")
        if sev not in _ALLOWED_SEVERITIES:
            sev = "info"
        action_view = it.get("action_view")
        if action_view not in _ALLOWED_VIEWS:
            action_view = None
        action_label = it.get("action_label")
        if action_view is None:
            action_label = None
        elif action_label:
            action_label = str(action_label)[:24]

        out.append({
            "icon": str(it.get("icon") or "🐷")[:4],
            "title": title[:140],
            "message": str(it.get("message") or "")[:240],
            "severity": sev,
            "action_label": action_label,
            "action_view": action_view,
            "key": str(it.get("key") or f"ai-{idx}")[:120],
        })
        if len(out) >= 6:
            break
    return out


__all__ = [
    "generate_ai_patterns",
    "generate_ai_insights",
    "PATTERNS_CACHE_TTL_SECONDS",
    "INSIGHTS_CACHE_TTL_SECONDS",
]
