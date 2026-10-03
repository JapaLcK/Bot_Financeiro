"""Página própria, PR 2 — `POST /billing/checkout/bump` (o order bump).

Pela rota, com banco real (lock, conta, log) e um Stripe falso de
`Session.retrieve`/`list_line_items`/`modify`.

I   isolamento: sessão de outra conta (metadata e/ou customer), de outro modo
    ou de outra origem → 404 sem `modify`; a própria → 200 (POSITIVO).
L   o carrinho: {1,3} partindo de {2} → plano pelo id, 1 e 3 por preço, 2 sai;
    o mesmo conjunto → sem `modify`. Unitários de `linhas_do_bump`.
V   entrada: `price` no corpo → 422; posição além da foto, repetida, 0,
    negativa, `sid` torto → 400 sem tocar no Stripe; sem CSRF 403; sem login 401.
S   sessão paga/expirada → 409; recusa do Stripe → 409 + log sem URL; outra
    falha do Stripe → 502.
W   ponta a ponta pelo webhook: foto de 3 na assinatura, fatura com plano + 2
    cadernos, `invoice.paid` antes e depois do `completed` → `amount_cents` =
    só o plano.

Controles (rodados, ver o relato do PR):
  · sem o filtro de `finbot_user_id`           → I[outro-usuario-meta] vermelho;
  · sem o filtro de `customer`                 → I[outro-customer] vermelho;
  · sem `extra="forbid"` (aceita o `price`)    → V 422 vermelho;
  · `linhas_do_bump` sem manter o plano        → L {1,3} e o unitário vermelhos;
  · sem o `return None` (modify sempre)        → L mesmo conjunto vermelho;
  · `nullcontext()` no lugar do lock           → test_i_lock vermelho;
  · status checado antes do dono               → I[outro-usuario-fechada] vermelho;
  · `str(uid) in` / `.strip()` no dono         → I formatos do finbot_user_id vermelhos;
  · StripeError do retrieve vira 404           → S 502[retrieve] vermelho;
  · sem o `limiter.limit`                      → V 429 vermelho;
  · `desejados` sem dedupe                     → unitário do repetido vermelho;
  · sem olhar `expires_at`                     → S vencida-ainda-open vermelho.
"""
from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.services.extras_assinar import linhas_do_bump, para_metadata
from test_billing_checkout import (  # noqa: F401 — `_reset_rate_limiter` é fixture autouse
    _CSRF_HEADERS, _CSRF_TOKEN, _FakeInvalidRequestError, _FakeStripeError, _auth_user_setup,
    _reset_rate_limiter,
)
from test_billing_webhook_lifecycle import _T_LIFE, _fake_sub, _post as _webhook
from test_ebook_webhook import (  # noqa: F401 — `indicado` e `_event_logs` são fixtures
    _SUB, _cobrado, _comissoes, _event_logs, _fatura, _linha, _linhas, _sql, indicado,
)

_SID = "cs_test_bump1"
_FOTO = ["price_x1", "price_x2", "price_x3"]
_CLIENTE = "cus_bump"


def _meta(uid, origem="assinar"):
    return {"finbot_user_id": str(uid), "origem": origem,
            **para_metadata([(p, f"https://pdf.test/{n}") for n, p in enumerate(_FOTO, 1)])}


