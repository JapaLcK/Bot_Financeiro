"""Testes do link público de afiliado (GET /r/{code}).

O afiliado vem do banco real (`create_affiliate`, que já entrega
status='active' por default do schema) porque o par lookup + `status ==
"active"` decide se o cookie sai, e `test_afiliado_desativado_nao_seta_cookie`
exercita o lado `disabled` — sem ele o banco real não pagaria por si: com
todos os casos active, trocar o handler por `if affiliate:` mantinha o
arquivo verde. A fixture `user_id` limpa o afiliado no teardown
(`affiliates.user_id ... on delete cascade`).

Os dois casos de `secure` andam em par de propósito: `shared.DASHBOARD_URL`
depende do ambiente, então só um deles é o que fica vermelho sem o conserto
— mas um dos dois fica, em qualquer ambiente.
"""
import pytest
from fastapi.testclient import TestClient

from db.affiliates import (
    REF_COOKIE_MAX_AGE_DAYS,
    create_affiliate,
    set_affiliate_status,
)
import frontend.finance_bot_websocket_custom as dashboard


def _ref_cookie(resp):
    """Único set-cookie `ref_code=` da resposta, ou None."""
    return next(
        (h for h in resp.headers.get_list("set-cookie") if h.startswith("ref_code=")),
        None,
    )


def _attrs(cookie):
    """Só os atributos do set-cookie. O valor é um code sorteado de um alfabeto
    que contém S,E,C,U,R,E (`_CODE_ALPHABET`), então procurar "secure" no
    header inteiro daria vermelho por sorteio."""
    return cookie.partition(";")[2].lower()


@pytest.fixture
def affiliate_code(user_id):
    return create_affiliate(user_id)["code"]


def test_link_valido_seta_cookie(affiliate_code):
    """Pede em MINÚSCULO de propósito: o cookie tem de trazer o `code` canônico
    do banco (maiúsculo), não a entrada crua da URL."""
    client = TestClient(dashboard.app)
    resp = client.get(f"/r/{affiliate_code.lower()}", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"
    cookie = _ref_cookie(resp)
    assert cookie is not None
    assert affiliate_code in cookie
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    assert f"max-age={REF_COOKIE_MAX_AGE_DAYS * 24 * 3600}" in cookie.lower()


def test_link_honra_cookie_secure_do_app(monkeypatch, affiliate_code):
    """COOKIE_SECURE do app inclui a blindagem APP_ENV=prod."""
    monkeypatch.setattr(dashboard, "COOKIE_SECURE", True)
    client = TestClient(dashboard.app)
    resp = client.get(f"/r/{affiliate_code}", follow_redirects=False)
    cookie = _ref_cookie(resp)
    assert cookie is not None
    assert "secure" in _attrs(cookie)


def test_link_ignora_secure_quando_app_nao_exige(monkeypatch, affiliate_code):
    """Par do teste acima: o cookie SEGUE a constante do app nas duas direções."""
    monkeypatch.setattr(dashboard, "COOKIE_SECURE", False)
    client = TestClient(dashboard.app)
    resp = client.get(f"/r/{affiliate_code}", follow_redirects=False)
    cookie = _ref_cookie(resp)
    assert cookie is not None
    assert "secure" not in _attrs(cookie)


def test_codigo_inexistente_nao_seta_cookie():
    client = TestClient(dashboard.app)
    resp = client.get("/r/naoexiste", follow_redirects=False)
    assert resp.status_code == 302
    assert _ref_cookie(resp) is None


def test_afiliado_desativado_nao_seta_cookie(user_id):
    """O código EXISTE e mesmo assim não atribui: é o caso que o `status ==
    "active"` do handler decide sozinho (com `if affiliate:` fica vermelho)."""
    aff = create_affiliate(user_id)
    assert set_affiliate_status(aff["id"], "disabled")
    client = TestClient(dashboard.app)
    resp = client.get(f"/r/{aff['code']}", follow_redirects=False)
    assert resp.status_code == 302
    assert _ref_cookie(resp) is None


def test_codigo_hostil_nao_seta_cookie(affiliate_code):
    """Espelho de test_prospect_referrals.py::test_link_invalido_nao_seta_cookie.
    Aqui a superfície é maior: o `/i/{code}` só passa por regex, e no `/r/{code}`
    6 dos 7 payloads chegam ao handler e viram query (`get_affiliate_by_code`) —
    o `..%2F..%2Fetc%2Fpasswd` toma 404 no roteamento (o cliente normaliza o
    path) e prova só que a rota não casa.

    Os dois primeiros são os que medem injeção, e é por eles que o teste pede
    `affiliate_code`: com a query montada por f-string, a aspa solta vira 500 e
    o `or '1'='1'` casa a tabela inteira, devolvendo um afiliado ATIVO que nada
    tem a ver com o code pedido (302 com cookie alheio). SEM a fixture não há
    linha garantida — a tautologia poderia varrer uma tabela vazia e sair verde
    com o buraco aberto, ou depender de linha deixada por outro teste. O code
    hostil não casa nenhum afiliado no código são, então o cookie continua None.
    Um `'--` sozinho não serve: ele comenta o resto, o SQL fica válido e sem
    linhas, indistinguível do comportamento correto."""
    client = TestClient(dashboard.app)
    hostis = (
        "abc%27",                  # aspa solta -> quebra a query se concatenada
        "x' or '1'='1'--",         # tautologia -> vazaria afiliado alheio
        "abc%0Ainjected:%201",     # CRLF -> header splitting
        "..%2F..%2Fetc%2Fpasswd",  # travessia de caminho
        "ç" * 30,                  # não-ASCII
        "a" * 5000,                # muito longo
        "a b c",                   # espaços
    )
    for bad in hostis:
        resp = client.get(f"/r/{bad}", follow_redirects=False)
        assert resp.status_code < 500, f"5xx para {bad!r}: {resp.status_code}"
        assert _ref_cookie(resp) is None, f"cookie setado para {bad!r}"
