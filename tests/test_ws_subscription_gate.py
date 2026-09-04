"""Gate de assinatura no WebSocket (/ws/{id}) — achado MÉDIA-3 do Tester.

O backstop 402 das rotas de dados (_enforce_subscription_gate, shared.py) não
cobre o WS: sem plano, o snapshot pintava o dashboard inteiro antes do veredito
do paywall. O websocket_endpoint agora espelha o mesmo gate, com as mesmas
primitivas (needs_plan_selection/has_app_access) e a mesma isenção do app iOS.
"""
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import db
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


def test_ua_de_app_nao_abre_o_ws_sem_plano(user_id, monkeypatch):
    """A perna needs_plan_selection do gate nega igual com e sem UA de app.

    Havia isenção aqui pela diretriz 3.1.1, decidida por `"PigBankApp" in
    ws.headers` — uma segunda cópia da checagem que o shared.py fazia. Como o
    header é escolhido pelo cliente, bastava pedir para abrir o WS sem plano e
    receber o snapshot com os dados. Controle negativo: repor o `not in_app and`
    no lambda do gate faz a segunda perna deste teste voltar a receber snapshot.
    """
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
        # mesma conta, UA do app: negado igual — o header não concede nada
        client.cookies.set("dashboard_token", make_dashboard_token(user_id, hours=1))
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/ws/{user_id}", headers={"user-agent": "Mozilla/5.0 PigBankApp"}
            ) as ws:
                ws.receive_json()
        # Controle positivo: carimbado o plano, o MESMO UA de app conecta. Sem
        # isto o caso acima passaria num gate que recusa todo mundo.
        #
        # Pelo db.mark_plan_selected, não por UPDATE cru: get_auth_user tem
        # cache com TTL (db_support._auth_user_cache) e é a escrita oficial que
        # o invalida. Um UPDATE em SQL deixa o cache quente e o gate segue
        # negando — foi o que aconteceu na primeira versão deste teste.
        db.mark_plan_selected(user_id)
        with client.websocket_connect(
            f"/ws/{user_id}", headers={"user-agent": "Mozilla/5.0 PigBankApp"}
        ) as ws:
            assert ws.receive_json()["type"] == "snapshot"
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from auth_accounts where user_id = %s", (user_id,))
            conn.commit()
