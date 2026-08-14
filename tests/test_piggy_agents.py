"""Regressões dos Agentes do Piggy (achados de review do PR #3).

Cobrem os dois fixes testáveis sem Postgres:
- F1: a view de Agentes não pode morrer com ImportError quando o plans-v2
  (plans_v2_enabled/require_min_tier) não está no código.
- F3: o Repórter não manda e-mail pra quem se descadastrou (engagement_opt_out).

F2 (aportes no _month_stats) e F5 (limite atômico no activate_agent) dependem de
DB real — verificados por inspeção/SQL, não aqui.
"""


# ── F1: _plan_allows_multiple sobrevive sem os helpers do plans-v2 ────────────

def test_plan_allows_multiple_cai_pra_is_pro_sem_plans_v2(monkeypatch):
    import core.services.plan_service as ps
    from frontend.routes.agents import _plan_allows_multiple

    # Simula o estado da branch pushada: só is_pro existe.
    monkeypatch.delattr(ps, "plans_v2_enabled", raising=False)
    monkeypatch.delattr(ps, "require_min_tier", raising=False)
    monkeypatch.setattr(ps, "is_pro", lambda uid: True)
    assert _plan_allows_multiple(123) is True

    monkeypatch.setattr(ps, "is_pro", lambda uid: False)
    assert _plan_allows_multiple(123) is False


def test_plan_allows_multiple_usa_tier_quando_plans_v2_presente(monkeypatch):
    import core.services.plan_service as ps
    from frontend.routes.agents import _plan_allows_multiple

    monkeypatch.setattr(ps, "plans_v2_enabled", lambda: True, raising=False)
    monkeypatch.setattr(ps, "require_min_tier", lambda uid, tier: tier == "plus", raising=False)
    monkeypatch.setattr(ps, "is_pro", lambda uid: False)  # não deve ser usado no ramo v2
    assert _plan_allows_multiple(123) is True


# ── F3: Repórter respeita engagement_opt_out ─────────────────────────────────

def _arm_reporter(monkeypatch, *, opted_out: bool):
    """Arma o envio de e-mail do Repórter via run_agent_emails_once (o
    _reporter_run_for_user só registra o evento no feed; o e-mail — e o
    respeito ao opt-out — vive no mini-digest por agente)."""
    import core.services.piggy_agents as pa
    import db
    import core.services.email_service as es
    import core.services.plan_service as ps

    sent: list = []

    monkeypatch.setattr(
        db, "list_agents_pending_email",
        lambda: [{"agent_id": 1, "user_id": 42, "kind": "reporter",
                  "config": {}, "last_emailed_at": None}],
    )
    monkeypatch.setattr(
        db, "list_unemailed_events",
        lambda agent_id: [{"id": 1, "payload": {"titulo": "A manchete de agosto",
                                                "mensagem": "Sobrou R$ 400,00"}}],
    )
    monkeypatch.setattr(db, "mark_events_emailed", lambda ids: None)
    monkeypatch.setattr(db, "touch_agent_emailed", lambda agent_id: None)
    monkeypatch.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": opted_out})
    monkeypatch.setattr(db, "get_user_email", lambda uid: "user@example.com")
    monkeypatch.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
    monkeypatch.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
    monkeypatch.setattr(es, "send_agent_report_email", lambda *a, **k: sent.append(a))

    pa.run_agent_emails_once()
    return sent


def test_reporter_nao_envia_email_para_quem_descadastrou(monkeypatch):
    sent = _arm_reporter(monkeypatch, opted_out=True)
    assert sent == []


def test_reporter_envia_email_quando_nao_descadastrado(monkeypatch):
    sent = _arm_reporter(monkeypatch, opted_out=False)
    assert len(sent) == 1


# ── Agentes: lançado geral, com freio de emergência pra voltar ao beta ────────

def test_agents_ui_enabled_default_lancado_geral(monkeypatch):
    # Lançado: sem env, TODO usuário vê os agentes. O limite Free vs pago mora
    # no gate por tier (agent_kind_allowed), não aqui.
    import core.services.plan_service as ps
    monkeypatch.delenv("AGENTS_UI_ENABLED", raising=False)
    monkeypatch.delenv("AGENTS_BETA_EMAILS", raising=False)
    monkeypatch.delenv("AGENTS_BETA_USER_IDS", raising=False)
    assert ps.agents_ui_enabled(1, "lucaskuramoti06@gmail.com") is True
    assert ps.agents_ui_enabled(1, "estranho@example.com") is True


