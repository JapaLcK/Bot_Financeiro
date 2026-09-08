"""ATAQUE DO TESTER (temporario) — item 7: o que chega ao psycopg pelas linhas do diff."""
import pytest
from fastapi.testclient import TestClient
import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard

CSRF = "atk-csrf"; INEX = 999_999_999

@pytest.fixture(scope="module", autouse=True)
def _tbl():
    import asyncio; asyncio.run(admin_dashboard.ensure_admin_tables())

@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    async def _noop(*a, **k): return None
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop)
    try: dashboard.limiter._storage.reset()
    except Exception: pass

def _cli():
    c = TestClient(dashboard.app, base_url="https://testserver", raise_server_exceptions=False)
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    r = c.post("/admin/auth/login", headers={dashboard.CSRF_HEADER_NAME: CSRF},
               json={"username":"admin","password":"secret-admin"})
    assert r.status_code == 200
    return c

def _post(c, url, texto):
    return c.post(url, content=texto, headers={"Content-Type":"application/json",
                                               dashboard.CSRF_HEADER_NAME: CSRF})

NOTAS = [
  ("controle_note_normal", '"pago via pix"'),
  ("controle_note_acento", '"pagamento concluído — ação"'),
  ("controle_note_emoji",  '"\\ud83d\\udc16 ok"'),
  ("NUL_no_meio",          '"a\\u0000b"'),
  ("NUL_sozinho",          '"\\u0000"'),
  ("NUL_no_fim",           '"pago\\u0000"'),
  ("surrogate_solitario",  '"\\ud800"'),
]
ACOES = ["paid", "reject"]

@pytest.mark.parametrize("acao", ACOES)
@pytest.mark.parametrize("nome,nota", NOTAS, ids=[n[0] for n in NOTAS])
def test_payout_note_no_psycopg(nome, nota, acao):
    r = _post(_cli(), f"/admin/api/affiliates/payouts/{INEX}/{acao}", '{"note": %s}' % nota)
    print(f"\n  note/{acao:6} {nome:22} -> {r.status_code}")
    assert r.status_code != 500, r.text[:250]

# --- caminho REAL (sem espiao) dos numeros gigantes: chegam no banco? ---
@pytest.mark.parametrize("v,nome", [
  ("9"*4300, "int_4300dig"), ("1"+"0"*400, "int_401dig"),
  ("1.7976931348623157e308", "float_1e308"), ("-9"+"9"*300, "int_neg_grande"),
], ids=["4300dig","401dig","1e308","neg_grande"])
def test_months_gigante_caminho_real(monkeypatch, v, nome):
    r = _post(_cli(), f"/admin/api/users/{INEX}/plan", '{"plan":"pro","months":%s}' % v)
    print(f"\n  months REAL {nome:16} -> {r.status_code} {r.text[:90]}")
    assert r.status_code != 500, r.text[:250]

@pytest.mark.parametrize("v,nome", [
  ("9"*4300, "int_4300dig"), ("1"+"0"*400, "int_401dig"),
  ("1.7976931348623157e308", "float_1e308"), ("-500", "negativo"),
], ids=["4300dig","401dig","1e308","negativo"])
def test_bps_gigante_caminho_real(monkeypatch, v, nome):
    import db
    monkeypatch.setattr(db, "find_user_id_by_email", lambda e: 4242)
    r = _post(_cli(), "/admin/api/affiliates", '{"email":"x@y.com","commission_bps":%s}' % v)
    print(f"\n  bps REAL    {nome:16} -> {r.status_code} {r.text[:90]}")
    assert r.status_code != 500, r.text[:250]

# --- NUL nos outros campos que o diff toca ---
@pytest.mark.parametrize("campo,corpo,url", [
  ("login_username", '{"username":"a\\u0000b","password":"x"}', "/admin/auth/login"),
  ("afiliado_code",  '{"email":"x@y.com","code":"AB\\u0000CD"}', "/admin/api/affiliates"),
  ("afiliado_status",'{"status":"a\\u0000b"}', f"/admin/api/affiliates/{INEX}/status"),
  ("plan_nome",      '{"plan":"a\\u0000b","months":1}', f"/admin/api/users/{INEX}/plan"),
])
def test_nul_nos_outros_campos(monkeypatch, campo, corpo, url):
    import db
    monkeypatch.setattr(db, "find_user_id_by_email", lambda e: 4242)
    c = TestClient(dashboard.app, base_url="https://testserver", raise_server_exceptions=False)
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    if url != "/admin/auth/login":
        c = _cli()
    r = _post(c, url, corpo)
    print(f"\n  NUL {campo:18} -> {r.status_code} {r.text[:90]}")
    assert r.status_code != 500, r.text[:250]
