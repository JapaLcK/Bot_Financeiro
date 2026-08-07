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

class _FakeCM:
    """Context manager mínimo pra get_conn()/conn.cursor() — o _month_stats
    real é substituído, então o cursor nunca é usado de verdade."""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCM()


def _arm_reporter(monkeypatch, *, opted_out: bool):
    import core.services.piggy_agents as pa
    import db
    import core.services.email_service as es
    from datetime import date

    sent: list = []

    monkeypatch.setattr(pa, "get_conn", lambda: _FakeCM())
    monkeypatch.setattr(
        pa, "_month_stats",
        lambda cur, uid, a, b: {"entrou": 1000.0, "saiu": 500.0, "aportes": 100.0, "sobrou": 400.0},
    )
    monkeypatch.setattr(db, "record_agent_event", lambda *a, **k: True)  # manchete nova
    monkeypatch.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": opted_out})
    monkeypatch.setattr(db, "get_user_email", lambda uid: "user@example.com")
    monkeypatch.setattr(es, "send_agent_report_email", lambda *a, **k: sent.append(a))

    agent = {"agent_id": 1, "user_id": 42, "kind": "reporter"}
    pa._reporter_run_for_user(agent, date(2026, 8, 5))
    return sent


def test_reporter_nao_envia_email_para_quem_descadastrou(monkeypatch):
    sent = _arm_reporter(monkeypatch, opted_out=True)
    assert sent == []


def test_reporter_envia_email_quando_nao_descadastrado(monkeypatch):
    sent = _arm_reporter(monkeypatch, opted_out=False)
    assert len(sent) == 1


# ── Beta dos Agentes: allowlist por e-mail (lançamento em fases) ──────────────

def test_agents_ui_enabled_default_so_emails_de_teste(monkeypatch):
    import core.services.plan_service as ps
    monkeypatch.delenv("AGENTS_UI_ENABLED", raising=False)
    monkeypatch.delenv("AGENTS_BETA_EMAILS", raising=False)
    monkeypatch.delenv("AGENTS_BETA_USER_IDS", raising=False)
    assert ps.agents_ui_enabled(1, "lucaskuramoti06@gmail.com") is True
    assert ps.agents_ui_enabled(1, "HIAGOJO2016@gmail.com") is True   # case-insensitive
    assert ps.agents_ui_enabled(1, "estranho@example.com") is False


def test_agents_ui_enabled_flag_global_abre_geral(monkeypatch):
    import core.services.plan_service as ps
    monkeypatch.setenv("AGENTS_UI_ENABLED", "1")
    assert ps.agents_ui_enabled(1, "qualquer@example.com") is True


def test_agents_ui_enabled_env_sobrescreve_default(monkeypatch):
    import core.services.plan_service as ps
    monkeypatch.delenv("AGENTS_UI_ENABLED", raising=False)
    monkeypatch.setenv("AGENTS_BETA_EMAILS", "novo@example.com")
    assert ps.agents_ui_enabled(1, "novo@example.com") is True
    assert ps.agents_ui_enabled(1, "lucaskuramoti06@gmail.com") is False
