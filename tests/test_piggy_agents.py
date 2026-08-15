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
    # claim devolve todos: simula "nenhum evento mudou entre leitura e envio"
    monkeypatch.setattr(db, "claim_agent_events_for_email",
                        lambda evs: [e["id"] for e in evs])
    monkeypatch.setattr(db, "suppress_agent_events", lambda ids: len(ids))
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
    # retrato é factual → impacto 0 (não entra em saved_365d/salvos_ano, senão o
    # P&L não realizado seria contado como "economia" a cada snapshot mensal).
    assert retrato["valor_impacto"] == 0.0
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


def test_rv_insights_taxa_do_mes_exige_cobertura_total():
    # Cobertura parcial: só o ITUB4 (peso pequeno) tem last_month_rate. Não pode
    # apresentar a taxa do subconjunto como se fosse a variação da carteira toda.
    from core.services.piggy_agents import faria_limer_insights
    pos = [
        {"ticker": "PETR4", "market_value": 9000.0, "invested": 8000.0},               # sem taxa
        {"ticker": "ITUB4", "market_value": 1000.0, "invested": 900.0, "last_month_rate": 10.0},
    ]
    msg = faria_limer_insights(pos, _rv_summary(pos), "2026-08")[0]["payload"]["mensagem"]
    assert "No mês" not in msg          # cobertura incompleta → omite a variação do mês


def test_list_rv_positions_amount_zero_conta_como_custo_desconhecido(monkeypatch):
    # amount:0 vindo do conector não é custo real — não pode virar "lucro" = valor
    # de mercado inteiro. Fica cost_known=False e P&L 0 (custo cai pro market_value).
    import db.rv as rv

    class _Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def fetchall(self):
            return [{"of_investment_id": 1, "name": "Petrobras", "type": "EQUITY",
                     "subtype": "STOCK", "balance": 1000.0, "currency": "BRL",
                     "raw": {"amount": 0, "code": "PETR4"}}]

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _Cur()

    monkeypatch.setattr(rv, "ensure_user", lambda uid: None)
    monkeypatch.setattr(rv, "get_conn", lambda: _Conn())
    pos = rv.list_rv_positions(1)
    assert len(pos) == 1
    assert pos[0]["cost_known"] is False       # amount 0 → custo desconhecido
    assert pos[0]["invested"] == 1000.0        # fallback pro valor de mercado
    assert pos[0]["pnl"] == 0.0                # sem lucro fantasma


def test_rv_portfolio_summary_usa_posicoes_passadas_sem_rebuscar(monkeypatch):
    # Passar positions deriva o resumo do MESMO snapshot — não pode reler o banco
    # (evita misturar lista antiga com totais novos se um sync commitar no meio).
    import db.rv as rv

    def _boom(*a, **k):
        raise AssertionError("não deveria reler list_rv_positions quando positions foi passado")

    monkeypatch.setattr(rv, "list_rv_positions", _boom)
    positions = [
        {"market_value": 6000.0, "invested": 5000.0, "cost_known": True},
        {"market_value": 4000.0, "invested": 4000.0, "cost_known": True},
    ]
    summ = rv.rv_portfolio_summary(1, positions=positions)
    assert summ["market_value"] == 10000.0
    assert summ["invested"] == 9000.0
    assert summ["pnl"] == 1000.0
    assert summ["cost_known"] is True
    assert summ["count"] == 2


def test_hook_por_item_so_roda_detectores_de_delta(monkeypatch):
    # run_agents_for_user roda por ITEM do Pluggy. Agentes whole-portfolio (Faria
    # Limer e Barão) agregam TODAS as conexões e dedupam por mês: rodar aqui grava
    # um retrato parcial que o dedupe congela até virar o mês. Os dois ficam fora
    # deste hook (rodam no fim do sync completo + no loop horário); só os
    # detectores de delta (xerife/detetive/cofre) seguem por item.
    import core.services.piggy_agents as pa

    called = []
    monkeypatch.setattr(pa, "AGENTS_ENABLED", True)
    for name in ("run_xerife_once", "run_detetive_once", "run_cofre_once",
                 "run_barao_once", "run_faria_limer_once"):
        monkeypatch.setattr(pa, name, (lambda n: (lambda **kw: called.append(n) or {"ok": True}))(name))

    out = pa.run_agents_for_user(42, trigger="of_sync")
    assert "run_faria_limer_once" not in called
    assert "run_barao_once" not in called          # agregado: fora do hook por-item
    assert "faria_limer" not in out and "barao" not in out
    assert {"xerife", "detetive", "cofre"} <= set(out)   # delta agents rodaram


def test_sync_completo_roda_os_agregados_uma_vez(monkeypatch):
    # No fim de sync_pluggy_user, com o snapshot já coerente, os dois agregados
    # rodam — uma vez cada, pro usuário sincado.
    import core.services.pluggy_sync as ps
    import core.services.piggy_agents as pa

    chamados = []
    monkeypatch.setattr(ps, "get_open_finance_snapshot", lambda uid: {
        "connections": [{"provider": "pluggy", "provider_item_id": "i1"},
                        {"provider": "pluggy", "provider_item_id": "i2"}]})
    monkeypatch.setattr(ps, "sync_pluggy_item", lambda item: {"ok": True})
    monkeypatch.setattr(pa, "run_faria_limer_once",
                        lambda **kw: chamados.append(("faria", kw.get("user_id"))) or {"ok": True})
    monkeypatch.setattr(pa, "run_barao_once",
                        lambda **kw: chamados.append(("barao", kw.get("user_id"))) or {"ok": True})

    ps.sync_pluggy_user(42)
    assert chamados == [("faria", 42), ("barao", 42)]   # um disparo de cada, no fim