def _pronto(monkeypatch, uid, carrinho=("price_plano", "price_x2"), meta=None, **sessao):
    """Stripe falso com UMA sessão `elements` aberta do `uid`; `sessao` e `meta` trocam campos."""
    db.set_stripe_customer(uid, _CLIENTE)
    s = {"id": _SID, "customer": _CLIENTE, "ui_mode": "elements", "status": "open",
         "expires_at": int(time.time()) + 3600,
         "metadata": {**_meta(uid), **(meta or {})}, **sessao}
    fake = SimpleNamespace(retrieves=[], modifies=[], erro_modify=None, erro_lista=None,
                           erro_retrieve=None, sono=0, dentro=0, pico=0, trava=threading.Lock(),
                           error=SimpleNamespace(StripeError=_FakeStripeError,
                                                 InvalidRequestError=_FakeInvalidRequestError))

    def retrieve(sid):
        fake.retrieves.append(sid)
        if fake.erro_retrieve:
            raise fake.erro_retrieve
        if sid != _SID:
            raise _FakeInvalidRequestError("No such checkout.session", code="resource_missing")
        return s

    def list_line_items(sid, limit=None):
        assert (sid, limit) == (_SID, 100)
        if fake.erro_lista:
            raise fake.erro_lista
        with fake.trava:   # quantos pedidos estão lendo o carrinho AO MESMO TEMPO
            fake.dentro += 1
            fake.pico = max(fake.pico, fake.dentro)
        time.sleep(fake.sono)
        with fake.trava:
            fake.dentro -= 1
        return {"data": [{"id": f"li_{p}", "price": {"id": p}} for p in carrinho]}

    def modify(sid, **kw):
        fake.modifies.append((sid, kw))
        if fake.erro_modify:
            raise fake.erro_modify

    fake.checkout = SimpleNamespace(Session=SimpleNamespace(
        retrieve=retrieve, list_line_items=list_line_items, modify=modify))
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return fake


def _bump(client, posicoes, sid=_SID, **extra):
    return client.post("/billing/checkout/bump", json={"sid": sid, "posicoes": posicoes, **extra},
                       headers=_CSRF_HEADERS)


def _recusas(uid):
    return [r["details"] for r in _sql(
        "select details from system_event_logs"
        " where event_type = 'ebook_oferta_recusada' and user_id = %s", (uid,))]


# ── I. isolamento ───────────────────────────────────────────────────────────

_ENTRA_1 = {"line_items": [{"id": "li_price_plano"}, {"id": "li_price_x2"},
                           {"price": "price_x1", "quantity": 1}]}


@pytest.mark.parametrize("caso,sessao,meta,esperado", [
    ("propria-assinar", {}, {}, 200),
    ("propria-precos", {}, {"origem": "precos"}, 200),
    ("outro-usuario-meta", {}, {"finbot_user_id": "999"}, 404),
    ("outro-customer", {"customer": "cus_de_outro"}, {}, 404),
    ("outro-usuario-ambos", {"customer": "cus_de_outro"}, {"finbot_user_id": "999"}, 404),
    ("embedded-page", {"ui_mode": "embedded_page"}, {}, 404),
    ("hosted-page", {"ui_mode": "hosted_page"}, {}, 404),
    ("origem-quiz", {}, {"origem": "quiz"}, 404),
    ("sem-origem", {}, {"origem": None}, 404),
    # fechada E alheia: 404, nunca 409 (o 409 diria o estado da sessão de outro)
    ("outro-usuario-fechada", {"status": "complete"}, {"finbot_user_id": "999"}, 404),
])
def test_i_isolamento_404_indistinguivel_sem_modify(request, user_id, monkeypatch,
                                                     caso, sessao, meta, esperado):
    uid, _, client = _auth_user_setup(f"bump-i-{caso}-{user_id}")
    fake = _pronto(monkeypatch, uid, meta=meta, **sessao)

    resp = _bump(client, [1, 2])
    assert resp.status_code == esperado, resp.text
    if esperado == 200:
        assert resp.json() == {"ok": True} and fake.modifies == [(_SID, _ENTRA_1)]
    else:
        assert fake.modifies == []
        assert resp.json() == {"detail": "Sessão de pagamento não encontrada."}


