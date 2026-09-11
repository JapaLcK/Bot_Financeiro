"""TTL do estado in-memory do tutorial do WhatsApp (adapters/whatsapp/wa_tutorial.py).

Sem o TTL, quem abandona o tutorial no meio fica em `_STATE` para sempre,
chaveado por wa_id, no processo do WhatsApp.
"""
import time

import pytest

from adapters.whatsapp import wa_tutorial

ABANDONOU = "5511999990000"
ATIVO = "5511888880000"


@pytest.fixture(autouse=True)
def _sem_envio(monkeypatch):
    monkeypatch.setattr(wa_tutorial, "send_interactive_buttons", lambda **kw: None)
    monkeypatch.setattr(wa_tutorial, "send_text", lambda **kw: None)
    wa_tutorial._STATE.clear()
    yield
    wa_tutorial._STATE.clear()


def test_abandonado_some_depois_do_ttl():
    """Negativo: sem a poda em _set_step, a entrada velha continua no dicionário."""
    wa_tutorial._STATE[ABANDONOU] = {
        "step": "step_3",
        "at": time.time() - wa_tutorial._TTL - 1,
    }

    wa_tutorial.handle_tutorial_button(ATIVO, "tut_start")

    assert ABANDONOU not in wa_tutorial._STATE
    assert wa_tutorial._STATE[ATIVO]["step"] == "step_1"


def test_usuario_ativo_nao_e_expulso(monkeypatch):
    """Positivo: o TTL é de INATIVIDADE — clicando dentro da janela, o tour
    pode durar mais que _TTL sem o usuário perder o estado."""
    agora = {"t": 1_000_000.0}
    monkeypatch.setattr(wa_tutorial.time, "time", lambda: agora["t"])

    wa_tutorial.handle_tutorial_button(ATIVO, "tut_start")
    agora["t"] += wa_tutorial._TTL * 0.9
    wa_tutorial.handle_tutorial_button(ATIVO, "tut_2")
    agora["t"] += wa_tutorial._TTL * 0.9  # 1,8×_TTL desde o começo do tour
    wa_tutorial.handle_tutorial_button(ATIVO, "tut_cc")

    assert wa_tutorial._STATE[ATIVO]["step"] == "step_cc"
