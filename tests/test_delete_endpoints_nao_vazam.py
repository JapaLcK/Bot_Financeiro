"""As portas HTTP destrutivas não vazam o texto da exceção — e deixam log.

`frontend/dashboard.js` renderiza o `detail` da HTTPException direto no modal
(`throw new Error(detail.detail || …)`). Os quatro pontos que este arquivo
cobre mandavam pra lá `str(exc)` cru — jargão de banco ("lançamento sem
'efeitos'") no melhor caso, `DETAIL: Key (…)=(…)` do psycopg no pior — e três
deles não registravam NADA no log. Um caminho que apaga dinheiro tem de ser o
contrário: mensagem de produto pro usuário, causa no log pro suporte.

Controle POSITIVO no fim: o caminho legítimo continua apagando e devolvendo
200 — as guardas novas não podem recusar tudo.
"""
from __future__ import annotations

import logging
import os

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard


def _client(user_id: int) -> TestClient:
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "del@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    return client


def _headers() -> dict:
    return {dashboard.CSRF_HEADER_NAME: "test-csrf-token"}


def _logs(caplog, prefixo: str) -> list:
    return [r for r in caplog.records if r.getMessage().startswith(prefixo)]


def test_delete_launch_sem_efeitos_nao_vaza_e_loga(user_id, caplog):
    """Condição de DOMÍNIO: 400 com frase de produto, WARNING (não ERROR — o
    `_DashboardHandler` espelha ERROR em `backend_errors_24h`)."""
    lid, _seq, _bal = db.add_launch_and_update_balance(
        user_id, "despesa", 300, "aluguel", "paguei 300 aluguel"
    )
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update launches set efeitos=null where id=%s and user_id=%s",
                        (lid, user_id))
        conn.commit()

    with caplog.at_level(logging.WARNING):
        resp = _client(user_id).delete(f"/launches/{user_id}/{lid}", headers=_headers())

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "efeitos" not in detail.lower(), detail
    assert "psycopg" not in detail.lower(), detail
    assert "não dá pra desfazer com segurança" not in detail, "str(exc) vazou pro modal"
    assert "intacto" in detail.lower(), detail

    linhas = _logs(caplog, "delete_launch: falha")
    assert len(linhas) == 1, [r.getMessage() for r in caplog.records]
    assert linhas[0].levelname == "WARNING", linhas[0].levelname
    assert f"user_id={user_id}" in linhas[0].getMessage()
    assert f"launch_id={lid}" in linhas[0].getMessage()
    assert linhas[0].exc_info is None, \
        "traceback é só pro ramo técnico (ERROR); condição de domínio esperada não precisa de pilha"
    # a linha continua lá: recusar é o ponto.
    assert [int(r["id"]) for r in db.list_launches(user_id, limit=5)] == [lid]


def test_delete_launch_erro_tecnico_nao_vaza_o_psycopg(user_id, monkeypatch, caplog):
    """Falha inesperada: 500 genérico, ERROR no log, e o texto do driver — que
    pode trazer valor e descrição da linha — fica FORA do `detail`."""
    def explode(uid, lid):
        raise RuntimeError('DETAIL: Key (id)=(42) mercadinho segredo')

    monkeypatch.setattr(dashboard, "delete_launch_and_rollback", explode)

    with caplog.at_level(logging.WARNING):
        resp = _client(user_id).delete(f"/launches/{user_id}/424242", headers=_headers())

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "DETAIL" not in detail and "segredo" not in detail, detail
    assert "tenta de novo" in detail.lower(), detail
    linhas = _logs(caplog, "delete_launch: falha")
    assert len(linhas) == 1 and linhas[0].levelname == "ERROR", linhas
    assert "segredo" not in linhas[0].getMessage(), "str(e) também não entra no log"


def test_delete_credit_transaction_erro_nao_vaza_e_loga(user_id, monkeypatch, caplog):
    def explode(uid, tx_id):
        raise RuntimeError('DETAIL: Key (id)=(9) mercadinho segredo')

    monkeypatch.setattr(dashboard, "undo_credit_transaction", explode)

    with caplog.at_level(logging.WARNING):
        resp = _client(user_id).delete(f"/credit-transactions/{user_id}/9", headers=_headers())

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "segredo" not in detail and "DETAIL" not in detail, detail
    linhas = _logs(caplog, "undo_credit_transaction: falha")
    assert len(linhas) == 1 and linhas[0].levelname == "ERROR", linhas
    assert "credit_tx_id=9" in linhas[0].getMessage()


