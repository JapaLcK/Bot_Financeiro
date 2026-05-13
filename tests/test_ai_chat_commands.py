"""
Cobre o roteamento de `core/services/ai_chat_commands.handle_ai_chat_command`,
incluindo o comportamento da Sprint 3: Pro user não precisa mais de prefix
"piggy/pergunta/ia" — toda mensagem cai na IA.

Estrutura dos cenários:
  - Free sem prefix → None (segue fluxo tradicional)
  - Free com prefix → gate "PigBank+"
  - Free com pending → gate + limpa pending
  - Pro sem prefix → IA é chamada com texto cru
  - Pro com prefix → IA é chamada com o prefix removido
  - Pro com pending → IA é chamada
  - Texto vazio → None
  - IA falha (exception) → mensagem de erro padrão

Mocka `is_pro`, `db.ai_get_pending_action`/`ai_clear_pending_action` e o
`ai_chat.chat` real pra isolar o gate. Não toca em DB nem em LLM.
"""
from __future__ import annotations

import sys
import types

import pytest

from core.services import ai_chat_commands as mod


@pytest.fixture
def patches(monkeypatch):
    """Helper pra mockar is_pro, pending action e ai_chat.chat de uma vez."""
    state = {
        "is_pro": False,
        "pending": None,
        "ai_called_with": None,
        "ai_raises": False,
        "clear_pending_called": False,
    }

    def fake_is_pro(user_id):
        return state["is_pro"]

    def fake_get_pending(user_id):
        return state["pending"]

    def fake_clear_pending(user_id):
        state["clear_pending_called"] = True

    # ai_chat.chat é importado dentro da função; intercepta via sys.modules
    fake_ai_chat_mod = types.ModuleType("core.services.ai_chat")

    def fake_chat(user_id, text, *, monthly_limit, platform):
        if state["ai_raises"]:
            raise RuntimeError("simulated AI failure")
        state["ai_called_with"] = {
            "user_id": user_id,
            "text": text,
            "platform": platform,
        }
        return f"🐷 IA disse: {text}"

    fake_ai_chat_mod.chat = fake_chat
    monkeypatch.setitem(sys.modules, "core.services.ai_chat", fake_ai_chat_mod)

    monkeypatch.setattr(mod, "is_pro", fake_is_pro)
    monkeypatch.setattr(mod.db, "ai_get_pending_action", fake_get_pending)
    monkeypatch.setattr(mod.db, "ai_clear_pending_action", fake_clear_pending)

    return state


# ─── Free sem prefix → None ─────────────────────────────────────────────────


def test_free_sem_prefix_retorna_none(patches):
    patches["is_pro"] = False
    out = mod.handle_ai_chat_command(1, "saldo", platform="whatsapp")
    assert out is None
    assert patches["ai_called_with"] is None


def test_free_msg_vazia_retorna_none(patches):
    patches["is_pro"] = False
    assert mod.handle_ai_chat_command(1, "", platform="whatsapp") is None
    assert mod.handle_ai_chat_command(1, "   ", platform="whatsapp") is None


# ─── Free com prefix → gate Pro ─────────────────────────────────────────────


def test_free_com_prefix_piggy_recebe_gate_pro(patches):
    patches["is_pro"] = False
    out = mod.handle_ai_chat_command(1, "piggy quanto gastei?", platform="whatsapp")
    assert out is not None
    assert "PigBank+" in out
    assert "precos" in out
    assert patches["ai_called_with"] is None


def test_free_com_prefix_pergunta_recebe_gate_pro(patches):
    patches["is_pro"] = False
    out = mod.handle_ai_chat_command(1, "pergunta meu saldo", platform="whatsapp")
    assert out is not None and "PigBank+" in out


def test_free_com_prefix_so_piggy_puro_tambem_recebe_gate(patches):
    patches["is_pro"] = False
    out = mod.handle_ai_chat_command(1, "piggy", platform="whatsapp")
    assert out is not None and "PigBank+" in out


