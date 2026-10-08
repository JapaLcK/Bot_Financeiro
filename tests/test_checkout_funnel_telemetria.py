"""Telemetria do funil: kinds `viewed_pricing` e `expired` em checkout_funnel_events.

Cobre o schema (check alargado, unique parcial de `completed` intacta), o dedupe
de 24h do `record_pricing_viewed`, o GET /precos (só logado grava), o webhook
`checkout.session.expired` e a invariância do `_fetch_checkout_funnel`.
"""
from __future__ import annotations

import asyncio
import sys

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from db.connection import get_conn
from tests.test_billing_webhook_lifecycle import _FakeStripe, _post


def _rows(uid: int, kind: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select session_id from checkout_funnel_events "
                "where user_id = %s and kind = %s order by id",
                (uid, kind),
            )
            return cur.fetchall()


def _insert(uid, kind, session_id=None, hours_ago=0, minutes_ago=0):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into checkout_funnel_events (user_id, session_id, kind, created_at) "
                "values (%s, %s, %s, now() - make_interval(hours => %s, mins => %s))",
                (uid, session_id, kind, hours_ago, minutes_ago),
            )
        conn.commit()


@pytest.fixture()
def uid(user_id):
    yield user_id
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from checkout_funnel_events where user_id = %s", (user_id,))
        conn.commit()


# ── schema ──────────────────────────────────────────────────────────────────

def test_check_aceita_os_4_kinds_e_rejeita_invalido(uid):
    for k in ("started", "completed", "viewed_pricing", "expired"):
        _insert(uid, k, f"s_{k}_{uid}")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(uid, "kind_invalido")


def test_init_db_2x_e_nome_do_check(uid):
    db.init_db()
    db.init_db()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select pg_get_constraintdef(oid) as d from pg_constraint "
                "where conrelid = 'checkout_funnel_events'::regclass "
                "and conname = 'checkout_funnel_events_kind_check'")
            row = cur.fetchone()
    assert row is not None and "viewed_pricing" in row["d"] and "expired" in row["d"]
    _insert(uid, "viewed_pricing")  # continua valendo depois do 2º init_db


def test_check_nao_validado_com_4_kinds_e_indice_novo_existem():
    db.init_db()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select convalidated, pg_get_constraintdef(oid) as d from pg_constraint "
                "where conrelid = 'checkout_funnel_events'::regclass "
                "and conname = 'checkout_funnel_events_kind_check'")
            row = cur.fetchone()
            cur.execute("select 1 as x from pg_indexes where tablename = 'checkout_funnel_events' "
                        "and indexname = 'idx_checkout_funnel_user_kind_created'")
            idx = cur.fetchone()
    assert row["convalidated"] is False  # `not valid`: init_db não varre linha legada
    assert all(k in row["d"] for k in ("started", "completed", "viewed_pricing", "expired"))
    assert idx is not None


def test_migracao_do_check_antigo_preserva_legadas_e_alarga(uid):
    # Reproduz o estado de produção pré-PR: check de 2 kinds, validado, linhas legadas.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from checkout_funnel_events where kind in ('viewed_pricing', 'expired')")
            cur.execute("alter table checkout_funnel_events "
                        "drop constraint checkout_funnel_events_kind_check")
            cur.execute("alter table checkout_funnel_events "
                        "add constraint checkout_funnel_events_kind_check "
                        "check (kind in ('started', 'completed'))")
        conn.commit()
    try:
        _insert(uid, "started", f"cs_leg_{uid}")
        _insert(uid, "completed", f"cs_leg_{uid}")
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(uid, "viewed_pricing")
        db.init_db()
        assert len(_rows(uid, "started")) == 1 and len(_rows(uid, "completed")) == 1
        for k in ("viewed_pricing", "expired"):
            _insert(uid, k)
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert(uid, "kind_invalido")
    finally:
        db.init_db()  # nunca deixa o banco do teste com o check antigo


def test_reentrega_de_completed_continua_uma_linha(uid):
    db.record_checkout_completed(uid, f"cs_dup_{uid}")
    db.record_checkout_completed(uid, f"cs_dup_{uid}")
    assert len(_rows(uid, "completed")) == 1


# ── record_pricing_viewed ───────────────────────────────────────────────────

def test_pricing_viewed_dedupe_24h_por_usuario(uid):
    db.record_pricing_viewed(uid)
    db.record_pricing_viewed(uid)
    assert len(_rows(uid, "viewed_pricing")) == 1


def test_pricing_viewed_fora_da_janela_grava_de_novo(uid):
    _insert(uid, "viewed_pricing", hours_ago=25)
    db.record_pricing_viewed(uid)
    assert len(_rows(uid, "viewed_pricing")) == 2


def test_pricing_viewed_borda_da_janela_24h(uid):
    _insert(uid, "viewed_pricing", hours_ago=23, minutes_ago=59)
    db.record_pricing_viewed(uid)
    assert len(_rows(uid, "viewed_pricing")) == 1  # 23h59 ainda dentro: não grava


