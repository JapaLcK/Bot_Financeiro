"""Funil v3, PR 3 — o webhook do Stripe e o e-book da /assinar.

Tudo pela rota `POST /billing/webhook`, com banco real e o `_FakeStripe` de
`tests/test_billing_webhook_lifecycle.py`.

B. `checkout.session.completed` grava a pendência (`ebook_entregas`) com a FOTO
   da metadata, ANTES dos outros efeitos: falha → 5xx e nada depois rodou.
C. `invoice.paid`/`invoice.payment_succeeded`: o e-book sai do valor que vira
   e-mail de cobrança, comissão e rastreio (`amount_cents` = só o plano).

Controles (rodados, não previstos — ver o relato do PR):
  · try/except em volta do `registrar`           → B5 vermelho;
  · `registrar` depois do `_fire_email`          → B5 vermelho;
  · sem o `on conflict` no `registrar`           → B1 vermelho;
  · sem a subtração do e-book                    → C1 vermelho;
  · subtraindo o `amount` bruto                  → C4 vermelho;
  · comparando `price.id` no nível da linha      → C3 vermelho.
  Positivos: B4 (a /precos segue 200 e sem linha) e C6 (sem e-book, igual a hoje).
"""
from __future__ import annotations

import pytest

import db
import frontend.finance_bot_websocket_custom as dashboard
from _billing_grants_helpers import garantir_system_event_logs
from core.services import admin_notify
from core.services import email_service as es
from db.affiliates import create_affiliate, record_referral
from db.connection import get_conn
from test_billing_webhook_lifecycle import _T_LIFE, _cleanup_trial, _fake_sub, _post, _setup

_PRECO = "price_ebook_foto"
_URL = "https://drive.test/uc?id=abc&export=download"
_SUB = "sub_eb"


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        return cur.fetchall() if cur.description else None


def _linhas(uid):
    return _sql("select session_id, ebook_price, ebook_url, fechada_em from ebook_entregas"
                " where user_id = %s", (uid,))


def _espioes(monkeypatch) -> dict[str, list]:
    vistos: dict[str, list] = {"send_pro_welcome_email": [], "send_pro_charged_email": [],
                               "notify_new_pro": [], "record_checkout_completed": []}

    def _grava(nome, ret=True):
        def _fn(*a, **kw):
            vistos[nome].append(a)
            return ret
        _fn.__name__ = nome   # chave da dedupe do `_fire_email`
        return _fn

    monkeypatch.setattr(es, "send_pro_welcome_email", _grava("send_pro_welcome_email"))
    monkeypatch.setattr(es, "send_pro_charged_email", _grava("send_pro_charged_email"))
    monkeypatch.setattr(es, "send_founder_email_once", lambda *a, **k: True)
    monkeypatch.setattr(admin_notify, "notify_new_pro", _grava("notify_new_pro", None))
    original = db.record_checkout_completed

    def _rcc(*a):
        vistos["record_checkout_completed"].append(a)
        return original(*a)
    monkeypatch.setattr(db, "record_checkout_completed", _rcc)
    return vistos


def _checkout(uid, *, sid="cs_eb_1", evt="evt_eb_1", ebook=True, url=_URL, ui_mode="embedded_page"):
    meta = {"finbot_user_id": str(uid), "origem": "assinar" if ebook else "precos"}
    if ebook:
        meta["ebook_price"] = _PRECO
        if url:
            meta["ebook_url"] = url
    return {"type": "checkout.session.completed", "id": evt, "created": _T_LIFE,
            "data": {"object": {"id": sid, "metadata": meta, "subscription": _SUB,
                                "ui_mode": ui_mode, "amount_total": 990 if ebook else 0,
                                "currency": "brl"}}}


# ── B. a pendência no checkout ────────────────────────────────────────────────

@pytest.mark.parametrize("ui_mode", ["embedded_page", "hosted_page"])
def test_b1_b3_checkout_grava_uma_pendencia_mesmo_reentregue(user_id, monkeypatch, ui_mode):
    uid, client, fake = _setup(monkeypatch, f"eb-b1-{ui_mode[:3]}-{user_id}")
    vistos = _espioes(monkeypatch)
    try:
        for _ in range(2):
            r = _post(client, fake, _checkout(uid, ui_mode=ui_mode), subs={_SUB: _fake_sub("trialing")})
            assert r.status_code == 200, r.text
        assert _linhas(uid) == [{"session_id": "cs_eb_1", "ebook_price": _PRECO,
                                 "ebook_url": _URL, "fechada_em": None}]
        assert len(vistos["send_pro_welcome_email"]) == 1
    finally:
        _cleanup_trial(uid)


