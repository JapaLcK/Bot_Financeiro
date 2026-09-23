"""Preferência antiga não autoriza envio semanal depois de downgrade."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.services import plan_service as plans


@pytest.mark.parametrize("tier,expected", [("essencial", 0), ("plus", 1), ("pro", 1)])
def test_whatsapp_gate_antes_do_claim_e_envio(monkeypatch, tier, expected):
    from adapters.whatsapp import wa_app as wa
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setattr(plans, "get_plan_tier", lambda uid: tier)
    monday = datetime(2026, 9, 14, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(wa, "now_tz", lambda: monday)
    monkeypatch.setattr(wa, "_periodic_template_config", lambda kind: {"name": "weekly"})
    monkeypatch.setattr(wa, "_runtime_instance_details", lambda: {})
    monkeypatch.setattr(wa, "list_users_with_weekly_report_enabled", lambda: [100])
    monkeypatch.setattr(wa, "get_daily_report_prefs", lambda uid: {"hour": 9, "minute": 0})
    monkeypatch.setattr(wa, "filtrar_por_acesso", lambda uids: uids)
    monkeypatch.setattr(wa, "list_identities_by_user", lambda uid: [{"provider": "whatsapp", "external_id": "5511999999999"}])
    claims, built, sent = [], [], []
    monkeypatch.setattr(wa, "claim_weekly_report_send", lambda *a: claims.append(a) or True)
    monkeypatch.setattr(wa, "build_weekly_report_summary", lambda *a, **kw: built.append(a) or {})
    monkeypatch.setattr(wa, "_send_periodic_template", lambda *a: sent.append(a))
    wa._periodic_report_tick()
    assert len(claims) == len(built) == len(sent) == expected


@pytest.mark.parametrize("tier,expected", [("essencial", 0), ("plus", 1), ("pro", 1)])
def test_discord_nao_envia_semanal_apos_downgrade(monkeypatch, tier, expected):
    from core.reports import reports_daily as reports
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setattr(plans, "get_plan_tier", lambda uid: tier)
    monkeypatch.setattr(reports, "now_tz", lambda: datetime(2026, 9, 14, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(reports, "list_users_with_weekly_report_enabled", lambda: [100])
    monkeypatch.setattr(reports, "filtrar_por_acesso", lambda uids: uids)
    monkeypatch.setattr(reports, "list_identities_by_user", lambda uid: [{"provider": "discord", "external_id": "100"}])
    built = []
    monkeypatch.setattr(reports, "build_weekly_report_text", lambda *a, **kw: built.append(a) or "Resumo")
    send = AsyncMock()
    bot = SimpleNamespace(fetch_user=AsyncMock(return_value=SimpleNamespace(send=send)))
    asyncio.run(reports._periodic_reports_discord.coro(bot))
    assert len(built) == send.await_count == bot.fetch_user.await_count == expected


def test_classificador_sem_direito_ou_gate_indisponivel_nao_chama_ia(monkeypatch):
    from core.intent_classifier import classify
    import openai
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-no-network")
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: pytest.fail("IA não autorizada"))
    monkeypatch.setattr(plans, "get_plan_tier", lambda uid: "free")
    assert classify("gasto fixo de 100 todo dia 10", user_id=100).intent == "out_of_scope"
    def unavailable(*args):
        raise RuntimeError("plano indisponível")
    monkeypatch.setattr(plans, "plan_gate_ok", unavailable)
    assert classify("gasto fixo de 100 todo dia 10", user_id=100).intent == "out_of_scope"


def test_planos_acesso_sem_assinatura_continua_bloqueado(user_id, monkeypatch):
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as dashboard
    from token_utils import make_dashboard_token
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")
    monkeypatch.setattr(dashboard.limiter, "enabled", False)
    token = make_dashboard_token(user_id, hours=1)
    client = TestClient(dashboard.app)
    for path in (f"/forecast/{user_id}", f"/analytics/{user_id}/kpis", f"/insights/{user_id}/current"):
        assert client.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 402
        other_token = make_dashboard_token(user_id + 1, hours=1)
        assert client.get(path, headers={"Authorization": f"Bearer {other_token}"}).status_code == 403
