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
