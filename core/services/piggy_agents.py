"""Agentes do Piggy — detectores e runner (Fase A: Xerife, Repórter, Carteiro).

Arquitetura (espelha recurring_charger.py):
  - detectores são funções SÍNCRONAS `run_<kind>_once(today)` chamadas do
    loop async via asyncio.to_thread;
  - idempotência vem do banco: agent_events tem UNIQUE(agent_id, dedupe_key),
    então rodar um detector N vezes no mesmo dia não duplica alerta;
  - o loop roda de hora em hora; cada detector decide sozinho se tem algo
    a fazer naquele tick (ex.: Repórter só age nos primeiros dias do mês).

Gatilhos:
  1. cron horário (run_agents_loop, registrado no lifespan);
  2. pós-sync Open Finance (run_agents_for_user, chamado de pluggy_sync) —
     roda só o Xerife, só pro usuário sincado.

Canais na Fase A: feed do dashboard (sempre) + e-mail do Repórter (manchete
mensal, canal principal dele). WhatsApp fica pra quando os templates Meta
forem aprovados — mesmo padrão dormente do lembrete de boletos.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from typing import Any

from db.connection import get_conn

# ── Config ───────────────────────────────────────────────────────────────────

# Desliga o subsistema inteiro sem deploy (mesmo espírito de RUN_BACKGROUND_TASKS).
AGENTS_ENABLED = os.getenv("AGENTS_ENABLED", "1") != "0"
AGENTS_INTERVAL_SEC = int(os.getenv("AGENTS_INTERVAL_SEC", str(60 * 60)))

# Xerife: defaults dos thresholds (config do agente pode sobrescrever).
XERIFE_MULTIPLIER = 2.5     # gasto único > N× a média da categoria
XERIFE_MIN_VALOR = 50.0     # ignora anomalia abaixo disso (ruído)
XERIFE_MIN_SAMPLES = 5      # mínimo de lançamentos no histórico da categoria

MESES_PT = [
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def _fmt_brl(v: float) -> str:
    s = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


# ── Xerife: anomalia de gasto + limite de categoria ──────────────────────────

def _xerife_detect_for_user(agent: dict[str, Any], today: date) -> int:
    """Detecta pro usuário do agente; retorna quantos eventos novos gravou."""
    from db import record_agent_event

    cfg = agent.get("config") or {}
    mult = float(cfg.get("multiplicador") or XERIFE_MULTIPLIER)
    minimo = float(cfg.get("minimo") or XERIFE_MIN_VALOR)
    user_id = agent["user_id"]
    fired = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Anomalia: lançamento das últimas 24h muito acima da média (90d)
            # da própria categoria. Histórico exclui as últimas 24h pra o
            # próprio gasto anômalo não puxar a média.
            cur.execute(
                """
                with hist as (
                  select lower(categoria) as cat, avg(valor) as media, count(*) as n
                  from launches
                  where user_id = %s
                    and tipo in ('despesa', 'saida')
                    and is_internal_movement = false
                    and categoria is not null
                    and criado_em >= now() - interval '90 days'
                    and criado_em <  now() - interval '24 hours'
                  group by 1
                )
                select l.id, l.valor, l.categoria, coalesce(l.alvo, l.nota, '') as descricao,
                       h.media
                from launches l
                join hist h on lower(l.categoria) = h.cat
                where l.user_id = %s
                  and l.tipo in ('despesa', 'saida')
                  and l.is_internal_movement = false
                  and l.criado_em >= now() - interval '24 hours'
                  and h.n >= %s
                  and l.valor >= %s
                  and l.valor > %s * h.media
                """,
                (user_id, user_id, XERIFE_MIN_SAMPLES, minimo, mult),
            )
            anomalias = cur.fetchall() or []

            # Limite mensal por categoria (config: {"limites": {"delivery": 200}}).
            limites: dict[str, Any] = cfg.get("limites") or {}
            estouros: list[dict[str, Any]] = []
            if limites:
                cur.execute(
                    """
                    select lower(categoria) as cat, sum(valor) as total
                    from launches
                    where user_id = %s
                      and tipo in ('despesa', 'saida')
                      and is_internal_movement = false
                      and categoria is not null
                      and criado_em >= date_trunc('month', now())
                    group by 1
                    """,
                    (user_id,),
                )
                gastos = {r["cat"]: float(r["total"]) for r in (cur.fetchall() or [])}
                for cat, limite in limites.items():
                    try:
                        limite_f = float(limite)
                    except (TypeError, ValueError):
                        continue
                    total = gastos.get(cat.lower(), 0.0)
                    if limite_f > 0 and total > limite_f:
                        estouros.append({"cat": cat.lower(), "total": total, "limite": limite_f})

    for a in anomalias:
        media = float(a["media"])
        valor = float(a["valor"])
        ok = record_agent_event(
            agent["agent_id"], user_id, "xerife",
            dedupe_key=f"anomalia:{a['id']}",
            payload={
                "tipo": "anomalia",
                "launch_id": a["id"],
                "categoria": a["categoria"],
                "descricao": (a["descricao"] or "")[:120],
                "valor": valor,
                "media": round(media, 2),
                "titulo": f"Gasto fora do padrão em {a['categoria']}",
                "mensagem": (
                    f"{_fmt_brl(valor)} em {a['categoria']}"
                    f"{' (' + a['descricao'] + ')' if a['descricao'] else ''} — "
                    f"seu normal na categoria é {_fmt_brl(media)}. Tá tudo certo?"
                ),
            },
        )
        fired += 1 if ok else 0

    ym = today.strftime("%Y-%m")
    for e in estouros:
        ok = record_agent_event(
            agent["agent_id"], user_id, "xerife",
            dedupe_key=f"limite:{e['cat']}:{ym}",
            payload={
                "tipo": "limite",
                "categoria": e["cat"],
                "total": round(e["total"], 2),
                "limite": e["limite"],
                "titulo": f"Limite de {e['cat']} estourado",
                "mensagem": (
                    f"Você combinou {_fmt_brl(e['limite'])}/mês em {e['cat']} "
                    f"e já foi {_fmt_brl(e['total'])}. Seguro a onda com você?"
                ),
            },
            valor_impacto=None,
        )
        fired += 1 if ok else 0
    return fired


def run_xerife_once(today: date | None = None, user_id: int | None = None) -> dict:
    """Roda o Xerife pra todos os agentes ativos (ou só um usuário)."""
    from db import list_users_with_active_agents

    today = today or date.today()
    agents = list_users_with_active_agents("xerife")
    if user_id is not None:
        agents = [a for a in agents if a["user_id"] == user_id]
    fired = 0
    for agent in agents:
        try:
            fired += _xerife_detect_for_user(agent, today)
        except Exception as exc:
            print(f"[agents] xerife user={agent['user_id']}: {exc}", file=sys.stderr)
    return {"ok": True, "agents": len(agents), "fired": fired}


# ── Repórter: a manchete do mês ──────────────────────────────────────────────

def _month_stats(cur, user_id: int, first: date, nxt: date) -> dict[str, float]:
    cur.execute(
        """
        select
          coalesce(sum(valor) filter (where tipo = 'receita'
                                        and is_internal_movement = false), 0)      as entrou,
          coalesce(sum(valor) filter (where tipo in ('despesa', 'saida')
                                        and is_internal_movement = false), 0)      as saiu,
          -- aportes vêm de deposito/saque_caixinha, que db/pockets.py grava SEMPRE
          -- como is_internal_movement=true. Por isso o guard de movimento interno
          -- fica NOS filtros de entrou/saiu (pra não contar transferência como
          -- receita/despesa), e NÃO no where global — senão aportes zeraria e
          -- "sobrou" (receitas−despesas−aportes) sairia inflado.
          coalesce(sum(valor) filter (where tipo = 'deposito_caixinha'), 0)
            - coalesce(sum(valor) filter (where tipo = 'saque_caixinha'), 0)      as aportes
        from launches
        where user_id = %s
          and criado_em >= %s and criado_em < %s
        """,
        (user_id, first, nxt),
    )
    row = cur.fetchone() or {}
    entrou = float(row.get("entrou") or 0)
    saiu = float(row.get("saiu") or 0)
    aportes = float(row.get("aportes") or 0)
    # Regra de domínio: "sobrou" = receitas − despesas − aportes (≠ saldo).
    return {"entrou": entrou, "saiu": saiu, "aportes": aportes,
            "sobrou": entrou - saiu - aportes}


def _reporter_run_for_user(agent: dict[str, Any], today: date) -> bool:
    from db import record_agent_event, get_user_email, get_auth_user

    user_id = agent["user_id"]
    first_this = today.replace(day=1)
    last_month_end = first_this - timedelta(days=1)
    first_prev = last_month_end.replace(day=1)
    first_prev2 = (first_prev - timedelta(days=1)).replace(day=1)
    ym = first_prev.strftime("%Y-%m")

    with get_conn() as conn:
        with conn.cursor() as cur:
            stats = _month_stats(cur, user_id, first_prev, first_this)
            prev = _month_stats(cur, user_id, first_prev2, first_prev)

    if stats["entrou"] == 0 and stats["saiu"] == 0:
        return False  # mês sem movimento não rende manchete

    delta_pct = None
    if prev["sobrou"] != 0:
        delta_pct = round((stats["sobrou"] - prev["sobrou"]) / abs(prev["sobrou"]) * 100)

    mes_nome = MESES_PT[first_prev.month]
    titulo = f"A manchete de {mes_nome}"
    resumo = (
        f"Entrou {_fmt_brl(stats['entrou'])}, saiu {_fmt_brl(stats['saiu'])} — "
        f"sobrou {_fmt_brl(stats['sobrou'])}"
        + (f" ({'+' if delta_pct >= 0 else ''}{delta_pct}% vs mês anterior)."
           if delta_pct is not None else ".")
    )

    inserted = record_agent_event(
        agent["agent_id"], user_id, "reporter",
        dedupe_key=f"manchete:{ym}",
        payload={
            "tipo": "manchete", "ym": ym, "mes": mes_nome,
            "titulo": titulo, "mensagem": resumo,
            "entrou": round(stats["entrou"], 2), "saiu": round(stats["saiu"], 2),
            "aportes": round(stats["aportes"], 2), "sobrou": round(stats["sobrou"], 2),
            "delta_pct": delta_pct,
        },
        channel="email",
    )
    if not inserted:
        return False  # manchete do mês já publicada

    # E-mail é o canal principal do Repórter. Falha de e-mail não desfaz o
    # evento do feed — o dashboard sempre registra. Mas respeita quem se
    # descadastrou dos e-mails de engajamento (engagement_opt_out, setado no
    # /unsubscribe e no comando "parar emails"): a manchete continua no feed,
    # só o envio proativo é suprimido.
    try:
        auth = get_auth_user(user_id)
        opted_out = bool(auth and auth.get("engagement_opt_out"))
        email = None if opted_out else get_user_email(user_id)
        if email:
            from core.services.email_service import send_agent_report_email
            corpo = (
                f"<p>🎤 Direto da redação, a manchete de <strong>{mes_nome}</strong>:</p>"
                f"<div class=\"box\"><p style=\"margin:0\">"
                f"<strong>Entrou:</strong> {_fmt_brl(stats['entrou'])}<br/>"
                f"<strong>Saiu:</strong> {_fmt_brl(stats['saiu'])}<br/>"
                f"<strong>Aportes nas caixinhas:</strong> {_fmt_brl(stats['aportes'])}<br/>"
                f"<strong>Sobrou:</strong> {_fmt_brl(stats['sobrou'])}"
                + (f" ({'+' if delta_pct >= 0 else ''}{delta_pct}% vs mês anterior)"
                   if delta_pct is not None else "")
                + "</p></div>"
                f"<p>O detalhe completo tá no seu dashboard. Quer que eu abra a conta "
                f"de alguma categoria? É só perguntar no WhatsApp. 🐷</p>"
                f"<p class=\"sig\">— Repórter, o porquinho de plantão</p>"
            )
            send_agent_report_email(email, user_id, f"🎤 {titulo} — PigBank", corpo)
    except Exception as exc:
        print(f"[agents] reporter email user={user_id}: {exc}", file=sys.stderr)
    return True


def run_reporter_once(today: date | None = None) -> dict:
    """Publica a manchete do mês fechado (só age nos primeiros 3 dias do mês)."""
    from db import list_users_with_active_agents

    today = today or date.today()
    if today.day > 3:
        return {"ok": True, "skipped": "fora da janela de fechamento"}
    agents = list_users_with_active_agents("reporter")
    sent = 0
    for agent in agents:
        try:
            sent += 1 if _reporter_run_for_user(agent, today) else 0
        except Exception as exc:
            print(f"[agents] reporter user={agent['user_id']}: {exc}", file=sys.stderr)
    return {"ok": True, "agents": len(agents), "published": sent}


# ── Carteiro: boletos chegando (D-3 e D-0) ───────────────────────────────────

def run_carteiro_once(today: date | None = None) -> dict:
    """Registra no feed as contas pendentes vencendo em 3 dias e no dia.

    Não toca em reminder_last_sent_on — essa coluna pertence ao lembrete
    WhatsApp (dormente, wa_app._bill_reminder_tick). Dedupe própria via
    agent_events.
    """
    from db import list_users_with_active_agents, record_agent_event

    today = today or date.today()
    agents = list_users_with_active_agents("carteiro")
    fired = 0
    for agent in agents:
        user_id = agent["user_id"]
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        select b.id, b.due_date, b.amount,
                               coalesce(b.name, r.name, 'Conta') as nome,
                               (b.due_date - %s) as dias
                        from bill_instances b
                        left join recurring_expenses r on r.id = b.recurring_id
                        where b.user_id = %s
                          and b.status = 'pending'
                          and (b.due_date - %s) in (3, 0)
                        """,
                        (today, user_id, today),
                    )
                    bills = cur.fetchall() or []
            for b in bills:
                dias = int(b["dias"])
                quando = "vence HOJE" if dias == 0 else f"vence em {dias} dias"
                ok = record_agent_event(
                    agent["agent_id"], user_id, "carteiro",
                    dedupe_key=f"bill:{b['id']}:{dias}",
                    payload={
                        "tipo": "boleto",
                        "bill_id": b["id"],
                        "nome": b["nome"],
                        "valor": float(b["amount"]),
                        "vencimento": b["due_date"].isoformat(),
                        "titulo": f"{b['nome']} {quando}",
                        "mensagem": (
                            f"📬 {b['nome']} ({_fmt_brl(float(b['amount']))}) {quando} "
                            f"({b['due_date'].strftime('%d/%m')})."
                        ),
                    },
                )
                fired += 1 if ok else 0
        except Exception as exc:
            print(f"[agents] carteiro user={user_id}: {exc}", file=sys.stderr)
    return {"ok": True, "agents": len(agents), "fired": fired}


