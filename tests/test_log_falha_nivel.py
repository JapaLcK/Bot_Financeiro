"""O NÍVEL do log é contrato, não estética — e as duas portas têm de concordar.

`_DashboardHandler` (`core/observability.py:23`) está no logger RAIZ e espelha
WARNING e ERROR em `system_event_logs` com `level = record.levelname.lower()`;
`core/admin_dashboard.py:586` conta `backend_errors_24h WHERE level='error'`.
Enquanto existiram DUAS cópias de `_log_falha` — `core/handlers/pending.py`
(warning) e `core/services/ai_chat/tools/launches.py` (error) — a MESMA condição
inflava o contador de erros do admin por uma porta e não pela outra.

Estes testes afirmam o `levelname` e comparam as duas portas na mesma condição.
Um teste que só checa "logou" passa com WARNING e com ERROR, e não mede nada.

Regra medida aqui (a mesma dos `except`, não outra):
  - condição de domínio ESPERADA (`LaunchNoEffects`, `InvestmentLotHasWithdrawal`)
    → warning;
  - falha técnica/inesperada → error, nas duas portas (a 3ª,
    `core/handlers/credit.py`, é o `test_credit_delete_erro_sem_str.py`, que já
    passa pelo mesmo helper importado).
"""
import logging

import db
from core.handlers import pending as h_pending
from core.services.ai_chat.tools import get_tool
from core.services.ai_chat.tools import launches as tool_mod


def _niveis(caplog, prefixo: str) -> list[str]:
    return [r.levelname for r in caplog.records if r.getMessage().startswith(prefixo)]


def _whatsapp_apaga(user_id: int) -> str:
    db.set_pending_action(user_id, "delete_launch",
                          {"launch_id": 4242, "display_id": 9})
    return h_pending.resolve_delete(user_id, confirmed=True)


def _ai_chat_apaga(user_id: int) -> str:
    return get_tool("delete_launch").execute(user_id, {"launch_id": "9"})


def test_condicao_de_dominio_loga_warning_nas_duas_portas(user_id, monkeypatch, caplog):
    """Aporte cujo lote já teve resgate: condição esperada, com contorno pelo
    próprio usuário. Não é incidente do backend — não pode contar como erro em
    porta nenhuma."""
    def explode(uid, launch_id):
        raise db.InvestmentLotHasWithdrawal("lote já teve resgate")

    monkeypatch.setattr(db, "delete_launch_and_rollback", explode)
    monkeypatch.setattr(tool_mod.db, "resolve_user_seq_to_id", lambda uid, lid: 4242)

    with caplog.at_level(logging.WARNING):
        resp_wa = _whatsapp_apaga(user_id)
        resp_ai = _ai_chat_apaga(user_id)

    assert "resgate" in resp_wa.lower() and "resgate" in resp_ai.lower()
    niveis = _niveis(caplog, "delete_launch_lote_com_resgate:")
    assert niveis == ["WARNING", "WARNING"], (
        f"WhatsApp e /ai/chat têm de logar a MESMA condição no MESMO nível: {niveis}"
    )


def test_lancamento_sem_efeitos_loga_warning_nas_duas_portas(user_id, monkeypatch, caplog):
    """A outra condição de domínio, pelo mesmo critério."""
    def explode(uid, launch_id):
        raise db.LaunchNoEffects("lançamento sem 'efeitos'")

    monkeypatch.setattr(db, "delete_launch_and_rollback", explode)
    monkeypatch.setattr(tool_mod.db, "resolve_user_seq_to_id", lambda uid, lid: 4242)

    with caplog.at_level(logging.WARNING):
        _whatsapp_apaga(user_id)
        _ai_chat_apaga(user_id)

    niveis = _niveis(caplog, "delete_launch_sem_efeitos:")
    assert niveis == ["WARNING", "WARNING"], niveis


def test_falha_tecnica_continua_error_nas_duas_portas(user_id, monkeypatch, caplog):
    """Controle POSITIVO: unificar não podia silenciar o que É incidente. Banco
    caído / deadlock / bug continua `error` — e agora nas DUAS portas, não só
    na do /ai/chat."""
    def explode(uid, launch_id):
        raise RuntimeError("connection to server was lost")

    monkeypatch.setattr(db, "delete_launch_and_rollback", explode)
    monkeypatch.setattr(tool_mod.db, "resolve_user_seq_to_id", lambda uid, lid: 4242)

    with caplog.at_level(logging.WARNING):
        resp_wa = _whatsapp_apaga(user_id)
        resp_ai = _ai_chat_apaga(user_id)

    assert "de novo" in resp_wa.lower() and "de novo" in resp_ai.lower(), \
        "causa inesperada é a única em que 'tenta de novo' é o conselho certo"
    niveis = _niveis(caplog, "delete_launch:")
    assert niveis == ["ERROR", "ERROR"], niveis
    assert "lost" not in resp_wa and "lost" not in resp_ai, "str(e) não vai pro usuário"
