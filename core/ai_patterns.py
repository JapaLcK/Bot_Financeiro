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

# Bumpe quando mudar prompt/payload de forma significativa. Cache antigo
# vira garbage instantaneamente (kind diferente = miss).
_PROMPT_VERSION_PATTERNS = 7
_PROMPT_VERSION_INSIGHTS = 7


def _cache_kind(base: str) -> str:
    if base == "patterns":
        return f"patterns_v{_PROMPT_VERSION_PATTERNS}"
    if base == "insights":
        return f"insights_v{_PROMPT_VERSION_INSIGHTS}"
    return base


def _days_left_in_month(today: date) -> int:
    if today.month == 12:
        nxt = date(today.year + 1, 1, 1)
    else:
        nxt = date(today.year, today.month + 1, 1)
    return (nxt - today).days


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

_MONTH_PT = ["jan", "fev", "mar", "abr", "mai", "jun",
             "jul", "ago", "set", "out", "nov", "dez"]


def _fmt_period_pt(from_iso: str | None, to_iso: str | None) -> str:
    """'2025-12-01' / '2026-06-01' → 'dez/2025 a mai/2026'."""
    try:
        from datetime import date as _date
        if not from_iso or not to_iso:
            return "últimos 6 meses"
        fd = _date.fromisoformat(from_iso[:10])
        td = _date.fromisoformat(to_iso[:10])
        # to_date é exclusive (1º dia do mês seguinte). Subtrai 1 dia pra
        # pegar o último mês COBERTO.
        from datetime import timedelta as _td
        td_inclusive = td - _td(days=1)
        f_lbl = f"{_MONTH_PT[fd.month - 1]}/{fd.year}"
        t_lbl = f"{_MONTH_PT[td_inclusive.month - 1]}/{td_inclusive.year}"
        return f"{f_lbl} a {t_lbl}"
    except Exception:
        return "últimos 6 meses"


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

    months_covered = 6
    try:
        behaviors = compute_behavioral_patterns(user_id, months=months_covered)
        window = behaviors.get("window") or {}
        period_label = _fmt_period_pt(window.get("from"), window.get("to"))

        # Pré-calcula totais agregados pra ajudar o LLM a citar números corretos
        total_expense_all = 0.0
        for hb in (behaviors.get("hour_buckets") or []):
            total_expense_all += float(hb.get("total") or 0)

        # ── Enriquece TOP_MERCHANTS com avg_per_month + freq_per_month ──
        # Pra LLM nunca precisar dividir total/meses sozinho (a fonte da
        # alucinação "dividido por 10 meses" quando o período é 6).
        enriched_merchants = []
        for m in (behaviors.get("top_merchants") or []):
            total = float(m.get("total") or 0)
            count = int(m.get("count") or 0)
            avg_per_month = total / months_covered if months_covered > 0 else 0
            avg_per_tx = (total / count) if count > 0 else 0
            freq_per_month = count / months_covered if months_covered > 0 else 0
            enriched_merchants.append({
                **m,
                "avg_per_month": round(avg_per_month, 2),
                "avg_per_transaction": round(avg_per_tx, 2),
                "frequency_per_month": round(freq_per_month, 2),
            })

        out["_meta"] = {
            "period_label": period_label,           # ex: "dez/2025 a mai/2026"
            "months_covered": months_covered,       # SEMPRE 6 — usar este divisor
            "total_expense_period": round(total_expense_all, 2),
            "data_source_note": (
                "Todos os valores no JSON são EXATOS — não faça aritmética. "
                "Use SEMPRE valores prontos: avg_per_month/avg_per_transaction/"
                "avg_monthly/avg_daily/frequency_per_month. NUNCA divida você "
                "mesmo um 'total' por meses/semanas — o divisor é 6 (months_covered)."
            ),
        }
        out["behaviors"] = {
            "window": window,
            "hour_buckets": behaviors.get("hour_buckets"),
            "weekend_split": behaviors.get("weekend_split"),
            "salary_burn": behaviors.get("salary_burn"),
            "top_merchants": enriched_merchants,
        }
    except Exception as e:
        logger.warning("ai_patterns: behaviors falhou: %s", e)
        out["behaviors"] = None

    try:
        fd, td = resolve_window(months=months_covered)
        cats = compute_categories(user_id, fd, td)
        # Top 8 categorias por gasto pra LLM ter contexto sem explodir tokens.
        # Enriquecemos com avg_per_month e pct_of_total (não confiar no LLM
        # pra calcular nada).
        total_cats = sum(float(c.get("total") or 0) for c in (cats or []))
        top_cats_raw = (cats or [])[:8]
        enriched_cats = []
        for c in top_cats_raw:
            total = float(c.get("total") or 0)
            count = int(c.get("count") or 0)
            avg_pm = total / months_covered if months_covered > 0 else 0
            avg_tx = (total / count) if count > 0 else 0
            pct = (total / total_cats * 100.0) if total_cats > 0 else 0.0
            enriched_cats.append({
                **c,
                "avg_per_month": round(avg_pm, 2),
                "avg_per_transaction": round(avg_tx, 2),
                "pct_of_total_spending": round(pct, 1),
            })
        out["top_categories"] = enriched_cats
    except Exception as e:
        logger.warning("ai_patterns: categories falhou: %s", e)
        out["top_categories"] = []

    return out


