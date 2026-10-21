"""Invariante do `csrf()` de `_apoio_auth_app`: o jar sai com UM `csrf_token`.

Depois de um GET, o jar já tem o cookie do servidor (`testserver.local`); o do
teste vai para o domínio "". Com os dois, quem vence no header é a ordem do jar,
e a httpx2 2.16.0 a inverteu (21 testes em 403). Contar os cookies mede o helper
em qualquer versão da httpx2, inclusive na fixada no `requirements.txt`.
"""
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import ALVO, CORPO, csrf


def _csrfs(client):
    return [c for c in client.cookies.jar if c.name == dashboard.CSRF_COOKIE_NAME]


def test_csrf_depois_de_get_deixa_um_cookie_so_e_passa_no_middleware():
    c = TestClient(dashboard.app)
    c.get("/login")
    assert len(_csrfs(c)) == 1, "o GET deveria plantar o cookie do servidor"

    h = csrf(c)
    vivos = _csrfs(c)
    assert len(vivos) == 1, [(k.domain, k.value) for k in vivos]
    assert vivos[0].value == h[dashboard.CSRF_HEADER_NAME]

    # Sem sessão: passa pelo CSRF e para na autenticação.
    assert c.post(ALVO, json=CORPO, headers=h).status_code == 401


def test_csrf_em_jar_vazio_planta_um_cookie():
    c = TestClient(dashboard.app)
    h = csrf(c)
    vivos = _csrfs(c)
    assert len(vivos) == 1
    assert vivos[0].value == h[dashboard.CSRF_HEADER_NAME]
