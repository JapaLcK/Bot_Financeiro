"""
Cobre `core/services/billing_commands.handle_billing_command`:

- Triggers de "assinar" / "cancelar" / "plano" reconhecidos (incluindo puros).
- Gate de pending action: comando puro durante confirmação cede o turno.
- Texto não relacionado retorna None.
- Display "Status: Inativo" pra user Pro com webhook nunca rodou vira "Ativo".
- Free pedindo "cancelar" puro recebe msg "tá no Free".

Mocka `is_pro`, `db.ai_get_pending_action`, `db.get_auth_user` e
`core.dashboard_links.build_dashboard_link` pra isolar o gate.
"""
from __future__ import annotations

import pytest

from core.services import billing_commands as mod


@pytest.fixture
def patches(monkeypatch):
    state = {
        "is_pro": False,
        "pending": None,
        "pending_raises": False,
        "auth_user": {"plan": "free", "last_payment_status": None, "plan_expires_at": None},
        "dashboard_link": "https://pigbankai.com/d/TOKEN?next=/precos",
    }

    def fake_is_pro(user_id):
        return state["is_pro"]

    def fake_get_pending(user_id):
        if state["pending_raises"]:
            raise RuntimeError("db down")
        return state["pending"]

    def fake_get_auth(user_id):
        return state["auth_user"]

    def fake_dashboard_link(user_id, hours=None, next_path=None):
        return state["dashboard_link"]

    import sys, types
    fake_plan_service = types.ModuleType("core.services.plan_service")
    fake_plan_service.is_pro = fake_is_pro
    monkeypatch.setitem(sys.modules, "core.services.plan_service", fake_plan_service)

    import db
    monkeypatch.setattr(db, "ai_get_pending_action", fake_get_pending, raising=False)
    monkeypatch.setattr(db, "get_auth_user", fake_get_auth, raising=False)

    # build_dashboard_link foi importado direto pra namespace local do mod,
    # então patchamos a referência lá em vez de no core.dashboard_links.
    monkeypatch.setattr(mod, "build_dashboard_link", fake_dashboard_link)

    return state


# ─── Triggers reconhecidos ──────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "assinar",
    "assinar plano",
    "assinar pigbank+",
    "fazer upgrade",
    "upgrade",
    "quero pro",
    "renovar",
    "renovar plano",
    "/assinar",
    "/assinar plano",
])
def test_assinar_triggers_chamam_handle_assinar(text, patches):
    out = mod.handle_billing_command(99, text, platform="whatsapp")
    assert out is not None
    # Free sem pending → mensagem com link de checkout
    assert "PigBank+" in out
    assert "pigbankai.com" in out


@pytest.mark.parametrize("text", [
    "cancelar",
    "cancelar plano",
    "cancelar assinatura",
    "encerrar plano",
    "/cancelar",
])
def test_cancelar_triggers_chamam_handle_cancelar(text, patches):
    patches["is_pro"] = True
    out = mod.handle_billing_command(99, text, platform="whatsapp")
    assert out is not None
    assert "cancelar" in out.lower() or "portal" in out.lower()


@pytest.mark.parametrize("text", [
    "plano",
    "meu plano",
    "minha assinatura",
    "ver plano",
    "qual meu plano",
    "/plano",
])
def test_plano_triggers_chamam_handle_plano(text, patches):
    out = mod.handle_billing_command(99, text, platform="whatsapp")
    assert out is not None
    # Free: deve mostrar info de plano Free
    assert "Free" in out or "PigBank" in out


# ─── Pending action cede o turno ────────────────────────────────────────────


def test_cancelar_puro_com_pending_retorna_none(patches):
    """Bug clássico: user confirmando delete manda "cancelar" pra desistir.
    Se o billing pegasse, viraria comando de billing. Tem que ceder."""
    patches["pending"] = {"some": "pending"}
    out = mod.handle_billing_command(99, "cancelar", platform="whatsapp")
    assert out is None


def test_assinar_puro_com_pending_retorna_none(patches):
    patches["pending"] = {"some": "pending"}
    out = mod.handle_billing_command(99, "assinar", platform="whatsapp")
    assert out is None


def test_assinar_plano_com_pending_tambem_retorna_none(patches):
    """Comando completo + pending: pending tem prioridade pra evitar
    estado preso."""
    patches["pending"] = {"some": "pending"}
    out = mod.handle_billing_command(99, "assinar plano", platform="whatsapp")
    assert out is None


