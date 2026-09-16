"""CSRF e entrega da sessão: o app fala sem cookie, e o navegador não afrouxou.

Os dois controles que o `CLAUDE.md` §3 exige:

  * **negativo** — três mutações, cada uma medida NESTE arquivo: desligar a
    isenção do CSRF (4 vermelhos); mandar cookie para o app em `_entrega_sessao`
    (1); tirar a condição de corpo JSON, que reabre login CSRF (2).
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


def test_cookie_do_admin_tambem_e_credencial_ambiente(sessao):
    """O QUARTO cookie. As rotas de /admin moram no mesmo `app`, logo no mesmo
    middleware — a lista tinha três e a revisão pegou a ausência."""
    from core.admin_dashboard import ADMIN_AUTH_COOKIE_NAME

    client, dados = sessao
    client.cookies.set(ADMIN_AUTH_COOKIE_NAME, "sessao-de-admin")
    r = client.post(
        ALVO,
        headers={"Authorization": f"Bearer {dados['access_token']}"},
        json=CORPO,
    )
    assert r.status_code == 403, r.text


def test_cookie_duplicado_vazio_nao_apaga_a_credencial_ambiente(sessao):
    """`Cookie: x=v; x=` guarda a ÚLTIMA ocorrência, e `.get()` devolve "".

    Por valor, a duplicata vazia apagava do teste uma credencial que ESTÁ no
    jar. Por presença da chave, não apaga.
    """
    client, dados = sessao
    r = client.post(
        ALVO,
        headers={
            "Authorization": f"Bearer {dados['access_token']}",
            "Cookie": f"dashboard_token={dados['dashboard_token']}; dashboard_token=",
        },
        json=CORPO,
    )
    assert r.status_code == 403, r.text


# ── O header do app NÃO é credencial nem isenção ─────────────────────────────

def test_header_do_app_falsificado_com_cookie_nao_isenta(sessao):
    """`X-PigBank-Client` é alegação de quem chama, e não tira o CSRF.

    Com um cookie de sessão no jar existe credencial ambiente, e nenhuma
    alegação do cliente muda isso. Se o header participasse da decisão, mandá-lo
    seria o contorno.
    """
    client, dados = sessao
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dados["dashboard_token"])
    r = client.post(
        ALVO,
        headers={
            "Authorization": f"Bearer {dados['access_token']}",
            dashboard.APP_CLIENT_HEADER: "app",
        },
        json=CORPO,
    )
    assert r.status_code == 403, r.text


def test_sem_o_header_do_app_a_isencao_continua_valendo(sessao):
    """A isenção não depende do header — depende de não haver cookie."""
    client, dados = sessao
    r = client.post(
        ALVO,
        headers={"Authorization": f"Bearer {dados['access_token']}"},
        json=CORPO,
    )
    assert r.status_code == 200, r.text


def test_login_do_app_passa_sem_cookie_e_sem_csrf(monkeypatch):
    """O caso que a revisão pegou: no login o app ainda não tem token nenhum.

    Sem cookie e sem CSRF, a rota tem de responder — senão o app não consegue
    nem entrar, que é o que a versão anterior da isenção causava.
    """
    import db
    from fastapi.testclient import TestClient
    from _apoio_auth_app import EMAIL, UID, limpa_rate_limits, noop_log

    db.ensure_user(UID)
    limpa_rate_limits("login", EMAIL)
    monkeypatch.setattr(
        db,
        "login_auth_user",
        lambda e, p: {"user_id": UID, "email": e.strip().lower(), "plan": "free"},
    )
    monkeypatch.setattr(db, "create_link_code", lambda user_id, minutes_valid: "ABC123")
    monkeypatch.setattr(dashboard, "log_auth_login_event", noop_log)

    client = TestClient(dashboard.app)
    r = client.post(
        "/auth/login",
        headers={dashboard.APP_CLIENT_HEADER: "app"},
        json={"email": EMAIL, "password": "seja-o-que-for"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["access_token"]
    enviados = [c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")]
    for cookie in ("auth_token", "dashboard_token", "refresh_token"):
        assert cookie not in enviados


def test_rate_limit_do_login_do_app_continua_por_ip():
    """Entrada do app não ganha balde próprio: força bruta segue contida por IP."""
    from frontend.routes.shared import chave_de_rate_limit
    from _apoio_auth_app import req as _req

    assert chave_de_rate_limit(_req("/auth/login")) == "10.0.0.1"


def test_formulario_cross_site_nao_ganha_isencao(sessao):
    """Login CSRF, a porta que a auditoria achou aberta.

    Os cookies de SESSÃO são `SameSite=lax` e nunca viajaram num POST
    cross-site; quem barrava um `<form>` de terceiro apontado para `/auth/login`
    era o cookie de CSRF, que é `strict`. Com a isenção só por ausência de
    cookie, a página do atacante faria o navegador da vítima entrar na CONTA
    DELE — e a vítima seguiria usando o site achando que é a sua.

    A 2ª condição (corpo JSON) fecha isso: um formulário cross-site só emite
    `urlencoded`, `multipart` ou `text/plain`, os três tipos que dispensam
    preflight.
    """
    client, _ = sessao
    r = client.post(
        "/auth/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        content="email=vitima@example.com&password=x",
    )
    assert r.status_code == 403, r.text


def test_texto_puro_tambem_nao_ganha_isencao(sessao):
    """`text/plain` é o terceiro tipo que um `<form>` consegue emitir."""
    client, _ = sessao
    r = client.post(
        "/auth/login",
        headers={"Content-Type": "text/plain"},
        content='{"email":"vitima@example.com","password":"x"}',
    )
    assert r.status_code == 403, r.text


def test_a_lista_de_cookies_de_sessao_e_a_do_codigo(sessao):
    """Deriva do CÓDIGO, não copia a lista — fecha a CLASSE, não a instância.

    O parametrizado acima enumera três nomes à mão, então um QUINTO cookie
    nascer não deixaria nenhum vermelho. Este varre a tupla real.
    """
    client, dados = sessao
    for nome in dashboard.COOKIES_DE_SESSAO:
        client.cookies.clear()
        client.cookies.set(nome, "valor-qualquer")
        r = client.post(
            ALVO,
            headers={"Authorization": f"Bearer {dados['access_token']}"},
            json=CORPO,
        )
        assert r.status_code == 403, f"{nome} não reativou o CSRF: {r.text}"