def test_sync_completo_um_agregado_nao_derruba_o_outro(monkeypatch):
    # Fail-soft por agente: se o Faria explode, o Barão ainda roda e o sync conclui.
    import core.services.pluggy_sync as ps
    import core.services.piggy_agents as pa

    chamados = []
    monkeypatch.setattr(ps, "get_open_finance_snapshot", lambda uid: {
        "connections": [{"provider": "pluggy", "provider_item_id": "i1"}]})
    monkeypatch.setattr(ps, "sync_pluggy_item", lambda item: {"ok": True})
    monkeypatch.setattr(pa, "run_faria_limer_once",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(pa, "run_barao_once",
                        lambda **kw: chamados.append("barao") or {"ok": True})

    out = ps.sync_pluggy_user(42)
    assert out["ok"] is True
    assert chamados == ["barao"]


def test_run_barao_once_respeita_kill_switch(monkeypatch):
    # AGENTS_ENABLED=0 desliga o subsistema. Como o Barão passou a ser chamado
    # direto do hook de sync (fora do run_agents_for_user), o guard tem que estar
    # no próprio runner.
    import core.services.piggy_agents as pa
    monkeypatch.setattr(pa, "AGENTS_ENABLED", False)
    monkeypatch.setattr(
        pa, "_barao_detect_for_user",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria rodar")),
    )
    out = pa.run_barao_once(user_id=42)
    assert out.get("disabled") is True
    assert out.get("fired") == 0


def test_taxa_do_mes_pondera_pelo_valor_inicial():
    # last_month_rate vem em %. Peso = valor no INÍCIO do mês (mv/(1+taxa/100)).
    # Duas posições de R$100 no início, +100% e 0% → fim R$200 e R$100. Ponderar
    # pelo valor atual daria ~+66,7%; pelo inicial dá +50% (rentabilidade real).
    from core.services.piggy_agents import _weighted_month_rate
    pos = [
        {"market_value": 200.0, "last_month_rate": 100.0},
        {"market_value": 100.0, "last_month_rate": 0.0},
    ]
    assert abs(_weighted_month_rate(pos) - 50.0) < 1e-6


def test_run_faria_limer_once_respeita_kill_switch(monkeypatch):
    # AGENTS_ENABLED=0 desliga o subsistema. Como o Faria é chamado direto do hook
    # de sync (fora do run_agents_for_user), o guard tem que estar no runner.
    import core.services.piggy_agents as pa
    monkeypatch.setattr(pa, "AGENTS_ENABLED", False)
    monkeypatch.setattr(
        pa, "_faria_limer_detect_for_user",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria rodar")),
    )
    out = pa.run_faria_limer_once(user_id=42)
    assert out.get("disabled") is True
    assert out.get("fired") == 0


def test_faria_detector_grava_via_upsert_autocorrigivel(monkeypatch):
    # O detector deve usar record_or_refresh_agent_event (upsert que corrige um
    # retrato parcial ainda pendente), não o record_agent_event first-write-wins.
    from datetime import date
    import db
    import core.services.piggy_agents as pa

    calls = []
    monkeypatch.setattr(db, "list_rv_positions", lambda uid: [
        {"ticker": "PETR4", "market_value": 8000.0, "invested": 7000.0},
        {"ticker": "ITUB4", "market_value": 2000.0, "invested": 2000.0},
    ])
    monkeypatch.setattr(db, "rv_portfolio_summary", lambda uid, positions=None: {
        "market_value": 10000.0, "invested": 9000.0, "cost_known": True,
        "pnl": 1000.0, "pnl_pct": 1000.0 / 9000.0, "count": 2,
    })
    monkeypatch.setattr(db, "record_or_refresh_agent_event",
                        lambda *a, **k: calls.append(k) or True)
    monkeypatch.setattr(db, "record_agent_event", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("detector deve usar o upsert, não record_agent_event")))

    fired = pa._faria_limer_detect_for_user({"agent_id": 1, "user_id": 42}, date(2026, 8, 13))
    assert fired >= 1
    keys = [k["dedupe_key"] for k in calls]
    assert any(dk.startswith("rv_retrato:") for dk in keys)


def test_faria_detector_apaga_concentracao_pendente_que_deixou_de_valer(monkeypatch):
    # Run parcial gravou rv_concentracao; o run coerente vê carteira equilibrada e
    # não reproduz o insight → o pendente (não visto/emailado) tem que ser apagado,
    # senão o alerta falso fica elegível pro feed/e-mail o mês todo.
    from datetime import date
    import db
    import core.services.piggy_agents as pa

    deleted = []
    # carteira equilibrada: 3 ativos ~33% cada → só rv_retrato, sem concentração
    monkeypatch.setattr(db, "list_rv_positions", lambda uid: [
        {"ticker": "GGRC11", "market_value": 3400.0, "invested": 3000.0},
        {"ticker": "ITUB4", "market_value": 3300.0, "invested": 3000.0},
        {"ticker": "PETR4", "market_value": 3300.0, "invested": 3000.0},
    ])
    monkeypatch.setattr(db, "rv_portfolio_summary", lambda uid, positions=None: {
        "market_value": 10000.0, "invested": 9000.0, "cost_known": True,
        "pnl": 1000.0, "pnl_pct": 1000.0 / 9000.0, "count": 3,
    })
    monkeypatch.setattr(db, "record_or_refresh_agent_event", lambda *a, **k: True)
    monkeypatch.setattr(db, "mark_agent_event_stale",
                        lambda agent_id, dedupe_key: deleted.append(dedupe_key) or True)

    pa._faria_limer_detect_for_user({"agent_id": 1, "user_id": 42}, date(2026, 8, 13))
    assert "rv_concentracao:2026-08" in deleted     # obsoleto → limpo
    assert "rv_retrato:2026-08" not in deleted      # produzido neste run → mantido


def test_email_do_faria_respeita_carencia_de_autocorrecao(monkeypatch):
    # Evento do Faria só entra em e-mail depois da carência (janela em que o upsert
    # ainda pode corrigir um snapshot parcial). Fresco → segura; vencido → envia.
    from datetime import datetime, timedelta, timezone
    import db
    import core.services.piggy_agents as pa
    import core.services.email_service as es
    import core.services.plan_service as ps

    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    def _arm(fired_at):
        sent, claimed = [], []

        def _claim(evs):
            ids = [e["id"] for e in evs]
            claimed.extend(ids)
            return ids

        monkeypatch.setattr(db, "list_agents_pending_email", lambda: [
            {"agent_id": 9, "user_id": 42, "kind": "faria_limer",
             "config": {}, "last_emailed_at": None}])
        monkeypatch.setattr(db, "list_unemailed_events", lambda agent_id: [
            {"id": 7, "payload": {"titulo": "Sua renda variável",
                                  "mensagem": "R$ 10.000,00 em 2 ativos"},
             "fired_at": fired_at}])
        monkeypatch.setattr(db, "claim_agent_events_for_email", _claim)
        monkeypatch.setattr(db, "touch_agent_emailed", lambda agent_id: None)
        monkeypatch.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": False})
        monkeypatch.setattr(db, "get_user_email", lambda uid: "user@example.com")
        monkeypatch.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
        monkeypatch.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
        monkeypatch.setattr(es, "send_agent_report_email", lambda *a, **k: sent.append(a))
        pa.run_agent_emails_once(now=now)
        return sent, claimed

    # fresco (5 min): dentro da carência → não envia nem reivindica (refresh pode corrigir)
    sent, claimed = _arm(now - timedelta(minutes=5))
    assert sent == [] and claimed == []
    # vencido (2h): reivindica e envia
    sent, claimed = _arm(now - timedelta(hours=2))
    assert len(sent) == 1 and claimed == [7]


def test_email_pula_evento_que_mudou_entre_leitura_e_claim(monkeypatch):
    # TOCTOU: se o claim condicional não reivindica nada (o evento foi refrescado
    # entre a leitura e o claim — fired_at mudou), o tick NÃO envia e-mail com o
    # payload velho em cache; o evento sai no próximo tick, já estável.
    from datetime import datetime, timedelta, timezone
    import db
    import core.services.piggy_agents as pa
    import core.services.email_service as es
    import core.services.plan_service as ps

    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    sent, touched = [], []
    monkeypatch.setattr(db, "list_agents_pending_email", lambda: [
        {"agent_id": 9, "user_id": 42, "kind": "faria_limer",
         "config": {}, "last_emailed_at": None}])
    monkeypatch.setattr(db, "list_unemailed_events", lambda agent_id: [
        {"id": 7, "payload": {"titulo": "T", "mensagem": "M"},
         "fired_at": now - timedelta(hours=2)}])
    monkeypatch.setattr(db, "claim_agent_events_for_email", lambda evs: [])  # ninguém
    monkeypatch.setattr(db, "touch_agent_emailed", lambda agent_id: touched.append(agent_id))
    monkeypatch.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": False})
    monkeypatch.setattr(db, "get_user_email", lambda uid: "user@example.com")
    monkeypatch.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
    monkeypatch.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
    monkeypatch.setattr(es, "send_agent_report_email", lambda *a, **k: sent.append(a))

    out = pa.run_agent_emails_once(now=now)
    assert sent == [] and touched == []
    assert out["sent"] == 0