def test_pending_check_falha_nao_bloqueia_billing(patches):
    """Se db.ai_get_pending_action lança, segue pra resposta normal — não
    pode quebrar o fluxo de billing por causa de check defensivo."""
    patches["pending_raises"] = True
    out = mod.handle_billing_command(99, "assinar plano", platform="whatsapp")
    assert out is not None  # respondeu normal


# ─── Texto não relacionado ──────────────────────────────────────────────────


def test_texto_aleatorio_retorna_none(patches):
    assert mod.handle_billing_command(99, "saldo", platform="whatsapp") is None
    assert mod.handle_billing_command(99, "gastei 50 no mercado", platform="whatsapp") is None
    assert mod.handle_billing_command(99, "", platform="whatsapp") is None
    assert mod.handle_billing_command(99, "   ", platform="whatsapp") is None


def test_match_negativo_nao_consulta_db(patches, monkeypatch):
    """Otimização: texto que não bate em nenhum trigger sai sem tocar no
    DB. Importante porque toda mensagem do bot passa por aqui."""
    called = {"hit": False}
    import db
    def boom(_uid):
        called["hit"] = True
        raise RuntimeError("não devia ter chamado")
    monkeypatch.setattr(db, "ai_get_pending_action", boom, raising=False)

    mod.handle_billing_command(99, "saldo", platform="whatsapp")
    assert called["hit"] is False


# ─── Display: Pro com webhook nunca rodou ───────────────────────────────────


def test_plano_pro_sem_webhook_mostra_ativo(patches):
    patches["is_pro"] = True
    patches["auth_user"] = {
        "plan": "pro",
        "last_payment_status": None,
        "plan_expires_at": None,
    }
    out = mod.handle_billing_command(99, "plano", platform="whatsapp")
    assert "Status: Ativo" in out
    # Não deve dizer "Inativo" — bug que o Lucas viu
    assert "Inativo" not in out
    assert "Renovação" in out


def test_plano_pro_status_inactive_explicito_tambem_vira_ativo(patches):
    """`last_payment_status="inactive"` herdado também conta como
    uninitialized."""
    patches["is_pro"] = True
    patches["auth_user"] = {
        "plan": "pro",
        "last_payment_status": "inactive",
        "plan_expires_at": None,
    }
    out = mod.handle_billing_command(99, "plano", platform="whatsapp")
    assert "Status: Ativo" in out
    assert "Inativo" not in out


def test_plano_pro_status_trialing_mostra_trial(patches):
    from datetime import datetime, timezone, timedelta
    patches["is_pro"] = True
    expires = datetime.now(timezone.utc) + timedelta(days=5)
    patches["auth_user"] = {
        "plan": "pro",
        "last_payment_status": "trialing",
        "plan_expires_at": expires,
    }
    out = mod.handle_billing_command(99, "plano", platform="whatsapp")
    assert "Trial em andamento" in out
    assert "Fim do trial" in out


def test_plano_pro_status_past_due_mostra_atraso(patches):
    patches["is_pro"] = True
    patches["auth_user"] = {
        "plan": "pro",
        "last_payment_status": "past_due",
        "plan_expires_at": None,
    }
    out = mod.handle_billing_command(99, "plano", platform="whatsapp")
    assert "atraso" in out.lower()


# ─── Free pedindo cancelar ──────────────────────────────────────────────────


def test_free_cancelar_recebe_mensagem_explicativa(patches):
    patches["is_pro"] = False
    out = mod.handle_billing_command(99, "cancelar plano", platform="whatsapp")
    assert "Free" in out
    assert "não tem o que cancelar" in out or "nao tem o que cancelar" in out


# ─── Discord vs WhatsApp ────────────────────────────────────────────────────


def test_discord_usa_double_asterisk_pra_negrito(patches):
    out = mod.handle_billing_command(99, "assinar plano", platform="discord")
    assert "**PigBank+**" in out


def test_whatsapp_usa_single_asterisk_pra_negrito(patches):
    out = mod.handle_billing_command(99, "assinar plano", platform="whatsapp")
    assert "*PigBank+*" in out
    # Não pode ter **
    assert "**PigBank+**" not in out
