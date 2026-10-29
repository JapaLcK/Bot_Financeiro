"""
core/ai_patterns.py — Sprint 7. Padrões proativos via LLM (OpenAI) e o cache dos insights.

Estratégia dos padrões: backend agrega dados estruturados, manda pra o modelo com
prompt específico, recebe JSON validado, cacheia em `ai_proactive_cache`. Os insights
usam o mesmo cache, mas NÃO passam pelo LLM (D12 do PL-06): saem dos detectores de
`db/insights.py` e `core/services/insights_detectores.py`, com o texto deles.

Cache TTL por kind:
- `insights`: até 6h ou até a meia-noite de Brasília (estado financeiro muda rápido — orçamento, salário, vencimento)
- `patterns`: 24h (padrões comportamentais são estáveis)

Fallback: se LLM falha (sem API key, erro de rede, JSON inválido), os padrões
saem vazios (frontend mostra empty state).

Por que LLM e não heurística (nos padrões):
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
import threading
from typing import Any

from core.services.modelo_openai import CHAT_MODEL, completion_options
from utils_date import now_tz


logger = logging.getLogger(__name__)

MODEL = CHAT_MODEL
TEMPERATURE = 0.4  # ligeiramente acima do chat IA pra ter variedade nas narrativas

INSIGHTS_CACHE_TTL_SECONDS = 6 * 3600     # 6h
PATTERNS_CACHE_TTL_SECONDS = 24 * 3600    # 24h

# Bumpe quando mudar o prompt/payload dos padrões ou o CONTRATO dos itens de insights
# (`core/services/insights_contrato.py`). Cache antigo vira miss (kind diferente).
_PROMPT_VERSION_PATTERNS = 8
_PROMPT_VERSION_INSIGHTS = 12


def _ttl_insights() -> int:
    """6 h ou até a meia-noite de Brasília, o que vier antes (P4 do PL-06 B2): o `bill_due` muda de texto por dia."""
    agora = now_tz()
    return min(INSIGHTS_CACHE_TTL_SECONDS, int((agora - agora.replace(hour=0, minute=0, second=0, microsecond=0))
                                               .total_seconds()))


def _cache_kind(base: str) -> str:
    if base == "patterns":
        return f"patterns_v{_PROMPT_VERSION_PATTERNS}"
    if base == "insights":
        return f"insights_v{_PROMPT_VERSION_INSIGHTS}"
    return base


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
        compute_kpis,
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

    # Adiciona kpi highlights — peak_day e largest_expense — são fontes
    # ricas de padrão (ex: "Seu pior dia gastou R$ 800") que não exigem
    # aritmética nenhuma.
    try:
        fd, td = resolve_window(months=months_covered)
        kpis = compute_kpis(user_id, fd, td)
        out["highlights"] = {
            "peak_day": kpis.get("peak_day"),
            "largest_expense": kpis.get("largest_expense"),
            "total_income": kpis.get("total_income"),
            "total_expense": kpis.get("total_expense"),
            "savings_rate": kpis.get("savings_rate"),
        }
    except Exception as e:
        logger.warning("ai_patterns: kpi highlights falhou: %s", e)
        out["highlights"] = None

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

PATTERNS_SYSTEM_PROMPT = """\
Você é o Piggy 🐷, o assistente financeiro do PigBank. Sua tarefa: analisar
um JSON com métricas comportamentais agregadas de um usuário e identificar
**4 a 6 PADRÕES INTERESSANTES E VARIADOS**. Tente cobrir tipos diferentes
de padrão pra não ficar repetitivo — não force todos no mesmo molde.

Tom: amigo curioso e observador, anti-fricção, sem julgar. PT-BR coloquial.

Cada padrão é uma DESCOBERTA específica sobre o comportamento. Pode ser
QUANTITATIVO (com números) ou QUALITATIVO (só descrevendo o padrão).
Quando a amostra é pequena ou valores disparatados, prefira QUALITATIVO.

═══ TIPOS DE PADRÃO QUE VOCÊ PODE EXPLORAR ═══

1. **Concentração** — categoria/merchant/dia que domina o gasto.
   - "34% do seu gasto é em [categoria_top]" (use pct_of_total_spending)
   - "Você fez X visitas à [merchant] em 6 meses" (use frequency_per_month × 6)
   - "Seu maior gasto único foi R$ X em [largest_expense.alvo]"

2. **Periodicidade** — quando o gasto se concentra.
   - "84% do seu gasto é em dias úteis"
   - "Você tende a gastar mais à tarde do que de manhã" (qualitativo se low_sample)
   - "Sextas concentram boa parte do seu gasto"

3. **Comparação direta (Nx)** — SÓ se ambos lados têm sample bom.
   - "Você gasta 2,3x mais em [X] do que em [Y]"
   - Use o sanitizer mental: o número Nx tem que bater com os valores citados.

4. **Salary burn / ritmo** — relação com a renda.
   - "Você consome 80% da renda até o dia X" (use salary_burn)
   - "Sua taxa de poupança é Y%" (use highlights.savings_rate × 100)

5. **Pico** — momento extremo identificável.
   - "Seu pior dia foi [peak_day.date] com R$ X em gastos"
   - "Seu maior gasto: R$ X em [largest_expense.alvo]"

6. **Acionável (tone='tip')** — dica baseada em ticket médio ou avg/mês.
   - "Cortar 1 visita à [merchant] economiza ~R$ X" (avg_per_transaction)
   - "Eliminar gastos com [merchant] economiza R$ X/mês" (avg_per_month)

