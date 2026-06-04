"""
Cobre o roteamento de `core/services/ai_chat_commands.handle_ai_chat_command`.

Comandos determinísticos têm precedência sobre a IA: um user Pro sem prefix
"piggy/pergunta/ia" e sem pending da IA recebe None aqui, pra que o
classify/route trate o comando (saldo, listar, "apagar CC17"). A IA só entra
quando há prefix explícito, pending da IA, ou pelo fallback de baixa confiança
em handle_incoming.

Estrutura dos cenários:
  - Free sem prefix → None (segue fluxo tradicional)
  - Free com prefix → gate "PigBank+"
  - Free com pending → gate + limpa pending
  - Pro sem prefix → None (comando determinístico tem precedência)
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


# ─── Pro sem prefix → None (comando determinístico tem precedência) ──────────


def test_pro_sem_prefix_retorna_none_deixa_classifier_rodar(patches):
    # Pro sem prefix e sem pending: não interceptamos aqui. Devolve None pra
    # que o comando determinístico (saldo→balance.check) rode pelo route().
    # A IA só entra no fallback de handle_incoming se o classifier falhar.
    patches["is_pro"] = True
    out = mod.handle_ai_chat_command(42, "saldo", platform="whatsapp")
    assert out is None
    assert patches["ai_called_with"] is None


def test_pro_msg_complexa_sem_prefix_tambem_retorna_none(patches):
    # Mesmo uma pergunta solta sem prefix devolve None aqui — o roteamento
    # pra IA acontece no fallback de baixa confiança em handle_incoming, não
    # neste gate.
    patches["is_pro"] = True
    out = mod.handle_ai_chat_command(42, "quanto gastei em alimentação esse mês?", platform="discord")
    assert out is None
    assert patches["ai_called_with"] is None


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
    # Prefix força o caminho da IA, que aqui está mockada pra falhar.
    out = mod.handle_ai_chat_command(42, "piggy saldo", platform="whatsapp")
    assert out is not None
    assert "🐷" in out
    assert "ruim" in out.lower() or "suporte" in out.lower()


def test_pending_action_check_falha_continua_como_sem_pending(patches, monkeypatch):
    patches["is_pro"] = True

    def boom(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(mod.db, "ai_get_pending_action", boom)

    # Com prefix, a IA é chamada normalmente apesar da falha no check de pending.
    out = mod.handle_ai_chat_command(42, "piggy saldo", platform="whatsapp")
    assert out == "🐷 IA disse: saldo"


# ─── Guard anti-sequestro: comando determinístico não é engolido por ──────────
# ─── ai_pending órfão (regressão das confirmações empilhadas/inconsistentes) ───
#
# O ai_pending só existe pra confirmar um write (delete, etc.) esperando
# "sim"/"não". Se o user, em vez de confirmar, manda OUTRO comando determinístico
# claro, ele abandonou a confirmação: o guard limpa o pending órfão e devolve
# None, pra o comando rodar pelo fluxo determinístico em vez de a IA gerar uma
# 2ª confirmação (com formatação diferente — origem do bug da screenshot).


def test_pending_orfao_comando_deterministico_limpa_e_devolve_none(patches):
    patches["is_pro"] = True
    patches["pending"] = {"tool_name": "delete_launch"}
    out = mod.handle_ai_chat_command(42, "Apagar id 5", platform="whatsapp")
    assert out is None, "comando determinístico deve seguir pelo route(), não pela IA"
    assert patches["clear_pending_called"] is True, "pending órfão deve ser limpo"
    assert patches["ai_called_with"] is None, "IA não pode gerar uma 2ª confirmação"


def test_pending_orfao_outro_comando_deterministico_tambem_limpa(patches):
    # "saldo" classifica com confiança máxima → mesmo tratamento.
    patches["is_pro"] = True
    patches["pending"] = {"tool_name": "delete_all_launches"}
    out = mod.handle_ai_chat_command(42, "saldo", platform="whatsapp")
    assert out is None
    assert patches["clear_pending_called"] is True
    assert patches["ai_called_with"] is None


def test_pending_sim_nao_dispara_guard_vai_pra_ia(patches):
    """'sim' é a resposta esperada da confirmação — o guard NÃO pode limpar o
    pending; a msg segue pra IA retomar a ação."""
    patches["is_pro"] = True
    patches["pending"] = {"tool_name": "delete_launch"}
    out = mod.handle_ai_chat_command(42, "sim", platform="whatsapp")
    assert out == "🐷 IA disse: sim"
    assert patches["clear_pending_called"] is False
    assert patches["ai_called_with"] is not None


def test_pending_nao_dispara_guard_vai_pra_ia(patches):
    patches["is_pro"] = True
    patches["pending"] = {"tool_name": "delete_launch"}
    out = mod.handle_ai_chat_command(42, "não", platform="whatsapp")
    assert out == "🐷 IA disse: não"
    assert patches["clear_pending_called"] is False


def test_pending_frase_ambigua_nao_dispara_guard_vai_pra_ia(patches):
    """Frase de baixa confiança (out_of_scope) NÃO mata o pending — pode ser
    continuação da conversa; segue pra IA, que retoma ou cancela."""
    patches["is_pro"] = True
    patches["pending"] = {"tool_name": "delete_launch"}
    out = mod.handle_ai_chat_command(42, "na verdade deixa quieto", platform="whatsapp")
    assert patches["clear_pending_called"] is False
    assert patches["ai_called_with"] is not None