def test_optout_suprime_sem_congelar(monkeypatch):
    # Opt-out (por agente e global) usa suppress_agent_events, NÃO o claim: o
    # evento sai da fila de e-mail mas segue vivo no feed (emailed_at continua
    # null → upsert ainda corrige e a limpeza de obsoleto ainda remove).
    from datetime import datetime, timedelta, timezone
    import db
    import core.services.piggy_agents as pa
    import core.services.email_service as es
    import core.services.plan_service as ps

    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    def _arm(*, email_enabled=True, optout=False):
        suppressed, claimed = [], []
        monkeypatch.setattr(db, "list_agents_pending_email", lambda: [
            {"agent_id": 9, "user_id": 42, "kind": "faria_limer",
             "config": {"email_enabled": email_enabled}, "last_emailed_at": None}])
        monkeypatch.setattr(db, "list_unemailed_events", lambda agent_id: [
            {"id": 7, "payload": {"titulo": "T", "mensagem": "M"},
             "fired_at": now - timedelta(hours=2)}])
        monkeypatch.setattr(db, "suppress_agent_events",
                            lambda ids: suppressed.extend(ids) or len(ids))
        monkeypatch.setattr(db, "claim_agent_events_for_email",
                            lambda evs: claimed.extend(e["id"] for e in evs) or [])
        monkeypatch.setattr(db, "touch_agent_emailed", lambda agent_id: None)
        monkeypatch.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": optout})
        monkeypatch.setattr(db, "get_user_email", lambda uid: "user@example.com")
        monkeypatch.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
        monkeypatch.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
        monkeypatch.setattr(es, "send_agent_report_email",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("não envia")))
        pa.run_agent_emails_once(now=now)
        return suppressed, claimed

    sup, cla = _arm(email_enabled=False)
    assert sup == [7] and cla == []      # e-mail off: suprime, sem claim/congelamento
    sup, cla = _arm(optout=True)
    assert sup == [7] and cla == []      # opt-out global: idem


def test_faria_ignora_posicoes_fora_do_brl(monkeypatch):
    # Mensagens formatam R$ — somar nominal de outra moeda mentiria no total e na
    # concentração (R$10k + US$50k ≠ "R$ 60.000"). Posição não-BRL fica fora.
    from datetime import date
    import db
    import core.services.piggy_agents as pa
    from db.rv import rv_portfolio_summary

    recorded = []
    monkeypatch.setattr(db, "list_rv_positions", lambda uid: [
        {"ticker": "PETR4", "market_value": 8000.0, "invested": 7000.0,
         "cost_known": True, "currency": "BRL"},
        {"ticker": "AAPL", "market_value": 50000.0, "invested": 30000.0,
         "cost_known": True, "currency": "USD"},   # dominaria tudo se somasse
    ])
    monkeypatch.setattr(db, "rv_portfolio_summary",
                        lambda uid, positions=None: rv_portfolio_summary(uid, positions=positions))
    monkeypatch.setattr(db, "record_or_refresh_agent_event",
                        lambda *a, **k: recorded.append(k) or True)
    monkeypatch.setattr(db, "mark_agent_event_stale", lambda *a, **k: True)

    pa._faria_limer_detect_for_user({"agent_id": 1, "user_id": 42}, date(2026, 8, 13))
    tipos = {k["payload"]["tipo"]: k["payload"] for k in recorded}
    retrato = tipos["rv_retrato"]
    assert "R$ 8.000,00" in retrato["mensagem"]          # só o BRL
    assert "1 ativo" in retrato["mensagem"] and "2 ativos" not in retrato["mensagem"]
    assert "rv_concentracao" not in tipos                # 1 ativo só → sem alerta


# ── Integração real com o banco: o SQL do pipeline de eventos ────────────────
# Estes exercitam as queries DE VERDADE. Os testes acima mockam db.*, então um
# erro de sintaxe no SQL passava batido (aconteceu: uma vírgula faltando no
# ON CONFLICT derrubava todo o Faria silenciosamente, porque o detector é
# fail-soft e só logava a exceção).

def _mk_agent(user_id: int, kind: str = "faria_limer") -> int:
    import db
    ag = db.activate_agent(user_id, kind)
    assert ag, "não criou o agente"
    return ag["id"]


def test_record_or_refresh_sql_roundtrip(user_id):
    """Insere → re-grava igual → muda payload → congela após e-mail."""
    import db
    from db import get_conn
    agent_id = _mk_agent(user_id)
    key = "rv_retrato:2026-08"

    def _row():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select payload, fired_at, suppressed_at, seen_at, emailed_at"
                    " from agent_events where agent_id=%s and dedupe_key=%s",
                    (agent_id, key),
                )
                return cur.fetchone()

    # 1) primeira gravação insere
    assert db.record_or_refresh_agent_event(
        agent_id, user_id, "faria_limer", key, {"v": 1}, valor_impacto=0.0) is True
    assert _row()["payload"] == {"v": 1}
    fired1 = _row()["fired_at"]

    # 2) re-gravação IDÊNTICA não mexe (não reseta a carência de estabilidade)
    assert db.record_or_refresh_agent_event(
        agent_id, user_id, "faria_limer", key, {"v": 1}, valor_impacto=0.0) is False
    assert _row()["fired_at"] == fired1

    # 3) suprimido + já visto: payload novo ainda corrige, limpa suppressed_at
    #    e renova fired_at (seen_at não congela — só o e-mail congela)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update agent_events set suppressed_at=now(), seen_at=now(),"
                " fired_at=now() - interval '3 hours' where agent_id=%s", (agent_id,))
        conn.commit()
    assert db.record_or_refresh_agent_event(
        agent_id, user_id, "faria_limer", key, {"v": 2}, valor_impacto=0.0) is True
    r = _row()
    assert r["payload"] == {"v": 2}
    assert r["suppressed_at"] is None      # supressão vale pro snapshot, não pro mês
    assert r["seen_at"] is not None        # visto no feed não impede a correção
    assert r["fired_at"] > fired1          # idade reiniciou (payload mudou)

    # 4) depois de emailado o feed AINDA corrige — o e-mail é retrato datado, o
    #    app é o estado atual, e divergirem é correto. O que não pode é reenviar:
    #    isso quem impede são as queries de fila, não o upsert.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update agent_events set emailed_at=now() where agent_id=%s", (agent_id,))
        conn.commit()
    assert db.record_or_refresh_agent_event(
        agent_id, user_id, "faria_limer", key, {"v": 3}, valor_impacto=0.0) is True
    assert _row()["payload"] == {"v": 3}, "feed devia refletir o valor novo"
    assert _row()["emailed_at"] is not None, "não podia ter voltado a ficar pendente"
    assert db.list_unemailed_events(agent_id) == [], "evento emailado não pode voltar pra fila"


