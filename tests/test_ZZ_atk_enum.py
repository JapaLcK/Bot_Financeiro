"""ATAQUE DO TESTER — item 7: enumeracao INDEPENDENTE, SEM stub de db e SEM
mutar log_system_event (os testes anteriores escondiam sites atras de monkeypatch)."""
import hashlib, hmac, pytest
from fastapi.testclient import TestClient
import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard

CSRF = "atk-csrf"; SECRET = "atk-secret"; INEX = 999_999_999
NUL = '\\u0000'; SUR = '\\ud800'

@pytest.fixture(scope="module", autouse=True)
def _tbl():
    import asyncio; asyncio.run(admin_dashboard.ensure_admin_tables())

@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    # NOTA: log_system_event NAO e' mockado aqui, de proposito.
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SECRET)
    try: dashboard.limiter._storage.reset()
    except Exception: pass

def _cli(logado=True):
    c = TestClient(dashboard.app, base_url="https://testserver", raise_server_exceptions=False)
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    if logado:
        r = c.post("/admin/auth/login", headers={dashboard.CSRF_HEADER_NAME: CSRF},
                   json={"username":"admin","password":"secret-admin"})
        assert r.status_code == 200, r.text
    return c

def _post(c, url, texto):
    return c.post(url, content=texto, headers={"Content-Type":"application/json",
                                               dashboard.CSRF_HEADER_NAME: CSRF})

def _hook(texto):
    raw = texto.encode(); sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return TestClient(dashboard.app, raise_server_exceptions=False).post(
        "/open-finance/pluggy/webhook", content=raw,
        headers={"Content-Type":"application/json","X-Pluggy-Signature":f"sha256={sig}"})

# TODO campo derivado do corpo em CADA rota do diff, x NUL/surrogate.
CASOS_ADMIN = [
 ("login/username",    False, "/admin/auth/login",                          '{"username":"a%s b","password":"z"}'),
 ("login/password",    False, "/admin/auth/login",                          '{"username":"admin","password":"a%s b"}'),
 ("afiliado/email",    True,  "/admin/api/affiliates",                      '{"email":"a%s@y.com"}'),
 ("afiliado/code",     True,  "/admin/api/affiliates",                      '{"email":"x@y.com","code":"AB%sCD"}'),
 ("afiliado/status",   True,  f"/admin/api/affiliates/{INEX}/status",       '{"status":"a%sb"}'),
 ("plan/plan",         True,  f"/admin/api/users/{INEX}/plan",              '{"plan":"a%sb","months":1}'),
 ("payout/note-paid",  True,  f"/admin/api/affiliates/payouts/{INEX}/paid", '{"note":"a%sb"}'),
 ("payout/note-rej",   True,  f"/admin/api/affiliates/payouts/{INEX}/reject",'{"note":"a%sb"}'),
]

@pytest.mark.parametrize("veneno,vid", [(NUL,"NUL"), (SUR,"surrogate")])
@pytest.mark.parametrize("nome,logado,url,tpl", CASOS_ADMIN, ids=[c[0] for c in CASOS_ADMIN])
def test_admin_campo(nome, logado, url, tpl, veneno, vid):
    r = _post(_cli(logado), url, tpl % veneno)
    print(f"\n  {vid:9} {nome:20} -> {r.status_code} {r.text[:70]}")
    assert r.status_code != 500, r.text[:250]

CASOS_HOOK = [
 ("hook/itemId",         '{"event":"item/error","itemId":"a%sb"}'),
 ("hook/item.id",        '{"event":"item/error","item":{"id":"a%sb"}}'),
 ("hook/item_id",        '{"event":"item/error","item_id":"a%sb"}'),
 ("hook/event_name",     '{"event":"a%sb","itemId":"atk-en"}'),
 ("hook/campo_qualquer", '{"event":"item/error","itemId":"atk-en","z":"a%sb"}'),
 ("hook/txIds",          '{"event":"transactions/deleted","itemId":"atk-en","transactionIds":["a%sb"]}'),
 ("hook/txIds_dict",     '{"event":"transactions/deleted","itemId":"atk-en","transactionIds":[{"a":1}]}'),
 ("hook/txIds_nested",   '{"event":"transactions/deleted","itemId":"atk-en","transactionIds":[[1,2],null,true]}'),
 ("hook/evento_desconh", '{"event":"item/updated","itemId":"atk-en","z":"a%sb"}'),
]

@pytest.mark.parametrize("veneno,vid", [(NUL,"NUL"), (SUR,"surrogate")])
@pytest.mark.parametrize("nome,tpl", CASOS_HOOK, ids=[c[0] for c in CASOS_HOOK])
def test_hook_campo(nome, tpl, veneno, vid):
    r = _hook(tpl % veneno if "%s" in tpl else tpl)
    print(f"\n  {vid:9} {nome:20} -> {r.status_code} {r.text[:70]}")
    assert r.status_code != 500, r.text[:250]

# controles positivos: os mesmos campos com conteudo legitimo
CTRL = [
 ("ctl login",   False, "/admin/auth/login", '{"username":"admin","password":"secret-admin"}', 200),
 ("ctl afiliado",True,  "/admin/api/affiliates", '{"email":"nao-existe-atk@y.com"}', 404),
 ("ctl status",  True,  f"/admin/api/affiliates/{INEX}/status", '{"status":"active"}', 404),
 ("ctl plan",    True,  f"/admin/api/users/{INEX}/plan", '{"plan":"pro","months":3}', 404),
 ("ctl payout",  True,  f"/admin/api/affiliates/payouts/{INEX}/paid", '{"note":"pago"}', 404),
]
@pytest.mark.parametrize("nome,logado,url,corpo,esp", CTRL, ids=[c[0] for c in CTRL])
def test_controle_positivo(nome, logado, url, corpo, esp):
    r = _post(_cli(logado), url, corpo)
    print(f"\n  {nome:22} -> {r.status_code} (esperado {esp})")
    assert r.status_code == esp, r.text[:200]

def test_controle_positivo_hook():
    r = _hook('{"event":"item/error","itemId":"atk-ctl-ok","item":{"id":"atk-ctl-ok"},"x":1.5}')
    print(f"\n  ctl hook               -> {r.status_code}")
    assert r.status_code == 200, r.text[:200]
