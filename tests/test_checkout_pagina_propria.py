"""Página própria, PR 1 — a sessão `ui_mode="elements"` nas duas origens.

Pela rota `POST /billing/create-checkout` (fake do Stripe de
`tests/test_billing_checkout.py`, com `Price.retrieve` e `list_line_items`).

F   foto de 3 nos DOIS metadatas, sem `optional_items`; resposta com as caixas.
C   filtra ANTES de recortar: o 1º inválido cede a vaga ao 4º.
I   flag off, ou sem `pagina`: kwargs e resposta IDÊNTICOS aos de antes (positivo).
M   reaproveitamento: elements × embedded_page nos dois sentidos; o mesmo modo
    reaproveita e marca `no_carrinho` pelo carrinho real.
E   `Price.retrieve` falha → plano sem caixas + log; InvalidRequestError → 502.

Controles (rodados, ver o relato do PR):
  · foto fora da `subscription_data`        → F vermelho;
  · sem a linha do `ui_mode` no matcher     → M vermelho;
  · sem `elementos or` no guarda do refazer → E 502 vermelho (vira 503 do KeyError);
  · `elementos = payload.pagina` (sem a flag) → I flag-off e M flag-off vermelhos;
  · `da_env()[:CAIXAS]` (recorta antes)     → C vermelho;
  · capa sem checar https                   → F vermelho;
  · adaptive off só na /assinar (regra antiga) → F[precos-*] vermelho;
  · `embutido = payload.embutido` (sem `or elementos`) → F[precos-sem-embutido] vermelho;
  · flag por `bool(getenv)` ou lista ampliada → I valores da flag vermelho;
  · sem exigir `unit_amount` int > 0        → C valor-livre/valor-zero vermelhos;
  · `valor_centavos` fixo em `_na_tela`     → F e M reaproveita vermelhos;
  · adaptive por `or embutido` (não `or elementos`) → I precos-embutido vermelho.
"""
from __future__ import annotations

import pytest

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.services.extras_assinar import da_metadata
from test_billing_checkout import (  # noqa: F401 — `_reset_rate_limiter` é fixture autouse
    _ASSINAR_E, _PRECOS, _FakeInvalidRequestError, _FakeStripeError, _assinar_pronto,
    _auth_user_setup, _extras_env, _post, _reset_rate_limiter,
)
from test_ebook_webhook import _event_logs, _sql  # noqa: F401 — `_event_logs` é fixture autouse

_PAG_A = {**_ASSINAR_E, "pagina": True}            # o que o assinar.js novo manda
_PAG_P = {**_PRECOS, "pagina": True}               # o que a precos.html nova manda
_PAG_PR = {**_PRECOS, "embutido": True, "pagina": True}   # a /assinar?origem=precos


def _url(n):
    return f"https://pdf.test/{n}"


_VALOR = {1: 1190, 2: 990, 3: 1490, 4: 1790}   # um por preço: pega valor trocado


def _preco(n, **troca):
    produto = {"active": True, "name": f"Caderno {n}", "description": f"Desc {n}",
               "images": [f"https://img.test/{n}.png"]}
    produto.update(troca.pop("produto", {}))
    return {"id": f"price_x{n}", "active": True, "currency": "brl", "type": "one_time",
            "unit_amount": _VALOR[n], "product": produto, **troca}


def _pronto(monkeypatch, n=3, ligada=True, precos=None, carrinho=None):
    """Stripe falso + N slots na env + catálogo do `Price.retrieve`."""
    fake = _assinar_pronto(monkeypatch)
    _extras_env(monkeypatch, {i: (f"price_x{i}", _url(i)) for i in range(1, n + 1)})
    monkeypatch.setenv("CHECKOUT_PAGINA_PROPRIA", "1" if ligada else "")
    catalogo = {f"price_x{i}": _preco(i) for i in range(1, n + 1)}
    catalogo.update(precos or {})
    fake.retrieves = []
    fake.carrinho = carrinho or {}

    class _Price:
        @staticmethod
        def retrieve(preco, expand=None):
            assert expand == ["product"]
            fake.retrieves.append(preco)
            item = catalogo[preco]
            if isinstance(item, Exception):
                raise item
            return item

    def list_line_items(sid, limit=None):
        assert limit == 100
        return {"data": [{"price": {"id": p}} for p in fake.carrinho[sid]]}

    fake.Price = _Price
    fake.checkout.Session.list_line_items = staticmethod(list_line_items)
    return fake