# ── Orquestração ─────────────────────────────────────────────────────────────

def run_all_agents_once(today: date | None = None) -> dict:
    today = today or date.today()
    return {
        "xerife": run_xerife_once(today),
        "reporter": run_reporter_once(today),
        "carteiro": run_carteiro_once(today),
    }


def run_agents_for_user(user_id: int, trigger: str = "of_sync") -> dict:
    """Hook pós-sync do Open Finance: roda só o Xerife, só pro usuário sincado.

    Fail-soft por contrato — quem chama (pluggy_sync) não pode quebrar por
    causa de agente.
    """
    if not AGENTS_ENABLED:
        return {"ok": True, "disabled": True}
    try:
        return run_xerife_once(user_id=user_id)
    except Exception as exc:
        print(f"[agents] run_for_user({user_id}, {trigger}): {exc}", file=sys.stderr)
        return {"ok": False, "error": str(exc)}


async def run_agents_loop() -> None:
    """Loop perpétuo (asyncio task no lifespan). Tick horário."""
    import asyncio

    if not AGENTS_ENABLED:
        print("[agents] AGENTS_ENABLED=0 — loop não iniciado")
        return
    await asyncio.sleep(5)
    while True:
        try:
            result = await asyncio.to_thread(run_all_agents_once)
            total = sum(
                int(v.get("fired") or v.get("published") or 0)
                for v in result.values() if isinstance(v, dict)
            )
            if total:
                print(f"[agents] tick: {total} disparo(s) — {result}")
        except Exception as exc:
            print(f"[agents] tick falhou: {exc}", file=sys.stderr)
        await asyncio.sleep(AGENTS_INTERVAL_SEC)