def _collect_insights_data(user_id: int) -> dict[str, Any]:
    """Junta estado financeiro ATUAL pro LLM gerar alertas/dicas acionáveis."""
    from db import (
        get_budgets_status_for_month,
        compute_kpis,
        resolve_window,
    )
    from db.recurring import list_recurring_expenses

    out: dict[str, Any] = {}
    today = date.today()
    out["_meta"] = {
        "today": today.isoformat(),
        "month_label": f"{_MONTH_PT[today.month - 1]}/{today.year}",
        "day_of_month": today.day,
        "days_left_in_month": _days_left_in_month(today),
        "data_source_note": (
            "Todos os números refletem o ESTADO ATUAL no mês corrente, exceto "
            "onde explicitamente indicado outro período."
        ),
    }

    # Orçamentos do mês corrente
    try:
        out["budgets"] = get_budgets_status_for_month(user_id)
    except Exception as e:
        logger.warning("ai_patterns: budgets falhou: %s", e)
        out["budgets"] = None

    # Recurring com reajustes recentes (enrich com change_pct e change_brl
    # pré-calculados — LLM não precisa fazer (atual-anterior)/anterior).
    try:
        recurrings = list_recurring_expenses(user_id, include_inactive=False)
        enriched_recs = []
        for r in (recurrings or []):
            cur_amt = float(r.get("amount") or 0)
            prev_amt = r.get("last_amount")
            change_pct = None
            change_brl = None
            if prev_amt is not None and float(prev_amt) > 0:
                prev_f = float(prev_amt)
                change_brl = round(cur_amt - prev_f, 2)
                change_pct = round((cur_amt - prev_f) / prev_f * 100.0, 1)
            enriched_recs.append({
                "name": r.get("name"),
                "amount": cur_amt,
                "last_amount": prev_amt,
                "last_amount_changed_at": r.get("last_amount_changed_at"),
                "change_brl": change_brl,           # ex: 5.00 (subiu R$ 5)
                "change_pct": change_pct,           # ex: 11.1 (subiu 11,1%)
                "category": r.get("category"),
                "is_essential": r.get("is_essential"),
                "due_day": r.get("due_day"),
            })
        out["recurrings"] = enriched_recs
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
um JSON com métricas comportamentais agregadas de um usuário e identificar
3 a 5 PADRÕES INTERESSANTES.

Tom: amigo curioso e observador, anti-fricção, sem julgar. PT-BR coloquial.

Cada padrão é uma DESCOBERTA específica sobre o comportamento, não regra
genérica. Pode ser QUANTITATIVO (com números) ou QUALITATIVO (só
descrevendo o padrão) — escolha QUALITATIVO sempre que a amostra é
pequena ou os valores muito disparatados.

Exemplos do tom que queremos:

Quantitativos (quando dados são robustos):
- "Você gasta 2,3x mais em iFood depois das 22h" (subtitle: "ticket médio R$ 67 vs R$ 29")
- "Sextas e sábados concentram 51% do seu lazer"
- "Você queima 80% do salário até o dia 12"
- "Dica do Piggy: cortar 1 jantar fora por semana economiza ~R$ 240/mês"

Qualitativos (quando dados são frágeis — preferíveis a alucinar números):
- "Você tende a gastar mais à tarde do que de manhã"
- "Seu padrão de consumo se concentra no fim de semana"
- "Quase todo seu gasto noturno vem de delivery"

Regra prática: SE não tem certeza do número, NÃO cite o número.
Qualitativo > quantitativo errado.

╔══════════════════════════════════════════════════════════════════════════╗
║ ANTI-ALUCINAÇÃO — REGRAS ABSOLUTAS                                       ║
╠══════════════════════════════════════════════════════════════════════════╣
║ 1. Use SOMENTE números EXATOS que existem no JSON. NUNCA invente.        ║
║                                                                          ║
║ 2. ⚠️ PROIBIDO FAZER ARITMÉTICA. Não some, não divida, não multiplique.  ║
║    Todos os números que você precisa já estão calculados:                ║
║      • Gasto/mês de uma categoria   → top_categories[i].avg_per_month    ║
║      • Gasto/mês de um merchant     → top_merchants[i].avg_per_month     ║
║      • Ticket médio (1 transação)   → ...[i].avg_per_transaction         ║
║      • Frequência de visitas/mês    → top_merchants[i].frequency_per_month║
║      • Gasto/mês por horário        → hour_buckets[i].avg_monthly        ║
║      • Gasto/dia útil ou fim semana → weekend_split.weekday.avg_daily    ║
║      • % do gasto total             → top_categories[i].pct_of_total_spending║
║    Se você se pegar dividindo "total/N meses", PARE. O valor pronto      ║
║    já existe. O período coberto é SEMPRE `_meta.months_covered` (=6).   ║
║    Erros típicos a evitar: "R$ 4000 dividido por 10 meses = R$ 400" —   ║
║    isso é alucinação de divisor; o divisor correto sempre é 6.          ║
║                                                                          ║
║ 3. NUNCA mencione marcas, lojas, apps ou produtos específicos a menos    ║
║    que apareçam LITERALMENTE em `behaviors.top_merchants[].name`.        ║
║    PROIBIDO inferir "iFood", "Uber", "delivery", "Netflix", "Spotify"   ║
║    a partir de categorias genéricas como "alimentacao", "transporte",   ║
║    "lazer", "compras online", "assinaturas". Se quiser falar de uma     ║
║    categoria, use o NOME DELA tal como aparece no JSON                  ║
║    (ex: "Você gasta R$ 200/mês em alimentação", NÃO                     ║
║     "Você gasta R$ 200/mês em delivery").                                ║
║                                                                          ║
║ 4. Comparações 'Nx mais' SÓ se conseguir mostrar a divisão X/Y onde X e ║
║    Y são VALORES COMPARÁVEIS — ou seja, AMBOS são médias diárias, ou    ║
║    AMBOS médias mensais, ou AMBOS tickets médios. NUNCA misture total   ║
║    acumulado com média.                                                  ║
║                                                                          ║
║ 5. Para comparar gasto por horário, use 'avg_monthly' OU                ║
║    'avg_per_transaction' dos hour_buckets — NUNCA 'total' (que é a soma ║
║    dos 6 meses inteiros).                                                ║
║    ⚠️ Se UM dos buckets tem `low_sample: true` (count<5), AINDA dá pra  ║
║    falar do padrão MAS sem citar números nem Nx — vire QUALITATIVO:     ║
║       ✅ "Você tende a gastar mais à tarde do que de manhã"             ║
║       ✅ "Quase todo seu gasto se concentra à noite"                    ║
║       ❌ "Você gasta 4,4x mais à tarde" (proibido com low_sample)       ║
║       ❌ "R$ 1.660 à tarde vs R$ 32 de manhã" (proibido com low_sample) ║
║    Quando os dois lados têm sample bom (low_sample=false), aí pode citar║
║    valores e Nx como sempre.                                             ║
║                                                                          ║
║ 6. Para dia da semana, use 'avg_daily' dentro do weekend_split.          ║
║                                                                          ║
║ 7. Se o JSON traz um número fracionário (ex: avg_day_to_80pct: 14),      ║
║    use o INTEIRO direto, sem '.5' ou '.0'. Dia 14, não dia 14,5.        ║
║                                                                          ║
║ 8. SEMPRE cite o período coberto explicitamente no subtitle, usando o    ║
║    `_meta.period_label` (ex: "últimos 6 meses (dez/2025 a mai/2026)").  ║
║    Nunca cite outro número de meses no texto — só o que está em          ║
║    `_meta.months_covered` (=6).                                          ║
║                                                                          ║
║ 9. Se uma seção do JSON está null ou vazia, NÃO faça pattern sobre ela. ║
║                                                                          ║
║10. Categoria "outros" / "sem categoria" é ruído — evite padrões focados ║
║    nela. Foque em categorias específicas com nome claro.                 ║
║                                                                          ║
║11. Se top_merchants contém merchants pouco descritivos (tipo "outros",  ║
║    "rifa", "ações", "investimento bitcoin", "gastei 800 pescaria",      ║
║    "stanley presente dia das maes"), prefira agregar por CATEGORIA em   ║
║    vez de citar um merchant individual.                                  ║
║                                                                          ║
║12. Dicas acionáveis (tone='tip'): a economia sugerida deve ser um valor ║
║    PRONTO do JSON, não calculado por você. Há SÓ DUAS formas válidas:   ║
║                                                                          ║
║    (a) "Cortar 1 visita ao [merchant] economiza R$ X" — onde R$ X é     ║
║        EXATAMENTE igual a top_merchants[i].avg_per_transaction.         ║
║        Aqui, "1 visita" = "1 transação" = ticket médio. NUNCA escreva  ║
║        '/mês' nesse caso — é o valor de UMA transação.                  ║
║        Exemplo: "Cortar 1 visita à pescaria economiza ~R$ 85"           ║
║                                                                          ║
║    (b) "Eliminar gastos com [merchant/categoria] economiza R$ X/mês" —  ║
║        onde R$ X é EXATAMENTE top_merchants[i].avg_per_month OU         ║
║        top_categories[i].avg_per_month. Aqui é o gasto MENSAL inteiro. ║
║        Exemplo: "Cortar gastos com rifa economiza R$ 426/mês"           ║
║                                                                          ║
║    Confusão a EVITAR: "Cortar 1 visita economiza R$ Y/mês" onde R$ Y =  ║
║    avg_per_month. Isso é INCORRETO — 1 visita ≠ todo o gasto mensal.    ║
║    Cortar 1 visita = 1 ticket = avg_per_transaction (sem /mês).         ║
║                                                                          ║
║    NÃO componha frases tipo "cortar metade", "reduzir 20%", "cortar 1   ║
║    por semana" — exigem aritmética sua e levam a alucinação.            ║
╚══════════════════════════════════════════════════════════════════════════╝

