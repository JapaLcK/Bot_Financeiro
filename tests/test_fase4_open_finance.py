"""Contratos bancários iOS: marco derivado, remoção individual e callback tardio.

Controles negativos: retirar guarda pré-lock/sob-lock ressuscita o item nos
respectivos ataques. Positivos: item novo e reconexão viva continuam aceitos.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import threading

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import db
import db.open_finance_onboarding as onboarding
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as routes
from conftest import promote_to_pro
from db.connection import get_conn
from db.open_finance_state import mark_sync_result, pluggy_item_lock
from db.privacy import reset_user_data
from db.users import _hash_password
from test_of_item_ownership import _auth, _item_remoto

PASSWORD = "senha-fase4-123"


def _login(uid):
    with get_conn() as conn:
        conn.execute("insert into auth_accounts (user_id,email,password_hash) values (%s,%s,%s)",
                     (uid, f"fase4-{uid}@t.local", _hash_password(PASSWORD)))
        conn.commit()


def _bank(uid, *, item=None, status="UPDATED", sync=True, mirror="account", health=None,
          reason=None, reconnected=None, provider="pluggy"):
    item = item or f"fase4-{uid}"
    with get_conn() as conn:
        row = conn.execute(
            """insert into open_finance_connections
                 (user_id,provider,provider_item_id,status,institution_id,institution_name,
                  last_sync_at,health,status_reason,reconnected_at)
               values (%s,%s,%s,%s,'612','Nubank',%s,%s,%s,%s) returning id""",
            (uid, provider, item, status, datetime.now(timezone.utc) if sync else None,
             Jsonb(health) if health else None, reason, reconnected),
        ).fetchone()
        cid = row["id"]
        if mirror == "account":
            conn.execute("insert into open_finance_accounts "
                         "(connection_id,provider_account_id,name,type,balance) "
                         "values (%s,%s,'Conta zerada','BANK',0)", (cid, item + '-a'))
        elif mirror == "investment":
            conn.execute("insert into open_finance_investments "
                         "(connection_id,provider_investment_id,name,balance) "
                         "values (%s,%s,'Investimento zerado',0)", (cid, item + '-i'))
        conn.commit()
    return cid, item


def _get(client, uid):
    _auth(client, uid)
    return client.get('/onboarding/open-finance')


def test_marco_sem_plano_sem_prova_nao_aceita_wizard_e_nao_tem_default(user_id):
    _login(user_id)
    client = TestClient(dashboard.app)
    assert _get(client, user_id).json() == {"ok": True, "completed": False, "completed_at": None}
    headers = _auth(client, user_id)
    assert client.post('/onboarding/state', headers=headers,
                       json={"completed": True, "step": 5, "event": "skip"}).status_code == 200
    assert _get(client, user_id).json()['completed'] is False
    with get_conn() as conn:
        col = conn.execute("select column_default from information_schema.columns "
                           "where table_name='auth_accounts' and column_name="
                           "'open_finance_onboarding_completed_at'").fetchone()
    assert col['column_default'] is None
    assert TestClient(dashboard.app).get('/onboarding/open-finance').status_code == 401


@pytest.mark.parametrize("kind", ['account', 'investment'])
@pytest.mark.parametrize("partial", [False, True])
def test_sync_atual_com_espelho_zero_promove_uma_vez_e_persiste_apos_remover(user_id, kind, partial):
    _login(user_id)
    _bank(user_id, mirror=kind, health={"item_status": "UPDATED", "stale_products": ['CREDIT']} if partial else None)
    # Um outro banco sem sync não impede a prova do banco válido.
    _bank(user_id, item=f'fase4-{user_id}-pendente', sync=False)
    client = TestClient(dashboard.app)
    state = _get(client, user_id).json()
    assert state['completed'] is True
    assert datetime.fromisoformat(state['completed_at']).tzinfo is not None
    assert _get(client, user_id).json() == state
    assert db.disconnect_open_finance_connection(user_id) == 2
    assert _get(TestClient(dashboard.app), user_id).json() == state
    reset_user_data(user_id, PASSWORD)
    assert _get(TestClient(dashboard.app), user_id).json()['completed'] is False


@pytest.mark.parametrize('changes', [
    {'sync': False}, {'mirror': None}, {'status': 'UPDATING', 'sync': False},
    {'status': 'PAUSED'}, {'status': 'DELETED'}, {'status': 'ERROR'},
    {'status': 'LOGIN_ERROR'}, {'reason': 'item_missing'},
    {'provider': 'mock_pluggy'}, {'reconnected': datetime.now(timezone.utc) + timedelta(days=1)},
    {'sync': False, 'health': {'item_status': 'UPDATED'}},
    {'sync': False, 'health': {'item_status': 'UPDATED', 'stale_products': ['CREDIT']}},
    {'reconnected': datetime.now(timezone.utc) + timedelta(days=1),
     'health': {'item_status': 'UPDATED', 'stale_products': ['CREDIT']}},
])
def test_marco_recusa_provas_insuficientes(user_id, changes):
    _login(user_id)
    _bank(user_id, **changes)
    assert onboarding.get_open_finance_onboarding(user_id)['completed'] is False


def test_observacao_saudavel_sem_sync_nao_promove(user_id):
    _login(user_id)
    cid, _ = _bank(user_id, sync=False)
    mark_sync_result(cid, ok=None, health={'item_status': 'UPDATED'}, status='ACTIVE')
    assert onboarding.get_open_finance_onboarding(user_id)['completed'] is False


def test_marco_isola_usuario_com_banco_sadio_do_vizinho(user_id):
    _login(user_id)
    other = user_id + 1
    db.ensure_user(other)
    try:
        _login(other)
        _bank(other)
        assert onboarding.get_open_finance_onboarding(user_id)['completed'] is False
        assert onboarding.get_open_finance_onboarding(other)['completed'] is True
    finally:
        with get_conn() as conn:
            conn.execute('delete from users where id=%s', (other,))
            conn.commit()


@pytest.mark.parametrize('operation', ['delete', 'reconnect'])
def test_promocao_serializa_prova_e_carimbo_com_delete_ou_reconexao(user_id, monkeypatch, operation):
    _login(user_id)
    _, item = _bank(user_id)
    started, done = threading.Event(), threading.Event()
    errors = []
    original = onboarding._read_open_finance_connections
    workers = []

    def mutate():
        started.set()
        try:
            if operation == 'delete':
                db.disconnect_open_finance_connection(user_id)
            else:
                db.save_pluggy_open_finance_item(user_id, _item_remoto(item, user_id))
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    def read_locked(cur, uid, *, lock=False):
        rows = original(cur, uid, lock=lock)
        worker = threading.Thread(target=mutate)
        workers.append(worker)
        worker.start()
        assert started.wait(2)
        assert not done.wait(.1), 'writer atravessou a transação de prova antes do carimbo'
        return rows

    monkeypatch.setattr(onboarding, '_read_open_finance_connections', read_locked)
    assert onboarding.get_open_finance_onboarding(user_id)['completed'] is True
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()
    assert errors == []
    # O marco é histórico: a mutação só conclui após a prova válida ser carimbada.
    assert onboarding.get_open_finance_onboarding(user_id)['completed'] is True


@pytest.fixture
def remote(monkeypatch, user_id):
    scheduled, deleted = [], []
    monkeypatch.setattr(routes, 'get_pluggy_item', lambda item: _item_remoto(item, user_id))
    monkeypatch.setattr(routes, '_schedule_pluggy_sync', lambda item: scheduled.append(item))
    monkeypatch.setattr(routes, 'create_pluggy_api_key', lambda: 'key')
    monkeypatch.setattr(routes, 'delete_pluggy_item', lambda item, api_key=None: deleted.append(item))
    return scheduled, deleted


def test_delete_individual_preserva_outro_banco_e_manualmente_mesclado(user_id, remote, monkeypatch):
    promote_to_pro(user_id)
    cid, item = _bank(user_id)
    other_cid, other_item = _bank(user_id, item=f'fase4-{user_id}-outro')
    # Lista global deve ser impossível neste caminho: provar que não toca outro item.
    monkeypatch.setattr(routes, 'list_pluggy_item_ids', lambda uid: pytest.fail('enumerou todos os bancos'))
    with get_conn() as conn:
        launch = conn.execute("insert into launches (user_id,tipo,valor,categoria,source,efeitos) "
                              "values (%s,'expense',10,'outros','manual',%s) returning id",
                              (user_id, Jsonb({'delta_conta': -10}))).fetchone()['id']
        conn.execute("insert into open_finance_transactions "
                     "(account_id,provider_transaction_id,description,amount,transaction_date,"
                     "imported_launch_id,reconciliation_status) "
                     "select id,'tx-fase4','Compra',-10,current_date,%s,'auto_merged' "
                     "from open_finance_accounts where connection_id=%s", (launch, cid))
        conn.commit()
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    resp = client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=headers)
    assert resp.json() == {'ok': True, 'deleted': 1}, resp.text
    assert remote[1] == [item]
    snapshot = db.get_open_finance_snapshot(user_id)
    assert [c['id'] for c in snapshot['connections']] == [other_cid]
    assert len(snapshot['accounts']) == 1
    with get_conn() as conn:
        assert conn.execute('select 1 from launches where user_id=%s and id=%s',
                            (user_id, launch)).fetchone()
    assert 'removed' in db.item_registry_origins(item)
    assert db.item_registry_origins(other_item) == set()
    assert client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=headers).status_code == 404


def test_delete_individual_inexistente_ou_alheio_404_uniforme(user_id, remote):
    promote_to_pro(user_id)
    other = user_id + 1
    db.ensure_user(other)
    try:
        cid, _ = _bank(other)
        client = TestClient(dashboard.app)
        headers = _auth(client, user_id)
        missing = client.delete(f'/open-finance/{user_id}/connections/-1', headers=headers)
        alien = client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=headers)
        assert alien.status_code == missing.status_code == 404
        assert alien.json() == missing.json() == {'detail': {'code': 'OF_CONNECTION_NOT_FOUND',
                                                           'message': 'Não achamos esse banco nas suas conexões.'}}
        assert remote[1] == []
        assert len(db.get_open_finance_snapshot(other)['connections']) == 1
        assert client.delete(f'/open-finance/{other}/connections/{cid}', headers=headers).status_code == 403
    finally:
        with get_conn() as conn:
            conn.execute('delete from users where id=%s', (other,))
            conn.commit()


def test_delete_individual_lock_ocupado_e_falha_remota_best_effort(user_id, remote, monkeypatch):
    promote_to_pro(user_id)
    cid, item = _bank(user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    monkeypatch.setenv('OF_SYNC_LOCK_WAIT_MS', '100')
    with pluggy_item_lock(item) as locked:
        assert locked
        assert client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=headers).status_code == 503
    assert remote[1] == []
    assert len(db.get_connections_by_item_id(item)) == 1
    monkeypatch.setattr(routes, 'create_pluggy_api_key', lambda: (_ for _ in ()).throw(RuntimeError('fora')))
    assert client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=headers).json() == {'ok': True, 'deleted': 1}
    assert db.get_connections_by_item_id(item) == []
    assert 'removed' in db.item_registry_origins(item)


@pytest.mark.parametrize('when', ['before', 'during_remote', 'before_lock'])
def test_callback_removido_vetado_antes_e_sob_lock(user_id, remote, monkeypatch, when):
    promote_to_pro(user_id)
    _, item = _bank(user_id)
    if when == 'before':
        db.disconnect_open_finance_connection(user_id)
        monkeypatch.setattr(routes, 'get_pluggy_item', lambda item: pytest.fail('buscou item já removido'))
    elif when == 'during_remote':
        def fetch(item):
            db.disconnect_open_finance_connection(user_id)
            return _item_remoto(item, user_id)
        monkeypatch.setattr(routes, 'get_pluggy_item', fetch)
    else:
        @contextmanager
        def after_delete(item, *, budget_ms=None):
            db.disconnect_open_finance_connection(user_id)
            with pluggy_item_lock(item, budget_ms=budget_ms) as locked:
                yield locked
        monkeypatch.setattr(routes, 'pluggy_item_lock', after_delete)
    client = TestClient(dashboard.app)
    resp = client.post(f'/open-finance/{user_id}/pluggy-item', headers=_auth(client, user_id),
                       json={'item': {'id': item}})
    assert resp.status_code == 409, resp.text
    assert resp.json() == {'detail': {'code': 'OF_ITEM_REMOVED',
                                     'message': 'Esse banco foi desconectado. Inicie uma nova conexão.'}}
    assert db.get_connections_by_item_id(item) == []
    assert remote[0] == []


def test_callback_novo_e_reconexao_viva_continuam_validos(user_id, remote):
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    item = f'novo-fase4-{user_id}'
    for _ in range(2):
        assert client.post(f'/open-finance/{user_id}/pluggy-item', headers=headers,
                           json={'item': {'id': item}}).status_code == 200
    assert len(db.get_connections_by_item_id(item)) == 1
    assert remote[0] == [item, item]


def test_marco_promovido_por_sync_real_parcial_de_investimentos(user_id):
    _login(user_id)
    cid, _ = _bank(user_id, sync=False)
    mark_sync_result(cid, ok=True, status="ACTIVE", status_reason="investments_read_failed")
    assert db.get_open_finance_snapshot(user_id)['connections'][0]['ui']['state'] == 'partial'
    assert onboarding.get_open_finance_onboarding(user_id)['completed'] is True


def test_sem_plano_le_marco_mas_nao_acessa_nem_remove_bancos(user_id, remote):
    _login(user_id)
    cid, _ = _bank(user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    assert client.get('/onboarding/open-finance').status_code == 200
    assert client.get(f'/open-finance/{user_id}').status_code == 402
    assert client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=headers).status_code == 402
    assert remote[1] == []


def test_delete_individual_revalida_id_apos_lock(user_id, remote, monkeypatch):
    import db.open_finance_state as state
    promote_to_pro(user_id)
    cid, item = _bank(user_id)
    real_lock = state.pluggy_items_lock

    @contextmanager
    def removed_before_lock(items):
        db.disconnect_open_finance_connection(user_id, cid)
        with real_lock(items) as got:
            yield got

    monkeypatch.setattr(state, 'pluggy_items_lock', removed_before_lock)
    client = TestClient(dashboard.app)
    resp = client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=_auth(client, user_id))
    assert resp.status_code == 404
    assert resp.json()['detail']['code'] == 'OF_CONNECTION_NOT_FOUND'
    assert remote[1] == []
    assert 'removed' in db.item_registry_origins(item)


def test_delete_individual_pausado_tambem_serializa_com_reconexao(user_id, remote, monkeypatch):
    promote_to_pro(user_id)
    cid, item = _bank(user_id, status='PAUSED')
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    monkeypatch.setenv('OF_SYNC_LOCK_WAIT_MS', '100')
    with pluggy_item_lock(item) as got:
        assert got
        assert client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=headers).status_code == 503
    assert client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=headers).status_code == 200
    assert remote[1] == []  # remoto já foi removido pela pausa


def test_remocao_sem_dono_no_registry_nao_veta_item_novo(user_id, remote):
    promote_to_pro(user_id)
    item = f'fase4-sem-dono-{user_id}'
    with get_conn() as conn:
        conn.execute("insert into open_finance_item_registry (provider_item_id,origin) values (%s,'removed')",
                     (item,))
        conn.commit()
    try:
        client = TestClient(dashboard.app)
        resp = client.post(f'/open-finance/{user_id}/pluggy-item', headers=_auth(client, user_id),
                           json={'item': {'id': item}})
        assert resp.status_code == 200, resp.text
        assert remote[0] == [item]
    finally:
        with get_conn() as conn:
            conn.execute('delete from open_finance_item_registry where provider_item_id=%s and user_id is null',
                         (item,))
            conn.commit()