def test_claim_unclaim_suppress_delete_sql(user_id):
    """Claim condicional, unclaim, supressão e limpeza — contra o banco real."""
    import db
    from db import get_conn
    agent_id = _mk_agent(user_id, "barao")
    key = "parado:2026-08"
    db.record_or_refresh_agent_event(agent_id, user_id, "barao", key, {"v": 1}, valor_impacto=1.0)

    ev = db.list_unemailed_events(agent_id)
    assert len(ev) == 1

    # claim com fired_at DESATUALIZADO não reivindica (fecha o TOCTOU)
    from datetime import timedelta
    stale = [{"id": ev[0]["id"], "fired_at": ev[0]["fired_at"] - timedelta(hours=1)}]
    assert db.claim_agent_events_for_email(stale) == []

    # claim com fired_at correto reivindica
    claimed = db.claim_agent_events_for_email(ev)
    assert claimed == [ev[0]["id"]]
    assert db.list_unemailed_events(agent_id) == []       # saiu da fila

    # unclaim devolve pra fila (envio falhou → nada foi enviado)
    assert db.unclaim_agent_events(claimed) == 1
    assert len(db.list_unemailed_events(agent_id)) == 1

    # supressão tira da fila SEM congelar: o refresh ainda corrige
    assert db.suppress_agent_events(claimed) == 1
    assert db.list_unemailed_events(agent_id) == []
    assert db.record_or_refresh_agent_event(
        agent_id, user_id, "barao", key, {"v": 2}, valor_impacto=1.0) is True
    assert len(db.list_unemailed_events(agent_id)) == 1    # upsert limpou a supressão

    # condição que deixou de valer: sai do feed e das filas, mas a linha fica
    # (soft delete) pra preservar o emailed_at, que é o dedupe de entrega
    assert db.mark_agent_event_stale(agent_id, key) is True
    assert db.list_unemailed_events(agent_id) == []
    assert db.list_agent_events(user_id, limit=10) == []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n, min(stale_at) as st from agent_events"
                        " where agent_id=%s", (agent_id,))
            row = cur.fetchone()
            assert row["n"] == 1 and row["st"] is not None


def test_barao_corrige_evento_parcial_do_mes(user_id):
    """Achado P1 do Codex no PR #51: quem sincou ANTES do fix já tem o evento
    parcial gravado. A execução coerente precisa corrigir esse evento — com
    record_agent_event (DO NOTHING) o valor errado ficaria até virar o mês."""
    import db
    from datetime import date
    import core.services.piggy_agents as pa

    ag = db.activate_agent(user_id, "barao")
    hoje = date(2026, 8, 14)
    key = f"parado:{hoje.strftime('%Y-%m')}"

    # estado deixado pelo bug: evento do mês com o saldo parcial do 1º item
    db.record_or_refresh_agent_event(
        ag["id"], user_id, "barao", key,
        {"tipo": "parado", "idle": 1500.0, "titulo": "R$ 1.500,00 parado rendendo nada"},
        valor_impacto=13.13)

    def _idle():
        e = [x for x in db.list_agent_events(user_id, limit=5) if x["kind"] == "barao"]
        return float(e[0]["payload"]["idle"])

    assert _idle() == 1500.0

    # execução coerente (pós-fix), agora com o saldo agregado dos dois bancos
    import unittest.mock as m
    with m.patch.object(pa, "get_conn") as gc:
        cur = gc.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"idle": 21500.0}
        pa._barao_detect_for_user({"agent_id": ag["id"], "user_id": user_id}, hoje)

    assert _idle() == 21500.0, "o evento parcial do mês NÃO foi corrigido"


def test_sync_completo_sobrevive_a_import_quebrado(monkeypatch):
    """Achado P2 do Codex no PR #51: o import de piggy_agents fica dentro do
    guard. Se o módulo explodir ao importar (env malformado), o sync não pode
    levantar depois de já ter persistido os dados financeiros."""
    import builtins
    import core.services.pluggy_sync as ps

    monkeypatch.setattr(ps, "get_open_finance_snapshot", lambda uid: {
        "connections": [{"provider": "pluggy", "provider_item_id": "i1"}]})
    monkeypatch.setattr(ps, "sync_pluggy_item", lambda item: {"ok": True, "accounts_synced": 1})

    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "core.services.piggy_agents":
            raise ValueError("invalid literal for int(): AGENTS_INTERVAL_SEC")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    out = ps.sync_pluggy_user(42)          # não pode levantar
    assert out["ok"] is True
    assert out["items_synced"] == 1


def test_barao_remove_alerta_quando_saldo_cai_abaixo_do_minimo(user_id):
    """P1 do Codex (2ª rodada, PR #51): se o saldo coerente fica abaixo do
    mínimo, o detector saía cedo e o alerta parcial apodrecia no feed — com o
    valor_impacto ainda somando em saved_365d."""
    import db
    from datetime import date
    import unittest.mock as m
    import core.services.piggy_agents as pa

    ag = db.activate_agent(user_id, "barao")
    hoje = date(2026, 8, 14)
    db.record_or_refresh_agent_event(
        ag["id"], user_id, "barao", f"parado:{hoje.strftime('%Y-%m')}",
        {"tipo": "parado", "idle": 1500.0, "titulo": "R$ 1.500,00 parado rendendo nada"},
        valor_impacto=13.13)
    assert [e for e in db.list_agent_events(user_id, limit=5) if e["kind"] == "barao"]

    # execução coerente: saldo agregado abaixo do mínimo (BARAO_MIN_IDLE=1000)
    with m.patch.object(pa, "get_conn") as gc:
        cur = gc.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"idle": 200.0}
        assert pa._barao_detect_for_user({"agent_id": ag["id"], "user_id": user_id}, hoje) == 0

    restantes = [e for e in db.list_agent_events(user_id, limit=5) if e["kind"] == "barao"]
    assert restantes == [], "alerta obsoleto do Barão ficou no feed"


def test_barao_tem_carencia_de_estabilidade_no_email():
    """P1 do Codex (2ª rodada): sem entrada em _AGENT_EMAIL_MIN_AGE_MIN, um
    snapshot parcial do tick horário seria emailado na mesma passada e o
    emailed_at congelaria o valor errado pelo resto do mês."""
    from core.services.piggy_agents import _AGENT_EMAIL_MIN_AGE_MIN
    assert _AGENT_EMAIL_MIN_AGE_MIN.get("barao", 0) >= 45
    assert _AGENT_EMAIL_MIN_AGE_MIN.get("barao") == _AGENT_EMAIL_MIN_AGE_MIN.get("faria_limer")


