"""Funil v3, PR 4 de 4 — a oferta de N produtos extras na /assinar e na /precos.

Pela rota `POST /billing/create-checkout` (fake do Stripe de
`tests/test_billing_checkout.py`); o P1 segue pelo `POST /billing/webhook`.

U   `para_metadata` → `da_metadata` ida e volta; 1 item = as chaves de hoje.
S2  10 slots + rastreio: `optional_items` em ordem, 29 chaves ≤ 50, valor ≤ 500,
    igual nos dois metadatas (sessão e assinatura), nas duas origens.
S3  slot 3 com URL de 501 caracteres sai sozinho; o aviso não leva a URL.
S4  preço repetido entra uma vez.
S5  buraco (slots 1 e 3): a ordem se mantém, a foto numera por posição.
S6  o hospedado da /assinar leva os N extras.
P1  /precos ponta a ponta, com e sem trial: checkout → webhook → N pendências →
    1ª fatura = só o plano (0 no trial: sem e-mail nem comissão).
A1  `da_env` tira espaço e quebra de linha coladas no Railway.
R1  Stripe recusa o extra (preço arquivado/inexistente): refaz UMA vez sem os
    extras e sem a foto `ebook*` nos dois metadatas, com log `ebook_oferta_recusada`;
    sem extras, recusa repetida ou erro que não é InvalidRequestError = 502 de hoje.
S1 e P0 moram em `tests/test_billing_checkout.py` (a tabela de 5 casos e a
`test_precos_manda_o_mesmo_checkout_de_antes`).

Controles (rodados, ver o relato do PR):
  · sem a checagem de 500 por slot          → S3 vermelho;
  · `optional_items` de volta sob `origem == "assinar"` → P0[com-produtos] vermelho;
  · sem o `.strip()`                        → A1 vermelho;
  · sem o refazer                           → R1 vermelho;
  · refazendo com a foto `ebook*`           → R1 vermelho;
  · sem o `_is_missing_stripe_customer` no guarda do refazer → R1 cliente apagado vermelho.
  Positivos: S1 (só o slot 1 = igual a antes) e P0[sem-produtos].
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import frontend.finance_bot_websocket_custom as dashboard
from core.services.extras_assinar import da_env, da_metadata, para_metadata
from test_billing_checkout import (  # noqa: F401 — `_reset_rate_limiter` é fixture autouse
    _ASSINAR_E, _ASSINAR_H, _PRECOS, _CSRF_HEADERS, _CSRF_TOKEN, _FakeInvalidRequestError,
    _FakeStripeError, _assinar_pronto, _auth_user_setup, _extras_env, _post, _reset_rate_limiter,
)
from test_billing_webhook_lifecycle import _T_LIFE, _fake_sub
from test_billing_webhook_lifecycle import _post as _webhook
from test_ebook_webhook import (  # noqa: F401 — `indicado` e `_event_logs` são fixtures
    _SUB, _cobrado, _comissoes, _event_logs, _fatura, _linha, _linhas, _sql, indicado,
)


def _url(n, tamanho=40):
    base = f"https://pdf.test/{n}/"
    return base + "x" * (tamanho - len(base))


def _slots(*ns, tamanho=40):
    return {n: (f"price_x{n}", _url(n, tamanho)) for n in ns}


def _metas(fake):
    kw = fake.last_session_kwargs
    return kw["metadata"], kw["subscription_data"]["metadata"]


def _opcionais(fake):
    return [i["price"] for i in fake.last_session_kwargs.get("optional_items", [])]


# ── U. a foto ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [0, 1, 3, 10])
def test_u_para_metadata_e_da_metadata_ida_e_volta(n):
    itens = [(f"price_{i}", f"https://u.test/{i}") for i in range(1, n + 1)]
    meta = para_metadata(itens)
    assert da_metadata(meta) == itens
    assert len(meta) == 2 * n
    if n == 1:
        assert meta == {"ebook_price": "price_1", "ebook_url": "https://u.test/1"}


# ── S. a sessão ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("corpo", [_ASSINAR_E, _PRECOS], ids=["assinar", "precos"])
def test_s2_dez_slots_cabem_no_metadata_do_stripe(request, user_id, monkeypatch, corpo):
    uid, _, client = _auth_user_setup(f"s2-{request.node.callspec.id}-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, _slots(*range(1, 11), tamanho=500))
    client.cookies.set("_ga", "GA1.1.1234567890.1712345678")
    client.cookies.set("_fbp", "fb.1.1596403881668.1116446470")
    client.cookies.set("_fbc", "fb.1.1554763741205.IwAR2Ta-abcDEF_ghi123-XYZ")

    assert _post(client, corpo).status_code == 200
    assert _opcionais(fake) == [f"price_x{n}" for n in range(1, 11)]
    sessao, assinatura = _metas(fake)
    assert sessao == assinatura
    assert len(sessao) == 6 + 3 + 20 <= 50
    assert all(len(k) <= 40 and len(v) <= 500 for k, v in sessao.items())
    assert da_metadata(sessao) == [(f"price_x{n}", _url(n, 500)) for n in range(1, 11)]


def test_s3_url_de_501_tira_so_aquele_slot_e_nao_vaza_no_log(user_id, monkeypatch, caplog):
    _, _, client = _auth_user_setup(f"s3-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    slots = _slots(1, 2, 4, tamanho=500)
    slots[3] = ("price_x3", _url(3, 501))
    _extras_env(monkeypatch, slots)

    with caplog.at_level("WARNING"):
        assert _post(client, _ASSINAR_E).status_code == 200
    assert _opcionais(fake) == ["price_x1", "price_x2", "price_x4"]
    assert da_metadata(_metas(fake)[0]) == [(f"price_x{n}", _url(n, 500)) for n in (1, 2, 4)]
    avisos = [r.getMessage() for r in caplog.records if "ebook_nao_oferecido" in r.getMessage()]
    assert len(avisos) == 1 and "slot 3" in avisos[0] and "501" in avisos[0]
    assert not any("pdf.test" in r.getMessage() for r in caplog.records)


def test_s4_preco_repetido_entra_uma_vez(user_id, monkeypatch, caplog):
    _, _, client = _auth_user_setup(f"s4-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, {1: ("price_x1", _url(1)), 2: ("price_x1", _url(2)),
                              3: ("price_x3", _url(3))})

    with caplog.at_level("WARNING"):
        assert _post(client, _ASSINAR_E).status_code == 200
    assert _opcionais(fake) == ["price_x1", "price_x3"]
    assert da_metadata(_metas(fake)[0]) == [("price_x1", _url(1)), ("price_x3", _url(3))]
    assert any("slot 2" in r.getMessage() for r in caplog.records
               if "ebook_nao_oferecido" in r.getMessage())


def test_s5_buraco_mantem_a_ordem_e_a_foto_numera_por_posicao(user_id, monkeypatch):
    _, _, client = _auth_user_setup(f"s5-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, _slots(1, 3))

    assert _post(client, _ASSINAR_E).status_code == 200
    assert _opcionais(fake) == ["price_x1", "price_x3"]
    sessao, _ = _metas(fake)
    assert (sessao["ebook_price"], sessao["ebook_2_price"]) == ("price_x1", "price_x3")
    assert "ebook_3_price" not in sessao


def test_s6_hospedado_da_assinar_leva_os_extras(user_id, monkeypatch):
    _, _, client = _auth_user_setup(f"s6-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, _slots(1, 2))

    resp = _post(client, _ASSINAR_H)
    assert resp.status_code == 200 and "checkout_url" in resp.json()
    assert "ui_mode" not in fake.last_session_kwargs
    assert _opcionais(fake) == ["price_x1", "price_x2"]
    assert da_metadata(_metas(fake)[1]) == [("price_x1", _url(1)), ("price_x2", _url(2))]


# ── P1. a /precos ponta a ponta ──────────────────────────────────────────────

@pytest.mark.parametrize("trial", [False, True], ids=["sem-trial", "com-trial"])
def test_p1_precos_com_dois_extras_da_compra_a_fatura(user_id, indicado, monkeypatch, trial):
    """A sessão da /precos nasce com os 2 extras, o webhook grava as 2
    pendências com a foto e a 1ª fatura cobra/comissiona só o plano: sem trial,
    o plano cheio; com trial, o plano sai 0 e não há e-mail nem comissão."""
    uid, _, fake_webhook, vistos, _ = indicado
    fake = _assinar_pronto(monkeypatch, elegivel=trial)
    _extras_env(monkeypatch, _slots(1, 2))
    from fastapi.testclient import TestClient
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, f"wh-eb-c-{user_id}@t.com"))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_TOKEN)

    resp = client.post("/billing/create-checkout", json=_PRECOS, headers=_CSRF_HEADERS)
    assert resp.status_code == 200, resp.text
    sessao, assinatura = _metas(fake)
    assert sessao["origem"] == "precos" and sessao["td"] == ("15" if trial else "0")
    assert fake.last_session_kwargs["subscription_data"].get("trial_period_days") == (15 if trial else None)

    plano = 0 if trial else 1990
    monkeypatch.setitem(sys.modules, "stripe", fake_webhook)
    sub = _fake_sub("trialing" if trial else "active", "price_plano")
    sub["items"]["data"][0]["price"].update({"unit_amount": 1990, "currency": "brl"})
    sub["metadata"] = assinatura
    evento = {"type": "checkout.session.completed", "id": "evt_p1", "created": _T_LIFE,
              "data": {"object": {"id": "cs_p1", "subscription": _SUB, "ui_mode": "hosted_page",
                                  "metadata": sessao, "amount_total": plano + 990 + 1490,
                                  "currency": "brl"}}}
    r = _webhook(client, fake_webhook, evento, subs={_SUB: sub})
    assert r.status_code == 200, r.text
    assert sorted((l["session_id"], l["ebook_price"], l["ebook_url"]) for l in _linhas(uid)) == [
        ("cs_p1", "price_x1", _url(1)), ("cs_p1", "price_x2", _url(2))]

    r = _webhook(client, fake_webhook, _fatura(
        "invoice.paid", uid, "in_p1", plano + 990 + 1490, "subscription_create",
        [_linha("price_x1", 990), _linha("price_x2", 1490), _linha("price_plano", plano)]))
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid)) == (([], []) if trial else ([19.9], [1990]))


# ── A1. espaço colado no Railway ─────────────────────────────────────────────

@pytest.mark.parametrize("preco,url,esperado", [
    (" price_a", "https://u.test/a", [("price_a", "https://u.test/a")]),
    ("price_a\n", " https://u.test/a\n", [("price_a", "https://u.test/a")]),
    ("  ", "https://u.test/a", []),
    ("price_a", "  ", []),
], ids=["espaco-antes", "quebra-de-linha", "preco-em-branco", "url-em-branco"])
def test_a1_da_env_tira_espaco_do_railway(monkeypatch, preco, url, esperado):
    _extras_env(monkeypatch, {1: (preco, url)})
    assert da_env() == esperado


# ── R1. extra recusado pelo Stripe não derruba a venda do plano ──────────────

class _FakeConexao(_FakeStripeError):
    pass


def _create_que_recusa(fake, *, sempre=False, erro=None, so_cliente_apagado=False) -> list[dict]:
    """Troca o `Session.create` do fake: recusa `optional_items` (ou tudo) como o
    Stripe recusa preço arquivado, ou só diz "cliente apagado" na 1ª chamada.
    Devolve os kwargs de cada chamada."""
    original = fake.checkout.Session
    chamadas: list[dict] = []

    def create(**kw):
        chamadas.append(kw)
        if erro is not None:
            raise erro
        if so_cliente_apagado:
            if len(chamadas) == 1:
                raise _FakeInvalidRequestError(
                    "No such customer", param="customer", code="resource_missing")
            return original.create(**kw)
        if sempre or "optional_items" in kw:
            raise _FakeInvalidRequestError(
                "You cannot specify an optional item with inactive price",
                param="optional_items[0][price]")
        return original.create(**kw)
    fake.checkout = SimpleNamespace(Session=SimpleNamespace(
        create=create, list=original.list, expire=original.expire))
    return chamadas


def _recusas(uid):
    return _sql("select details from system_event_logs"
                " where event_type = 'ebook_oferta_recusada' and user_id = %s", (uid,))


@pytest.mark.parametrize("corpo", [_ASSINAR_E, _PRECOS], ids=["assinar", "precos"])
def test_r1_extra_recusado_refaz_sem_extras_e_sem_a_foto(request, user_id, monkeypatch, corpo):
    uid, _, client = _auth_user_setup(f"r1-{request.node.callspec.id}-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, _slots(1, 2))
    chamadas = _create_que_recusa(fake)

    assert _post(client, corpo).status_code == 200
    assert len(chamadas) == 2 and "optional_items" in chamadas[0]
    kw = fake.last_session_kwargs
    assert "optional_items" not in kw
    for meta in _metas(fake):
        assert not [k for k in meta if k.startswith("ebook")]
        assert meta["origem"] == corpo.get("origem", "precos")
    (log,) = _recusas(uid)
    assert log["details"]["precos"] == ["price_x1", "price_x2"]
    assert "inactive price" in log["details"]["stripe"]
    assert "pdf.test" not in str(log["details"])


@pytest.mark.parametrize("slots,chamadas_esperadas", [({}, 1), ({1: ("price_x1", _url(1))}, 2)],
                         ids=["sem-extras", "com-extras"])
def test_r1_recusa_que_nao_e_do_extra_segue_502(user_id, monkeypatch, slots, chamadas_esperadas):
    """POSITIVO do fallback: sem extras, ou recusado também sem eles, é o 502 de hoje."""
    uid, _, client = _auth_user_setup(f"r1-502-{len(slots)}-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, slots)
    chamadas = _create_que_recusa(fake, sempre=True)

    assert _post(client, _ASSINAR_E).status_code == 502
    assert len(chamadas) == chamadas_esperadas
    assert len(_recusas(uid)) == chamadas_esperadas - 1


def test_r1_erro_de_conexao_nao_refaz(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"r1-conn-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, _slots(1, 2))
    chamadas = _create_que_recusa(fake, erro=_FakeConexao("conexão caiu"))

    assert _post(client, _ASSINAR_E).status_code == 502
    assert len(chamadas) == 1 and _recusas(uid) == []


def test_r1_cliente_apagado_no_create_nao_e_recusa_do_extra(user_id, monkeypatch):
    """POSITIVO do guarda: "cliente apagado" no `create` sobe para o retry de
    fora (cliente novo) e a sessão final mantém os extras e a foto, sem log."""
    uid, _, client = _auth_user_setup(f"r1-cus-{user_id}")
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, _slots(1, 2))
    chamadas = _create_que_recusa(fake, so_cliente_apagado=True)

    assert _post(client, _ASSINAR_E).status_code == 200
    assert len(chamadas) == 2
    assert _opcionais(fake) == ["price_x1", "price_x2"]
    for meta in _metas(fake):
        assert da_metadata(meta) == [("price_x1", _url(1)), ("price_x2", _url(2))]
    assert _recusas(uid) == []