OUTRAS REGRAS:
- Evite redundância — não repita padrão com palavras diferentes.
- Pelo menos 1 padrão deve ser ACIONÁVEL (uma dica de economia).
- Formatação de R$: vírgula decimal (R$ 1.234,56). Não escrever USD.
- Use no máximo 1 emoji por padrão e só se fizer sentido contextual.
- Subtitle deve mostrar o cálculo de forma transparente.

FORMATO DE SAÍDA — JSON ESTRITO, NADA ALÉM DISSO:
{
  "items": [
    {
      "icon": "🌙",
      "title": "Você gasta 2,3x mais em delivery à noite",
      "subtitle": "ticket médio R$ 67 vs R$ 29 nas outras refeições · últimos 6 meses (dez/2025 a mai/2026)",
      "tone": "neutral"                     // "neutral" | "warn" | "tip"
    }
  ]
}

Se não houver sinal suficiente pra padrões verificáveis, retorne {"items": []}.
"""


INSIGHTS_SYSTEM_PROMPT = """\
Você é o Piggy 🐷, o assistente financeiro do PigBank AI. Sua tarefa: analisar
um JSON com o ESTADO FINANCEIRO ATUAL de um usuário (orçamentos do mês,
recorrentes, metas, KPIs) e gerar 3 a 5 INSIGHTS ACIONÁVEIS.

