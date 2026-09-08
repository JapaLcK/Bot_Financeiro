"""ATAQUE DO TESTER (temporario) — irmaos do A2 que sobrevivem ao parse novo."""
import hashlib, hmac, json, pytest
from fastapi.testclient import TestClient
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as open_finance_routes

SECRET = "test-webhook-secret"

@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    async def _noop(*a, **k): return None
    monkeypatch.setattr(open_finance_routes, "log_system_event", _noop)
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SECRET)

def _post(texto: str):
    raw = texto.encode("utf-8")
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return TestClient(dashboard.app, raise_server_exceptions=False).post(
        "/open-finance/pluggy/webhook", content=raw,
        headers={"Content-Type": "application/json",
                 "X-Pluggy-Signature": f"sha256={sig}"})

CASOS = [
    ("controle_corpo_bom",        '{"event":"item/error","itemId":"atk-ctl"}'),
    ("controle_1e400_do_A2",      '{"event":"item/error","itemId":"atk-1","x":1e400}'),
    ("controle_NaN_do_A2",        '{"event":"item/error","itemId":"atk-2","x":NaN}'),
    ("NUL_em_valor",              '{"event":"item/error","itemId":"atk-3","x":"a\\u0000b"}'),
    ("NUL_em_chave",              '{"event":"item/error","itemId":"atk-4","a\\u0000b":1}'),
    ("NUL_no_itemId",             '{"event":"item/error","itemId":"a\\u0000b"}'),
    ("NUL_no_item_id_aninhado",   '{"event":"item/error","item":{"id":"atk-5"},"z":{"w":["a\\u0000"]}}'),
    ("surrogate_solitario",       '{"event":"item/error","itemId":"atk-6","x":"\\ud800"}'),
    ("surrogate_par_valido_ctl",  '{"event":"item/error","itemId":"atk-7","x":"\\ud83d\\udc16"}'),
]

@pytest.mark.parametrize("nome,corpo", CASOS, ids=[c[0] for c in CASOS])
def test_irmao_do_A2(nome, corpo):
    r = _post(corpo)
    print(f"\n  >>> {nome:28} -> HTTP {r.status_code}")
    assert r.status_code != 500, f"{nome} deu 500: {r.text[:200]}"
