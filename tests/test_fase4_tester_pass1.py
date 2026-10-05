"""Sondas independentes do Tester; apenas superfícies alteradas da Fase 4."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import db.open_finance_onboarding as onboarding
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as routes
from conftest import promote_to_pro
from db.connection import get_conn
from test_fase4_open_finance import _bank, _login
from test_of_item_ownership import _auth


@pytest.mark.parametrize('cid', [0, -1, 2**100])
def test_delete_extremos_nao_busca_remoto_ou_vaza_excecao(user_id, monkeypatch, cid):
    promote_to_pro(user_id)
    monkeypatch.setattr(routes, 'delete_pluggy_item', lambda *a, **kw: pytest.fail('remoto para id inexistente'))
    client = TestClient(dashboard.app)
    resp = client.delete(f'/open-finance/{user_id}/connections/{cid}', headers=_auth(client, user_id))
    assert resp.status_code == 404, resp.text
    assert resp.json()['detail']['code'] == 'OF_CONNECTION_NOT_FOUND'


def test_reconexao_sync_no_mesmo_instante_e_espelho_zero_vale(user_id):
    _login(user_id)
    cid, _ = _bank(user_id)
    instant = datetime.now(timezone.utc)
    with get_conn() as conn:
        conn.execute('update open_finance_connections set last_sync_at=%s,reconnected_at=%s where user_id=%s and id=%s',
                     (instant, instant, user_id, cid))
        conn.commit()
    assert onboarding.get_open_finance_onboarding(user_id)['completed'] is True


def test_updating_com_carimbo_antigo_e_espelho_nao_promove(user_id):
    _login(user_id)
    _bank(user_id, status='UPDATING', sync=True)
    assert onboarding.get_open_finance_onboarding(user_id)['completed'] is False


def test_promocao_simultanea_retorna_um_unico_carimbo_persistido(user_id):
    _login(user_id)
    _bank(user_id)
    with ThreadPoolExecutor(max_workers=3) as pool:
        states = list(pool.map(lambda _: onboarding.get_open_finance_onboarding(user_id), range(3)))
    assert all(s['completed'] for s in states)
    assert states == [states[0]] * 3


def test_query_uid_nao_altera_identidade_do_novo_endpoint(user_id):
    _login(user_id)
    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    own = client.get('/onboarding/open-finance', headers=headers)
    forged = client.get('/onboarding/open-finance?user_id=1&uid=2&completed=true', headers=headers)
    assert forged.status_code == 200
    assert forged.json() == own.json() == {'ok': True, 'completed': False, 'completed_at': None}


def test_marco_sem_espelho_nao_promove(user_id):
    _login(user_id)
    _bank(user_id, mirror=None)
    assert onboarding.get_open_finance_onboarding(user_id)['completed'] is False
