"""Entrega da sessão: token no corpo para o app, cookie para o navegador.

As duas metades são a MESMA decisão e por isso moram na mesma função
(`_entrega_sessao`): um cliente que recebe token no corpo não pode receber
cookie junto. O `fetch` do React Native tem cookie jar ligado por padrão, então
um `Set-Cookie` viraria credencial ambiente guardada sem querer, e a SEGUNDA
escrita tomaria 403 depois de a primeira ter funcionado.

Controle negativo do grupo: forçar `_entrega_sessao` a mandar cookie sempre
deixa `test_login_do_app_nao_manda_cookie_nenhum` vermelho. Controle positivo:
`test_login_do_navegador_continua_recebendo_os_tres_cookies` e
`test_login_sem_header_do_app_nao_devolve_token_no_corpo` provam que a resposta
do site não mudou.
"""
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import login_http as _login_http
from _apoio_auth_app import sessao  # noqa: F401 — fixture, usada por injeção


def test_login_sem_header_do_app_nao_devolve_token_no_corpo(monkeypatch):
    """Sem `X-PigBank-Client: app`, a resposta é a de sempre.

    O que prende a decisão de não ampliar exposição para todo mundo.
    """
    corpo, _ = _login_http(monkeypatch, como_app=False)
    for chave in ("access_token", "refresh_token", "dashboard_token", "expires_in"):
        assert chave not in corpo, f"{chave} vazou sem o cliente ter pedido"


def test_login_com_header_do_app_devolve_as_tres_credenciais(monkeypatch):
    """Com o header, as três credenciais saem no corpo — é o que o app lê."""
    dados, _ = _login_http(monkeypatch, como_app=True)
    assert dados["access_token"]
    assert dados["refresh_token"].startswith("rt_")
    assert dados["dashboard_token"]
    assert dados["expires_in"] == dashboard.AUTH_COOKIE_MAX_AGE


def test_login_do_app_nao_manda_cookie_nenhum(monkeypatch):
    """O app NÃO pode receber Set-Cookie.

    O `fetch` do React Native tem cookie jar ligado por padrão: cookie que o
    servidor mande é cookie que o app guarda sem querer, e a requisição
    seguinte passa a levar credencial ambiente — aí o CSRF volta a exigir o par
    e a SEGUNDA escrita toma 403 depois de a primeira ter funcionado.
    """
    _, resposta = _login_http(monkeypatch, como_app=True)
    enviados = [
        c.split("=", 1)[0] for c in resposta.headers.get_list("set-cookie")
    ]
    for cookie in ("auth_token", "dashboard_token", "refresh_token"):
        assert cookie not in enviados, f"{cookie} foi mandado para o app"


def test_login_do_navegador_continua_recebendo_os_tres_cookies(monkeypatch):
    """Controle positivo da mesma decisão: o site não mudou."""
    _, resposta = _login_http(monkeypatch, como_app=False)
    enviados = [
        c.split("=", 1)[0] for c in resposta.headers.get_list("set-cookie")
    ]
    for cookie in ("auth_token", "dashboard_token", "refresh_token"):
        assert cookie in enviados, f"{cookie} sumiu do navegador"



def test_credencial_no_corpo_sai_com_no_store(monkeypatch):
    """Refresh token de 14 dias no corpo não pode ser guardado no caminho."""
    _, resposta = _login_http(monkeypatch, como_app=True)
    assert "no-store" in (resposta.headers.get("cache-control") or "")


def test_cadastro_pelo_google_entrega_credencial_ao_app(monkeypatch):
    """A QUARTA emissão de sessão, que o Codex pegou fora do canal.

    `/auth/google/complete-signup` é POST JSON e é alcançável pelo app, mas
    setava os três cookies direto e não devolvia token nenhum — então quem
    entrasse por Google no app ficava sem credencial para guardar. Os outros
    dois pontos de emissão do Google são REDIRECT (o callback e o `/d/{code}`),
    que só fazem sentido num navegador e ficam para a fase do login social.
    """
    import db
    from fastapi.testclient import TestClient
    from _apoio_auth_app import UID

    db.ensure_user(UID)
    monkeypatch.setattr(
        dashboard,
        "consume_pending_google_signup",
        lambda token, nome, telefone, origem: {
            "user_id": UID,
            "email": "google@example.com",
            "link_code": "ABC123",
        },
        raising=False,
    )
    import db as _db
    monkeypatch.setattr(
        _db,
        "consume_pending_google_signup",
        lambda token, nome, telefone, origem: {
            "user_id": UID,
            "email": "google@example.com",
            "link_code": "ABC123",
        },
        raising=False,
    )

    client = TestClient(dashboard.app)
    r = client.post(
        "/auth/google/complete-signup",
        headers={dashboard.APP_CLIENT_HEADER: "app"},
        json={
            "token": "tok",
            "name": "Fulano",
            "phone": "11999999999",
            "accepted_terms": True,
        },
    )
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["access_token"] and corpo["refresh_token"]
    enviados = [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]
    for cookie in ("auth_token", "dashboard_token", "refresh_token"):
        assert cookie not in enviados, f"{cookie} foi mandado para o app"
