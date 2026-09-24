""""Sim" logo depois de uma oferta da IA vai para a IA, não para o "não entendi".

Relato do dono (WhatsApp, 2026-09-24): a IA respondeu a um desabafo com "Quer
que eu mostre suas maiores despesas ou top categorias do mês?", sem guardar
pendência. O "Sim" classifica como `confirm.yes` com confiança 1.0, então não
caía no fallback de IA do `handle_incoming`, e o `route()` sem pendência
devolvia `NOT_UNDERSTOOD_MSG`.
"""
import db
from db.connection import get_conn
from core.intent_router import NOT_UNDERSTOOD_MSG
from _pendencia_credito_helpers import diga, novo_uid

OFERTA = ("🐷 Eu entendo, às vezes os gastos podem surpreender. Quer que eu "
          "mostre suas maiores despesas ou top categorias do mês?")


def _com_ia(monkeypatch):
    uid = novo_uid()
    chamadas = []
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda _uid: True)
    monkeypatch.setattr("core.services.ai_chat.chat",
                        lambda *a, **k: chamadas.append(a[1]) or "resposta do agente")
    return uid, chamadas


def _ia_disse(uid, texto, minutos_atras=0):
    msg_id = db.ai_append_message(uid, "assistant", texto)
    if minutos_atras:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "update ai_messages set created_at = now() - %s * interval '1 minute' "
                "where id = %s and user_id = %s",
                (minutos_atras, msg_id, uid),
            )
            conn.commit()


def test_sim_depois_da_oferta_vai_para_a_ia(monkeypatch):
    for resposta in ("Sim", "sim", "não", "Nao"):
        uid, chamadas = _com_ia(monkeypatch)
        db.ai_append_message(uid, "user", "Pqp como que eu gastei tanto")
        _ia_disse(uid, OFERTA)

        assert diga(uid, resposta) == "resposta do agente", resposta
        assert chamadas == [resposta]


def test_pergunta_terminada_em_emoji_tambem_conta(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, "Quer ver seus top gastos do mês? 🐷")

    assert diga(uid, "sim") == "resposta do agente"


def test_sim_sem_pergunta_da_ia_continua_nao_entendido(monkeypatch):
    """Positivo do outro lado: "sim" solto não vira chamada à IA (cota)."""
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, "🐷 Tamo junto! Qualquer coisa, é só chamar.")

    assert diga(uid, "sim") == NOT_UNDERSTOOD_MSG
    assert chamadas == []


def test_pergunta_velha_da_ia_nao_captura_o_sim(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, OFERTA, minutos_atras=11)

    assert diga(uid, "sim") == NOT_UNDERSTOOD_MSG
    assert chamadas == []


def test_historico_de_outro_usuario_nao_conta(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(novo_uid(), OFERTA)

    assert diga(uid, "sim") == NOT_UNDERSTOOD_MSG
    assert chamadas == []