def _metas(fake):
    kw = fake.last_session_kwargs
    return kw["metadata"], kw["subscription_data"]["metadata"]


def _recusas(uid):
    return [r["details"] for r in _sql(
        "select details from system_event_logs"
        " where event_type = 'ebook_oferta_recusada' and user_id = %s", (uid,))]


# ── F. a foto de 3 e a resposta ─────────────────────────────────────────────

@pytest.mark.parametrize("trial", [True, False], ids=["com-trial", "sem-trial"])
@pytest.mark.parametrize("corpo", [_PAG_A, _PAG_PR, _PAG_P],
                         ids=["assinar", "precos", "precos-sem-embutido"])
def test_f_foto_de_3_nos_dois_metadatas_sem_optional_items(request, user_id, monkeypatch,
                                                           corpo, trial):
    uid, _, client = _auth_user_setup(f"pp-f-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, n=4, precos={"price_x2": _preco(2, produto={
        "images": ["http://img.test/2.png"]}), "price_x3": _preco(3, produto={
            "images": [], "description": None})})
    monkeypatch.setattr("db.plans.is_trial_eligible_for_user", lambda _uid: trial)

    resp = _post(client, corpo)
    assert resp.status_code == 200, resp.text
    kw = fake.last_session_kwargs
    assert kw["ui_mode"] == "elements" and "/home?" in kw["return_url"]
    assert "optional_items" not in kw and "success_url" not in kw and "cancel_url" not in kw
    # BRL fixo nas DUAS origens: sem o campo, a `elements` da /precos nasce com
    # adaptive LIGADO (medido no Stripe de teste, 2026-10-03).
    assert kw["adaptive_pricing"] == {"enabled": False}
    assert "expires_at" in kw
    foto = [(f"price_x{i}", _url(i)) for i in (1, 2, 3)]
    sessao, assinatura = _metas(fake)
    assert da_metadata(sessao) == da_metadata(assinatura) == foto
    assert sessao == assinatura
    assert fake.retrieves == ["price_x1", "price_x2", "price_x3"]   # parou no 3º válido

    corpo_resp = resp.json()
    assert set(corpo_resp) == {"client_secret", "publishable_key", "trial_days", "interval",
                               "plan", "pagina", "extras"}
    assert corpo_resp["pagina"] is True and corpo_resp["trial_days"] == (15 if trial else 0)
    assert corpo_resp["extras"] == [
        {"posicao": 1, "nome": "Caderno 1", "descricao": "Desc 1",
         "imagem": "https://img.test/1.png", "valor_centavos": 1190, "no_carrinho": False},
        {"posicao": 2, "nome": "Caderno 2", "descricao": "Desc 2",
         "imagem": None, "valor_centavos": 990, "no_carrinho": False},   # http → sem capa
        {"posicao": 3, "nome": "Caderno 3", "descricao": None,
         "imagem": None, "valor_centavos": 1490, "no_carrinho": False},
    ]
    assert _recusas(uid) == []


# ── C. filtra antes de recortar ─────────────────────────────────────────────