Tom: amigo presente, anti-fricção, direto. PT-BR coloquial.

Diferença para padrões: insights são sobre o ESTADO ATUAL (orçamento
estourando AGORA, recorrente reajustou, meta atrasada). Padrões são
descobertas de comportamento ao longo do tempo.

╔══════════════════════════════════════════════════════════════════════════╗
║ ANTI-ALUCINAÇÃO — REGRAS ABSOLUTAS                                       ║
╠══════════════════════════════════════════════════════════════════════════╣
║ 1. Use SOMENTE números EXATOS do JSON. NUNCA invente valores.            ║
║                                                                          ║
║ 2. ⚠️ PROIBIDO FAZER ARITMÉTICA. Nada de divisões, multiplicações, %    ║
║    calculadas por você. Os valores prontos a usar:                       ║
║      • Orçamento atual                  → budgets.budgets[i].pct, .spent,║
║                                           .budget, .remaining            ║
║      • Recorrente reajustado           → recurrings[i].amount,           ║
║                                           recurrings[i].last_amount      ║
║      • Meta atrasada                    → goals[i].pct_complete,         ║
║                                           goals[i].monthly_pace_current, ║
║                                           goals[i].monthly_pace_needed   ║
║      • Dias restantes no mês           → _meta.days_left_in_month        ║
║      • Mês atual                       → _meta.month_label               ║
║    Se quiser citar "subiu 12%", o JSON precisa ter ESSE 12% pronto.     ║
║    Senão, descreva sem o ratio (ex: "passou de R$ X pra R$ Y").         ║
║                                                                          ║
║ 3. Cada insight precisa ser sobre uma row VERIFICÁVEL no JSON:           ║
║    - Orçamento → tem que existir em budgets.budgets[]                    ║
║    - Recorrente → tem que existir em recurrings[] com last_amount preenchido║
║    - Meta → tem que existir em goals[] com indicator='behind'/'tight'    ║
║                                                                          ║
║ 4. Insights são sobre o MÊS CORRENTE (`_meta.month_label`). Sempre cite  ║
║    o mês quando o insight é sobre orçamento ou consumo do mês.           ║
║                                                                          ║
║ 5. Para "faltam X dias no mês", use `_meta.days_left_in_month`.          ║
║                                                                          ║
║ 6. Inteiros sem '.5'. Não use 'dia 14,5' — arredonde pra inteiro.        ║
║                                                                          ║
║ 7. Se nada relevante na seção, NÃO faça insight forçado.                ║
║                                                                          ║
║ 8. NUNCA invente marcas, lojas, apps ou produtos. Se for citar um       ║
║    estabelecimento, ele tem que aparecer LITERALMENTE no JSON. Categoria║
║    como "alimentacao" NÃO autoriza falar "iFood" ou "delivery".          ║
╚══════════════════════════════════════════════════════════════════════════╝

OUTRAS REGRAS:
- Severidade:
  - critical → orçamento vermelho (≥100%), meta vencida, recorrente subiu >20%
  - warning  → orçamento amarelo (80-99%), meta behind, recorrente subiu 5-20%
  - info     → observação relevante mas sem ação urgente
- action_view permitidos: budgets | fixed | goals | analytics | null
- Formatação de R$: vírgula decimal (R$ 1.234,56)
- Máximo 1 emoji por insight (deixe o título limpo)
- Se nada relevante, retorne {"items": []}.

