"""Refresh sem cookie, token único e rate limit por usuário.

Controle negativo do grupo: trocar `_refresh_token_do_header` por "" deixa os
testes de refresh do app vermelhos; devolver `get_remote_address` sempre em
`chave_de_rate_limit` deixa `test_rate_limit_separa_usuarios_no_mesmo_ip`
vermelho (medido). Controle positivo:
`test_refresh_do_navegador_com_corpo_vazio_continua_funcionando` e
`test_cookie_tem_precedencia_sobre_o_header` provam que o site não mudou.
"""
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from frontend.routes.shared import chave_de_rate_limit, payload_de_sessao
from _apoio_auth_app import ALVO, CORPO, UID, csrf as _csrf, req as _req
from _apoio_auth_app import sessao  # noqa: F401 — fixture, usada por injeção


# ── Refresh sem cookie: a segurança sobreviveu ao caminho novo ───────────────

def _refresh_do_app(client: TestClient, token: str):
    """Como o app renova: refresh token no Authorization, sem cookie nenhum."""
    return client.post(
        "/auth/refresh",
        headers={
            "Authorization": f"Bearer {token}",
            dashboard.APP_CLIENT_HEADER: "app",
        },
    )


def test_refresh_pelo_header_rotaciona(sessao):
    """O token velho morre e o novo funciona — a rotação de sempre."""
    client, dados = sessao
    velho = dados["refresh_token"]

    r = _refresh_do_app(client, velho)
    assert r.status_code == 200, r.text
    novo = r.json()["refresh_token"]
    assert novo != velho
    assert novo.startswith("rt_")

    assert _refresh_do_app(client, novo).status_code == 200


def test_refresh_do_app_passa_sem_cookie_e_sem_csrf(sessao):
    """O caso que o CSRF barrava: no refresh o app não tem Bearer de sessão.

    O access token é justamente o que expirou, então a credencial que viaja é o
    próprio refresh token. Sem isto a rota devolvia 403 antes de chegar ao
    handler — medido, e foi o que fez o corpo virar header.
    """
    client, dados = sessao
    r = client.post(
        "/auth/refresh",
        headers={"Authorization": f"Bearer {dados['refresh_token']}"},
    )
    assert r.status_code == 200, r.text


def test_access_jwt_no_header_do_refresh_nao_serve_de_refresh(sessao):
    """Só `rt_` é refresh. Um access JWT ali é credencial errada, não atalho."""
    client, dados = sessao
    r = client.post(
        "/auth/refresh",
        headers={"Authorization": f"Bearer {dados['access_token']}"},
    )
    assert r.status_code == 401, r.text


def test_replay_pelo_header_revoga_tudo_do_usuario(sessao):
    """Apresentar o MESMO refresh duas vezes é sinal de roubo.

    A detecção mora em `core/refresh_tokens.py:129` e é a condição de saída da
    Fase 1: o caminho pelo header tem de sofrer a mesma revogação em massa que o
    caminho por cookie, senão o app teria trocado segurança por conveniência.
    """
    client, dados = sessao
    velho = dados["refresh_token"]

    primeira = _refresh_do_app(client, velho)
    assert primeira.status_code == 200, primeira.text
    emitido_apos_rotacao = primeira.json()["refresh_token"]

    # Replay do token já consumido.
    replay = _refresh_do_app(client, velho)
    assert replay.status_code == 401, replay.text
    assert replay.json()["detail"] == "invalid_refresh_token"

    # E a revogação foi EM MASSA: o token emitido na rotação legítima também morreu.
    apos = _refresh_do_app(client, emitido_apos_rotacao)
    assert apos.status_code == 401, apos.text


def test_refresh_do_navegador_com_corpo_vazio_continua_funcionando(sessao):
    """Regressão: os dois clientes web mandam JSON com corpo VAZIO.

    `frontend/login.html:113` e `frontend/static/auth-refresh.js:57` mandam
    `Content-Type: application/json` sem corpo. É por causa deste caso que a
    credencial do app foi para o `Authorization` e não para um modelo de corpo
    novo: corpo obrigatório aqui viraria 422 e o refresh do site pararia em
    silêncio, porque o interceptor só trata 401.
    """
    client, dados = sessao
    client.cookies.set(dashboard.REFRESH_COOKIE_NAME, dados["refresh_token"])
    r = client.post(
        "/auth/refresh",
        headers={**_csrf(client), "Content-Type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # Sem o header do app, nada de credencial no corpo.
    assert "refresh_token" not in r.json()


def test_cookie_tem_precedencia_sobre_o_header(sessao):
    """Header é a SEGUNDA fonte. Com cookie presente, é o cookie que vale."""
    client, dados = sessao
    client.cookies.set(dashboard.REFRESH_COOKIE_NAME, dados["refresh_token"])
    r = client.post(
        "/auth/refresh",
        headers={
            **_csrf(client),
            "Authorization": "Bearer rt_um_token_que_nao_existe",
        },
    )
    assert r.status_code == 200, r.text


# ── Um token só ──────────────────────────────────────────────────────────────

def test_access_jwt_vale_onde_o_dashboard_token_valia(sessao):
    """O app carrega UM token. O access JWT autentica a rota de dados."""
    client, dados = sessao
    r = client.post(
        ALVO,
        headers={"Authorization": f"Bearer {dados['access_token']}"},
        json=CORPO,
    )
    assert r.status_code == 200, r.text


def test_payload_de_sessao_recusa_token_de_outro_tipo():
    """Nem todo JWT assinado serve: `type` errado é recusa."""
    assert payload_de_sessao("") is None
    assert payload_de_sessao("nao-e-jwt") is None
    # Um token de tipo desconhecido, assinado com o MESMO segredo.
    import jwt as pyjwt

    from frontend.routes.shared import JWT_SECRET

    intruso = pyjwt.encode(
        {"sub": "1", "type": "outro-tipo"}, JWT_SECRET, algorithm="HS256"
    )
    assert payload_de_sessao(intruso) is None


# ── Rate limit por usuário ───────────────────────────────────────────────────

def test_rate_limit_separa_usuarios_no_mesmo_ip(sessao):
    """O caso do CGNAT: dois usuários atrás de um IP não dividem o balde."""
    _, dados = sessao
    chave = chave_de_rate_limit(
        _req(ALVO, cookies={dashboard.DASHBOARD_COOKIE_NAME: dados["dashboard_token"]})
    )
    assert chave == f"user:{UID}"
    assert chave != chave_de_rate_limit(_req(ALVO))


def test_rate_limit_de_auth_continua_por_ip(sessao):
    """Força bruta continua contida por IP — o controle não mudou de lado."""
    _, dados = sessao
    chave = chave_de_rate_limit(
        _req(
            "/auth/login",
            cookies={dashboard.DASHBOARD_COOKIE_NAME: dados["dashboard_token"]},
        )
    )
    assert chave == "10.0.0.1"


def test_rate_limit_sem_token_cai_no_ip():
    """Sem credencial legível, o comportamento é o de antes."""
    assert chave_de_rate_limit(_req(ALVO)) == "10.0.0.1"
    assert chave_de_rate_limit(
        _req(ALVO, cookies={dashboard.DASHBOARD_COOKIE_NAME: "lixo"})
    ) == "10.0.0.1"