═══ EXEMPLOS DE TOM ═══

Quantitativos (dados robustos):
- "Você gasta 2,3x mais em iFood depois das 22h" (subtitle: "ticket médio R$ 67 vs R$ 29")
- "Sextas concentram 51% do seu lazer"
- "Você consome 80% da renda até o dia 12"

Qualitativos (dados frágeis — preferíveis a alucinar):
- "Você tende a gastar mais à tarde do que de manhã"
- "Seu padrão de consumo se concentra no fim de semana"
- "Quase todo seu gasto noturno vem de delivery"

Regra prática: SE não tem certeza do número, NÃO cite o número.
Qualitativo > quantitativo errado, mas pattern existente > silêncio.
NÃO seja tímido — tente entregar 4-6 padrões variados. Vazio é pior
que conservador.

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
            **completion_options(MODEL, None, TEMPERATURE),
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
            logger.warning("ai_patterns: resposta sem 'items': %d chars", len(content))
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
    from core.services.plan_service import plan_gate_ok
    if not plan_gate_ok(user_id, "insights"):
        return []
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
    """Insights do PigBank (contrato v1, `core/services/insights_contrato.py`). Cache: `_ttl_insights`.

    Gate de plano -> cache -> (miss) fatos dos detectores, com o texto deles -> cache completo -> projeção
    por plano. Sem LLM (D12 do PL-06); o nome ficou pelos chamadores (rota, agendador, card, testes).
    Detector em falha: os itens dos outros saem, mas o cache NÃO é gravado (uma lista incompleta não pode
    valer 6 h como "nada a apontar"); o próximo pedido recalcula.
    """
    from core.services.plan_service import plan_gate_ok
    if not plan_gate_ok(user_id, "insights"):
        return []
    kind = _cache_kind("insights")
    itens = None if force else _get_cached(user_id, kind, _ttl_insights())
    if itens is None:
        # Uma geração por usuário em voo: o cache só é gravado DEPOIS das consultas dos detectores, então pedidos
        # concorrentes (2 abas, StrictMode) repetiriam todas. Quem espera relê o cache que o primeiro gravou (com
        # detector em falha não há cache, e o próximo da fila recalcula).
        # ponytail: trava por processo; com mais de uma réplica cada uma pode gerar 1x. Advisory lock do Postgres
        # prenderia 1 conexão do pool por espera.
        with _INSIGHTS_LOCKS.setdefault(user_id, threading.Lock()):
            itens = None if force else _get_cached(user_id, kind, _ttl_insights())
            if itens is None:
                itens = _gerar_insights(user_id, kind)
    return _projetar(user_id, itens)


_INSIGHTS_LOCKS: dict[int, threading.Lock] = {}


def _gerar_insights(user_id: int, kind: str) -> list[dict]:
    """Os fatos dos detectores, carimbados; o cache só é gravado se nenhum detector falhou."""
    from db import compute_active_insights
    from db.insight_estado import registrar
    itens, falhas = compute_active_insights(user_id)
    agora = now_tz().isoformat(timespec="seconds")
    for it in itens:
        it["explicacao"]["calculado_em"] = agora
    registrar(user_id, itens)  # antes do cache: item em cache sem registro daria 404 no dispensar; fail-open
    if not falhas:
        _save_cache(user_id, kind, itens)
    return itens


def motivo_vazio(user_id: int) -> str | None:
    """Por que a lista PROJETADA saiu vazia (só se chama assim): `dispensados` se o cache tem itens (todos
    dispensados), senão `sem_dados`/`sem_destaques`. Sem cache (um detector falhou) = None: não é "nada a apontar"."""
    itens = _get_cached(user_id, _cache_kind("insights"), _ttl_insights())
    if itens is None:
        return None
    if itens:
        return "dispensados"
    from db.insights import motivo_sem_insights
    return motivo_sem_insights(user_id)


def ler_insights(user_id: int) -> list[dict] | None:
    """O cache (até 6 h ou até a meia-noite de Brasília) com a mesma projeção da rota, sem gerar (o card `piggy` do chat). None = sem cache.
    Quem chama já passou no gate `insights`."""
    itens = _get_cached(user_id, _cache_kind("insights"), _ttl_insights())
    return None if itens is None else _projetar(user_id, itens)


def _projetar(user_id: int, itens: list[dict]) -> list[dict]:
    # O plano e as dispensas valem na LEITURA: o cache guarda `impacto`/`acoes` e quem caiu do Pro dentro das 6 h
    # não os vê; dispensar não invalida o cache.
    from core.services.insights_contrato import projetar
    from core.services.plan_service import plan_gate_ok
    from db.insight_estado import dispensadas
    return projetar(itens, acoes_ok=plan_gate_ok(user_id, "insights_acoes"),
                    dispensadas=dispensadas(user_id, [it.get("key") for it in itens]))


# ─────────────────────────────────────────────────────────────────────────────
# Sanitização — proteção contra LLM teimoso com schema
# ─────────────────────────────────────────────────────────────────────────────

_ALLOWED_TONES = {"neutral", "warn", "tip"}


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
        if len(out) >= 8:
            break
    return out


__all__ = [
    "generate_ai_patterns",
    "generate_ai_insights",
    "ler_insights",
    "PATTERNS_CACHE_TTL_SECONDS",
    "INSIGHTS_CACHE_TTL_SECONDS",
]
