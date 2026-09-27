"""PR-E — os estados de `db.open_finance_diagnostico` e a marca "remoção remota falhou".

Banco REAL; a Pluggy nunca é chamada (os logs de falha nascem pelos caminhos de
produção com o cliente dublado). O estado de cada linha do registry é escrito
direto, porque aqui a pergunta é a REGRA de `classifica_item`; a conversa pelas
rotas mora em `tests/test_of_operador_conversa.py`.

Controles do grupo (medidos, ver o relato da PR):
  • negativo D3 — tirar a consulta a `audit_events` de `classifica_item`:
    `test_sem_dono_com_auditoria_connected_e_legado` vira NUNCA_ATRIBUIDO;
  • positivo D3 — `test_sem_dono_e_sem_auditoria_e_nunca_atribuido`;
  • negativo da marca — tirar a leitura de `system_event_logs`: os 5 casos de
    formato e o de conta excluída ficam vermelhos;
  • positivo da marca — `test_operator_delete_posterior_resolve_a_marca`
    (a marca some, mas só com `operator_delete` DEPOIS da falha).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from core.audit import AuditEvent, record_audit_event
from db import open_finance_diagnostico as diag
from db.connection import get_conn
from db.open_finance_state import mark_items_removed
from test_account_deletion_pluggy import _item_de as _item_exclusao
from test_account_deletion_pluggy import _semeia as _semeia_exclusao
from test_account_reset import SENHA, _item_de as _item_reset, _semeia as _semeia_reset
from test_account_reset import _auth as _auth_reset
from test_account_reset import _zera_rate_limit  # noqa: F401 — autouse: limiter
from test_of_webhook_adopt_guards import _limpa_item


@pytest.fixture(autouse=True)
def tabelas_admin():
    """`system_event_logs` nasce preguiçosa (core/admin_dashboard.py)."""
    from core.admin_dashboard import ensure_admin_tables

    asyncio.run(ensure_admin_tables())


@pytest.fixture()
def item():
    i = f"diag-{uuid.uuid4().hex[:12]}"
    yield i
    _limpa(i)


def _limpa(i: str) -> None:
    _limpa_item(i)
    with get_conn() as c:
        c.execute("delete from system_event_logs where details::text like %s", (f"%{i}%",))
        c.execute("delete from audit_events where details->>'item_id' = %s", (i,))
        c.commit()


def _linha(uid, i: str, origin: str, *, tracked: bool = True, em: str | None = None) -> None:
    with get_conn() as c:
        c.execute(
            "insert into open_finance_item_registry"
            " (user_id, provider, provider_item_id, origin, removal_tracked, created_at)"
            " values (%s, 'pluggy', %s, %s, %s, coalesce(%s::timestamptz, now()))",
            (uid, i, origin, tracked, em))
        c.commit()


def _conecta(uid: int, i: str) -> None:
    db.save_pluggy_open_finance_item(
        uid, {"id": i, "status": "UPDATED", "connector": {"id": 612, "name": "Nubank"}})


# ── os estados ───────────────────────────────────────────────────────────────

def test_ultima_linha_removed_e_removido(user_id, item):
    _linha(user_id, item, "pluggy_item")
    _linha(user_id, item, "removed")
    assert diag.classifica_item(item) == diag.REMOVIDO


def test_ultima_pluggy_item_rastreada_e_interrompido(user_id, item):
    _linha(user_id, item, "webhook_adopt")
    assert diag.classifica_item(item) == diag.INTERROMPIDO


def test_ultima_sem_removal_tracked_e_legado(user_id, item):
    _linha(user_id, item, "pluggy_item", tracked=False)
    assert diag.classifica_item(item) == diag.LEGADO_AMBIGUO


def test_sem_dono_e_sem_auditoria_e_nunca_atribuido(item):
    _linha(None, item, "webhook")
    assert diag.classifica_item(item) == diag.NUNCA_ATRIBUIDO


def test_sem_dono_com_auditoria_connected_e_legado(user_id, item):
    """D3: o item TEVE dono (auditoria) e o rastro se perdeu — não é adotável."""
    _linha(None, item, "webhook")
    record_audit_event(user_id, AuditEvent.OPEN_FINANCE_CONNECTED,
                       details={"provider": "pluggy", "item_id": item})
    assert diag.classifica_item(item) == diag.LEGADO_AMBIGUO


def test_conectou_removeu_reconectou_olha_a_ultima(user_id, item):
    _linha(user_id, item, "pluggy_item")
    _linha(user_id, item, "removed")
    _linha(user_id, item, "pluggy_item")
    assert diag.classifica_item(item) == diag.INTERROMPIDO
    _conecta(user_id, item)
    assert diag.classifica_item(item) == diag.CONECTADO


def test_desempate_e_por_id_e_nao_por_created_at(user_id, item):
    """`created_at` INVERTIDO em relação ao `id` (o que duas sessões concorrentes
    produzem): quem decide é o `id`. Por `created_at`, sairia REMOVIDO."""
    _linha(user_id, item, "removed", em="2026-01-02")
    _linha(user_id, item, "pluggy_item", em="2026-01-01")
    assert diag.classifica_item(item) == diag.INTERROMPIDO


def test_mesma_transacao_decide_pelo_id(user_id, item):
    """Duas escritas na MESMA transação têm o mesmo `now()`: só o `id` desempata."""
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("insert into open_finance_item_registry (user_id, provider_item_id,"
                        " origin, removal_tracked) values (%s, %s, 'pluggy_item', true)",
                        (user_id, item))
            mark_items_removed(cur, user_id, [{"provider": "pluggy",
                               "provider_item_id": item, "status": "UPDATED"}],
                               last_event="disconnect")
        c.commit()
    assert diag.classifica_item(item) == diag.REMOVIDO


def test_conexao_sem_rastro_com_dono(user_id, item):
    _linha(None, item, "webhook")
    _conecta(user_id, item)
    assert diag.classifica_item(item) == diag.CONECTADO_SEM_RASTRO
    assert item in diag.conexoes_sem_rastro()
    assert item not in diag.listar_sem_conexao()


def test_listar_sem_conexao_traz_o_estado(user_id, item):
    _linha(user_id, item, "removed")
    assert diag.listar_sem_conexao()[item] == diag.REMOVIDO


# ── a marca "remoção remota falhou": um caso por formato de log ─────────────

def _pluggy_quebrada(monkeypatch, *, auth: bool = True):
    def _sem_auth():
        raise RuntimeError("sem credencial")

    def _delete(item_id, api_key=None):
        raise RuntimeError("pluggy 503")

    monkeypatch.setattr(of_routes, "create_pluggy_api_key",
                        (lambda: "k") if auth else _sem_auth)
    monkeypatch.setattr(of_routes, "delete_pluggy_item", _delete)


def _segundo_passe_quebrado(monkeypatch):
    """1º passe enumera nada (tudo vai para o 2º) e o 2º estoura inteiro."""
    def _fake(uid, item_ids=None, *, log_user_id=True):
        if item_ids is None:
            return []
        raise RuntimeError("helper fora do ar")

    monkeypatch.setattr(of_routes, "delete_pluggy_items_best_effort", _fake)


def test_formato_item_delete_failed_item_id(user_id, item, monkeypatch):
    _pluggy_quebrada(monkeypatch)
    of_routes.delete_pluggy_items_best_effort(user_id, [item])
    assert item in diag.itens_com_remocao_remota_falha()


def test_formato_disconnect_auth_failed_items(user_id, item, monkeypatch):
    _pluggy_quebrada(monkeypatch, auth=False)
    of_routes.delete_pluggy_items_best_effort(user_id, [item])
    assert item in diag.itens_com_remocao_remota_falha()


def test_formato_item_delete_failed_items_do_2o_passe(user_id, item, monkeypatch):
    _conecta(user_id, item)
    _segundo_passe_quebrado(monkeypatch)
    assert of_routes._disconnect_sob_lock(user_id) == 1
    assert item in diag.itens_com_remocao_remota_falha()


def test_formato_reset_cleanup_failed_items(user_id, monkeypatch):
    _semeia_reset(user_id)
    i = _item_reset(user_id)
    try:
        _segundo_passe_quebrado(monkeypatch)
        client = TestClient(dashboard.app)
        r = client.post("/settings/reset", json={"password": SENHA},
                        headers=_auth_reset(client, user_id))
        assert r.status_code == 200, r.text
        assert i in diag.itens_com_remocao_remota_falha()
    finally:
        _limpa(i)


def test_formato_exclusao_de_conta_so_pelo_log(user_id, monkeypatch):
    """Conta excluída: a cascata leva o registry, o log é o ÚNICO rastro."""
    _semeia_exclusao(user_id)
    i = _item_exclusao(user_id)
    _linha(user_id, i, "pluggy_item")
    try:
        _segundo_passe_quebrado(monkeypatch)
        db.process_due_account_deletions(limit=10)
        with get_conn() as c:
            n = c.execute("select count(*) as n from open_finance_item_registry"
                          " where provider_item_id = %s", (i,)).fetchone()["n"]
        assert n == 0, "pré-condição: a cascata levou o rastro"
        assert i in diag.itens_com_remocao_remota_falha()
        assert diag.classifica_item(i) == diag.NUNCA_ATRIBUIDO
    finally:
        _limpa(i)


def test_operator_delete_posterior_resolve_a_marca(user_id, item, monkeypatch):
    _linha(None, item, "operator_delete", em="2000-01-01")   # ANTES da falha: não vale
    _pluggy_quebrada(monkeypatch)
    of_routes.delete_pluggy_items_best_effort(user_id, [item])
    assert item in diag.itens_com_remocao_remota_falha()
    db.register_item(None, provider_item_id=item, origin="operator_delete")
    assert item not in diag.itens_com_remocao_remota_falha()


def test_item_com_conexao_viva_nao_tem_marca(user_id, item, monkeypatch):
    _pluggy_quebrada(monkeypatch)
    of_routes.delete_pluggy_items_best_effort(user_id, [item])
    _conecta(user_id, item)
    assert item not in diag.itens_com_remocao_remota_falha()
