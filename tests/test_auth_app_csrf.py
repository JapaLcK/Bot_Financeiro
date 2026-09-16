"""CSRF e entrega da sessão: o app fala sem cookie, e o navegador não afrouxou.

Os dois controles que o `CLAUDE.md` §3 exige:

  * **negativo** — faça `_sem_credencial_ambiente` devolver False: este grupo
    fica com 7 vermelhos (medido). Sem a isenção, Bearer sem cookie tomava 403.
    E force `_entrega_sessao` a mandar cookie sempre: mais 3 vermelhos.
  * **positivo** — `test_cookie_sem_csrf_continua_403`,
    `test_cookie_com_csrf_continua_passando` e
    `test_login_do_navegador_continua_recebendo_os_tres_cookies` provam que o
    caminho legítimo do site continua de pé. Sem eles o grupo passaria num
    servidor que simplesmente desligou o CSRF — pior que o bug.

A classe de bug que este arquivo NÃO alcança: nada aqui roda num aparelho nem
fala com APNs/Pluggy. É contrato de servidor, medido por TestClient.
"""
import pytest

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import ALVO, CORPO, csrf as _csrf, login_http as _login_http
from _apoio_auth_app import sessao  # noqa: F401 — fixture, usada por injeção


# ── A matriz de CSRF ─────────────────────────────────────────────────────────

def test_bearer_sem_cookie_passa_no_csrf(sessao):
    """Bearer válido, sem cookie nenhum, sem header de CSRF: a escrita acontece.

    É o caminho do app. Antes da mudança isto era 403.
    """
    client, dados = sessao
    r = client.post(
        ALVO,
        headers={"Authorization": f"Bearer {dados['access_token']}"},
        json=CORPO,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}


def test_bearer_invalido_morre_em_401_e_nao_em_403(sessao):
    """Credencial inválida tem de falhar na AUTENTICAÇÃO, não no CSRF.

    Se o CSRF barrasse antes, quem depura um login veria 403 "token CSRF
    ausente" no lugar do 401 que é o motivo real.
    """
    client, _ = sessao
    r = client.post(
        ALVO,
        headers={"Authorization": "Bearer nao-e-um-jwt"},
        json=CORPO,
    )
    assert r.status_code == 401, r.text


def test_cookie_sem_csrf_continua_403(sessao):
    """Controle positivo: o navegador NÃO foi afrouxado."""
    client, dados = sessao
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dados["dashboard_token"])
    r = client.post(ALVO, json=CORPO)
    assert r.status_code == 403, r.text


def test_cookie_com_csrf_continua_passando(sessao):
    """Controle positivo: o caminho legítimo do site segue funcionando."""
    client, dados = sessao
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dados["dashboard_token"])
    r = client.post(ALVO, headers=_csrf(client), json=CORPO)
    assert r.status_code == 200, r.text


def test_bearer_com_cookie_junto_ainda_exige_csrf(sessao):
    """Credencial ambiente presente ⇒ o par cookie+header volta a ser exigido.

    É a fronteira da isenção: basta UM cookie de sessão no jar para a
    requisição voltar a ser disparável por página de terceiro.
    """
    client, dados = sessao
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dados["dashboard_token"])
    r = client.post(
        ALVO,
        headers={"Authorization": f"Bearer {dados['access_token']}"},
        json=CORPO,
    )
    assert r.status_code == 403, r.text


@pytest.mark.parametrize("cookie", ["auth_token", "dashboard_token", "refresh_token"])
def test_qualquer_um_dos_tres_cookies_reativa_o_csrf(sessao, cookie):
    """Os TRÊS contam, não só o do dashboard — enumeração, não amostra."""
    client, dados = sessao
    client.cookies.set(cookie, "valor-qualquer")
    r = client.post(
        ALVO,
        headers={"Authorization": f"Bearer {dados['access_token']}"},
        json=CORPO,
    )
    assert r.status_code == 403, r.text


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

