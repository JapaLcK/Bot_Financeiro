"""ATAQUE DO TESTER (temporario) — matriz de int()/coercao por HTTP, itens 3/4/7."""
import hashlib, hmac, json, pytest
from fastapi.testclient import TestClient
import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as open_finance_routes

CSRF = "atk-csrf"; SECRET = "atk-secret"

@pytest.fixture(scope="module", autouse=True)
def _tbl():
    import asyncio; asyncio.run(admin_dashboard.ensure_admin_tables())

@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    async def _noop(*a, **k): return None
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop)
    monkeypatch.setattr(open_finance_routes, "log_system_event", _noop)
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", SECRET)
    try: dashboard.limiter._storage.reset()
    except Exception: pass

def _cli():
    c = TestClient(dashboard.app, base_url="https://testserver", raise_server_exceptions=False)
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    r = c.post("/admin/auth/login", headers={dashboard.CSRF_HEADER_NAME: CSRF},
               json={"username": "admin", "password": "secret-admin"})
    assert r.status_code == 200, r.text
    return c

def _post(c, url, texto):
    return c.post(url, content=texto, headers={"Content-Type": "application/json",
                                               dashboard.CSRF_HEADER_NAME: CSRF})

# as 15 formas que json.loads produz + extremos
VALORES = [
 ("int",            "7"), ("int_neg", "-7"), ("int_zero", "0"),
 ("int_4300dig",    "9"*4300), ("int_4301dig", "9"*4301),
 ("int_401dig",     "1"+"0"*400),
 ("float",          "1.5"), ("float_zero", "0.0"), ("float_neg", "-1.5"),
 ("float_1e308",    "1.7976931348623157e308"),
 ("float_5e-324",   "5e-324"), ("float_1e-400", "1e-400"),
 ("str_num",        '"1500"'), ("str_txt", '"abc"'), ("str_vazia", '""'),
 ("str_espacos",    '"   "'), ("str_4301dig", '"'+"9"*4301+'"'),
 ("str_arabe",      '"\\u0661\\u0662"'), ("str_underscore", '"1_5"'),
 ("str_float",      '"1.5"'), ("str_hex", '"0x10"'),
 ("bool_true",      "true"), ("bool_false", "false"),
 ("null",           "null"), ("lista", "[10]"), ("lista_vazia", "[]"),
 ("objeto",         '{"a":1}'), ("objeto_vazio", "{}"),
 ("nan",            "NaN"), ("infinity", "Infinity"), ("menos_inf", "-Infinity"),
 ("1e400",          "1e400"), ("1E400", "1E400"), ("1e_mais_400", "1e+400"),
 ("0ponto1e400",    "0.1e400"), ("1e999999", "1e999999"),
]

@pytest.fixture
def espioes(monkeypatch):
    ch = {"plan": [], "afil": []}
    import db, db.affiliates
    def _plan(plan, months, **kw): ch["plan"].append((plan, months, kw)); return None
    def _afil(uid, code, bps): ch["afil"].append((uid, code, bps)); return {"id":1,"code":code or "G","status":"active","commission_bps":bps}
    monkeypatch.setattr(admin_dashboard, "set_account_plan", _plan)
    monkeypatch.setattr(db, "find_user_id_by_email", lambda e: 4242)
    monkeypatch.setattr(db.affiliates, "create_affiliate", _afil)
    return ch

@pytest.mark.parametrize("nome,v", VALORES, ids=[x[0] for x in VALORES])
def test_months(espioes, nome, v):
    c = _cli()
    r = _post(c, "/admin/api/users/4242/plan", '{"plan":"pro","months":%s}' % v)
    print(f"\n  months  {nome:16} -> {r.status_code}  espiao={espioes['plan']}")
    assert r.status_code != 500, r.text[:200]

@pytest.mark.parametrize("nome,v", VALORES, ids=[x[0] for x in VALORES])
def test_bps(espioes, nome, v):
    c = _cli()
    r = _post(c, "/admin/api/affiliates", '{"email":"x@y.com","commission_bps":%s}' % v)
    print(f"\n  bps     {nome:16} -> {r.status_code}  espiao={espioes['afil']}")
    assert r.status_code != 500, r.text[:200]

@pytest.mark.parametrize("nome,v", VALORES, ids=[x[0] for x in VALORES])
def test_code(espioes, nome, v):
    c = _cli()
    r = _post(c, "/admin/api/affiliates", '{"email":"x@y.com","code":%s}' % v)
    print(f"\n  code    {nome:16} -> {r.status_code}  espiao={espioes['afil']}")
    assert r.status_code != 500, r.text[:200]

# --- tamanho absurdo / aninhamento: RecursionError no parse ---
@pytest.mark.parametrize("prof", [100, 1000, 5000, 20000], ids=["d100","d1k","d5k","d20k"])
def test_aninhamento_profundo(prof):
    corpo = '{"a":' * prof + '1' + '}' * prof
    c = _cli()
    r = _post(c, "/admin/api/users/4242/plan", corpo)
    print(f"\n  aninhado plan  {prof:6} -> {r.status_code}")
    assert r.status_code != 500, r.text[:200]

@pytest.mark.parametrize("prof", [100, 1000, 5000, 20000], ids=["d100","d1k","d5k","d20k"])
def test_aninhamento_profundo_webhook(prof):
    corpo = '{"a":' * prof + '1' + '}' * prof
    raw = corpo.encode(); sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    r = TestClient(dashboard.app, raise_server_exceptions=False).post(
        "/open-finance/pluggy/webhook", content=raw,
        headers={"Content-Type":"application/json","X-Pluggy-Signature":f"sha256={sig}"})
    print(f"\n  aninhado hook  {prof:6} -> {r.status_code}")
    assert r.status_code != 500, r.text[:200]