def test_b2_a_linha_guarda_a_foto_e_nao_a_env_do_momento(user_id, monkeypatch):
    uid, client, fake = _setup(monkeypatch, f"eb-b2-{user_id}")
    _espioes(monkeypatch)
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_EBOOK", "price_novo")
    monkeypatch.setattr(dashboard, "EBOOK_URL", "https://outra.test/novo.pdf")
    try:
        assert _post(client, fake, _checkout(uid), subs={_SUB: _fake_sub("trialing")}).status_code == 200
        (linha,) = _linhas(uid)
        assert (linha["ebook_price"], linha["ebook_url"]) == (_PRECO, _URL)
    finally:
        _cleanup_trial(uid)


def test_b4_sessao_da_precos_nao_grava_nada(user_id, monkeypatch):
    """POSITIVO: checkout comum segue 200 e sem linha."""
    uid, client, fake = _setup(monkeypatch, f"eb-b4-{user_id}")
    vistos = _espioes(monkeypatch)
    try:
        assert _post(client, fake, _checkout(uid, ebook=False), subs={_SUB: _fake_sub("trialing")}).status_code == 200
        assert _linhas(uid) == []
        assert len(vistos["send_pro_welcome_email"]) == 1
    finally:
        _cleanup_trial(uid)


def test_b5_falha_na_pendencia_vira_5xx_antes_dos_outros_efeitos(user_id, monkeypatch):
    uid, client, fake = _setup(monkeypatch, f"eb-b5-{user_id}")
    vistos = _espioes(monkeypatch)
    import db.ebook_entregas as ee
    original = ee.registrar

    def _explode(*a):
        raise RuntimeError("postgres indisponivel")
    monkeypatch.setattr(ee, "registrar", _explode)
    try:
        r = _post(client, fake, _checkout(uid), subs={_SUB: _fake_sub("trialing")})
        assert r.status_code >= 500, "a Stripe precisa do 5xx para reentregar"
        assert vistos["record_checkout_completed"] == []
        assert vistos["send_pro_welcome_email"] == []
        assert vistos["notify_new_pro"] == []

        monkeypatch.setattr(ee, "registrar", original)
        assert _post(client, fake, _checkout(uid)).status_code == 200
        assert len(_linhas(uid)) == 1
        assert len(vistos["send_pro_welcome_email"]) == 1
    finally:
        _cleanup_trial(uid)


def test_b6_sem_a_foto_da_url_grava_a_prova_e_loga_uma_vez(user_id, monkeypatch):
    uid, client, fake = _setup(monkeypatch, f"eb-b6-{user_id}")
    _espioes(monkeypatch)
    try:
        assert _post(client, fake, _checkout(uid, url=None), subs={_SUB: _fake_sub("trialing")}).status_code == 200
        (linha,) = _linhas(uid)
        assert linha["ebook_url"] is None
        (n,) = _sql("select count(*) as n from system_event_logs"
                    " where event_type = 'ebook_sem_url' and user_id = %s", (uid,))
        assert n["n"] == 1
    finally:
        _cleanup_trial(uid)


# ── C. a fatura: o e-book sai do valor do plano ───────────────────────────────

def _linha(price, amount, *descontos):
    return {"amount": amount, "pricing": {"price_details": {"price": price}},
            "discount_amounts": [{"amount": d} for d in descontos]}


def _fatura(tipo, uid, inv, amount_paid, reason, linhas, created=_T_LIFE):
    return {"type": tipo, "id": f"evt_{inv}", "created": created,
            "data": {"object": {"id": inv, "metadata": {"finbot_user_id": str(uid)},
                                "subscription": _SUB, "amount_paid": amount_paid,
                                "currency": "brl", "billing_reason": reason,
                                "lines": {"data": linhas}}}}


def _sub(ebook_price=_PRECO):
    sub = _fake_sub("active", "price_plano")
    sub["metadata"] = {"ebook_price": ebook_price} if ebook_price else {}
    return sub


