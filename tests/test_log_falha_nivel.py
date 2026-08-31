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
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

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


def _lancamento(user_id: int, nota: str) -> int:
    db.add_launch_and_update_balance(user_id, "despesa", 50.0, nota, nota)
    return int(db.list_launches(user_id, limit=1)[0]["id"])


def test_bulk_condicao_de_dominio_loga_warning_e_apaga_o_resto(user_id, caplog):
    """"apaga #2, #5 e #7" com um lançamento antigo no meio não pode contar
    como incidente do backend enquanto "apaga #2" sozinho conta como warning —
    é a MESMA função (`delete_launch_and_rollback`) e a MESMA condição.

    O controle POSITIVO está no mesmo bulk de propósito: o `except` novo não
    pode engolir o laço: o lançamento saudável tem de ser apagado.
    """
    antigo = _lancamento(user_id, "antigo sem efeitos")
    normal = _lancamento(user_id, "normal")
    from db.connection import get_conn
    with get_conn() as conn:  # o que LaunchNoEffects enxerga: `efeitos` nulo
        with conn.cursor() as cur:
            cur.execute("update launches set efeitos=null where id=%s and user_id=%s",
                        (antigo, user_id))
        conn.commit()

    db.set_pending_action(user_id, "delete_launch_bulk", {
        "launch_ids": [antigo, normal],
        "display_ids": {str(antigo): 2, str(normal): 5},
    })

    with caplog.at_level(logging.WARNING, logger="core.handlers.pending"):
        resp = h_pending.resolve_delete(user_id, confirmed=True)

    assert _niveis(caplog, "delete_launch_bulk:") == ["WARNING"], \
        [(r.levelname, r.getMessage()) for r in caplog.records]
    assert "⚠️ Falha: #2" in resp, resp
    assert "**#5**" in resp, resp
    restantes = [int(r["id"]) for r in db.list_launches(user_id, limit=10)]
    assert restantes == [antigo], f"o bulk parou no lançamento de domínio: {restantes}"


# ── quem INSTALA o handler: o processo do app, não o import tardio do WhatsApp ─
#
# O `_DashboardHandler` só chega ao logger raiz quando alguém chama
# `get_logger()`/`_configure_root_logger()`. Com `RUN_BACKGROUND_TASKS=0` (ou
# antes do import tardio da cadeia do WhatsApp no `_wa_worker`) ninguém
# chamava, e a falha de operação destrutiva morria no stderr — fora do
# `system_event_logs` e do `backend_errors_24h`.
#
# Teste IN-PROCESS não mede isso: dentro do pytest o handler JÁ está no raiz
# (`tests/conftest.py:324` importa o `ai_router`) e `_root_configured` é True —
# um `assert` de "está instalado" passa verde na árvore SEM o conserto. Por
# isso subprocesso com interpretador limpo. Não encosta no banco: o
# `log_system_event_sync` é trocado por um coletor ANTES do import do app (o
# `emit` resolve o global na hora da chamada).
_SUBPROCESSO = r'''
import json
import core.observability as obs

gravados = []
obs.log_system_event_sync = lambda level, **kw: gravados.append(level)

import frontend.finance_bot_websocket_custom as m
from fastapi.testclient import TestClient

with TestClient(m.app):          # lifespan de verdade, como no processo web
    pass

obs._log_falha("delete_card", 1, RuntimeError("falha tecnica"), card_id=7)
print("RESULTADO:" + json.dumps({"gravou": len(gravados)}))
'''


def test_processo_do_app_grava_falha_destrutiva_sem_background_tasks():
    """CONTRATO, não implementação: um processo que serve o `app` grava a falha
    de operação destrutiva em `system_event_logs`, com `RUN_BACKGROUND_TASKS=0`.

    Controle negativo medido na árvore sem o conserto: `{"gravou": 0}`."""
    raiz = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESSO], cwd=raiz,
        env={**os.environ, "RUN_BACKGROUND_TASKS": "0", "PYTHONPATH": "."},
        capture_output=True, text=True, timeout=180,
    )
    linha = next((l for l in proc.stdout.splitlines()
                  if l.startswith("RESULTADO:")), None)
    assert linha, f"subprocesso não chegou ao fim:\n{proc.stdout}\n{proc.stderr}"
    assert json.loads(linha[len("RESULTADO:"):]) == {"gravou": 1}, (
        "o `_DashboardHandler` não está no logger raiz do processo do app: a "
        "falha da rota destrutiva sai só no stderr e não chega ao "
        f"`system_event_logs`.\n{proc.stderr[-2000:]}"
    )


