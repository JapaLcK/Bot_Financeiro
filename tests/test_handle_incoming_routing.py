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
def free_small_uid():
    import uuid as _uuid
    import db as _db
    from conftest import em_carencia
    uid = int(_uuid.uuid4().int % 1_000_000_000)
    _db.ensure_user(uid)
    return em_carencia(uid)


def _msg(uid: int, text: str) -> IncomingMessage:
    return IncomingMessage(
        platform="whatsapp", user_id=uid, text=text,
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


def test_carencia_com_cota_esgotada_manda_ao_cartao_e_saldo_segue(spy_ai, free_small_uid):
    """Pela conversa: a carência estoura a cota e ouve "atualize o cartão" (não
    "assine", que a /precos recusaria com 409); o assunto seguinte não fica preso."""
    from datetime import date

    import db
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update auth_accounts set ai_messages_this_month = 1000000, ai_month_reset_at = %s "
            "where user_id = %s",
            (date.today().replace(day=1), free_small_uid),
        )
        conn.commit()

    out = hi.handle_incoming(_msg(free_small_uid, "piggy quanto gastei com mercado?"))
    assert spy_ai == []
    from core.services import billing_copy
    # 1.000.000 estoura também a cota do Plus pago: pagar não a devolve este mês.
    assert out[0].text == (
        "🐷 Suas mensagens com o Piggy deste mês acabaram!\n"
        + billing_copy.IA_COTA_EM_CARENCIA_SEM_COTA
    )

    out = hi.handle_incoming(_msg(free_small_uid, "saldo"))
    assert spy_ai == []
    assert "Conta Corrente" in out[0].text