def test_agents_ui_enabled_flag_global_abre_geral(monkeypatch):
    import core.services.plan_service as ps
    monkeypatch.setenv("AGENTS_UI_ENABLED", "1")
    assert ps.agents_ui_enabled(1, "qualquer@example.com") is True


def test_agents_ui_enabled_freio_volta_pro_beta(monkeypatch):
    # AGENTS_UI_ENABLED=0 é o freio de emergência: volta pro modo beta,
    # só a allowlist de e-mail/id passa.
    import core.services.plan_service as ps
    monkeypatch.setenv("AGENTS_UI_ENABLED", "0")
    monkeypatch.delenv("AGENTS_BETA_EMAILS", raising=False)
    monkeypatch.delenv("AGENTS_BETA_USER_IDS", raising=False)
    assert ps.agents_ui_enabled(1, "lucaskuramoti06@gmail.com") is True
    assert ps.agents_ui_enabled(1, "HIAGOJO2016@gmail.com") is True   # case-insensitive
    assert ps.agents_ui_enabled(1, "estranho@example.com") is False


def test_agents_ui_enabled_freio_respeita_allowlist_custom(monkeypatch):
    import core.services.plan_service as ps
    monkeypatch.setenv("AGENTS_UI_ENABLED", "0")
    monkeypatch.setenv("AGENTS_BETA_EMAILS", "novo@example.com")
    assert ps.agents_ui_enabled(1, "novo@example.com") is True
    assert ps.agents_ui_enabled(1, "lucaskuramoti06@gmail.com") is False


# ── Faria Limer: acompanhamento de renda variável (função pura, sem DB) ──────
# faria_limer_insights só transforma posições+resumo (o que list_rv_positions /
# rv_portfolio_summary já entregam) em eventos factuais — dá pra testar isolado.

def _rv_summary(positions):
    """Resumo equivalente ao rv_portfolio_summary, pra alimentar os insights."""
    mv = sum(p["market_value"] for p in positions)
    invested = sum(p.get("invested", 0.0) for p in positions)
    pnl = mv - invested
    # espelha rv_portfolio_summary: custo confiável só quando TODA posição o traz
    # (nos fixtures, ter `invested` = custo conhecido, salvo cost_known explícito).
    cost_known = bool(positions) and all(
        p.get("cost_known", "invested" in p) for p in positions
    )
    return {"market_value": mv, "invested": invested, "cost_known": cost_known,
            "pnl": pnl, "pnl_pct": (pnl / invested) if invested > 0 else 0.0,
            "count": len(positions)}


def test_rv_insights_carteira_vazia_ou_minima():
    from core.services.piggy_agents import faria_limer_insights
    assert faria_limer_insights([], {}, "2026-08") == []
    # abaixo do mínimo (FARIA_RV_MIN_MV=100) → ruído, não dispara
    pos = [{"ticker": "MGLU3", "market_value": 40.0, "invested": 50.0}]
    assert faria_limer_insights(pos, _rv_summary(pos), "2026-08") == []


def test_rv_insights_retrato_do_mes():
    from core.services.piggy_agents import faria_limer_insights
    pos = [
        {"ticker": "GGRC11", "market_value": 6000.0, "invested": 5000.0, "last_month_rate": 1.2},
        {"ticker": "ITUB4",  "market_value": 5000.0, "invested": 5000.0, "last_month_rate": 0.8},
    ]
    ins = faria_limer_insights(pos, _rv_summary(pos), "2026-08")
    retrato = next(i for i in ins if i["payload"]["tipo"] == "rv_retrato")
    assert retrato["dedupe_key"] == "rv_retrato:2026-08"
    assert retrato["valor_impacto"] == 1000.0            # pnl = 11000 - 10000
    msg = retrato["payload"]["mensagem"]
    assert "2 ativos" in msg
    assert "+10,0%" in msg                               # pnl_pct
    # média ponderada por valor de mercado: (1,2·6000 + 0,8·5000)/11000 ≈ 1,02%
    assert "No mês, a carteira variou ~+1,02%" in msg
    assert "não é recomendação" in msg                   # trava anti-conselho


