# core/handlers/launches.py
from __future__ import annotations
import logging
import os
import re
from datetime import date, timedelta

import db
from utils_text import (
    fmt_brl, is_internal_category, canonicalize_category_label, normalize_text,
    merchant_key, RECURRING_SUGGESTION_BLOCKLIST,
)
from utils_date import extract_date_from_text, today_tz, parse_period_from_text, month_range_today
from core.services.category_service import infer_category, learn_from_inference
from parsers import (
    parse_receita_despesa_natural,
    split_financial_transactions,
    describe_valueless_launch,
    _extract_valor,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers de data
# ---------------------------------------------------------------------------

def _parse_date_entity(entities: dict, original_text: str) -> date | None:
    """
    Tenta obter uma data de:
      1. entities["date_filter"] — pode ser ISO "2026-04-03", "hoje", "ontem" ou "dia 4"
      2. texto original via extract_date_from_text
    Retorna um objeto date ou None.
    """
    raw = entities.get("date_filter")
    if raw:
        raw_s = str(raw).strip().lower()

        # palavras especiais
        today = today_tz()
        if raw_s == "hoje":
            return today
        if raw_s == "ontem":
            return today - timedelta(days=1)

        # ISO direto
        try:
            return date.fromisoformat(raw_s)
        except ValueError:
            pass

        # tenta extrair do valor em si ("dia 4", "03/04", etc.)
        dt, _ = extract_date_from_text(raw_s)
        if dt:
            return dt.date()

    # fallback: extrai do texto original
    dt, _ = extract_date_from_text(original_text)
    if dt:
        return dt.date()

    return None


def _fmt_date_label(d: date) -> str:
    today = today_tz()
    if d == today:
        return "hoje"
    if d == today - timedelta(days=1):
        return "ontem"
    return d.strftime("%d/%m/%Y")


# ---------------------------------------------------------------------------
# list_launches — com suporte a filtro de data
# ---------------------------------------------------------------------------

# Tipos que são ações internas de gerenciamento (não movimentações financeiras)
# Esses registros existem na tabela launches para fins de rollback/auditoria,
# mas não devem aparecer na listagem do usuário.
_INTERNAL_TIPOS = {
    "criar_caixinha", "delete_pocket",
    "create_investment", "delete_investment",
}


def list_launches(user_id: int, limit: int = 10, entities: dict | None = None, original_text: str = "") -> str:
    entities = entities or {}

    target_date = _parse_date_entity(entities, original_text)

    if target_date:
        # busca por dia específico
        rows = db.get_launches_by_period(user_id, target_date, target_date)
        label = _fmt_date_label(target_date)

        # filtra tipos internos de gerenciamento
        rows = [r for r in rows if r.get("tipo") not in _INTERNAL_TIPOS]

        if not rows:
            return f"Nenhum lançamento encontrado em **{label}**."

        # calcula totais
        total_despesas = sum(float(r["valor"]) for r in rows if r.get("tipo") == "despesa")
        total_receitas = sum(float(r["valor"]) for r in rows if r.get("tipo") == "receita")

        lines = []
        for r in rows:
            tipo   = r.get("tipo", "")
            valor  = fmt_brl(float(r["valor"])) if r.get("valor") is not None else "-"
            nota   = r.get("nota") or r.get("alvo") or "-"
            cat    = r.get("categoria") or ""
            cat_txt = f" [{cat}]" if cat else ""
            lines.append(f"#{r.get('user_seq') or r['id']} • {tipo} • {valor} • {nota}{cat_txt}")

        header = f"🧾 **Lançamentos de {label}**"
        summary_parts = []
        if total_despesas > 0:
            summary_parts.append(f"💸 Gastos: {fmt_brl(total_despesas)}")
        if total_receitas > 0:
            summary_parts.append(f"💰 Receitas: {fmt_brl(total_receitas)}")
        summary = "\n".join(summary_parts)

        return f"{header}:\n" + "\n".join(lines) + (f"\n\n{summary}" if summary else "")

    # sem filtro de data → últimos N lançamentos (busca mais para compensar os internos filtrados)
    rows = db.list_launches(user_id, limit=limit + 20)
    rows = [r for r in rows if r.get("tipo") not in _INTERNAL_TIPOS][:limit]
    if not rows:
        return "Você ainda não tem lançamentos."

    today = today_tz()

    _TIPO_EMOJI = {
        "despesa":              "💸",
        "receita":              "💰",
        "entrada":              "💰",
        "saida":                "💸",
        "aporte_investimento":  "📈",
        "resgate_investimento": "📉",
        "create_investment":    "📈",
        "transferencia":        "↔️",
    }

    lines = []
    for r in rows:
        tipo   = r.get("tipo", "")
        valor  = r.get("valor")
        alvo   = (r.get("alvo") or "").strip()
        nota   = (r.get("nota") or "").strip()
        criado = r.get("criado_em")

        # limpa nota técnica de investimento
        if tipo in ("create_investment", "aporte_investimento") and nota and "taxa=" in nota:
            try:
                m_taxa = re.search(r"taxa=([0-9.]+)", nota)
                m_per  = re.search(r"periodo=(\w+)", nota)
                taxa   = float(m_taxa.group(1)) * 100 if m_taxa else None
                per    = m_per.group(1) if m_per else ""
                per    = "ao mês" if per.startswith("month") else "ao dia" if per.startswith("day") else per
                nota   = f"{taxa:.4g}% {per}" if taxa is not None else nota
            except Exception:
                pass

        # descrição: prefere nota se informativa, senão usa alvo
        descricao = nota if nota and nota.lower() not in ("-", alvo.lower()) else alvo
        if not descricao:
            descricao = tipo

        # formata data de forma amigável
        if criado is not None:
            try:
                d = criado.date() if hasattr(criado, "date") else __import__("datetime").datetime.fromisoformat(str(criado)).date()
                if d == today:
                    data_str = "hoje"
                elif d == today - timedelta(days=1):
                    data_str = "ontem"
                else:
                    data_str = d.strftime("%d/%m")
            except Exception:
                data_str = str(criado)[:10]
        else:
            data_str = "-"

        emoji     = _TIPO_EMOJI.get(tipo, "•")
        valor_str = fmt_brl(float(valor)) if valor is not None else "-"
        # Mostra user_seq (numeração por usuário, começa em #1) em vez do
        # id global. Fallback pro id interno enquanto o backfill não rodou.
        display_id = r.get("user_seq") or r.get("id")
        id_str    = f" [#{display_id}]" if display_id else ""
        lines.append(f"{emoji} {data_str} • {valor_str} • {descricao}{id_str}")

    # mini resumo de despesas/receitas no período exibido
    total_despesas = sum(
        float(r["valor"])
        for r in rows
        if r.get("tipo") in ("despesa", "saida") and not r.get("is_internal_movement")
    )
    total_receitas = sum(
        float(r["valor"])
        for r in rows
        if r.get("tipo") in ("receita", "entrada") and not r.get("is_internal_movement")
    )

    summary_parts = []
    if total_despesas > 0:
        summary_parts.append(f"💸 Gastos: {fmt_brl(total_despesas)}")
    if total_receitas > 0:
        summary_parts.append(f"💰 Receitas: {fmt_brl(total_receitas)}")
    summary = "  |  ".join(summary_parts)

    header = f"🧾 **Últimos {len(rows)} lançamentos**:"
    body   = "\n".join(lines)
    return f"{header}\n{body}" + (f"\n\n{summary}" if summary else "")


# ---------------------------------------------------------------------------
# spend_query — "quanto gastei [na categoria X] [período]"
# ---------------------------------------------------------------------------

# Palavras de período/conectores que NÃO fazem parte de um nome de categoria.
# Usadas pra limpar o rabo capturado depois de "categoria"/"com"/"no".
_CAT_STOP_WORDS = {
    "hoje", "ontem", "anteontem", "semana", "mes", "ano", "passado", "passada",
    "ultimo", "ultima", "ultimos", "ultimas", "dias", "dia", "essa", "esta",
    "esse", "este", "nessa", "nesta", "nesse", "neste", "dessa", "desta",
    "desse", "deste", "no", "na", "do", "da", "de", "em", "dos", "das", "nos",
    "nas", "recentes", "recente", "total", "gastei", "gastou", "gasto", "gasta",
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
    "agosto", "setembro", "outubro", "novembro", "dezembro",
}


def _extract_query_category(text: str) -> str | None:
    """Extrai o nome da categoria de uma pergunta de gasto.

    Reconhece "na categoria X", "categoria X" e, como fallback, "com/no/na X".
    Remove palavras de período ("esta semana", "julho", ...) do rabo capturado.
    Retorna o rótulo canônico da categoria ou None se não houver categoria.
    """
    norm = normalize_text(text)  # sem acento, minúsculo

    m = re.search(r"\bcategoria\s+(?:de\s+)?(.+)$", norm)
    if not m:
        # "gastei com/em/no/na X". Palavras de período ("em julho", "no mes")
        # caem no filtro _CAT_STOP_WORDS abaixo, então não viram categoria.
        m = re.search(r"\b(?:com|em|no|na)\s+(.+)$", norm)
    if not m:
        return None

    toks = [
        tk for tk in m.group(1).split()
        if tk not in _CAT_STOP_WORDS and not tk.isdigit()
    ]
    cat = " ".join(toks).strip()
    if not cat:
        return None
    return canonicalize_category_label(cat) or cat


def spend_query(user_id: int, text: str, entities: dict | None = None) -> str:
    """Responde "quanto gastei [na categoria X] [período]" com o total gasto.

    - Interpreta o período em linguagem natural (esta semana, este mês, julho,
      últimos 7 dias, ontem, ...). Sem período reconhecido → mês corrente.
    - Com categoria → total daquela categoria (launches + cartão).
    - Sem categoria → total de despesas no período + top categorias.
    """
    period = parse_period_from_text(text)
    if period:
        start, end, period_label = period
    else:
        # sem período reconhecido → mês corrente cheio (igual ao dashboard)
        start, end = month_range_today()
        period_label = "neste mês"

    categoria = _extract_query_category(text)

    if categoria:
        total = db.sum_spent_in_category_period(user_id, categoria, start, end)
        label = canonicalize_category_label(categoria) or categoria
        if total <= 0:
            return f"🐷 Você não teve gastos em **{label}** {period_label}."

        lines = [f"💸 Você gastou **{fmt_brl(total)}** em **{label}** {period_label}."]

        # top 5 maiores lançamentos dessa categoria no período (mesma regra do
        # dashboard: cartão pelo mês da fatura) → lista bate com o total acima
        maiores = db.get_largest_expenses(
            user_id, start, end, limit=5, categoria=categoria, by_bill_month=True
        )
        if maiores:
            lines.append("")
            lines.append("🔝 Maiores gastos:")
            for m in maiores:
                desc = (m.get("descricao") or "").strip() or "—"
                lines.append(f"• {fmt_brl(m['valor'])} • {desc}")
        return "\n".join(lines)

    # sem categoria → total geral + top categorias do período.
    # O total vem da MESMA fonte das categorias (launches + cartão por mês da
    # fatura), então bate com o "gastos do mês" do dashboard e o total é sempre
    # igual à soma das categorias.
    cats = db.get_top_expense_categories(user_id, start, end, limit=1000, by_bill_month=True)
    total = sum(c["total"] for c in cats)
    if total <= 0:
        return f"🐷 Você não teve gastos {period_label}."

    lines = [f"💸 Você gastou **{fmt_brl(total)}** {period_label}."]
    tops = cats[:3]
    lines.append("")
    lines.append("📊 Top categorias:")
    for t in tops:
        lines.append(f"• {t['categoria']}: {fmt_brl(t['total'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# add / add_from_entities — registra receita/despesa
# ---------------------------------------------------------------------------

def _recurring_suggestion_enabled() -> bool:
    """Flag de kill-switch. Ligada por padrão; desliga só com env explícito."""
    return (os.getenv("AUTO_FIX_SUGGESTION_ENABLED") or "on").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _maybe_recurring_offer(
    user_id: int, descr: str, valor: float, categoria_final: str, criado_em, launch_id,
) -> dict | None:
    """Se esta despesa repete uma anterior (mesma descrição + mesmo valor, em mês
    distinto) numa categoria elegível, devolve o payload da oferta de gasto fixo.
    Senão, None. Nunca levanta — detecção é best-effort e não pode quebrar o
    lançamento."""
    if not _recurring_suggestion_enabled():
        return None
    try:
        cat_canon = canonicalize_category_label(categoria_final) or categoria_final
        if cat_canon in RECURRING_SUGGESTION_BLOCKLIST:
            return None
        key = merchant_key(descr)
        if not key:
            return None
        when = criado_em if isinstance(criado_em, (date,)) else None
        if when is None:
            from datetime import datetime as _dt
            when = criado_em if isinstance(criado_em, _dt) else today_tz()
        from db.recurring import find_recurring_candidate
        prior_months = find_recurring_candidate(
            user_id, key, valor,
            current_year=when.year, current_month=when.month,
            exclude_launch_id=int(launch_id) if launch_id else None,
        )
        if prior_months < 1:
            return None
        name = (descr or "").strip() or (cat_canon.capitalize() if cat_canon else "Gasto fixo")
        return {
            "name": name,
            "amount": float(valor),
            "category": categoria_final,
            "due_day": int(when.day),
            "merchant_key": key,
        }
    except Exception:
        logger.warning(
            "detecção de gasto fixo falhou (user %s, descr=%r) — seguindo sem oferta",
            user_id, descr, exc_info=True,
        )
        return None


def add_from_entities(
    user_id: int,
    *,
    tipo: str,
    valor: float,
    alvo: str | None = None,
    nota: str | None = None,
    categoria: str | None = None,
    category_reason: str | None = None,
    criado_em=None,
    is_internal: bool | None = None,
    platform: str = "whatsapp",
) -> str:
    """Registra um lançamento a partir de args já estruturados (sem regex).

    Chamado por:
      - `add()` quando o parser regex já extraiu (ou caiu nos entities)
      - tool de IA `add_launch` (LLM extrai os args)

    Toda lógica compartilhada (categorização, learn, DB write, botão WhatsApp,
    alerta de orçamento) vive aqui — fonte única de verdade.
    """
    if valor <= 0:
        return "Não consegui identificar o valor. Tente: *gastei 50 no mercado*"

    # Teto mensal de lançamentos do tier (Grátis no v2; no-op com v2 off).
    # PlanLimitExceeded sobe pro handle_incoming, que responde com a mensagem
    # amigável de upgrade — mesmo padrão das caixinhas/cartões.
    from core.services.plan_service import check_can_create_launch
    check_can_create_launch(user_id)

    alvo_clean = (alvo or "").strip()
    nota_clean = (nota or "").strip() or alvo_clean

    if categoria:
        reason_final = category_reason or "explicit"
        if reason_final == "ai":
            # Categoria veio do LLM (entities do classificador ou tool add_launch),
            # não de hashtag explícita nem do parser determinístico. O LLM erra
            # categoria de vez em quando (ex.: áudio "gastei 500 no mercado" indo
            # pra "alimentação"). Faz cross-check com as regras determinísticas:
            # um match confiante de regra do usuário / ticker / LOCAL_RULES que
            # CONTRADIZ a IA vence. allow_ai=False pra não gastar 2ª chamada de LLM.
            categoria_ai = canonicalize_category_label(categoria) or categoria
            local = infer_category(user_id, nota_clean, None, allow_ai=False)
            if local.reason in {"user_rule", "user_category", "ticker_match", "local_rule"} and local.category != categoria_ai:
                logger.info(
                    "categoria da IA (%s) sobreposta por regra local (%s via %s) — nota=%r",
                    categoria_ai, local.category, local.reason, nota_clean,
                )
                categoria_final = local.category
                reason_final = local.reason
            else:
                categoria_final = categoria_ai
        else:
            categoria_final = categoria
    else:
        res = infer_category(user_id, nota_clean, None)
        categoria_final = res.category or "outros"
        reason_final = res.reason

    is_int = (
        is_internal if is_internal is not None
        else is_internal_category(categoria_final)
    )

    launch_id, user_seq, new_balance = db.add_launch_and_update_balance(
        user_id=user_id,
        tipo=tipo,
        valor=valor,
        alvo=alvo_clean or None,
        nota=nota_clean,
        categoria=categoria_final,
        criado_em=criado_em,
        is_internal_movement=is_int,
    )

    learn_from_inference(
        user_id,
        nota_clean,
        categoria_final,
        target_hint=alvo_clean,
        reason=reason_final,
    )

    # Reconciliação reversa (Open Finance): se o banco já importou esse gasto, funde
    # com o lançamento que o usuário acabou de fazer — não duplica no "sobrou".
    if not is_int:
        try:
            db.reconcile_manual_launch(user_id, launch_id)
        except Exception:
            pass

    # Detecção "essa despesa se repete → sugere gasto fixo". Só para despesa
    # real (não movimentação interna). A oferta divide a linha de pending_actions
    # com o botão de recategorizar; quando há oferta de recorrente ela vence
    # (é mais valiosa) e o botão de recategorizar é suprimido nesse lançamento.
    recurring_offer = None
    if tipo == "despesa" and not is_int:
        recurring_offer = _maybe_recurring_offer(
            user_id, nota_clean, valor, categoria_final, criado_em, launch_id,
        )

    if recurring_offer:
        try:
            db.set_pending_action(user_id, "confirm_recurring_offer", recurring_offer)
        except Exception:
            logger.warning(
                "falha ao salvar pending confirm_recurring_offer (user %s) — oferta perdida",
                user_id, exc_info=True,
            )
            recurring_offer = None
    # Botão "categoria errada?" no WhatsApp (one-shot, lido por
    # _send_reply_with_optional_buttons no wa_runtime e limpo em seguida).
    elif platform == "whatsapp" and launch_id:
        try:
            db.set_pending_action(
                user_id,
                "recategorize_launch_offer",
                {"launch_id": int(launch_id), "user_seq": int(user_seq)},
            )
        except Exception:
            logger.warning(
                "falha ao salvar pending recategorize_launch_offer (user %s, launch %s) — botão de recategorizar não vai aparecer",
                user_id, launch_id, exc_info=True,
            )

    emoji = "💸" if tipo == "despesa" else "💰"
    resposta = (
        f"{emoji} **{tipo.capitalize()} registrada**: {fmt_brl(valor)}\n"
        f"🏷️ Categoria: {categoria_final}\n"
        f"🏦 Saldo: {fmt_brl(float(new_balance))}\n"
        f"ID: #{user_seq}"
    )

    if tipo == "despesa" and not is_int and categoria_final:
        try:
            from datetime import datetime
            from core.budget_alerts import evaluate_after_expense, format_alert_text
            when = criado_em if isinstance(criado_em, datetime) else datetime.now()
            alert = evaluate_after_expense(user_id, categoria_final, valor, when)
            if alert:
                resposta += format_alert_text(alert)
        except Exception:
            logger.warning(
                "avaliação de alerta de orçamento falhou (user %s, categoria %r) — alerta pode ter sido perdido",
                user_id, categoria_final, exc_info=True,
            )

    if recurring_offer:
        resposta += (
            f"\n\n💡 Você já lançou *{recurring_offer['name']}* de {fmt_brl(valor)} "
            f"em outro mês. Quer marcar como *gasto fixo* (a Piggy lança sozinha "
            f"todo mês)? Responda *sim* ou *não*."
        )

    # Nudge de onboarding: no primeiríssimo lançamento do usuário no WhatsApp,
    # aponta o próximo passo (aprender fazendo). Dispara UMA vez — a trava é o
    # evento em system_event_logs; user_seq == 1 é só o pré-filtro barato pra não
    # consultar o banco a cada lançamento (e o evento ainda cobre o caso de o
    # user_seq voltar a 1 depois de um "apagar tudo"). Só pra lançamento real
    # (não movimentação interna) e quando não há oferta de recorrente competindo
    # pela atenção.
    if platform == "whatsapp" and not is_int and not recurring_offer and user_seq == 1:
        try:
            from core.observability import recent_event_exists, log_system_event_sync
            if not recent_event_exists("onboarding_first_launch_nudge", user_id, within_days=36500):
                resposta += (
                    "\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\n"
                    "Agora tenta **saldo**. Quando quiser, tem caixinhas, cartões e "
                    "dashboard: é só mandar **ajuda**."
                )
                log_system_event_sync(
                    "info",
                    "onboarding_first_launch_nudge",
                    "Nudge de primeiro lançamento enviado no WhatsApp.",
                    source="launches",
                    user_id=user_id,
                )
        except Exception:
            logger.warning(
                "nudge de primeiro lançamento falhou (user %s) — seguindo sem ele",
                user_id, exc_info=True,
            )

    return resposta


def _ask_value_question(item: dict) -> str:
    """Pergunta amigável pelo valor de um lançamento que veio sem número."""
    desc = item.get("desc") or "esse lançamento"
    if item.get("tipo") == "receita":
        return f"🐷 Faltou o valor de *{desc}*. Quanto você recebeu? (só o número)"
    return f"🐷 Faltou o valor de *{desc}*. Quanto foi? (só o número)"


# Quantas vezes o MESMO valor precisa ter aparecido antes (pro mesmo tipo/descrição)
# pra ser considerado recorrente e lançado sozinho, sem perguntar.
_RECURRING_MIN_COUNT = 2


def infer_recurring_value(user_id: int, tipo: str, desc: str) -> float | None:
    """Descobre o valor recorrente de uma descrição ("aluguel", "internet"...).

    Retorna o valor SÓ quando a mesma descrição (mesmo tipo) já foi lançada com
    o MESMO valor em `_RECURRING_MIN_COUNT`+ lançamentos anteriores E esse valor
    é o dominante (sem empate). Caso contrário retorna None — o bot pergunta.

    Conservador de propósito: preencher sozinho um valor errado é pior do que
    perguntar. A comparação de descrição é por texto normalizado (minúsculas,
    sem acento), batendo tanto em `nota` quanto em `alvo` do histórico.
    """
    from collections import Counter
    from utils_text import normalize_text

    target = normalize_text(desc or "").strip()
    if not target:
        return None

    counter: Counter = Counter()
    for r in db.list_launches_by_tipo(user_id, tipo, limit=200):
        nota = normalize_text(r.get("nota") or "").strip()
        alvo = normalize_text(r.get("alvo") or "").strip()
        if target in (nota, alvo):
            try:
                counter[float(r["valor"])] += 1
            except (TypeError, ValueError):
                continue

    if not counter:
        return None
    ranked = counter.most_common(2)
    top_valor, top_freq = ranked[0]
    if top_freq < _RECURRING_MIN_COUNT or top_valor <= 0:
        return None
    # empate no topo (dois valores igualmente frequentes) → ambíguo, pergunta.
    if len(ranked) > 1 and ranked[1][1] == top_freq:
        return None
    return top_valor


_RECURRING_NOTICE = "🔁 _Usei seu valor recorrente. Se mudou, é só corrigir._"


def register_if_recurring(user_id: int, tipo: str, desc: str, platform: str) -> str | None:
    """Se `desc` tem um valor recorrente conhecido, registra o lançamento na hora
    (com aviso) e devolve a resposta. Senão, retorna None (o bot vai perguntar)."""
    valor = infer_recurring_value(user_id, tipo, desc)
    if valor is None:
        return None
    resp = add_from_entities(
        user_id,
        tipo=tipo,
        valor=float(valor),
        alvo=desc,
        nota=desc,
        platform=platform,
    )
    return f"{resp}\n{_RECURRING_NOTICE}"


# Palavras que cancelam a pergunta de valor pendente.
_CANCEL_WORDS = {"nao", "n", "cancelar", "cancela", "deixa", "esquece", "esquecer", "para", "pare"}


def resolve_multi_launch_value(user_id: int, text: str, pending: dict, platform: str = "whatsapp") -> str | None:
    """
    Resolve a pergunta de valor pendente de um lançamento múltiplo. O bot havia
    perguntado "quanto foi *aluguel*?"; esta resposta traz o valor.

    - resposta com valor → registra o item da frente da fila; se sobra fila,
      pergunta o próximo; senão encerra.
    - resposta de cancelamento → descarta o que faltava.
    - resposta sem valor (o user mudou de assunto) → abandona a pendência e
      retorna None pra que o roteador processe a mensagem normalmente.
    """
    from utils_text import normalize_text

    payload = pending.get("payload", {})
    queue: list[dict] = list(payload.get("queue") or [])
    if not queue:
        db.clear_pending_action(user_id)
        return None

    resp_norm = normalize_text(text).strip()
    if resp_norm in _CANCEL_WORDS:
        db.clear_pending_action(user_id)
        restantes = ", ".join(i.get("desc", "?") for i in queue)
        return f"❌ Beleza, deixei de lado: {restantes}."

    valor = _extract_valor(text)
    if valor is None or valor <= 0:
        # Não é um valor — o usuário mudou de assunto. Abandona a pendência e
        # deixa o roteador tratar a mensagem como um comando novo.
        db.clear_pending_action(user_id)
        return None

    head = queue.pop(0)
    resp = add_from_entities(
        user_id,
        tipo=head.get("tipo", "despesa"),
        valor=float(valor),
        alvo=head.get("desc"),
        nota=head.get("desc"),
        platform=platform,
    )

    if queue:
        db.set_pending_action(
            user_id, "multi_launch_values",
            {"queue": queue, "platform": platform},
        )
        return f"{resp}\n\n{_ask_value_question(queue[0])}"
    # fila vazia: não re-arma multi_launch_values. O add_from_entities acima já
    # gravou o pending de "categoria errada?" (WhatsApp), que fica valendo.
    return resp


def _register_parsed(user_id: int, parsed: dict, fallback_note: str, platform: str) -> str:
    """Registra um lançamento já parseado por `parse_receita_despesa_natural`."""
    return add_from_entities(
        user_id,
        tipo=parsed["tipo"],
        valor=float(parsed["valor"]),
        alvo=parsed.get("alvo") or "",
        nota=parsed.get("nota") or fallback_note,
        categoria=parsed.get("categoria") or "outros",
        category_reason=parsed.get("category_reason"),
        criado_em=parsed.get("criado_em"),
        is_internal=parsed.get("is_internal_movement", False),
        platform=platform,
    )


def add(user_id: int, text: str, entities: dict, platform: str = "whatsapp") -> str:
    from core.handlers import credit as h_credit

    credit_response = h_credit.try_handle_natural_credit_purchase(user_id, text)
    if credit_response is not None:
        return credit_response

    # Múltiplos lançamentos na mesma mensagem ("gastei 500 no ifood e mais 800
    # no mercado") — separa e registra cada um. split_financial_transactions só
    # devolve >1 item quando detecta de fato mais de um lançamento. Pedaços com
    # verbo mas SEM valor ("... e paguei o aluguel") viram pergunta: o bot
    # registra o que deu, enfileira os que faltam valor e pergunta um a um.
    parts = split_financial_transactions(text)
    if len(parts) > 1:
        responses = []
        missing: list[dict] = []
        for part in parts:
            p = parse_receita_despesa_natural(user_id, part)
            if p:
                responses.append(_register_parsed(user_id, p, part, platform))
                continue
            info = describe_valueless_launch(part)
            if info:
                tipo, desc = info
                # Valor recorrente conhecido ("aluguel" que sempre é o mesmo) →
                # lança sozinho. Senão, enfileira pra perguntar.
                auto = register_if_recurring(user_id, tipo, desc, platform)
                if auto is not None:
                    responses.append(auto)
                else:
                    missing.append({"tipo": tipo, "desc": desc})
        if missing:
            db.set_pending_action(
                user_id, "multi_launch_values",
                {"queue": missing, "platform": platform},
            )
            question = _ask_value_question(missing[0])
            return "\n\n".join(responses + [question]) if responses else question
        if responses:
            return "\n\n".join(responses)
        # nenhum pedaço virou lançamento válido — cai no fluxo single abaixo

    parsed = parse_receita_despesa_natural(user_id, text)

    if parsed:
        return _register_parsed(user_id, parsed, text, platform)

    tipo = entities.get("tipo", "despesa")
    valor = float(entities.get("valor", 0))
    return add_from_entities(
        user_id,
        tipo=tipo,
        valor=valor,
        alvo=entities.get("alvo") or "",
        nota=text,
        categoria=entities.get("categoria"),
        category_reason="ai",
        criado_em=None,
        platform=platform,
    )


# ---------------------------------------------------------------------------
# propose_delete / undo
# ---------------------------------------------------------------------------

def propose_delete(user_id: int, launch_id: int) -> str:
    """Propõe apagar um lançamento. `launch_id` é o id interno (PK).

    O display usa o `user_seq` desse lançamento; se não conseguir resolver,
    cai pro id interno.
    """
    display_id = launch_id
    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select user_seq from launches where id=%s and user_id=%s",
                    (launch_id, user_id),
                )
                row = cur.fetchone()
                if row and row.get("user_seq"):
                    display_id = int(row["user_seq"])
    except Exception:
        pass
    db.set_pending_action(
        user_id,
        "delete_launch",
        {"launch_id": int(launch_id), "display_id": int(display_id)},
    )
    return (
        f"⚠️ Isso vai apagar o lançamento **#{display_id}** e desfazer seus efeitos no saldo.\n"
        "Confirma? Responda **sim** ou **não**."
    )


def undo(user_id: int) -> str:
    # Último lançamento CRIADO (maior id), não o mais recente por data: "desfazer"
    # deve remover o que o usuário acabou de lançar, mesmo que seja retroativo
    # ("gastei 50 ontem" cria com criado_em no passado — list_launches ordena por
    # data e miraria o lançamento de hoje, apagando o errado).
    row = db.get_last_inserted_launch(user_id)
    if not row:
        return "Não há lançamentos para desfazer."
    last_id = int(row["id"])
    display_id = int(row.get("user_seq") or last_id)
    db.set_pending_action(
        user_id,
        "delete_launch",
        {"launch_id": last_id, "display_id": display_id},
    )
    tipo  = row.get("tipo", "")
    valor = fmt_brl(float(row.get("valor") or 0))
    return (
        f"⚠️ Desfazer o último lançamento: **#{display_id}** ({tipo} {valor})?\n"
        "Confirma? Responda **sim** ou **não**."
    )
