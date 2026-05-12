"""
Cobre a telemetria de fallback da IA — quando ela reconhece pergunta in-scope
mas sem tool adequada, chama `report_out_of_scope` que grava em
`ai_fallback_log` e devolve mensagem padrão.
"""
import db
from core.services.ai_chat._context import CURRENT_USER_MESSAGE
from core.services.ai_chat.tools.meta import _report_out_of_scope_execute


def _read_logs(user_id: int):
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select question, ai_reason from ai_fallback_log "
                "where user_id = %s order by created_at asc",
                (user_id,),
            )
            return cur.fetchall()


def test_report_out_of_scope_grava_log_e_devolve_msg_padrao(user_id):
    token = CURRENT_USER_MESSAGE.set("qual meu gasto por dia da semana?")
    try:
        msg = _report_out_of_scope_execute(user_id, {"reason": "breakdown por dia da semana"})
    finally:
        CURRENT_USER_MESSAGE.reset(token)

    assert "além do que consigo" in msg
    assert "Posso te ajudar com" in msg
    assert "dashboard" in msg

    logs = _read_logs(user_id)
    assert len(logs) == 1
    assert logs[0]["question"] == "qual meu gasto por dia da semana?"
    assert logs[0]["ai_reason"] == "breakdown por dia da semana"


def test_report_out_of_scope_sem_reason(user_id):
    token = CURRENT_USER_MESSAGE.set("alguma coisa estranha")
    try:
        msg = _report_out_of_scope_execute(user_id, {})
    finally:
        CURRENT_USER_MESSAGE.reset(token)

    assert "dashboard" in msg

    logs = _read_logs(user_id)
    assert len(logs) == 1
    assert logs[0]["ai_reason"] is None


def test_report_out_of_scope_trunca_pergunta_longa(user_id):
    long_q = "a" * 5000
    token = CURRENT_USER_MESSAGE.set(long_q)
    try:
        _report_out_of_scope_execute(user_id, {"reason": "x"})
    finally:
        CURRENT_USER_MESSAGE.reset(token)

    logs = _read_logs(user_id)
    assert len(logs[0]["question"]) <= 2000


def test_report_out_of_scope_nao_grava_question_vazia(user_id):
    # Sem CURRENT_USER_MESSAGE setado → cai no fallback "(pergunta não capturada)"
    msg = _report_out_of_scope_execute(user_id, {"reason": "test"})
    assert "dashboard" in msg

    logs = _read_logs(user_id)
    # A pergunta foi "(pergunta não capturada)", logada
    assert len(logs) == 1
    assert "não capturada" in logs[0]["question"]


def test_log_ai_fallback_silencioso_em_erro(user_id):
    """Telemetria não quebra fluxo — se inserir der pau, retorna sem erro."""
    # Inserir com user_id inexistente normalmente quebraria a FK, mas
    # log_ai_fallback engole exceção silenciosamente.
    db.log_ai_fallback(99999999999, "pergunta de fantasma", "ghost")
    # Se chegou aqui, não levantou exceção. ✓


def test_log_ai_fallback_question_vazia_eh_ignorada(user_id):
    db.log_ai_fallback(user_id, "", "x")
    db.log_ai_fallback(user_id, "   ", "x")
    assert _read_logs(user_id) == []