FORMATO DE SAÍDA — JSON ESTRITO:
{
  "items": [
    {
      "icon": "🍔",
      "title": "Alimentação estourou o orçamento de mai/2026",
      "message": "R$ 487,00 de R$ 500,00 (97%) e faltam 19 dias no mês.",
      "severity": "critical",
      "action_label": "Ajustar",
      "action_view": "budgets",
      "key": "budget:alimentacao:2026-05"
    }
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
    kind = _cache_kind("patterns")
    if not force:
        cached = _get_cached(user_id, kind, PATTERNS_CACHE_TTL_SECONDS)
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
        _save_cache(user_id, kind, [])
        return []

    items = _call_llm(PATTERNS_SYSTEM_PROMPT, data) or []
    items = _sanitize_pattern_items(items)
    _save_cache(user_id, kind, items)
    return items


def generate_ai_insights(user_id: int, *, force: bool = False) -> list[dict]:
    """Insights acionáveis via LLM. Cache 6h.

    Fallback: se LLM falha por qualquer motivo, usa heurística antiga
    (`db.insights.compute_active_insights`) pra não deixar o card vazio.
    """
    kind = _cache_kind("insights")
    if not force:
        cached = _get_cached(user_id, kind, INSIGHTS_CACHE_TTL_SECONDS)
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
    _save_cache(user_id, kind, items)
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Sanitização — proteção contra LLM teimoso com schema
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_TONES = {"neutral", "warn", "tip"}
_ALLOWED_SEVERITIES = {"critical", "warning", "info"}
_ALLOWED_VIEWS = {"budgets", "fixed", "goals", "analytics", "pockets", "recurring"}


# Regex pra extrair "Nx" / "N,Mx" / "N.Mx" do título — busca alegação de ratio.
import re as _re
_RATIO_RE = _re.compile(r"(\d+[\.,]?\d*)\s*x\b", _re.IGNORECASE)
# Captura R$ X,XX (formato BR) ou R$ X.XX no subtitle/title.
_MONEY_RE = _re.compile(r"R\$\s*([\d\.]+,\d{2}|\d+[\.,]?\d*)")


def _parse_brl(s: str) -> float | None:
    """Converte '1.660,91' → 1660.91 ou '1660.91' → 1660.91 ou retorna None."""
    if not s:
        return None
    try:
        s = s.strip()
        # Formato BR: "1.660,91" → "1660.91"
        if "," in s and "." in s:
            return float(s.replace(".", "").replace(",", "."))
        if "," in s:
            return float(s.replace(",", "."))
        return float(s)
    except (ValueError, AttributeError):
        return None


def _ratio_claim_is_consistent(title: str, subtitle: str) -> bool:
    """Se title alega 'Nx mais', valida que N ≈ maior_valor / menor_valor
    dos R$ encontrados no subtitle. Tolerância de 15%.

    Retorna True se:
      - title não tem alegação de "Nx" (nada a validar)
      - subtitle tem ≥2 valores R$ e o ratio bate (com tolerância)

    Retorna False se claim de Nx existe mas não bate ou não tem 2 valores
    pra checar. Itens com False são DESCARTADOS (segurança > completude).
    """
    m = _RATIO_RE.search(title)
    if not m:
        return True  # sem claim de Nx → nada a validar
    claimed = _parse_brl(m.group(1))
    if claimed is None or claimed <= 0:
        return False

    money_values = [
        _parse_brl(v) for v in _MONEY_RE.findall(subtitle or "")
    ]
    money_values = [v for v in money_values if v and v > 0]
    if len(money_values) < 2:
        # Claim de ratio sem 2 valores no subtitle pra verificar → suspeito
        return False

    hi = max(money_values)
    lo = min(money_values)
    if lo == 0:
        return False
    actual = hi / lo
    # Tolerância 15% pra absorver arredondamento
    if abs(actual - claimed) / actual <= 0.15:
        return True
    logger.info(
        "ai_patterns: ratio inconsistente descartado — title=%r claimed=%.2f actual=%.2f",
        title, claimed, actual,
    )
    return False


def _sanitize_pattern_items(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    for i, it in enumerate(items or []):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "") or "").strip()
        if not title:
            continue
        subtitle = str(it.get("subtitle") or "")[:200]
        # Cinto de segurança: se o item afirma "Nx" e os valores do subtitle
        # não confirmam, descarta (provavelmente alucinação aritmética).
        if not _ratio_claim_is_consistent(title, subtitle):
            continue
        out.append({
            "icon": str(it.get("icon") or "🐷")[:4],
            "title": title[:140],
            "subtitle": subtitle,
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