def test_rv_insights_concentracao_dispara_quando_um_ativo_domina():
    from core.services.piggy_agents import faria_limer_insights
    pos = [
        {"ticker": "PETR4", "market_value": 8000.0, "invested": 7000.0},
        {"ticker": "ITUB4", "market_value": 2000.0, "invested": 2000.0},
    ]
    ins = faria_limer_insights(pos, _rv_summary(pos), "2026-08")
    conc = next(i for i in ins if i["payload"]["tipo"] == "rv_concentracao")
    assert conc["dedupe_key"] == "rv_concentracao:2026-08"
    assert conc["valor_impacto"] == 0.0
    assert "PETR4" in conc["payload"]["titulo"]
    assert "80%" in conc["payload"]["titulo"]            # 8000 / 10000
    assert "decisão sua" in conc["payload"]["mensagem"]  # sem conselho


def test_rv_insights_carteira_equilibrada_nao_alerta_concentracao():
    from core.services.piggy_agents import faria_limer_insights
    pos = [
        {"ticker": "GGRC11", "market_value": 3400.0, "invested": 3000.0},
        {"ticker": "ITUB4",  "market_value": 3300.0, "invested": 3000.0},
        {"ticker": "PETR4",  "market_value": 3300.0, "invested": 3000.0},
    ]
    ins = faria_limer_insights(pos, _rv_summary(pos), "2026-08")
    tipos = {i["payload"]["tipo"] for i in ins}
    assert tipos == {"rv_retrato"}                       # nenhum ativo passa de 40%


def test_rv_insights_sem_taxa_do_mes_omite_variacao():
    from core.services.piggy_agents import faria_limer_insights
    # corretora não mandou last_month_rate → não inventa rentabilidade do mês
    pos = [{"ticker": "BBAS3", "market_value": 1500.0, "invested": 1400.0}]
    ins = faria_limer_insights(pos, _rv_summary(pos), "2026-08")
    msg = ins[0]["payload"]["mensagem"]
    assert "No mês" not in msg
    assert "1 ativo" in msg and "1 ativos" not in msg     # singular correto


def test_rv_insights_sem_custo_conhecido_nao_inventa_resultado():
    # Conector sem custo de aquisição: invested cai pro valor de mercado (P&L 0),
    # mas o retrato NÃO pode mostrar "R$ 0,00 (+0,0%)" — tem que dizer que não sabe.
    from core.services.piggy_agents import faria_limer_insights
    pos = [
        {"ticker": "PETR4", "market_value": 8000.0, "invested": 8000.0, "cost_known": False},
        {"ticker": "ITUB4", "market_value": 4000.0, "invested": 4000.0, "cost_known": False},
    ]
    ins = faria_limer_insights(pos, _rv_summary(pos), "2026-08")
    msg = next(i for i in ins if i["payload"]["tipo"] == "rv_retrato")["payload"]["mensagem"]
    assert "sem custo de aquisição conhecido" in msg
    assert "R$ 0,00" not in msg and "+0,0%" not in msg


def test_rv_insights_concentracao_agrega_mesmo_ticker_em_conexoes_diferentes():
    # Mesmo PETR4 em duas corretoras (30% + 30% = 60%) tem que somar como UM ativo
    # e disparar concentração — não pode ser mascarado como dois de 30%.
    from core.services.piggy_agents import faria_limer_insights
    pos = [
        {"ticker": "PETR4", "market_value": 3000.0, "invested": 3000.0},
        {"ticker": "PETR4", "market_value": 3000.0, "invested": 3000.0},
        {"ticker": "ITUB4", "market_value": 4000.0, "invested": 4000.0},
    ]
    ins = faria_limer_insights(pos, _rv_summary(pos), "2026-08")
    conc = next(i for i in ins if i["payload"]["tipo"] == "rv_concentracao")
    assert "PETR4" in conc["payload"]["titulo"]
    assert "60%" in conc["payload"]["titulo"]             # 6000 / 10000, agregado
    retrato = next(i for i in ins if i["payload"]["tipo"] == "rv_retrato")
    assert "2 ativos" in retrato["payload"]["mensagem"]   # PETR4 agrupado + ITUB4
