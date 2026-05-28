"""
Integração: comandos determinísticos têm precedência sobre a IA.

Pra um user Pro, "saldo"/"meus lancamentos"/"apagar CCnn" devem rodar pelo
route() tradicional (sem chamar a IA). A IA só entra com prefix explícito
("piggy ...") ou no fallback de baixa confiança de handle_incoming.

Regressão do bug onde handle_ai_chat_command mandava TODA msg de Pro pra IA,
engolindo os comandos determinísticos.
"""
from __future__ import annotations

import pytest

from core.types import IncomingMessage
import core.handle_incoming as hi


@pytest.fixture
def spy_ai(monkeypatch):
    """Espiona core.services.ai_chat.chat — registra se a IA foi chamada."""
    calls: list[str] = []
    import core.services.ai_chat as ai_chat_mod

    def fake_chat(user_id, text, *, monthly_limit, platform):
        calls.append(text)
        return f"[IA] {text}"

    monkeypatch.setattr(ai_chat_mod, "chat", fake_chat)
    return calls


@pytest.fixture
def pro_small_uid():
    """User Pro com id < 2bi. handle_incoming só re-normaliza ids > 2bi (via
    _internal_user_id), então um id pequeno é estável — espelha os ids
    canônicos internos reais (ex: user prod 88648360). Necessário pra is_pro
    enxergar o plano dentro de handle_incoming."""
    import uuid as _uuid
    import db as _db
    from db.connection import get_conn

    uid = int(_uuid.uuid4().int % 1_000_000_000)  # < 1bi, nunca normalizado
    _db.ensure_user(uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts(user_id, email, password_hash, plan) "
                "values (%s, %s, 'x', 'pro')",
                (uid, f"pro-{uid}@test.local"),
            )
        conn.commit()
    return uid  # _auto_cleanup_orphan_users (conftest) limpa depois


@pytest.fixture
def free_small_uid():
    import uuid as _uuid
    import db as _db
    uid = int(_uuid.uuid4().int % 1_000_000_000)
    _db.ensure_user(uid)
    return uid


def _msg(uid: int, text: str) -> IncomingMessage:
    return IncomingMessage(
        platform="discord", user_id=uid, text=text,
        message_id="1", attachments=[], external_id="", raw={},
    )


def test_pro_saldo_vai_pro_tradicional_sem_ia(spy_ai, pro_small_uid):
    out = hi.handle_incoming(_msg(pro_small_uid, "saldo"))
    assert spy_ai == []
    assert "Conta Corrente" in out[0].text


def test_pro_listar_vai_pro_tradicional_sem_ia(spy_ai, pro_small_uid):
    out = hi.handle_incoming(_msg(pro_small_uid, "meus lancamentos"))
    assert spy_ai == []
    assert "lançament" in out[0].text.lower()


def test_pro_com_prefix_piggy_vai_pra_ia(spy_ai, pro_small_uid):
    out = hi.handle_incoming(_msg(pro_small_uid, "piggy como economizo?"))
    assert spy_ai == ["como economizo?"]
    assert "[IA]" in out[0].text


def test_free_saldo_vai_pro_tradicional(spy_ai, free_small_uid):
    out = hi.handle_incoming(_msg(free_small_uid, "saldo"))
    assert spy_ai == []
    assert "Conta Corrente" in out[0].text
