"""Exclusão de conta (S2) x checkout concorrente: os dois disputam o MESMO advisory lock
`billing_checkout:<uid>` (`_billing_user_lock`).

  * quem toma o lock depois de a conta virar `processing`/`scheduled` (ou sem linha) leva
    403 e NENHUMA sessão Stripe nasce -- guarda dentro do `_billing_user_lock`, que cobre
    create-checkout, bump e Pix;
  * `cancelar_assinaturas` segura o lock em volta da fase Stripe: um checkout que já estava
    sob o lock termina ANTES, e a exclusão o enxerga e o expira.

Regra da guarda: 403 se NAO ha linha em `users` (conta ja apagada) ou se alguma linha de
`auth_accounts` esta `scheduled`/`processing`; `users` SEM `auth_accounts` (so-WhatsApp, que
assina por link magico) SEGUE.

Controle (CLAUDE.md §3), medido: sem a guarda em `_billing_user_lock` -> os casos 403 (a, b, c, g);
guarda "sem linha em auth_accounts -> 403" -> f; sem o ramo "sem users" -> b; sem o lock em
`cancelar_assinaturas` -> d. Positivos: e (conta normal) e f (so-WhatsApp pelo Pix).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as billing_pix
from core.services import stripe_exclusao
from frontend.routes.retomada import Comprador, comprador
from test_billing_checkout import _CSRF_HEADERS, _auth_user_setup, _patch_stripe, _reset_rate_limiter  # noqa: F401
from _paywall_gate_helpers import so_whatsapp
from test_conta_exclusao_stripe import _conta, contas, loja, sql  # noqa: F401


@pytest.fixture
def como(user_id):
    """Cliente cujo `comprador` já passou (a corrida: a dependência roda ANTES do lock)."""
    def _como(uid):
        dashboard.app.dependency_overrides[comprador] = lambda: Comprador(uid)
        client = TestClient(dashboard.app)
        client.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_HEADERS[dashboard.CSRF_HEADER_NAME])
        return client
    yield _como
    dashboard.app.dependency_overrides.pop(comprador, None)


def _stripe_ok(monkeypatch):
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    return _patch_stripe(monkeypatch)


@pytest.mark.parametrize("estado", ["processing", "scheduled"])
def test_a_conta_em_exclusao_leva_403_e_nenhuma_sessao_nasce(user_id, como, monkeypatch, estado):
    uid, _, _ = _auth_user_setup(f"lock-a-{estado}-{user_id}")
    sql("update auth_accounts set deletion_status = %s, deletion_scheduled_for = now() where user_id = %s",
        estado, uid)
    fake = _stripe_ok(monkeypatch)
    r = como(uid).post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    assert r.status_code == 403, r.text
    assert fake.session_create_calls == 0 and fake.customer_create_calls == 0


def test_b_conta_sem_linha_leva_403(user_id, como, monkeypatch):
    with db.get_conn() as conn:
        fantasma = int(conn.execute("select coalesce(max(user_id), 0) + 1000000 as n"
                                    " from auth_accounts").fetchone()["n"])
    fake = _stripe_ok(monkeypatch)
    r = como(fantasma).post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    assert r.status_code == 403, r.text
    assert fake.session_create_calls == 0


def test_c_bump_passa_pela_mesma_guarda(user_id, como, monkeypatch):
    uid, _, _ = _auth_user_setup(f"lock-c-{user_id}")
    sql("update auth_accounts set deletion_status = 'processing', deletion_scheduled_for = now()"
        " where user_id = %s", uid)
    _stripe_ok(monkeypatch)

    async def _passou(request):   # o bump chama `comprador` direto (import tardio), sem Depends
        return Comprador(uid)
    monkeypatch.setattr("frontend.routes.retomada.comprador", _passou)
    r = como(uid).post("/billing/checkout/bump", json={"sid": "cs_test_abc", "posicoes": [1]},
                       headers=_CSRF_HEADERS)
    assert r.status_code == 403, r.text


def test_e_conta_normal_cria_checkout_pelo_lock(user_id, como, monkeypatch):
    uid, _, _ = _auth_user_setup(f"lock-e-{user_id}")
    fake = _stripe_ok(monkeypatch)
    r = como(uid).post("/billing/create-checkout", json={"plan": "plus"}, headers=_CSRF_HEADERS)
    assert r.status_code == 200, r.text
    assert fake.session_create_calls == 1


def test_f_so_whatsapp_sem_auth_accounts_paga_pix(user_id, monkeypatch):
    """Quem so usa o WhatsApp (`users` sem `auth_accounts`) entra pelo link magico e paga:
    a guarda nao pode barra-lo."""
    uid = so_whatsapp()
    with db.get_conn() as conn:
        assert conn.execute("select 1 from auth_accounts where user_id = %s", (uid,)).fetchone() is None
    chamou = []
    monkeypatch.setattr(billing_pix, "criar_checkout", lambda *a, **k: chamou.append(1) or {"ok": True})
    client = TestClient(dashboard.app)
    client.get(f"/d/{db.create_dashboard_session(uid, hours=1.0)}?next=/precos", follow_redirects=False)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_HEADERS[dashboard.CSRF_HEADER_NAME])
    r = client.post("/billing/pix/checkout", headers=_CSRF_HEADERS, json={
        "plan": "plus", "interval": "annual", "cpf_cnpj": "52998224725", "extras": []})
    assert r.status_code == 200, r.text
    assert chamou == [1]


def test_g_pix_com_conta_em_processing_leva_403(user_id, monkeypatch):
    uid, _, _ = _auth_user_setup(f"lock-g-{user_id}")
    sql("update auth_accounts set deletion_status = 'processing', deletion_scheduled_for = now()"
        " where user_id = %s", uid)
    chamou = []
    monkeypatch.setattr(billing_pix, "criar_checkout", lambda *a, **k: chamou.append(1) or {"ok": True})

    async def _passou(request):   # `comprador_pix` ja passou (a corrida); a guarda esta no lock
        return Comprador(uid)
    monkeypatch.setattr(billing_pix, "comprador_pix", _passou)
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_HEADERS[dashboard.CSRF_HEADER_NAME])
    r = client.post("/billing/pix/checkout", headers=_CSRF_HEADERS, json={
        "plan": "plus", "interval": "annual", "cpf_cnpj": "52998224725", "extras": []})
    assert r.status_code == 403, r.text
    assert chamou == []


def _lock_key(uid):
    return f"billing_checkout:{uid}"


def test_d_exclusao_espera_o_lock_e_expira_o_checkout_criado_sob_ele(contas, loja):
    conta = _conta(contas, "cus_lock")
    with db.get_conn() as seg:   # o checkout concorrente: dono do lock
        seg.execute("select pg_advisory_lock(hashtext(%s))", (_lock_key(conta.uid),))
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(stripe_exclusao.cancelar_assinaturas, conta.uid)
            for _ in range(100):   # espera BLOQUEADA no lock (pg_locks), não por tempo
                if seg.execute("select 1 from pg_locks where locktype = 'advisory' and not granted"
                               " and objid = (hashtext(%s)::bigint & 4294967295)::oid",
                               (_lock_key(conta.uid),)).fetchone():
                    break
                time.sleep(0.05)
            else:
                pytest.fail("cancelar_assinaturas nao esperou o lock do checkout")
            assert not fut.done() and loja.eventos == []
            loja.sessao("cs_sob_lock", "cus_lock")   # o checkout termina e cria a sessão
            seg.execute("select pg_advisory_unlock(hashtext(%s))", (_lock_key(conta.uid),))
            seg.commit()
            fut.result(timeout=10)
    assert loja.sessoes["cs_sob_lock"]["status"] == "expired"
    # e a exclusão soltou o lock: outra conexão o toma sem esperar
    with db.get_conn() as outra:
        assert outra.execute("select pg_try_advisory_lock(hashtext(%s)) as ok",
                             (_lock_key(conta.uid),)).fetchone()["ok"]
        outra.execute("select pg_advisory_unlock(hashtext(%s))", (_lock_key(conta.uid),))
        outra.commit()
