import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard


@pytest.fixture(autouse=True)
def configured_admin(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop_log)
    # /admin/auth/login tem rate limit de 10/min (slowapi, storage em memória
    # compartilhado entre testes). Este arquivo faz 4 POSTs nessa rota; somados
    # aos de outros arquivos da mesma sessão isso estoura o teto e derruba um
    # teste com 429 (que se lê como regressão de segurança). Zera o storage por
    # teste — não afrouxa o limite em produção. Mesmo padrão de
    # `tests/test_admin_users_panel.py`.
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass


def _csrf_headers(client: TestClient) -> dict[str, str]:
    token = "test-admin-csrf"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def test_admin_dashboard_redirects_without_admin_session():
    response = TestClient(dashboard.app).get("/admin", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_login_sets_http_only_cookie_and_unlocks_dashboard():
    client = TestClient(dashboard.app, base_url="https://testserver")

    login = client.post(
        "/admin/auth/login",
        headers=_csrf_headers(client),
        json={"username": "admin", "password": "secret-admin"},
    )

    assert login.status_code == 200
    set_cookie = login.headers["set-cookie"]
    assert "admin_auth_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/admin" in set_cookie

    me = client.get("/admin/auth/me")
    assert me.status_code == 200
    assert me.json() == {"username": "admin"}

    dashboard_response = client.get("/admin", follow_redirects=False)
    assert dashboard_response.status_code == 200
    assert dashboard_response.headers["cache-control"] == "no-store"


def test_admin_logout_clears_http_only_cookie():
    client = TestClient(dashboard.app)
    response = client.post("/admin/auth/logout", headers=_csrf_headers(client))

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "admin_auth_token=" in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "Path=/admin" in set_cookie


def test_admin_login_com_senha_nao_ascii_no_fallback_plaintext():
    """A senha digitada chega ao `compare_digest` do fallback plaintext. Com
    acento ele levanta TypeError → 500 no login do admin, em vez do 401.

    O par deste é o test_admin_login_aceita_senha_acentuada_correta abaixo: sem
    ele o grupo passaria num código que recusasse toda senha acentuada.
    """
    client = TestClient(dashboard.app, base_url="https://testserver")

    errada = client.post(
        "/admin/auth/login",
        headers=_csrf_headers(client),
        json={"username": "admin", "password": "senhá-errada"},
    )
    assert errada.status_code == 401


def test_admin_login_aceita_senha_acentuada_correta(monkeypatch):
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "sênha-com-acento")
    client = TestClient(dashboard.app, base_url="https://testserver")

    login = client.post(
        "/admin/auth/login",
        headers=_csrf_headers(client),
        json={"username": "admin", "password": "sênha-com-acento"},
    )

    assert login.status_code == 200
    assert "admin_auth_token=" in login.headers["set-cookie"]


def test_admin_login_com_surrogate_no_corpo_json_nao_da_500():
    """O corpo JSON é o único transporte que entrega surrogate SOLITÁRIO ao
    handler: query usa `errors='replace'`, header e cookie são latin-1, e
    nenhum dos três produz surrogate — mas `json.loads('{"p": "\\udcff"}')`
    produz, e `_check_admin_password` lê a senha justamente dali.

    Controle negativo do lado `fornecido` do `constant_time_eq`: troque o
    `fornecido.encode("utf-8", "replace")` por `.encode("utf-8")` estrito em
    `core/secure_compare.py` e este teste fica vermelho com
    `UnicodeEncodeError` — 500 sem autenticação nenhuma, que é a classe que o
    helper existe pra fechar. O controle positivo do grupo é o
    test_admin_login_sets_http_only_cookie_and_unlocks_dashboard, que continua
    verde sob a mesma mutação.

    Corpo montado à mão de propósito: o `json=` do httpx serializa com
    `ensure_ascii=False` e levantaria no CLIENTE, sem chegar na rota.
    """
    client = TestClient(dashboard.app, base_url="https://testserver")

    response = client.post(
        "/admin/auth/login",
        headers={**_csrf_headers(client), "content-type": "application/json"},
        content=b'{"username": "admin", "password": "\\udcff"}',
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Credenciais inválidas."
