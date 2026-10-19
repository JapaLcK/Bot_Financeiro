"""Push "reconectar": a regra de `limpar_push_reconectar` (db/push.py) e a ordem no laço.

Só Atualizado e Parcial (ui.state) reabrem a trava; todo outro estado a mantém.
Helpers e fixtures vêm de `test_push_reconectar_laco` (caminho real, APNs espião).

CONTROLES (medidos em 2026-10-18; remeça se mexer no código), todos em `limpar_push_reconectar`:
  N9 tirar `and id = any(%s)` do UPDATE → um_saudavel_nao_abre_a_trava_do_quebrado vermelho.
  N1 só `("updated",)` → partial_abre_a_trava vermelho.
  N5 `+ item_missing` → item_missing_nao_abre_a_trava vermelho.
  N4 `+ paused, removed` → trava_mantida[paused] e [removed] vermelhos.
  N2 `+ updating` → trava_mantida[updating] e [investments_read_failed_sem_sync] vermelhos.
  Ordem: `limpar_push_reconectar` DEPOIS da leitura do laço (a ordem antiga) →
      curado_entre_a_leitura_e_a_limpeza_nao_reenvia vermelho (2 pushes).
"""
import pytest

import db
from _apoio_auth_app import sql
from core.services import open_finance_proactive as ofp
from db.open_finance import _read_open_finance_connections
from test_push_reconectar_laco import _cura, _event_logs, _quebra, _trava, app_pronto  # noqa: F401


def _ui(uid, cid):
    with db.get_conn() as c, c.cursor() as cur:
        return {x["id"]: x["ui"]["state"] for x in _read_open_finance_connections(cur, uid)}[cid]


def test_um_saudavel_nao_abre_a_trava_do_quebrado(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    a = _quebra(user_id, monkeypatch, "item-a")
    _cura(monkeypatch, "item-a")
    _quebra(user_id, monkeypatch, "item-b", "e_item_missing")
    for _ in range(3):
        ofp.run_reconnect_push()
    assert _ui(user_id, a) == "updated"
    assert len(espiao.chamadas) == 1


def test_item_missing_nao_abre_a_trava(user_id, monkeypatch, app_pronto):
    espiao, _ = app_pronto
    _quebra(user_id, monkeypatch, "item-e", "e_item_missing")
    ofp.run_reconnect_push()
    ofp.run_reconnect_push()
    assert len(espiao.chamadas) == 1


# (SQL aplicado depois de a conexão ficar saudável, estado esperado na tela)
@pytest.mark.parametrize("sqlset,esperado", [
    ("status='PAUSED'", "paused"),
    ("status='DELETED'", "removed"),
    ("status='ACTIVE', status_reason=null, health=null, reconnected_at=now()", "updating"),
    ("status='ACTIVE', status_reason='no_accounts'", "no_accounts"),
    ("status='ACTIVE', status_reason='investments_read_failed', reconnected_at=now()", "updating"),
], ids=["paused", "removed", "updating", "no_accounts", "investments_read_failed_sem_sync"])
def test_trava_mantida(user_id, monkeypatch, app_pronto, sqlset, esperado):
    cid = _quebra(user_id, monkeypatch)
    ofp.run_reconnect_push()
    assert _trava(cid) is not None
    _cura(monkeypatch)
    sql(f"update open_finance_connections set {sqlset} where id = %s", cid)
    assert _ui(user_id, cid) == esperado
    db.limpar_push_reconectar(user_id)
    assert _trava(cid) is not None


def test_partial_abre_a_trava(user_id, monkeypatch, app_pronto):
    cid = _quebra(user_id, monkeypatch)
    ofp.run_reconnect_push()
    _cura(monkeypatch)
    sql("update open_finance_connections set status_reason='investments_read_failed' where id = %s", cid)
    assert _ui(user_id, cid) == "partial"
    db.limpar_push_reconectar(user_id)
    assert _trava(cid) is None


def test_curado_entre_a_leitura_e_a_limpeza_nao_reenvia(user_id, monkeypatch, app_pronto):
    """Banco já avisado, curado logo depois de o laço ler a lista: a limpeza roda antes
    da leitura, então a trava segue valendo e a varredura não reivindica de novo."""
    espiao, _ = app_pronto
    cid = _quebra(user_id, monkeypatch)
    assert ofp.run_reconnect_push()["sent"] == 1
    real = ofp.list_connections_needing_reconnect

    def _le_e_cura(uid):
        r = real(uid)
        if uid == user_id:
            _cura(monkeypatch)
        return r
    monkeypatch.setattr(ofp, "list_connections_needing_reconnect", _le_e_cura)
    ofp.run_reconnect_push()
    assert _ui(user_id, cid) == "updated"
    assert len(espiao.chamadas) == 1