@pytest.mark.parametrize("formato,esperado", [
    (lambda u: u, 200),                  # int (SDK que não serializa) casa
    (lambda u: str(u), 200),
    (lambda u: f"0{u}", 404), (lambda u: f" {u}", 404), (lambda u: f"{u} ", 404),
    (lambda u: str(u)[:-1], 404), (lambda u: f"{u}0", 404),
], ids=["int", "str", "zero-a-esquerda", "espaco-antes", "espaco-depois", "prefixo", "sufixo"])
def test_i_finbot_user_id_casa_exato(request, user_id, monkeypatch, formato, esperado):
    uid, _, client = _auth_user_setup(f"bump-i-fmt-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, uid, meta={"finbot_user_id": formato(uid)})
    assert _bump(client, [1]).status_code == esperado
    assert len(fake.modifies) == (esperado == 200)


def test_i_lock_serializa_dois_pedidos_do_mesmo_usuario(user_id, monkeypatch):
    """Dois pedidos simultâneos nunca leem o carrinho ao mesmo tempo: sem o
    lock, os dois leriam o mesmo estado e um modify apagaria o outro."""
    uid, email, client_a = _auth_user_setup(f"bump-i-lock-{user_id}")
    client_b = TestClient(dashboard.app)
    client_b.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, email))
    client_b.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_TOKEN)
    fake = _pronto(monkeypatch, uid)
    # Aquece o pool async ANTES da corrida: com `_db_pool` nulo, os dois loops
    # (um por TestClient) disputam o `_db_pool_lock` e penduram (a mina de
    # tests/test_pool_async_entre_loops.py). Sid inexistente: 404, sem carrinho.
    assert _bump(client_a, [1], sid="cs_test_aquece").status_code == 404
    fake.sono = 0.3

    resps = [None, None]

    def _pede(i, cliente):
        resps[i] = _bump(cliente, [1])
    # Threads daemon com `join(timeout)`: uma pendura vira falha, e não trava a
    # suíte (o `with ThreadPoolExecutor` esperaria a thread presa para sempre).
    threads = [threading.Thread(target=_pede, args=(i, c), daemon=True)
               for i, c in enumerate((client_a, client_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "pendurou: pedido preso no lock ou no pool"
    assert [r.status_code for r in resps] == [200, 200]
    assert fake.pico == 1


def test_i_sid_inexistente_e_404_igual(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"bump-i-nx-{user_id}")
    fake = _pronto(monkeypatch, uid)
    resp = _bump(client, [1], sid="cs_test_outra")
    assert resp.status_code == 404 and resp.json() == {"detail": "Sessão de pagamento não encontrada."}
    assert fake.modifies == []


def test_i_conta_sem_customer_nao_casa_sessao_sem_customer(user_id, monkeypatch):
    """`None == None` não é dono: conta sem customer no Stripe nunca acha sessão."""
    uid, _, client = _auth_user_setup(f"bump-i-nc-{user_id}")
    fake = _pronto(monkeypatch, uid, customer=None)
    db.set_stripe_customer(uid, None)
    assert _bump(client, [1]).status_code == 404 and fake.modifies == []


# ── L. o carrinho ───────────────────────────────────────────────────────────

def test_l_1_e_3_partindo_do_2_preserva_o_plano_pelo_id(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"bump-l13-{user_id}")
    fake = _pronto(monkeypatch, uid)
    resp = _bump(client, [3, 1])
    assert resp.status_code == 200, resp.text
    # SEM `metadata` no modify: a foto é da criação e não muda.
    assert fake.modifies == [(_SID, {"line_items": [
        {"id": "li_price_plano"}, {"price": "price_x3", "quantity": 1},
        {"price": "price_x1", "quantity": 1}]})]


@pytest.mark.parametrize("carrinho,posicoes", [
    (("price_plano", "price_x2"), [2]), (("price_plano",), []),
    (("price_plano", "price_x1", "price_x3"), [3, 1]),
], ids=["so-o-2", "nenhum", "1-e-3"])
def test_l_mesmo_conjunto_nao_chama_o_modify(request, user_id, monkeypatch, carrinho, posicoes):
    uid, _, client = _auth_user_setup(f"bump-l-eq-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, uid, carrinho=carrinho)
    resp = _bump(client, posicoes)
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert fake.modifies == []


def test_l_desmarcar_tudo_deixa_so_o_plano(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"bump-l-0-{user_id}")
    fake = _pronto(monkeypatch, uid, carrinho=("price_plano", "price_x1", "price_x2"))
    assert _bump(client, []).status_code == 200
    assert fake.modifies == [(_SID, {"line_items": [{"id": "li_price_plano"}]})]


def _li(*precos):
    return [{"id": f"li_{p}", "price": {"id": p}} for p in precos]


@pytest.mark.parametrize("atuais,desejados,esperado", [
    (_li("plano"), ["a"], [{"id": "li_plano"}, {"price": "a", "quantity": 1}]),
    (_li("plano", "b"), ["a", "c"], [{"id": "li_plano"}, {"price": "a", "quantity": 1},
                                     {"price": "c", "quantity": 1}]),
    (_li("plano", "a", "b"), ["b"], [{"id": "li_plano"}, {"id": "li_b"}]),
    (_li("plano", "a"), [], [{"id": "li_plano"}]),
    (_li("plano", "a"), ["a"], None),
    (_li("plano"), [], None),
    (_li("a", "plano", "outro"), ["a"], None),   # não-extra nunca sai, em qualquer posição
    (_li("plano"), ["a", "a"], [{"id": "li_plano"}, {"price": "a", "quantity": 1}]),
    (_li("plano", "a"), ["a", "a"], None),
])
def test_l_linhas_do_bump(atuais, desejados, esperado):
    assert linhas_do_bump(atuais, ["a", "b", "c"], desejados) == esperado


# ── V. entrada ──────────────────────────────────────────────────────────────

def test_v_price_no_corpo_e_422_sem_stripe(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"bump-v-pr-{user_id}")
    fake = _pronto(monkeypatch, uid)
    resp = _bump(client, [1], price="price_qualquer")
    assert resp.status_code == 422
    assert (fake.retrieves, fake.modifies) == ([], [])


@pytest.mark.parametrize("posicoes,sid,esperado", [
    ([4], _SID, 400),                       # além da foto de 3: só se sabe depois do retrieve
    ([1, 1], _SID, 400), ([0], _SID, 400), ([-1], _SID, 400), ([11], _SID, 400),
    (list(range(1, 12)), _SID, 400),
    ([1], "cs_test_", 400), ([1], "cs_prod_abc", 400), ([1], "cs_test_a/b", 400),
    ([1], "cs_test_" + "a" * 201, 400),
    (["1"], _SID, 422), ([1.5], _SID, 422), ([True], _SID, 422),
], ids=["4-de-3", "repetida", "zero", "negativa", "11", "11-itens", "sid-vazio", "sid-prod",
        "sid-barra", "sid-longo", "texto", "fracao", "bool"])
def test_v_entrada_invalida_sem_modify(request, user_id, monkeypatch, posicoes, sid, esperado):
    uid, _, client = _auth_user_setup(f"bump-v-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, uid)
    assert _bump(client, posicoes, sid=sid).status_code == esperado
    assert fake.modifies == []
    if posicoes != [4]:
        assert fake.retrieves == []


def test_v_rate_limit_de_120_por_hora(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"bump-v-429-{user_id}")
    _pronto(monkeypatch, uid)
    codigos = [_bump(client, [1], sid="cs_test_naoexiste").status_code for _ in range(121)]
    assert codigos[:120] == [404] * 120 and codigos[120] == 429


def test_v_sem_csrf_e_403_e_sem_login_e_401(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"bump-v-auth-{user_id}")
    fake = _pronto(monkeypatch, uid)
    sem_csrf = client.post("/billing/checkout/bump", json={"sid": _SID, "posicoes": [1]})
    assert sem_csrf.status_code == 403

    anonimo = TestClient(dashboard.app)
    anonimo.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_TOKEN)
    assert _bump(anonimo, [1]).status_code == 401
    assert (fake.retrieves, fake.modifies) == ([], [])


# ── S. estado da sessão e erros do Stripe ───────────────────────────────────

@pytest.mark.parametrize("status", ["complete", "expired"])
def test_s_sessao_fechada_e_409(request, user_id, monkeypatch, status):
    uid, _, client = _auth_user_setup(f"bump-s-{status}-{user_id}")
    fake = _pronto(monkeypatch, uid, status=status)
    resp = _bump(client, [1])
    assert resp.status_code == 409 and resp.json()["detail"] == {"error": "sessao_fechada"}
    assert fake.modifies == []


@pytest.mark.parametrize("delta,esperado", [(-1, 409), (3600, 200)], ids=["vencida", "no-prazo"])
def test_s_open_com_expires_at_vencido_e_fechada(request, user_id, monkeypatch, delta, esperado):
    """O Stripe demora a varrer: `open` com prazo vencido é fechada para nós,
    sem modify e sem log de recusa falso. No prazo segue (POSITIVO)."""
    uid, _, client = _auth_user_setup(f"bump-s-exp-{request.node.callspec.id}-{user_id}")
    fake = _pronto(monkeypatch, uid, expires_at=int(time.time()) + delta)
    resp = _bump(client, [1])
    assert resp.status_code == esperado, resp.text
    assert len(fake.modifies) == (esperado == 200) and _recusas(uid) == []


def test_s_stripe_recusa_o_modify_409_e_log_sem_url(user_id, monkeypatch):
    uid, _, client = _auth_user_setup(f"bump-s-rec-{user_id}")
    fake = _pronto(monkeypatch, uid)
    fake.erro_modify = _FakeInvalidRequestError("This price is inactive", param="line_items")
    resp = _bump(client, [1, 3])
    assert resp.status_code == 409 and resp.json()["detail"] == {"error": "extra_recusado"}
    (log,) = _recusas(uid)
    assert log["precos"] == ["price_x1", "price_x3"] and "inactive" in log["stripe"]
    assert "pdf.test" not in str(log)


@pytest.mark.parametrize("onde", ["retrieve", "lista", "modify"])
def test_s_outra_falha_do_stripe_e_502(request, user_id, monkeypatch, onde):
    uid, _, client = _auth_user_setup(f"bump-s-502-{onde}-{user_id}")
    fake = _pronto(monkeypatch, uid)
    setattr(fake, f"erro_{onde}", _FakeStripeError("conexão caiu"))
    assert _bump(client, [1]).status_code == 502
    assert _recusas(uid) == []


# ── W. ponta a ponta pelo webhook ───────────────────────────────────────────

def _sub_com_foto(uid, status):
    sub = _fake_sub(status, "price_plano")
    sub["items"]["data"][0]["price"].update({"unit_amount": 1990, "currency": "brl"})
    sub["metadata"] = _meta(uid)
    return sub


@pytest.mark.parametrize("ordem", ["fatura-antes", "fatura-depois"])
@pytest.mark.parametrize("status,plano,esperado", [
    ("trialing", 0, ([], [])), ("active", 1990, ([19.9], [1990])),
], ids=["trial", "sem-trial"])
def test_w_fatura_com_2_cadernos_cobra_so_o_plano_nas_duas_ordens(indicado, ordem, status,
                                                                   plano, esperado):
    """Fatura = plano + cadernos 1 e 3 (o bump marcou 2 dos 3 da foto)."""
    uid, client, fake, vistos, _ = indicado
    linhas = [_linha("price_plano", plano), _linha("price_x1", 1190), _linha("price_x3", 1490)]
    fatura = _fatura("invoice.paid", uid, "in_w", plano + 2680, "subscription_create", linhas)
    completed = {"type": "checkout.session.completed", "id": "evt_w", "created": _T_LIFE,
                 "data": {"object": {"id": _SID, "subscription": _SUB, "ui_mode": "elements",
                                     "metadata": _meta(uid), "amount_total": plano + 2680,
                                     "currency": "brl"}}}
    subs = {_SUB: _sub_com_foto(uid, status)}
    for evento in ([fatura, completed] if ordem == "fatura-antes" else [completed, fatura]):
        r = _webhook(client, fake, evento, subs=subs)
        assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid)) == esperado
    # A pendência é da FOTO (os 3 oferecidos); quem não comprou vira `nao_comprou` no job.
    assert sorted(r["ebook_price"] for r in _linhas(uid)) == _FOTO