def test_pricing_viewed_24h01_grava(uid):
    _insert(uid, "viewed_pricing", hours_ago=24, minutes_ago=1)
    db.record_pricing_viewed(uid)
    assert len(_rows(uid, "viewed_pricing")) == 2


def test_pricing_viewed_dedupe_e_por_kind(uid):
    _insert(uid, "started", f"cs_k_{uid}")
    _insert(uid, "expired", f"cs_k_{uid}")
    db.record_pricing_viewed(uid)
    assert len(_rows(uid, "viewed_pricing")) == 1


def test_pricing_viewed_outro_usuario_tem_linha_propria(uid):
    import uuid
    outro = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    try:
        db.record_pricing_viewed(uid)
        db.record_pricing_viewed(outro)
        assert len(_rows(uid, "viewed_pricing")) == 1
        assert len(_rows(outro, "viewed_pricing")) == 1
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from checkout_funnel_events where user_id = %s", (outro,))
            conn.commit()


def test_falha_de_banco_nao_propaga(monkeypatch):
    import db.checkout_funnel as cf

    def _boom():
        raise RuntimeError("banco fora")

    monkeypatch.setattr(cf, "get_conn", _boom)
    db.record_pricing_viewed(1)
    db.record_checkout_expired(1, "cs_x")


# ── GET /precos e /continuar-compra ─────────────────────────────────────────

def _logado(uid):
    c = TestClient(dashboard.app)
    c.cookies.set("auth_token", dashboard._make_jwt(uid, f"u{uid}@funil.example.com"))
    return c


def test_precos_logado_grava_uma_linha(uid):
    r = _logado(uid).get("/precos")
    assert r.status_code == 200
    assert len(_rows(uid, "viewed_pricing")) == 1


def _n_viewed():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from checkout_funnel_events "
                        "where kind = 'viewed_pricing'")
            return cur.fetchone()["n"]


def test_precos_anonimo_e_cookie_invalido_servem_sem_gravar(uid):
    antes = _n_viewed()
    assert TestClient(dashboard.app).get("/precos").status_code == 200
    c = TestClient(dashboard.app)
    c.cookies.set("auth_token", "lixo.lixo.lixo")
    assert c.get("/precos").status_code == 200
    assert _n_viewed() == antes
    assert _rows(uid, "viewed_pricing") == []


def test_precos_anonimo_ignora_uid_da_query(uid):
    assert TestClient(dashboard.app).get(f"/precos?uid={uid}").status_code == 200
    assert _rows(uid, "viewed_pricing") == []


def test_precos_resolver_falhando_serve_200_sem_gravar(uid, monkeypatch):
    import frontend.routes.static_pages as sp

    def _boom(request):
        raise RuntimeError("resolver fora")

    monkeypatch.setattr(sp, "_resolve_page_user_id", _boom)
    assert _logado(uid).get("/precos").status_code == 200
    assert _rows(uid, "viewed_pricing") == []


def test_continuar_compra_nao_grava(uid):
    assert _logado(uid).get("/continuar-compra").status_code == 200
    assert _rows(uid, "viewed_pricing") == []


# ── webhook checkout.session.expired ────────────────────────────────────────

def _expired_event(session_id, uid=None):
    obj = {"id": session_id, "metadata": {"finbot_user_id": str(uid)} if uid else {}}
    return {"type": "checkout.session.expired", "id": f"evt_{session_id}",
            "data": {"object": obj}}


def _webhook(monkeypatch):
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    fake = _FakeStripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return TestClient(dashboard.app), fake


def test_webhook_expired_grava_com_session_id(uid, monkeypatch):
    client, fake = _webhook(monkeypatch)
    r = _post(client, fake, _expired_event("cs_exp_1", uid))
    assert r.status_code == 200, r.text
    assert [x["session_id"] for x in _rows(uid, "expired")] == ["cs_exp_1"]
    # reentrega: 200 (linha duplicada aceita)
    assert _post(client, fake, _expired_event("cs_exp_1", uid)).status_code == 200


def test_webhook_expired_sem_usuario_responde_200_sem_linha(monkeypatch):
    client, fake = _webhook(monkeypatch)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from checkout_funnel_events where kind = 'expired'")
            antes = cur.fetchone()["n"]
    assert _post(client, fake, _expired_event("cs_exp_orfa")).status_code == 200
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from checkout_funnel_events where kind = 'expired'")
            assert cur.fetchone()["n"] == antes


# ── painel: _fetch_checkout_funnel não muda com os kinds novos ──────────────

def _funil():
    from core.admin_dashboard import _fetch_checkout_funnel, db_connect

    async def go():
        async with await db_connect() as conn:
            async with conn.cursor() as cur:
                return await _fetch_checkout_funnel(cur)

    return asyncio.run(go())


def test_fetch_checkout_funnel_ignora_kinds_novos(uid):
    _insert(uid, "started", f"cs_pan_{uid}")
    antes = _funil()
    _insert(uid, "viewed_pricing")
    _insert(uid, "expired", f"cs_pan_{uid}")
    assert _funil() == antes
