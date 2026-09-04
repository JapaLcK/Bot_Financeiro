"""Meta CAPI: o clique no anúncio tem de chegar junto com a compra.

O evento server-side já ia com o e-mail (com hash), e só. Faltavam os dois
cookies que o próprio pixel cria e que o Meta usa pra casar a conversão:

  _fbp — o navegador;
  _fbc — o CLIQUE no anúncio (o pixel deriva do `fbclid` da URL de entrada).

Sem `_fbc`, a compra chega ao Meta sem a campanha que a gerou — que é o motivo
de existir a CAPI. Os dois viajam pelo mesmo caminho do `ga_client_id`: cookie no
POST do checkout → metadata do Stripe → webhook.

Como no arquivo do GA4, os testes leem o CORPO que iria pro Graph, não se uma
função foi chamada.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.services import meta_capi
from tests.test_billing_webhook_lifecycle import (
    _cleanup_trial,
    _fake_sub,
    _post,
    _setup,
)

_FBP = "fb.1.1596403881668.1116446470"
_FBC = "fb.1.1554763741205.IwAR2Ta-abcDEF_ghi123-XYZ"


class _Captura:
    def __init__(self):
        self.chamadas: list[dict] = []

    def post(self, url, params=None, json=None, timeout=None):
        self.chamadas.append({"url": url, "params": params or {}, "json": json or {}})
        return SimpleNamespace(status_code=200, text="{}")

    def unico(self) -> dict:
        assert len(self.chamadas) == 1, f"esperava 1 envio, veio {len(self.chamadas)}"
        return self.chamadas[0]["json"]


def _ligar_capi(monkeypatch, *, test_event_code: str | None = None) -> _Captura:
    monkeypatch.setenv("META_PIXEL_ID", "111111111111111")
    monkeypatch.setenv("META_PIXEL_ACCESS_TOKEN", "token-de-teste")
    if test_event_code:
        monkeypatch.setenv("META_TEST_EVENT_CODE", test_event_code)
    else:
        monkeypatch.delenv("META_TEST_EVENT_CODE", raising=False)
    captura = _Captura()
    monkeypatch.setattr(meta_capi, "requests", captura)
    return captura


def _sub_pago(metadata=None) -> dict:
    sub = _fake_sub("active", "price_capi")
    sub["items"]["data"][0]["price"].update({"unit_amount": 1990, "currency": "brl"})
    sub["metadata"] = metadata or {}
    return sub


def _evento_checkout(uid: int, metadata=None) -> dict:
    return {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_capi_1",
            "amount_total": 1990,
            "currency": "brl",
            "metadata": {"finbot_user_id": str(uid), **(metadata or {})},
            "subscription": "sub_capi",
        }},
    }


# ── o caminho completo: cookie → metadata → webhook → Graph ──────────────────

def test_compra_leva_os_cookies_do_pixel_pro_meta(user_id, monkeypatch):
    uid, client, fake = _setup(monkeypatch, f"capi-ok-{user_id}")
    captura = _ligar_capi(monkeypatch)
    try:
        meta = {"fbp": _FBP, "fbc": _FBC}
        r = _post(client, fake, _evento_checkout(uid, meta),
                  subs={"sub_capi": _sub_pago(meta)})
        assert r.status_code == 200, r.text

        corpo = captura.unico()
        user_data = corpo["data"][0]["user_data"]
        # Crus, e não com hash: o Meta ignora esses dois se vierem hasheados —
        # o evento seria aceito (2xx) e a atribuição continuaria perdida.
        assert user_data["fbp"] == _FBP
        assert user_data["fbc"] == _FBC
        # O e-mail continua indo com hash (64 hex do sha256), como antes.
        assert len(user_data["em"][0]) == 64
        assert "@" not in user_data["em"][0]
    finally:
        _cleanup_trial(uid)


def test_sem_cookies_o_evento_continua_indo(user_id, monkeypatch):
    """Compra orgânica, cookie bloqueado, assinatura criada antes desta versão:
    o evento não pode deixar de ser enviado por falta dos cookies — só perde
    qualidade de correspondência."""
    uid, client, fake = _setup(monkeypatch, f"capi-sem-{user_id}")
    captura = _ligar_capi(monkeypatch)
    try:
        _post(client, fake, _evento_checkout(uid), subs={"sub_capi": _sub_pago()})

        user_data = captura.unico()["data"][0]["user_data"]
        assert "fbp" not in user_data and "fbc" not in user_data
        assert user_data["em"]                      # o e-mail sozinho ainda vale
    finally:
        _cleanup_trial(uid)


def test_test_event_code_desvia_o_evento_pra_aba_de_teste(user_id, monkeypatch):
    """A aba "Testar eventos" do Events Manager não enxerga a CAPI sozinha. Com
    META_TEST_EVENT_CODE setado dá pra validar o server-side sem esperar uma
    venda real — e sem ele o campo NÃO pode ir, senão a venda de verdade viraria
    evento de teste e não contaria como conversão."""
    uid, client, fake = _setup(monkeypatch, f"capi-tst-{user_id}")
    captura = _ligar_capi(monkeypatch, test_event_code="TEST12345")
    try:
        _post(client, fake, _evento_checkout(uid), subs={"sub_capi": _sub_pago()})
        assert captura.unico()["test_event_code"] == "TEST12345"
    finally:
        _cleanup_trial(uid)

    uid2, client2, fake2 = _setup(monkeypatch, f"capi-prod-{user_id}")
    captura2 = _ligar_capi(monkeypatch)          # sem a env
    try:
        _post(client2, fake2, _evento_checkout(uid2), subs={"sub_capi": _sub_pago()})
        assert "test_event_code" not in captura2.unico()
    finally:
        _cleanup_trial(uid2)


# ── a ponta de entrada: cookie do navegador → metadata do Stripe ─────────────

def test_cookies_do_pixel_viram_metadata_do_checkout(user_id, monkeypatch):
    from tests.test_billing_checkout import (
        _CSRF_HEADERS,
        _auth_user_setup,
        _patch_stripe,
    )
    import frontend.finance_bot_websocket_custom as dashboard

    _, _, client = _auth_user_setup(f"capi-cookie-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_m")
    fake = _patch_stripe(monkeypatch)
    client.cookies.set("_fbp", _FBP)
    client.cookies.set("_fbc", _FBC)

    r = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert r.status_code == 200, r.text

    kwargs = fake.last_session_kwargs
    assert kwargs["metadata"]["fbp"] == _FBP
    assert kwargs["metadata"]["fbc"] == _FBC
    # A RENOVAÇÃO só enxerga o metadata da assinatura — sem a cópia, toda
    # cobrança pós-trial chegaria ao Meta sem a campanha.
    assert kwargs["subscription_data"]["metadata"]["fbc"] == _FBC


def test_cookie_forjado_nao_vira_metadata(user_id, monkeypatch):
    """Fronteira de confiança: o valor vem do cliente e ia parar no metadata do
    Stripe e no corpo do Graph. Fora do formato do Meta, é descartado."""
    from tests.test_billing_checkout import (
        _CSRF_HEADERS,
        _auth_user_setup,
        _patch_stripe,
    )
    import frontend.finance_bot_websocket_custom as dashboard

    _, _, client = _auth_user_setup(f"capi-forja-{user_id}")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_m")
    fake = _patch_stripe(monkeypatch)
    client.cookies.set("_fbp", "sou-um-cookie-inventado")

    r = client.post("/billing/create-checkout", headers=_CSRF_HEADERS)
    assert r.status_code == 200, r.text
    assert "fbp" not in fake.last_session_kwargs["metadata"]


def test_webhook_diz_o_que_decidiu_sobre_rastreio(user_id, monkeypatch, capsys):
    """Observabilidade, e não é firula: numa investigação real o log ficou mudo e
    "não chegou no Meta" era indistinguível de "nem tentou" — as duas davam
    silêncio. A linha tem de sair SEMPRE, com as flags de configuração, mesmo
    quando o envio é pulado."""
    uid, client, fake = _setup(monkeypatch, f"capi-log-{user_id}")
    _ligar_capi(monkeypatch)
    try:
        _post(client, fake, _evento_checkout(uid), subs={"sub_capi": _sub_pago()})
        saida = capsys.readouterr().out
        assert "rastreio checkout" in saida
        assert f"user={uid}" in saida
        assert "capi=True" in saida          # a flag, não só o rótulo
    finally:
        _cleanup_trial(uid)


def test_log_de_rastreio_sai_ate_com_a_capi_desligada(user_id, monkeypatch, capsys):
    """O caso que importa: desconfigurado, nada é enviado — e é justamente aí que
    o log precisa existir, senão o silêncio volta a não significar nada."""
    uid, client, fake = _setup(monkeypatch, f"capi-log-off-{user_id}")
    monkeypatch.delenv("META_PIXEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GA4_API_SECRET", raising=False)
    try:
        _post(client, fake, _evento_checkout(uid), subs={"sub_capi": _sub_pago()})
        saida = capsys.readouterr().out
        assert "rastreio checkout" in saida
        assert "capi=False" in saida
        assert "ga4=False" in saida
    finally:
        _cleanup_trial(uid)


def test_formato_do_cookie_e_validado():
    assert meta_capi.sanitize_fb_cookie(_FBP) == _FBP
    assert meta_capi.sanitize_fb_cookie(f"  {_FBC}  ") == _FBC

    for lixo in ["", "fb.1", "fb.1.abc.xyz", "xx.1.1596403881668.111",
                 "fb.1.1596403881668." + "x" * 500, None, 42, {"a": 1}]:
        assert meta_capi.sanitize_fb_cookie(lixo) is None, lixo