@pytest.mark.parametrize("invalido", [
    {"active": False}, {"currency": "usd"}, {"type": "recurring"}, {"produto": {"active": False}},
    {"unit_amount": None}, {"unit_amount": 0},
], ids=["preco-inativo", "usd", "recorrente", "produto-inativo", "valor-livre", "valor-zero"])
def test_c_primeiro_invalido_cede_a_vaga_ao_quarto(request, user_id, monkeypatch, invalido):
    uid, _, client = _auth_user_setup(f"pp-c-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, n=4, precos={"price_x1": _preco(1, **invalido)})

    resp = _post(client, _PAG_A)
    assert resp.status_code == 200, resp.text
    esperado = [(f"price_x{i}", _url(i)) for i in (2, 3, 4)]
    assert [da_metadata(m) for m in _metas(fake)] == [esperado, esperado]
    assert [e["nome"] for e in resp.json()["extras"]] == ["Caderno 2", "Caderno 3", "Caderno 4"]
    assert [d["precos"] for d in _recusas(uid)] == [["price_x1"]]


# ── I. flag off / sem `pagina` = o checkout de antes ────────────────────────

@pytest.mark.parametrize("valor,ligada", [
    ("", False), ("0", False), ("false", False), ("yes", False), ("TRUE", False),
    ("1", True), ("true", True), ("True", True), (" 1 ", True),
])
def test_i_valores_da_flag(monkeypatch, valor, ligada):
    from core.services.extras_assinar import pagina_propria_ligada
    monkeypatch.setenv("CHECKOUT_PAGINA_PROPRIA", valor)
    assert pagina_propria_ligada() is ligada


def _normaliza(kw, uid):
    # O que muda por usuário/relógio: o resto tem de ser byte a byte o de antes.
    kw = {k: v for k, v in kw.items() if k not in ("expires_at", "customer")}
    for meta in (kw["metadata"], kw["subscription_data"]["metadata"]):
        assert meta.pop("finbot_user_id") == str(uid)
    return kw


@pytest.mark.parametrize("ligada,pagina", [(False, True), (True, False)],
                         ids=["flag-off-com-pagina", "flag-on-sem-pagina"])
@pytest.mark.parametrize("base", [_ASSINAR_E, _PRECOS], ids=["assinar", "precos"])
def test_i_sem_flag_ou_sem_pagina_kwargs_identicos_aos_de_antes(request, user_id, monkeypatch,
                                                                base, ligada, pagina):
    """POSITIVO: o mesmo pedido de hoje (sem `pagina`, flag off) e a variante
    mandam ao Stripe os MESMOS kwargs e devolvem as MESMAS chaves. Na /precos
    com `pagina` e a flag off, isso é o hospedado (nunca o embutido)."""
    uid_a, _, client_a = _auth_user_setup(f"pp-i-a-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, ligada=False)
    antes = _post(client_a, base)
    assert antes.status_code == 200, antes.text
    kw_antes = _normaliza(fake.last_session_kwargs, uid_a)

    uid_b, _, client_b = _auth_user_setup(f"pp-i-b-{request.node.callspec.id}-{user_id}")
    db.set_stripe_customer(uid_b, "cus_test_b")   # o fake devolve sempre o mesmo id
    fake = _pronto(monkeypatch, ligada=ligada)
    depois = _post(client_b, {**base, "pagina": pagina})
    assert depois.status_code == 200, depois.text
    assert _normaliza(fake.last_session_kwargs, uid_b) == kw_antes
    assert set(depois.json()) == set(antes.json())
    assert fake.retrieves == []
    if base is _PRECOS:
        kw = fake.last_session_kwargs
        assert "checkout_url" in depois.json() and "ui_mode" not in kw
        assert "adaptive_pricing" not in kw   # POSITIVO: o hospedado da /precos segue sem


@pytest.mark.parametrize("pagina", [False, True], ids=["sem-pagina", "com-pagina"])
def test_i_precos_embutido_flag_off_igual_a_main(request, user_id, monkeypatch, pagina):
    """/assinar?origem=precos com a flag OFF (alcançável quando o PR 3 mandar
    `origem` da query): o embutido de hoje, fixado contra a origin/main 1d58a67e
    (`embedded_page`, 1 h, `optional_items`, SEM `adaptive_pricing`, que lá só
    vale para `origem == "assinar"`)."""
    _, _, client = _auth_user_setup(f"pp-i-pe-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, ligada=False)

    resp = _post(client, {**_PRECOS, "origem": "precos", "embutido": True, "pagina": pagina})
    assert resp.status_code == 200, resp.text
    kw = fake.last_session_kwargs
    assert set(kw) == {"customer", "payment_method_types", "line_items", "mode", "locale",
                       "allow_promotion_codes", "metadata", "subscription_data",
                       "optional_items", "expires_at", "ui_mode", "return_url"}
    assert kw["ui_mode"] == "embedded_page"
    assert set(resp.json()) == {"client_secret", "publishable_key", "trial_days", "interval", "plan"}


# ── M. reaproveitamento por modo, nos dois sentidos ─────────────────────────

_SEMEADA = "cs_semeada_pp"


def _semeia(fake, uid, ui_mode, origem="assinar"):
    db.set_stripe_customer(uid, "cus_test_123")
    meta = {"finbot_user_id": str(uid), "interval": "monthly", "plan": "plus",
            "price_id": "price_mensal_abc", "origem": origem, "td": "0",
            "ebook_price": "price_x1", "ebook_url": _url(1),
            "ebook_2_price": "price_x2", "ebook_2_url": _url(2),
            "ebook_3_price": "price_x3", "ebook_3_url": _url(3)}
    fake.open_sessions.append({"id": _SEMEADA, "customer": "cus_test_123", "status": "open",
                               "ui_mode": ui_mode, "metadata": meta, "url": None,
                               "client_secret": f"{_SEMEADA}_secret_x"})


@pytest.mark.parametrize("semeada,corpo,ligada", [
    ("elements", _ASSINAR_E, True),       # JS velho não reaproveita a página própria
    ("elements", _PAG_A, False),          # flag desligada: recarregar descarta a elements
    ("embedded_page", _PAG_A, True),      # página própria não reaproveita o embutido
], ids=["elements+embutido", "elements+flag-off", "embedded+pagina"])
def test_m_modo_diferente_nao_reaproveita(request, user_id, monkeypatch, semeada, corpo, ligada):
    uid, _, client = _auth_user_setup(f"pp-m-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, ligada=ligada)
    _semeia(fake, uid, semeada)

    resp = _post(client, corpo)
    assert resp.status_code == 200, resp.text
    assert (fake.session_create_calls, fake.session_expire_calls) == (1, 1)
    assert _SEMEADA not in resp.json()["client_secret"]


@pytest.mark.parametrize("corpo,origem", [(_PAG_A, "assinar"), (_PAG_PR, "precos")],
                         ids=["assinar", "precos"])
def test_m_mesmo_modo_reaproveita_e_marca_o_carrinho(request, user_id, monkeypatch, corpo, origem):
    """Recarregar (ou vir da /precos) com 2 cadernos no carrinho: as caixas vêm
    da FOTO da sessão e `no_carrinho` do carrinho real, sem sessão nova."""
    uid, _, client = _auth_user_setup(f"pp-mr-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, n=4, carrinho={
        _SEMEADA: ["price_mensal_abc", "price_x1", "price_x3"]})
    _semeia(fake, uid, "elements", origem)

    resp = _post(client, corpo)
    assert resp.status_code == 200, resp.text
    assert (fake.session_create_calls, fake.session_expire_calls) == (0, 0)
    corpo_resp = resp.json()
    assert corpo_resp["client_secret"] == f"{_SEMEADA}_secret_x" and corpo_resp["trial_days"] == 0
    assert corpo_resp["pagina"] is True
    assert [(e["posicao"], e["nome"], e["valor_centavos"], e["no_carrinho"])
            for e in corpo_resp["extras"]] == [
        (1, "Caderno 1", 1190, True), (2, "Caderno 2", 990, False), (3, "Caderno 3", 1490, True)]


def test_m_reaproveitada_sem_ler_o_carrinho_e_503(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"pp-m503-{user_id}")
    fake = _pronto(monkeypatch)
    _semeia(fake, uid, "elements")   # `fake.carrinho` sem a sessão → KeyError no list

    assert _post(client, _PAG_A).status_code == 503
    assert fake.session_create_calls == 0


# ── E. erros do Stripe ──────────────────────────────────────────────────────

def test_e_retrieve_que_falha_vende_o_plano_sem_caixas(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"pp-e-ret-{user_id}")
    fake = _pronto(monkeypatch, precos={"price_x2": _FakeStripeError("conexão caiu")})

    resp = _post(client, _PAG_A)
    assert resp.status_code == 200, resp.text
    assert resp.json()["extras"] == []
    for meta in _metas(fake):
        assert not [k for k in meta if k.startswith("ebook")]
    (log,) = _recusas(uid)
    assert log["precos"] == ["price_x1", "price_x2", "price_x3"]
    assert "conexão caiu" in log["stripe"] and "pdf.test" not in str(log)


def test_e_invalid_request_em_elementos_e_502_sem_keyerror(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"pp-e-502-{user_id}")
    fake = _pronto(monkeypatch)
    chamadas = []

    def create(**kw):
        chamadas.append(kw)
        raise _FakeInvalidRequestError("You cannot use elements here", param="ui_mode")
    fake.checkout.Session.create = staticmethod(create)

    assert _post(client, _PAG_A).status_code == 502
    assert len(chamadas) == 1 and _recusas(uid) == []