def _comissoes(uid):
    return [r["invoice_amount_cents"] for r in _sql(
        "select invoice_amount_cents from affiliate_commissions where referred_user_id = %s"
        " order by id", (uid,))]


@pytest.fixture
def indicado(user_id, monkeypatch):
    """(uid, client, fake, vistos, envios de rastreio) com afiliado ativo."""
    from types import SimpleNamespace
    from core.services import ga4_mp, meta_capi
    uid, client, fake = _setup(monkeypatch, f"eb-c-{user_id}")
    assert record_referral(create_affiliate(user_id)["code"], uid)
    envios: list = []
    capt = SimpleNamespace(post=lambda *a, **k: envios.append(k) or SimpleNamespace(status_code=200, text=""))
    for mod in (ga4_mp, meta_capi):
        monkeypatch.setattr(mod, "requests", capt)
    for k, v in {"GA4_MEASUREMENT_ID": "G-T", "GA4_API_SECRET": "s",
                 "META_PIXEL_ID": "1", "META_PIXEL_ACCESS_TOKEN": "t"}.items():
        monkeypatch.setenv(k, v)
    yield uid, client, fake, _espioes(monkeypatch), envios
    _cleanup_trial(uid)


_TIPOS = pytest.mark.parametrize("tipo", ["invoice.paid", "invoice.payment_succeeded"])


def _cobrado(vistos):
    return [a[2] for a in vistos["send_pro_charged_email"]]   # (email, plano, amount_brl, …)


@_TIPOS
def test_c1_c2_trial_com_ebook_so_a_fatura_do_plano_conta(indicado, tipo):
    uid, client, fake, vistos, envios = indicado
    r = _post(client, fake, _fatura(tipo, uid, "in_c1", 990, "subscription_create",
                                    [_linha(_PRECO, 990), _linha("price_plano", 0)]),
              subs={_SUB: _sub()})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid), envios) == ([], [], [])

    # a conversa: a fatura do fim do trial é a que comissiona (só a 1ª paga conta)
    r = _post(client, fake, _fatura(tipo, uid, "in_c2", 1990, "subscription_cycle",
                                    [_linha("price_plano", 1990)], created=_T_LIFE + 10))
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid)) == ([19.9], [1990])


@_TIPOS
@pytest.mark.parametrize("caso,amount_paid,linhas,esperado", [
    ("c3-sem-trial", 2980, [_linha(_PRECO, 990), _linha("price_plano", 1990)], 1990),
    ("c4-cupom-50", 1490, [_linha(_PRECO, 990, 495), _linha("price_plano", 1990, 995)], 995),
    ("c5-price-expandido", 2980, [_linha({"id": _PRECO}, 990), _linha({"id": "price_plano"}, 1990)], 1990),
])
def test_c3_c5_primeira_fatura_sem_trial_cobra_so_o_plano(indicado, tipo, caso, amount_paid, linhas, esperado):
    uid, client, fake, vistos, _ = indicado
    r = _post(client, fake, _fatura(tipo, uid, f"in_{caso}", amount_paid, "subscription_create", linhas),
              subs={_SUB: _sub()})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid)) == ([esperado / 100], [esperado])


@_TIPOS
def test_c6_sem_ebook_na_metadata_renovacao_igual_a_hoje(indicado, tipo):
    """POSITIVO: sem a foto, nada é subtraído (nem se a linha tiver o preço)."""
    uid, client, fake, vistos, _ = indicado
    r = _post(client, fake, _fatura(tipo, uid, "in_c6", 1990, "subscription_cycle",
                                    [_linha("price_plano", 1990)]), subs={_SUB: _sub(None)})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid)) == ([19.9], [1990])


@_TIPOS
def test_c7_o_ebook_e_o_da_metadata_e_nao_o_da_env(indicado, tipo, monkeypatch):
    uid, client, fake, vistos, _ = indicado
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_EBOOK", "price_outro")
    r = _post(client, fake, _fatura(tipo, uid, "in_c7", 2980, "subscription_create",
                                    [_linha(_PRECO, 990), _linha("price_plano", 1990)]),
              subs={_SUB: _sub()})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid)) == ([19.9], [1990])