def test_free_com_pending_recebe_gate_e_limpa_pending(patches):
    patches["is_pro"] = False
    patches["pending"] = {"some": "pending"}
    out = mod.handle_ai_chat_command(1, "sim", platform="whatsapp")
    assert "PigBank+" in out
    assert patches["clear_pending_called"] is True
    assert patches["ai_called_with"] is None


# ─── Pro sem prefix → IA com texto cru (Sprint 3) ───────────────────────────


def test_pro_sem_prefix_chama_ia_com_texto_cru(patches):
    patches["is_pro"] = True
    out = mod.handle_ai_chat_command(42, "saldo", platform="whatsapp")
    assert out == "🐷 IA disse: saldo"
    assert patches["ai_called_with"] == {
        "user_id": 42,
        "text": "saldo",
        "platform": "whatsapp",
    }


def test_pro_msg_complexa_sem_prefix_chama_ia(patches):
    patches["is_pro"] = True
    out = mod.handle_ai_chat_command(42, "quanto gastei em alimentação esse mês?", platform="discord")
    assert "IA disse" in out
    assert patches["ai_called_with"]["text"] == "quanto gastei em alimentação esse mês?"
    assert patches["ai_called_with"]["platform"] == "discord"


# ─── Pro com prefix → prefix é removido ─────────────────────────────────────


def test_pro_com_prefix_piggy_remove_prefix_antes_de_chamar_ia(patches):
    patches["is_pro"] = True
    out = mod.handle_ai_chat_command(42, "piggy saldo", platform="whatsapp")
    assert out == "🐷 IA disse: saldo"
    assert patches["ai_called_with"]["text"] == "saldo"


def test_pro_com_prefix_pergunta_remove_prefix(patches):
    patches["is_pro"] = True
    out = mod.handle_ai_chat_command(42, "pergunta, meu saldo?", platform="whatsapp")
    assert patches["ai_called_with"]["text"] == "meu saldo?"


def test_pro_com_prefix_ia_remove_prefix(patches):
    patches["is_pro"] = True
    out = mod.handle_ai_chat_command(42, "ia: quanto devo?", platform="whatsapp")
    assert patches["ai_called_with"]["text"] == "quanto devo?"


def test_pro_com_piggy_puro_chama_ia_com_texto_original(patches):
    """`piggy` sozinho não deve ser engolido como prefix vazio — IA recebe
    o texto cru pra tratar como saudação ambígua."""
    patches["is_pro"] = True
    out = mod.handle_ai_chat_command(42, "piggy", platform="whatsapp")
    assert patches["ai_called_with"]["text"] == "piggy"


# ─── Pro com pending → IA é chamada ─────────────────────────────────────────


def test_pro_com_pending_chama_ia(patches):
    patches["is_pro"] = True
    patches["pending"] = {"some": "pending"}
    out = mod.handle_ai_chat_command(42, "sim", platform="whatsapp")
    assert out == "🐷 IA disse: sim"


def test_pro_com_pending_e_prefix_remove_prefix(patches):
    patches["is_pro"] = True
    patches["pending"] = {"some": "pending"}
    out = mod.handle_ai_chat_command(42, "piggy sim", platform="whatsapp")
    # Pending + prefix: remove o prefix pra IA não receber "piggy sim"
    assert patches["ai_called_with"]["text"] == "sim"


# ─── Erros ──────────────────────────────────────────────────────────────────


def test_ia_falha_retorna_mensagem_de_erro_padrao(patches):
    patches["is_pro"] = True
    patches["ai_raises"] = True
    out = mod.handle_ai_chat_command(42, "saldo", platform="whatsapp")
    assert out is not None
    assert "🐷" in out
    assert "ruim" in out.lower() or "suporte" in out.lower()


def test_pending_action_check_falha_continua_como_sem_pending(patches, monkeypatch):
    patches["is_pro"] = True

    def boom(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod.db, "ai_get_pending_action", boom)

    out = mod.handle_ai_chat_command(42, "saldo", platform="whatsapp")
    # IA continua sendo chamada — a falha no pending é só warning
    assert out == "🐷 IA disse: saldo"