def test_email_do_barao_segura_evento_recem_mudado(monkeypatch):
    """A carência aplicada de fato no runner de e-mail, para o kind barao."""
    from datetime import datetime, timedelta, timezone
    import db
    import core.services.piggy_agents as pa
    import core.services.email_service as es
    import core.services.plan_service as ps

    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    def _arm(fired_at):
        sent, claimed = [], []
        monkeypatch.setattr(db, "list_agents_pending_email", lambda: [
            {"agent_id": 9, "user_id": 42, "kind": "barao", "config": {}, "last_emailed_at": None}])
        monkeypatch.setattr(db, "list_unemailed_events", lambda aid: [
            {"id": 7, "payload": {"titulo": "T", "mensagem": "M"}, "fired_at": fired_at}])
        monkeypatch.setattr(db, "claim_agent_events_for_email",
                            lambda evs: claimed.extend(e["id"] for e in evs) or [e["id"] for e in evs])
        monkeypatch.setattr(db, "suppress_agent_events", lambda ids: len(ids))
        monkeypatch.setattr(db, "unclaim_agent_events", lambda ids: len(ids))
        monkeypatch.setattr(db, "touch_agent_emailed", lambda aid: None)
        monkeypatch.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": False})
        monkeypatch.setattr(db, "get_user_email", lambda uid: "u@e.com")
        monkeypatch.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
        monkeypatch.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
        monkeypatch.setattr(es, "send_agent_report_email", lambda *a, **k: sent.append(a) or True)
        pa.run_agent_emails_once(now=now)
        return sent, claimed

    sent, claimed = _arm(now - timedelta(minutes=5))    # snapshot recém-mudado
    assert sent == [] and claimed == []                 # segura: pode ser parcial
    sent, claimed = _arm(now - timedelta(hours=2))      # estável
    assert len(sent) == 1 and claimed == [7]


def _arm_agg_email(monkeypatch, *, kind, fired_at, sync_recente, now):
    """Arma run_agent_emails_once pra um agente whole-portfolio."""
    import db
    import core.services.piggy_agents as pa
    import core.services.email_service as es
    import core.services.plan_service as ps
    sent, claimed = [], []
    monkeypatch.setattr(db, "list_agents_pending_email", lambda: [
        {"agent_id": 9, "user_id": 42, "kind": kind, "config": {}, "last_emailed_at": None}])
    monkeypatch.setattr(db, "list_unemailed_events", lambda aid: [
        {"id": 7, "payload": {"titulo": "T", "mensagem": "M"}, "fired_at": fired_at}])
    monkeypatch.setattr(db, "user_synced_within", lambda uid, mins: sync_recente)
    monkeypatch.setattr(db, "claim_agent_events_for_email",
                        lambda evs: claimed.extend(e["id"] for e in evs) or [e["id"] for e in evs])
    monkeypatch.setattr(db, "suppress_agent_events", lambda ids: len(ids))
    monkeypatch.setattr(db, "unclaim_agent_events", lambda ids: len(ids))
    monkeypatch.setattr(db, "touch_agent_emailed", lambda aid: None)
    monkeypatch.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": False})
    monkeypatch.setattr(db, "get_user_email", lambda uid: "u@e.com")
    monkeypatch.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
    monkeypatch.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
    monkeypatch.setattr(es, "send_agent_report_email", lambda *a, **k: sent.append(a) or True)
    pa.run_agent_emails_once(now=now)
    return sent, claimed