def test_delete_installment_group_erro_nao_vaza_e_loga(user_id, monkeypatch, caplog):
    """A terceira porta de cartão não tinha `try/except` NENHUM: 500 com
    "Erro interno do servidor." do `admin_error_logging_middleware`. Log HAVIA
    (`http_unhandled_exception` com traceback) — o que faltava era frase de
    produto, e o `HTTPException` que a traz esconde a falha do middleware. Por
    isso o `exc_info` abaixo: sem ele, o conserto TIRA rastro."""
    import db.cards as cards_mod

    def explode(uid, gid):
        raise RuntimeError('DETAIL: Key (id)=(9) mercadinho segredo')

    monkeypatch.setattr(cards_mod, "undo_installment_group", explode)

    with caplog.at_level(logging.WARNING):
        resp = _client(user_id).delete(f"/installments/{user_id}/PC12345678", headers=_headers())

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "segredo" not in detail and "DETAIL" not in detail, detail
    linhas = _logs(caplog, "undo_installment_group: falha")
    assert len(linhas) == 1 and linhas[0].levelname == "ERROR", linhas
    assert "group_id=PC12345678" in linhas[0].getMessage()
    assert linhas[0].exc_info is not None, \
        "sem exc_info o traceback que o middleware gravava some (rastro MENOR que o de antes)"


def test_delete_card_erro_nao_vaza_e_loga(user_id, monkeypatch, caplog):
    """A QUARTA porta de cartão, irmã da `/installments`: também não tinha
    `try/except` nenhum, e apagar cartão leva junto faturas e compras
    (ON DELETE CASCADE). Mesma medição de duas colunas da irmã: o corpo era
    genérico, mas o traceback era gravado pelo middleware — o `exc_info` do
    `_log_falha` é o que impede a troca de um rastro melhor por um pior."""
    import db.cards as cards_mod

    monkeypatch.setattr(cards_mod, "get_card_by_id",
                        lambda uid, cid: {"id": cid, "name": "Nubank"})

    def explode(uid, cid):
        raise RuntimeError('DETAIL: Key (id)=(9) mercadinho segredo')

    monkeypatch.setattr(cards_mod, "delete_card", explode)

    with caplog.at_level(logging.WARNING):
        resp = _client(user_id).delete(f"/cards/{user_id}/9", headers=_headers())

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "segredo" not in detail and "DETAIL" not in detail, detail
    assert "tenta de novo" in detail.lower(), detail
    linhas = _logs(caplog, "delete_card: falha")
    assert len(linhas) == 1 and linhas[0].levelname == "ERROR", \
        [r.getMessage() for r in caplog.records]
    assert "card_id=9" in linhas[0].getMessage()
    assert "segredo" not in linhas[0].getMessage(), "str(e) não entra na MENSAGEM"
    # O traceback, sim: é o que o `admin_error_logging_middleware` já gravava em
    # `system_event_logs.details` na `main` e o `HTTPException(500)` desta rota
    # faria sumir. Vai pelo `exc_info`, no mesmo campo e no mesmo nível.
    assert linhas[0].exc_info is not None, "rastro MENOR que o da main"
    assert "segredo" in str(linhas[0].exc_info[1])


def test_delete_launch_subclasse_de_condicao_conhecida_nao_vira_500(user_id, monkeypatch, caplog):
    """O `except` casa por `isinstance`, então uma SUBCLASSE de
    `LaunchNoEffects` entra no handler. Com `_MSG_DELETE_LAUNCH[type(exc)]` ela
    levantava KeyError ali dentro: 500 com `detail=None` no lugar do 400 com
    frase de produto. Não há subclasse hoje — é a diferença entre os dois
    critérios de casamento que está presa aqui."""
    class LaunchNoEffectsDeAlgumSubtipo(dashboard.LaunchNoEffects):
        pass

    def explode(uid, lid):
        raise LaunchNoEffectsDeAlgumSubtipo("lançamento sem 'efeitos'.")

    monkeypatch.setattr(dashboard, "delete_launch_and_rollback", explode)

    with caplog.at_level(logging.WARNING):
        resp = _client(user_id).delete(f"/launches/{user_id}/424242", headers=_headers())

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail and "intacto" in detail.lower(), detail
    assert "efeitos" not in detail.lower(), detail


def test_delete_launch_comum_continua_200(user_id):
    """Controle POSITIVO: o caminho legítimo não foi estreitado — apaga,
    reverte o saldo e responde 200."""
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    lid, _seq, _b = db.add_launch_and_update_balance(
        user_id, "despesa", 300, "aluguel", "paguei 300 aluguel"
    )
    assert round(float(db.get_balance(user_id)), 2) == 700.0

    resp = _client(user_id).delete(f"/launches/{user_id}/{lid}", headers=_headers())

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert round(float(db.get_balance(user_id)), 2) == 1000.0