def test_rota_destrutiva_que_da_certo_nao_grava_nada(user_id, monkeypatch, caplog):
    """Controle POSITIVO do grupo: sem ele os dois testes acima passariam numa
    versão em que a ROTA loga o caminho feliz em WARNING/ERROR — pior que o bug,
    porque enche o `backend_errors_24h` de ruído.

    NÃO cobre a mutação "handler sem filtro de nível": o único registro do
    caminho feliz é um INFO do `httpx` (o cliente do `TestClient`), que é
    infraestrutura de teste e não existe em produção — contra essa mutação o
    teste fica vermelho por acidente, e cala se o `httpx` mudar de nível."""
    from fastapi.testclient import TestClient

    import core.observability as observability
    import db.cards as cards_mod
    import frontend.finance_bot_websocket_custom as dashboard

    monkeypatch.setattr(cards_mod, "undo_installment_group",
                        lambda uid, gid: {"deleted": 3})

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "del@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")

    with caplog.at_level(logging.INFO):
        resp = client.delete(f"/installments/{user_id}/PC12345678",
                             headers={dashboard.CSRF_HEADER_NAME: "test-csrf-token"})

    assert resp.status_code == 200, resp.text

    gravados: list[dict] = []
    monkeypatch.setattr(observability, "log_system_event_sync",
                        lambda level, **kw: gravados.append({"level": level, **kw}))
    handler = observability._DashboardHandler()
    for r in caplog.records:
        handler.emit(r)
    assert gravados == [], f"exclusão bem-sucedida gravou no system_event_logs: {gravados}"


def test_falha_de_rota_destrutiva_aparece_no_recent_ops(user_id, monkeypatch):
    """A falha das rotas de `frontend/routes/cards.py` tem de continuar VISÍVEL no
    painel de operações recentes do admin, e dizer QUAL rota falhou.

    Regressão que este teste prende: trocar a exceção crua por `HTTPException`
    mudou o `event_type` de `http_unhandled_exception` (o que o
    `admin_error_logging_middleware` gravava) para `logger.error` — e o
    `recent_ops` (`core/admin_dashboard.py`) filtra por allowlist de
    `event_type`. Junto some o `source`, que na `main` era
    `DELETE /cards/{user_id}/{card_id}` e virou o nome do logger do `_log_falha`
    (`core.observability`), igual para toda porta destrutiva.

    (A linha continua aparecendo na lista de erros do admin — `recent_errors`,
    as 100 das últimas 24h, sem filtro de `event_type`. O que sumia era o painel
    de OPERAÇÕES recentes.)

    Controle negativo medido na mesma árvore: sem a linha nova da allowlist a
    query devolve 0 desta falha, com ela devolve 1; e sem o `rota=` do call site
    o segundo `assert` cai (medido nos dois).
    """
    import asyncio

    from fastapi.testclient import TestClient

    import core.admin_dashboard as admin
    import db.cards as cards_mod
    import frontend.finance_bot_websocket_custom as dashboard
    from db.connection import get_conn

    def explode(uid, gid):
        raise RuntimeError("falha tecnica")

    monkeypatch.setattr(cards_mod, "undo_installment_group", explode)
    # `system_event_logs` nasce aqui, não no `init_db` — sem isto o teste depende
    # de outro arquivo ter chamado antes e some do isolado (medido).
    asyncio.run(admin.ensure_admin_tables())

    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "del@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")

    try:
        resp = client.delete(f"/installments/{user_id}/PC12345678",
                             headers={dashboard.CSRF_HEADER_NAME: "test-csrf-token"})
        assert resp.status_code == 500, resp.text

        ops = asyncio.run(admin.fetch_admin_overview())["recent_ops"]
        linhas = [o for o in ops if str(user_id) in (o["message"] or "")]
        assert linhas, (
            "falha de rota destrutiva sumiu das 25 operações recentes do admin "
            "(`recent_ops`), onde a `main` a mostrava")
        assert f"DELETE /installments/{user_id}/PC12345678" in linhas[0]["message"], (
            "o operador vê a falha e não sabe qual rota: "
            + linhas[0]["message"][:200])
    finally:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM system_event_logs WHERE message LIKE %s",
                        (f"%user_id={user_id}%",))
            conn.commit()