def test_agregado_nao_emaila_durante_sync_em_curso(monkeypatch):
    """P1 do Codex (3ª rodada, PR #51): um evento JÁ maduro cujo payload não muda
    no meio de um sync multi-item continuava 'estável', virava e-mail no mesmo
    tick, e o emailed_at recusava a correção do fim do sync — congelando o mês.
    Um sync recente do usuário agora segura o e-mail dos agregados."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    velho = now - timedelta(hours=6)          # bem além dos 45min de carência

    for kind in ("barao", "faria_limer"):
        sent, claimed = _arm_agg_email(monkeypatch, kind=kind, fired_at=velho,
                                       sync_recente=True, now=now)
        assert sent == [] and claimed == [], f"{kind} emailou com sync em curso"

        sent, claimed = _arm_agg_email(monkeypatch, kind=kind, fired_at=velho,
                                       sync_recente=False, now=now)
        assert len(sent) == 1 and claimed == [7], f"{kind} não emailou com sync quieto"


def test_sync_quiet_nao_afeta_agentes_de_delta(monkeypatch):
    """A trava é só dos agregados: o Repórter (sem carência) sai na hora mesmo
    com sync recente — senão um usuário que sinca sempre nunca receberia e-mail."""
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    sent, claimed = _arm_agg_email(monkeypatch, kind="reporter",
                                   fired_at=now - timedelta(minutes=1),
                                   sync_recente=True, now=now)
    assert len(sent) == 1 and claimed == [7]


def test_falha_do_sinal_de_sync_nao_bloqueia_email(monkeypatch):
    """O sinal é otimização: se a query explodir, o e-mail segue pela carência."""
    from datetime import datetime, timedelta, timezone
    import db
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    def _boom(uid, mins):
        raise RuntimeError("db fora do ar")

    monkeypatch.setattr(db, "user_synced_within", _boom)
    sent, claimed = _arm_agg_email(monkeypatch, kind="barao",
                                   fired_at=now - timedelta(hours=6),
                                   sync_recente=False, now=now)
    monkeypatch.setattr(db, "user_synced_within", _boom)   # _arm sobrescreve
    assert len(sent) == 1


def test_sync_empurra_carencia_dos_agregados_no_inicio(user_id):
    """P1 do Codex (4ª rodada, PR #51): entre o início do sync e o primeiro
    commit não existe last_sync_at pra denunciar sync em curso — os itens ainda
    estão buscando dados na Pluggy. Um agregado já maduro poderia ser emailado
    nessa janela. sync_pluggy_user empurra a carência antes de tocar nos itens."""
    import db
    from datetime import datetime, timedelta, timezone
    from db import get_conn

    ag_b = db.activate_agent(user_id, "barao")
    ag_x = db.activate_agent(user_id, "xerife")
    assert db.record_or_refresh_agent_event(
        ag_b["id"], user_id, "barao", "parado:2026-08", {"idle": 1500.0}, valor_impacto=13.0)
    assert db.record_or_refresh_agent_event(
        ag_x["id"], user_id, "xerife", "anomalia:1", {"titulo": "x"}, valor_impacto=0.0)

    antigo = datetime.now(timezone.utc) - timedelta(hours=6)
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("update agent_events set fired_at=%s where user_id=%s", (antigo, user_id))
        c.commit()

    def _fired(kind):
        with get_conn() as c:
            with c.cursor() as cur:
                cur.execute("select fired_at from agent_events where user_id=%s and kind=%s",
                            (user_id, kind))
                row = cur.fetchone()
                assert row is not None, f"evento do {kind} sumiu"
                return row["fired_at"]

    limite = datetime.now(timezone.utc) - timedelta(hours=5)
    assert _fired("barao") < limite and _fired("xerife") < limite

    def _hold(kind):
        with get_conn() as c:
            with c.cursor() as cur:
                cur.execute("select email_hold_until from agent_events"
                            " where user_id=%s and kind=%s", (user_id, kind))
                return cur.fetchone()["email_hold_until"]

    # é o que os caminhos de sync chamam antes de mexer na carteira
    from core.services.piggy_agents import _AGENT_EMAIL_MIN_AGE_MIN
    n = db.hold_agent_emails(user_id, list(_AGENT_EMAIL_MIN_AGE_MIN), 10)
    assert n == 1, "devia segurar só o agregado"

    # o agregado fica segurado pra frente...
    assert _hold("barao") > datetime.now(timezone.utc)
    # ...e o agente de delta não é tocado
    assert _hold("xerife") is None

    # P1 do Codex (7ª rodada): o hold NÃO pode mexer em fired_at — é a data que o
    # usuário vê, a ordenação do feed e a base de disparos_mes/saved_365d. Se
    # empurrasse, um evento de meses atrás voltaria a contar como novo.
    assert _fired("barao") < limite, "hold não pode empurrar fired_at"
    assert _fired("xerife") < limite


def test_sync_pluggy_user_segura_email_antes_dos_itens(monkeypatch):
    """O sync chama o hold ANTES de sincronizar os itens — é isso que cobre a
    janela em que ainda não há nenhum last_sync_at pra denunciar o sync."""
    import db
    import core.services.pluggy_sync as ps

    ordem = []
    monkeypatch.setattr(ps, "get_open_finance_snapshot", lambda uid: {
        "connections": [{"provider": "pluggy", "provider_item_id": "i1"},
                        {"provider": "pluggy", "provider_item_id": "i2"}]})
    monkeypatch.setattr(ps, "sync_pluggy_item",
                        lambda item: ordem.append(f"item:{item}") or {"ok": True})
    monkeypatch.setattr(db, "hold_agent_emails",
                        lambda uid, kinds, mins: ordem.append(("hold", uid, sorted(kinds))) or 1)
    import core.services.piggy_agents as pa
    monkeypatch.setattr(pa, "run_faria_limer_once", lambda **k: {"ok": True})
    monkeypatch.setattr(pa, "run_barao_once", lambda **k: {"ok": True})

    ps.sync_pluggy_user(42)

    assert ordem[0] == ("hold", 42, ["barao", "faria_limer"]), f"hold não veio primeiro: {ordem}"
    assert ordem[1:] == ["item:i1", "item:i2"]


def test_hold_nao_mexe_em_evento_ja_emailado(user_id):
    """O que já virou e-mail é imutável — o hold não pode ressuscitá-lo."""
    import db
    from datetime import datetime, timedelta, timezone
    from db import get_conn

    ag = db.activate_agent(user_id, "barao")
    db.record_or_refresh_agent_event(ag["id"], user_id, "barao", "parado:2026-08",
                                     {"idle": 1500.0}, valor_impacto=13.0)
    antigo = datetime.now(timezone.utc) - timedelta(hours=6)
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("update agent_events set fired_at=%s, emailed_at=now() where user_id=%s",
                        (antigo, user_id))
        c.commit()

    assert db.hold_agent_emails(user_id, ["barao"], 10) == 0
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select fired_at from agent_events where user_id=%s", (user_id,))
            assert cur.fetchone()["fired_at"] < datetime.now(timezone.utc) - timedelta(hours=5)


def test_refresh_manual_segura_email_antes_da_espera(monkeypatch):
    """P1 do Codex (5ª rodada, PR #51): refresh_and_sync_pluggy_user dispara o
    PATCH na Pluggy e ESPERA até OF_REFRESH_WAIT_SEC (18s) antes de chamar
    sync_pluggy_user. O touch de lá vem depois da espera — essa janela inteira
    ficava descoberta. O hold tem que acontecer antes do PATCH."""
    import db
    import core.services.pluggy_sync as ps

    ordem = []
    monkeypatch.setattr(ps, "get_open_finance_snapshot", lambda uid: {
        "connections": [{"provider": "pluggy", "provider_item_id": "i1"}]})
    monkeypatch.setattr(ps, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ps, "update_pluggy_item",
                        lambda item, key=None: ordem.append("PATCH") or True)
    monkeypatch.setattr(ps, "get_pluggy_item", lambda item, key=None: {"status": "UPDATED"})
    monkeypatch.setattr(ps, "sync_pluggy_user",
                        lambda uid: ordem.append("sync") or {"ok": True})
    monkeypatch.setattr(db, "hold_agent_emails",
                        lambda uid, kinds, mins: ordem.append("hold") or 1)

    ps.refresh_and_sync_pluggy_user(42, wait_seconds=0, poll_interval=0.01)

    assert "hold" in ordem, "não segurou o e-mail no refresh manual"
    assert ordem.index("hold") < ordem.index("PATCH"), \
        f"hold precisa vir ANTES do PATCH (e da espera): {ordem}"


def test_hold_agregados_e_fail_soft(monkeypatch):
    """O hold nunca pode derrubar o sync — é proteção, não requisito."""
    import db
    import core.services.pluggy_sync as ps

    def _boom(uid, kinds, mins):
        raise RuntimeError("db fora do ar")

    monkeypatch.setattr(db, "hold_agent_emails", _boom)
    ps._hold_aggregate_emails(42, "teste")          # não pode levantar

    monkeypatch.setattr(ps, "get_open_finance_snapshot", lambda uid: {
        "connections": [{"provider": "pluggy", "provider_item_id": "i1"}]})
    monkeypatch.setattr(ps, "sync_pluggy_item", lambda item: {"ok": True})
    import core.services.piggy_agents as pa
    monkeypatch.setattr(pa, "run_faria_limer_once", lambda **k: {"ok": True})
    monkeypatch.setattr(pa, "run_barao_once", lambda **k: {"ok": True})
    assert ps.sync_pluggy_user(42)["ok"] is True


def test_webhook_por_item_segura_email_antes_das_leituras(monkeypatch):
    """P1 do Codex (6ª rodada, PR #51): em produção o webhook chama
    sync_pluggy_item DIRETO (frontend/routes/open_finance.py), sem passar por
    sync_pluggy_user — então nenhum dos wrappers segurava o e-mail nesse
    caminho, o principal em produção."""
    import db
    import core.services.pluggy_sync as ps

    ordem = []
    monkeypatch.setattr(ps, "get_open_finance_connection_by_item_id",
                        lambda item: {"id": 1, "user_id": 42, "status": "ACTIVE"})
    monkeypatch.setattr(db, "hold_agent_emails",
                        lambda uid, kinds, mins: ordem.append(("hold", uid)) or 1)
    monkeypatch.setattr(ps, "create_pluggy_api_key",
                        lambda: ordem.append("api_key") or "k")
    monkeypatch.setattr(ps, "list_pluggy_accounts",
                        lambda item, key: ordem.append("le_contas") or [])
    monkeypatch.setattr(ps, "save_open_finance_sync", lambda cid, accs: {})
    monkeypatch.setattr(ps, "list_pluggy_investments", lambda item, key: [])
    monkeypatch.setattr(ps, "save_open_finance_investments", lambda cid, invs: {})
    monkeypatch.setattr(ps, "import_open_finance_launches", lambda uid, cid: {})
    monkeypatch.setattr(ps, "import_open_finance_credit", lambda uid, cid: {})
    monkeypatch.setattr(ps, "sync_imported_open_finance_updates", lambda uid, cid: {})

    ps.sync_pluggy_item("item-1")

    assert ("hold", 42) in ordem, "webhook não segurou o e-mail dos agregados"
    assert ordem.index(("hold", 42)) < ordem.index("le_contas"), \
        f"hold precisa vir ANTES das leituras remotas: {ordem}"


def test_refresh_periodico_segura_email_antes_do_patch(monkeypatch):
    """Mesmo P1: o tick periódico (refresh_all_pluggy_items) dispara o PATCH
    direto. Entre o PATCH e o webhook trazer os dados, o evento maduro seguiria
    reivindicável com o valor velho."""
    import db
    import core.services.pluggy_sync as ps

    ordem = []
    monkeypatch.setattr(ps, "list_pluggy_item_ids", lambda uid=None: ["i1", "i2"])
    monkeypatch.setattr(ps, "get_open_finance_connection_by_item_id",
                        lambda item: {"user_id": 42})
    monkeypatch.setattr(db, "hold_agent_emails",
                        lambda uid, kinds, mins: ordem.append(("hold", uid)) or 1)
    monkeypatch.setattr(ps, "update_pluggy_item",
                        lambda item, key=None: ordem.append(f"PATCH:{item}") or True)

    out = ps.refresh_all_pluggy_items(42)

    assert out["triggered"] == 2
    assert ordem[0] == ("hold", 42), f"hold tem que vir antes do 1º PATCH: {ordem}"
    # segura uma vez por usuário, não por item
    assert len([o for o in ordem if o == ("hold", 42)]) == 1


def test_ripe_respeita_o_hold_mesmo_com_evento_maduro(monkeypatch):
    """O hold é o que segura o e-mail; a idade sozinha não basta, porque um
    evento cujo payload não muda durante o sync continua 'maduro'."""
    from datetime import datetime, timedelta, timezone
    import db
    import core.services.piggy_agents as pa
    import core.services.email_service as es
    import core.services.plan_service as ps

    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    velho = now - timedelta(hours=6)

    def _arm(hold_until):
        sent = []
        monkeypatch.setattr(db, "list_agents_pending_email", lambda: [
            {"agent_id": 9, "user_id": 42, "kind": "barao", "config": {}, "last_emailed_at": None}])
        monkeypatch.setattr(db, "list_unemailed_events", lambda aid: [
            {"id": 7, "payload": {"titulo": "T", "mensagem": "M"},
             "fired_at": velho, "email_hold_until": hold_until}])
        monkeypatch.setattr(db, "user_synced_within", lambda uid, mins: False)
        monkeypatch.setattr(db, "claim_agent_events_for_email", lambda evs: [e["id"] for e in evs])
        monkeypatch.setattr(db, "suppress_agent_events", lambda ids: len(ids))
        monkeypatch.setattr(db, "unclaim_agent_events", lambda ids: len(ids))
        monkeypatch.setattr(db, "touch_agent_emailed", lambda aid: None)
        monkeypatch.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": False})
        monkeypatch.setattr(db, "get_user_email", lambda uid: "u@e.com")
        monkeypatch.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
        monkeypatch.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
        monkeypatch.setattr(es, "send_agent_report_email", lambda *a, **k: sent.append(a) or True)
        pa.run_agent_emails_once(now=now)
        return sent

    assert _arm(now + timedelta(minutes=5)) == [], "hold no futuro devia segurar"
    assert len(_arm(now - timedelta(minutes=1))) == 1, "hold vencido devia liberar"
    assert len(_arm(None)) == 1, "sem hold devia liberar"


def test_claim_recusa_evento_com_hold_instalado_depois_da_leitura(user_id):
    """P1 do Codex (8ª rodada, PR #51): o filtro de _ripe roda em MEMÓRIA sobre a
    leitura. Se um sync instalar email_hold_until depois dela, o claim precisa
    recusar assim mesmo — senão o e-mail sai durante o sync e o emailed_at
    congela o agregado do mês. Tirar o hold de fired_at removeu o CAS que antes
    fazia esse claim falhar, então a condição tem que estar no próprio update."""
    import db
    from db import get_conn

    ag = db.activate_agent(user_id, "barao")
    db.record_or_refresh_agent_event(ag["id"], user_id, "barao", "parado:2026-08",
                                     {"idle": 1500.0}, valor_impacto=13.0)
    lidos = db.list_unemailed_events(ag["id"])
    assert len(lidos) == 1
    assert lidos[0]["email_hold_until"] is None      # na leitura, sem hold

    # o sync instala o hold DEPOIS da leitura, antes do claim
    assert db.hold_agent_emails(user_id, ["barao"], 10) == 1

    assert db.claim_agent_events_for_email(lidos) == [], \
        "claim aceitou evento com hold instalado após a leitura"

    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select emailed_at from agent_events where user_id=%s", (user_id,))
            assert cur.fetchone()["emailed_at"] is None, "não podia ter marcado como emailado"

    # o evento segue disponível pro próximo tick, depois do hold vencer
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("update agent_events set email_hold_until = now() - interval '1 minute'"
                        " where user_id=%s", (user_id,))
        c.commit()
    assert db.claim_agent_events_for_email(db.list_unemailed_events(ag["id"])) == [lidos[0]["id"]]


def test_feed_corrige_apos_email_e_nao_reenvia(user_id):
    """P1 do Codex (9ª rodada, PR #51): a janela entre reivindicar e enviar é
    irredutível (I/O externo não é atômico com o banco). Em vez de encurtá-la,
    tiramos o dano: o feed passa a corrigir mesmo depois do e-mail.

    O e-mail é retrato datado, o app é o estado atual — divergirem é correto.
    O que NÃO pode é o evento voltar pra fila e ser reenviado."""
    import db
    from db import get_conn

    ag = db.activate_agent(user_id, "barao")
    key = "parado:2026-08"
    db.record_or_refresh_agent_event(ag["id"], user_id, "barao", key,
                                     {"idle": 21500.0}, valor_impacto=188.0)

    # simula o envio: claim + marca como emailado
    lidos = db.list_unemailed_events(ag["id"])
    assert db.claim_agent_events_for_email(lidos) == [lidos[0]["id"]]
    assert db.list_unemailed_events(ag["id"]) == []

    # sync que começou logo depois do envio traz o valor novo
    assert db.record_or_refresh_agent_event(
        ag["id"], user_id, "barao", key, {"idle": 25000.0}, valor_impacto=218.0) is True

    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select payload, emailed_at from agent_events"
                        " where agent_id=%s and dedupe_key=%s", (ag["id"], key))
            row = cur.fetchone()
    assert row["payload"] == {"idle": 25000.0}, "feed ficou preso no valor velho"
    assert row["emailed_at"] is not None, "não podia ter voltado a ficar pendente"

    # e o principal: não volta pra fila de e-mail (nada de reenvio)
    assert db.list_unemailed_events(ag["id"]) == []
    pend = [a for a in db.list_agents_pending_email() if a["agent_id"] == ag["id"]]
    assert pend == [], "agente voltou pra fila de e-mail após a correção"


def test_alerta_obsoleto_sai_do_feed_mesmo_depois_do_email(user_id):
    """P1 do Codex (10ª rodada, PR #51): ao liberar o refresh após o e-mail eu
    criei uma assimetria — valor errado se corrigia, mas condição que deixou de
    valer não saía. O alerta falso ficaria visível e contando em saved_365d por
    até 365 dias. A correção inversa segue a mesma regra do refresh."""
    import db
    from db import get_conn

    ag = db.activate_agent(user_id, "barao")
    key = "parado:2026-08"
    db.record_or_refresh_agent_event(ag["id"], user_id, "barao", key,
                                     {"idle": 21500.0}, valor_impacto=188.0)

    # o alerta foi enviado por e-mail
    lidos = db.list_unemailed_events(ag["id"])
    assert db.claim_agent_events_for_email(lidos) == [lidos[0]["id"]]

    # depois o usuário move o dinheiro: a condição deixou de valer
    assert db.mark_agent_event_stale(ag["id"], key) is True, \
        "alerta obsoleto não saiu do feed por já ter virado e-mail"

    # some do feed (soft delete: a linha fica, preservando o emailed_at)
    assert db.list_agent_events(user_id, limit=10) == []
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select count(*) as n, min(stale_at) as st, min(emailed_at) as em"
                        " from agent_events where agent_id=%s", (ag["id"],))
            row = cur.fetchone()
    assert row["n"] == 1 and row["st"] is not None
    assert row["em"] is not None, "histórico de entrega tem que sobreviver"
    # e para de contar no impacto acumulado
    ags = [a for a in db.list_agents(user_id) if a["kind"] == "barao"]
    assert float(ags[0]["saved_365d"]) == 0.0, "valor_impacto do alerta falso ainda conta"


def test_condicao_que_volta_no_mesmo_mes_nao_reenvia_email(user_id):
    """P1 do Codex (11ª rodada, PR #51): o hard delete apagava junto o emailed_at,
    que é onde mora o dedupe de entrega. Se a condição voltasse no mesmo mês, a
    linha nova nascia 'não emailada' e o usuário levava um SEGUNDO e-mail de um
    agente que é mensal (last_emailed_at atrasa, não impede)."""
    import db
    from db import get_conn

    ag = db.activate_agent(user_id, "barao")
    key = "parado:2026-08"

    # 1) alerta dispara e vira e-mail
    db.record_or_refresh_agent_event(ag["id"], user_id, "barao", key,
                                     {"idle": 21500.0}, valor_impacto=188.0)
    lidos = db.list_unemailed_events(ag["id"])
    assert db.claim_agent_events_for_email(lidos) == [lidos[0]["id"]]
    assert db.list_unemailed_events(ag["id"]) == []

    # 2) usuário move o dinheiro: condição some do feed e dos contadores
    assert db.mark_agent_event_stale(ag["id"], key) is True
    assert db.list_agent_events(user_id, limit=10) == []
    ags = [a for a in db.list_agents(user_id) if a["kind"] == "barao"]
    assert float(ags[0]["saved_365d"]) == 0.0

    # 3) o dinheiro volta no MESMO mês: evento ressuscita no feed...
    assert db.record_or_refresh_agent_event(
        ag["id"], user_id, "barao", key, {"idle": 30000.0}, valor_impacto=262.0) is True
    feed = db.list_agent_events(user_id, limit=10)
    assert len(feed) == 1 and feed[0]["payload"] == {"idle": 30000.0}

    # ...mas NÃO vira um segundo e-mail: o emailed_at sobreviveu ao soft delete
    assert db.list_unemailed_events(ag["id"]) == [], "evento voltou pra fila de e-mail"
    pend = [a for a in db.list_agents_pending_email() if a["agent_id"] == ag["id"]]
    assert pend == [], "agente voltou pra fila — usuário levaria e-mail duplicado no mês"

    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select count(*) as n, min(emailed_at) as em from agent_events"
                        " where agent_id=%s", (ag["id"],))
            row = cur.fetchone()
    assert row["n"] == 1, "não podia ter criado linha nova com a mesma chave mensal"
    assert row["em"] is not None, "histórico de entrega se perdeu"


def test_evento_obsoleto_sai_das_filas_de_email(user_id):
    """Evento marcado como obsoleto não pode virar e-mail — nem se ainda estivesse
    pendente quando a condição deixou de valer."""
    import db

    ag = db.activate_agent(user_id, "faria_limer")
    key = "rv_concentracao:2026-08"
    db.record_or_refresh_agent_event(ag["id"], user_id, "faria_limer", key,
                                     {"titulo": "PETR4 é 80%"}, valor_impacto=0.0)
    assert len(db.list_unemailed_events(ag["id"])) == 1

    assert db.mark_agent_event_stale(ag["id"], key) is True
    assert db.list_unemailed_events(ag["id"]) == []
    assert [a for a in db.list_agents_pending_email() if a["agent_id"] == ag["id"]] == []


# ── Invariantes do tombstone (stale_at) ──────────────────────────────────────
# Testes de REGRA, não de caso: travam a propriedade em si, então pegam qualquer
# caminho novo que esqueça o tombstone — sem depender de alguém lembrar do caso.

def test_invariante_evento_obsoleto_nao_aparece_em_nenhuma_leitura(user_id):
    """Regra: nenhuma query de leitura devolve evento obsoleto."""
    import db

    ag = db.activate_agent(user_id, "barao")
    key = "parado:2026-08"
    db.record_or_refresh_agent_event(ag["id"], user_id, "barao", key,
                                     {"idle": 21500.0}, valor_impacto=188.0)
    db.mark_agent_event_stale(ag["id"], key)

    assert db.list_agent_events(user_id, limit=20) == [], "feed"
    assert db.list_unemailed_events(ag["id"]) == [], "fila de eventos"
    assert [a for a in db.list_agents_pending_email()
            if a["agent_id"] == ag["id"]] == [], "fila de agentes"
    ags = [a for a in db.list_agents(user_id) if a["kind"] == "barao"]
    assert float(ags[0]["saved_365d"]) == 0.0 and int(ags[0]["fired_30d"]) == 0, "contadores"
    resumo = db.agents_summary(user_id)
    assert int(resumo["disparos_mes"]) == 0 and float(resumo["salvos_ano"]) == 0.0, "resumo"
    assert int(resumo["nao_lidos"]) == 0, "não-lidos"


def test_invariante_evento_obsoleto_nao_e_reivindicado_nem_marcado_visto(user_id):
    """Regra: nenhuma ESCRITA que decide sobre um evento age sobre obsoleto.

    Cobre a corrida real: o runner lê o evento vivo e o detector o marca obsoleto
    logo depois — o filtro das filas roda em memória sobre a leitura, então a
    condição precisa estar no próprio UPDATE."""
    import db
    from db import get_conn

    ag = db.activate_agent(user_id, "barao")
    key = "parado:2026-08"
    db.record_or_refresh_agent_event(ag["id"], user_id, "barao", key,
                                     {"idle": 21500.0}, valor_impacto=188.0)

    lidos = db.list_unemailed_events(ag["id"])          # leitura: ainda vivo
    assert len(lidos) == 1
    db.mark_agent_event_stale(ag["id"], key)            # vira obsoleto depois

    assert db.claim_agent_events_for_email(lidos) == [], \
        "reivindicou evento obsoleto — usuário receberia e-mail de algo que não vale"
    assert db.mark_agent_events_seen(user_id) == 0, \
        "marcou como visto um evento que o usuário não podia ver"

    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select emailed_at, seen_at from agent_events where agent_id=%s",
                        (ag["id"],))
            row = cur.fetchone()
    assert row["emailed_at"] is None and row["seen_at"] is None

    # e ao ressuscitar, o evento conta como não lido (não herda um seen_at falso)
    db.record_or_refresh_agent_event(ag["id"], user_id, "barao", key,
                                     {"idle": 30000.0}, valor_impacto=262.0)
    assert int(db.agents_summary(user_id)["nao_lidos"]) == 1
