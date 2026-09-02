"""Gate de assinatura no WebSocket (/ws/{id}) — achado MÉDIA-3 do Tester.

O backstop 402 das rotas de dados (_enforce_subscription_gate, shared.py) não
cobre o WS: sem plano, o snapshot pintava o dashboard inteiro antes do veredito
do paywall. O websocket_endpoint agora espelha o mesmo gate, com as mesmas
primitivas (needs_plan_selection/has_app_access) e a mesma isenção do app iOS.
"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import frontend.finance_bot_websocket_custom as dashboard
from token_utils import make_dashboard_token
from tests.conftest import promote_to_pro


def _connect(client: TestClient, uid: int):
    client.cookies.set("dashboard_token", make_dashboard_token(uid, hours=1))
    return client.websocket_connect(f"/ws/{uid}")


def test_sem_plano_ws_fecha_sem_snapshot(user_id, monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")   # v2 off: has_app_access decide
    monkeypatch.setenv("PAYWALL_ENABLED", "1")    # paywall ligado, user não é pro
    client = TestClient(dashboard.app)
    with pytest.raises(WebSocketDisconnect):
        with _connect(client, user_id) as ws:
            ws.receive_json()  # não pode chegar snapshot nenhum


def test_com_plano_ws_manda_snapshot_normal(user_id, monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("PAYWALL_ENABLED", "1")
    promote_to_pro(user_id)
    client = TestClient(dashboard.app)
    with _connect(client, user_id) as ws:
        msg = ws.receive_json()
    assert msg["type"] == "snapshot"
    assert msg["data"]["user_id"] == user_id


def test_paywall_desligado_segue_liberado(user_id, monkeypatch):
    """Config padrão de hoje (v2 on) — nada muda pra quem tem acesso."""
    monkeypatch.delenv("PAYWALL_ENABLED", raising=False)
    client = TestClient(dashboard.app)
    with _connect(client, user_id) as ws:
        msg = ws.receive_json()
    assert msg["type"] == "snapshot"


def test_isencao_ios_da_escolha_de_plano(user_id, monkeypatch):
    """A perna needs_plan_selection do gate: cadastro sem plano escolhido é
    negado na web, mas o app iOS (UA PigBankApp) passa — diretriz 3.1.1, a
    mesma isenção do _enforce_subscription_gate das rotas de dados."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")  # gate de escolha ativo (v2)
    from db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts(user_id, email, password_hash, plan) "
                "values (%s, %s, 'x', 'free')",  # plan_selected_at NULL
                (user_id, f"wsgate-{user_id}@test.local"),
            )
        conn.commit()
    try:
        client = TestClient(dashboard.app)
        # web: negado (não escolheu plano)
        with pytest.raises(WebSocketDisconnect):
            with _connect(client, user_id) as ws:
                ws.receive_json()
        # iOS: mesma conta, UA do app ⇒ aceita e snapshot chega
        client.cookies.set("dashboard_token", make_dashboard_token(user_id, hours=1))
        with client.websocket_connect(
            f"/ws/{user_id}", headers={"user-agent": "Mozilla/5.0 PigBankApp"}
        ) as ws:
            msg = ws.receive_json()
        assert msg["type"] == "snapshot"
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from auth_accounts where user_id = %s", (user_id,))
            conn.commit()
