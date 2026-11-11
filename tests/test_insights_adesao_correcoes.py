"""PL-06 B6, correções do §15 do plano (Tester r4 e Manager): quem é ato, quem é natural, e a adesão viva.

  1. baixa de Pix desfeita é perda NATURAL (margem de 1 h);
  2. admin que tira o recurso retira na hora, inclusive em carência e na MESMA transação; membro com grant
     `family` vigente não perde;
  3. o gatilho não infere downgrade: Plus que vence sobre Essencial é natural; a troca de preço é ato;
  4. sair da família retira sem plano próprio elegível e preserva com ele;
  5. critério único de adesão VIVA na tela e na exportação, com a fronteira exata de 60 min;
  7. cancelamento explícito em carência retira; o `deleted` da migração cartão→Pix não;
  9. INSERT sem adesão nunca guarda marca.
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

import db
import db_support
from _billing_grants_helpers import conta, garantir_system_event_logs
from _insights_adesao_apoio import aderir, ambiente, cliente, eventos, flags, patch, sql  # noqa: F401
from conftest import usuario_pagante
from core.services.billing_access import recompute_entitlement
from db.plan_grants import upsert_grant
from db.reports import insight_aviso_aderiu, insight_email_permitido
from test_insights_adesao_direito import _assinante, _assinou_pelo_webhook, _relogio_passou, _sub, webhook  # noqa: F401
from test_insights_adesao_perda import _deleted, _retiradas, externo  # noqa: F401

pytestmark = pytest.mark.usefixtures("ambiente")
_V = [3_000_000]


def _agora():
    return datetime.now(timezone.utc)


def _v():
    _V[0] += 1
    return _V[0]


# ── 1. baixa de Pix desfeita: natural ─────────────────────────────────────────

def test_baixa_de_pix_desfeita_e_perda_natural_com_margem(user_id, externo):
    """R11 do Tester: o `_revoke` da baixa desfeita NÃO retira; a adesão fica (sem envio) até 1 h."""
    from _dreno_pix_helpers import entregar, nova_cobranca
    conta(user_id, "free", None)
    sql("update auth_accounts set plan_selected_at = now() where user_id = %s", (user_id,))
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    db_support.invalidate_auth_user_cache(user_id)
    aderir(user_id)
    entregar("PAYMENT_RECEIVED_IN_CASH_UNDONE", cobranca)
    db_support.invalidate_auth_user_cache(user_id)
    assert db.get_auth_user(user_id)["plan"] == "free", "a baixa desfeita revogou o acesso"
    assert insight_aviso_aderiu(user_id) and not insight_email_permitido(user_id) and _retiradas(user_id) == []


# ── 2. admin ──────────────────────────────────────────────────────────────────

def _em_carencia(uid):
    sql("update auth_accounts set last_payment_status = 'past_due', past_due_since = now() - interval '2 days'"
        " where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)


@pytest.mark.parametrize("plano", ["free", "essencial"])
def test_admin_em_carencia_retira_na_hora(user_id, plano):
    """R14 e E1 do Tester: a carência não segura a adesão contra o ato do operador."""
    from core.admin_dashboard import set_account_plan
    _assinante(user_id)
    _em_carencia(user_id)
    set_account_plan(plano, 12, user_id=user_id)
    assert not insight_aviso_aderiu(user_id) and _retiradas(user_id) == ["admin"]


def test_admin_retira_na_mesma_transacao(user_id, monkeypatch):
    """O que roda DEPOIS do commit (sincronia da família) falhando não impede a retirada: ela já foi."""
    import core.services.family_access as fa
    from core.admin_dashboard import set_account_plan
    _assinante(user_id)

    def _quebra(*a, **k):
        raise RuntimeError("depois do commit")
    monkeypatch.setattr(fa, "sync_from_owner", _quebra)
    with pytest.raises(RuntimeError):
        set_account_plan("free", user_id=user_id)
    assert not insight_aviso_aderiu(user_id) and _retiradas(user_id) == ["admin"]


def test_admin_rebaixando_membro_com_grant_family_nao_retira():
    """R12 do Tester: a reprojeção devolve o Plus da família; a retirada lê o grant `family` vigente."""
    from core.admin_dashboard import set_account_plan
    from test_family_access import _entra, _titular
    owner = _titular("pro_max")
    membro = _entra(owner)
    sql("update auth_accounts set plan_selected_at = now() where user_id = %s", (membro,))
    db_support.invalidate_auth_user_cache(membro)
    aderir(membro)
    set_account_plan("free", user_id=membro)
    db_support.invalidate_auth_user_cache(membro)
    assert db.get_auth_user(membro)["plan"] != "free", "a família devolveu o plano"
    assert insight_aviso_aderiu(membro) and insight_email_permitido(membro) and _retiradas(membro) == []


# ── 3. downgrade: só por ato ──────────────────────────────────────────────────

def _plus_pix_sobre(uid, com_essencial):
    garantir_system_event_logs()
    sql("update auth_accounts set plan = 'free', plan_expires_at = null, plan_selected_at = now(),"
        " stripe_customer_id = null where user_id = %s", (uid,))
    if com_essencial:
        upsert_grant(uid, "admin", f"admin:{uid}", "essencial", _agora() - timedelta(days=1),
                     _agora() + timedelta(days=200), _v(), f"evt_ess_{uid}")
    upsert_grant(uid, "pix", f"pix:{uid}:plus", "pro", _agora() - timedelta(days=30), _agora() + timedelta(days=1),
                 _v(), f"evt_plus_{uid}")
    recompute_entitlement(uid)
    db_support.invalidate_auth_user_cache(uid)
    aderir(uid)


@pytest.mark.parametrize("com_essencial", [False, True])
def test_plus_que_vence_sobre_essencial_e_natural(com_essencial):
    """X1 do Manager: a base Essencial não transforma a expiração do Plus em downgrade."""
    uid = usuario_pagante("pro")
    _plus_pix_sobre(uid, com_essencial)
    sql("update plan_grants set ends_at = now() - interval '20 minutes' where user_id = %s and source = 'pix'", (uid,))
    _relogio_passou(uid, coberto_ate=_agora() - timedelta(minutes=20))
    recompute_entitlement(uid)
    db_support.invalidate_auth_user_cache(uid)
    assert db.get_auth_user(uid)["plan"] == ("essencial" if com_essencial else "free")
    assert insight_aviso_aderiu(uid), "a expiração natural do Plus retirou"
    upsert_grant(uid, "pix", f"pix:{uid}:plus2", "pro", _agora(), _agora() + timedelta(days=30), _v(), f"evt_p2_{uid}")
    recompute_entitlement(uid)
    assert insight_aviso_aderiu(uid) and insight_email_permitido(uid) and _retiradas(uid) == []


def test_troca_de_preco_com_plus_por_outro_caminho_nao_retira(webhook, monkeypatch):
    """A troca para Essencial é ato, mas quem tem `insights` por um Pix Plus não perde nada."""
    import frontend.finance_bot_websocket_custom as dashboard
    from _billing_grants_helpers import evt_paid
    from test_billing_webhook_lifecycle import _fake_sub
    uid, post, t = _assinou_pelo_webhook(webhook, "troca_pix")
    upsert_grant(uid, "pix", f"pix:{uid}:x", "pro", _agora(), _agora() + timedelta(days=60), _v(), f"evt_x_{uid}")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_ESSENCIAL_MENSAL", "price_essencial_b6")
    assert post(evt_paid(uid, "sub_b6", t + 10), subs={"sub_b6": _fake_sub("active", price_id="price_essencial_b6")}).status_code == 200
    assert insight_aviso_aderiu(uid) and _retiradas(uid) == []


# ── 4. sair da família ────────────────────────────────────────────────────────

@pytest.mark.parametrize("plano_proprio,fica", [(False, False), (True, True)])
def test_sair_da_familia(plano_proprio, fica):
    from db.family import leave_family
    from test_family_access import _entra, _titular
    owner = _titular("pro_max")
    membro = _entra(owner)
    sql("update auth_accounts set plan_selected_at = now() where user_id = %s", (membro,))
    if plano_proprio:
        upsert_grant(membro, "pix", f"pix:{membro}:proprio", "pro", _agora() - timedelta(days=1),
                     _agora() + timedelta(days=60), _v(), f"evt_prop_{membro}")
        recompute_entitlement(membro)
    db_support.invalidate_auth_user_cache(membro)
    aderir(membro)
    leave_family(membro)
    assert insight_aviso_aderiu(membro) is fica
    assert _retiradas(membro) == ([] if fica else ["familia"])


def test_sair_da_familia_com_plano_proprio_em_carencia_retira():
    """§16: sair é ato e vence a carência (como no admin). Só plano próprio com `insights` VIGENTE preserva."""
    from core.services.billing_dunning import carencia_aberta
    from db.family import leave_family
    from test_family_access import _entra, _titular
    owner = _titular("pro_max")
    membro = _entra(owner)
    sql("update auth_accounts set plan_selected_at = now(), last_payment_status = 'past_due',"
        " past_due_since = now() - interval '2 days' where user_id = %s", (membro,))
    db_support.invalidate_auth_user_cache(membro)
    aderir(membro)
    leave_family(membro)
    linha = db.get_auth_user(membro)
    assert carencia_aberta(linha["past_due_since"], linha["last_payment_status"], _agora()), "o cenário perdeu a carência"
    assert not insight_aviso_aderiu(membro) and _retiradas(membro) == ["familia"]


def test_S02_admin_com_grant_family_vencido_mas_active_retira():
    """Do Manager: grant `family` com `status = 'active'` mas já vencido não segura a adesão contra o admin."""
    from core.admin_dashboard import set_account_plan
    from test_family_access import _entra, _titular
    owner = _titular("pro_max")
    membro = _entra(owner)
    sql("update auth_accounts set plan_selected_at = now() where user_id = %s", (membro,))
    db_support.invalidate_auth_user_cache(membro)
    aderir(membro)
    sql("update plan_grants set ends_at = now() - interval '1 minute' where user_id = %s and source = 'family'", (membro,))
    set_account_plan("free", user_id=membro)
    db_support.invalidate_auth_user_cache(membro)
    assert db.get_auth_user(membro)["plan"] == "free"
    assert not insight_aviso_aderiu(membro) and _retiradas(membro) == ["admin"]


def test_S05_renovacao_essencial_dentro_da_margem_depois_do_plus_pix_e_natural(webhook, monkeypatch):
    """Do Manager, §15.3: Plus (Pix) que vence sobre Essencial é natural; a renovação do Essencial nessa hora
    não é troca de preço (o tier anterior DESTA assinatura já era Essencial)."""
    import frontend.finance_bot_websocket_custom as dashboard
    from _billing_grants_helpers import evt_paid
    from test_billing_webhook_lifecycle import _fake_sub
    uid, post, t = _assinou_pelo_webhook(webhook, "s05m")
    upsert_grant(uid, "pix", f"pix:{uid}:s05", "pro", _agora() - timedelta(days=29), _agora() + timedelta(days=1), _v(),
                 f"e_s05_{uid}")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_ESSENCIAL_MENSAL", "price_ess_s05")
    sub_ess = {"sub_b6": _fake_sub("active", price_id="price_ess_s05")}
    assert post(evt_paid(uid, "sub_b6", t + 10), subs=sub_ess).status_code == 200   # troca de preço, o Pix Plus cobre
    assert insight_aviso_aderiu(uid) and _retiradas(uid) == []
    sql("update plan_grants set ends_at = now() - interval '20 minutes' where user_id = %s and source = 'pix'", (uid,))
    recompute_entitlement(uid)
    db_support.invalidate_auth_user_cache(uid)
    assert insight_aviso_aderiu(uid), "a expiração do Plus retirou"
    assert post(evt_paid(uid, "sub_b6", t + 20), subs=sub_ess).status_code == 200   # renovação do Essencial
    assert insight_aviso_aderiu(uid) and _retiradas(uid) == [], f"renovação do Essencial virou ato: {_retiradas(uid)}"


# ── 5. adesão viva: tela e exportação ─────────────────────────────────────────

@pytest.mark.parametrize("segundos,viva", [(0, True), (1, False)])
def test_adesao_viva_fronteira_exata(segundos, viva):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select pb_insight_adesao_viva(now(), now() - interval '60 minutes' - make_interval(secs => %s)) as v",
                    (segundos,))
        assert cur.fetchone()["v"] is viva


def _cortado_ha(minutos):
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan_expires_at = now() + interval '1 day', plan_selected_at = now() where user_id = %s",
        (uid,))
    aderir(uid)
    _relogio_passou(uid, expira_ha=timedelta(minutes=minutos), coberto_ate=_agora() - timedelta(minutes=minutos))
    return uid


@pytest.mark.parametrize("minutos,mostra", [(59, True), (61, False), (30 * 60, False)])
def test_tela_e_exportacao_so_mostram_adesao_viva(minutos, mostra):
    from db.privacy import build_user_export_zip
    uid = _cortado_ha(minutos)
    r = cliente(uid).get(f"/settings/{uid}/notifications")
    assert r.status_code == 402
    assert (r.json()["detail"].get("insight_alert_email_enabled") is True) is mostra
    linha = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))).read("dados.json"))["dados"]["conta_login"][0]
    assert (linha["insight_aviso_aderiu_em"] is not None) is mostra


def test_adesao_morta_nao_impede_aderir_de_novo(user_id):
    """Adesão morta (perda há horas, sem escrita) e plano de volta sem escrita: ligar de novo funciona."""
    _assinante(user_id)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local session_replication_role = replica")
        cur.execute("update auth_accounts set insight_aviso_coberto_ate = now() - interval '3 hours' where user_id = %s",
                    (user_id,))
        conn.commit()
    assert not insight_aviso_aderiu(user_id)
    d = patch(user_id, insight_alert_email_enabled=True)
    assert d.status_code == 200 and d.json()["insight_alert_email_enabled"] is True
    assert insight_aviso_aderiu(user_id) and _retiradas(user_id) == ["direito"]


# ── 7. Stripe: carência e migração para o Pix ─────────────────────────────────

def test_cancelamento_explicito_durante_a_carencia_retira(webhook):
    from test_billing_dunning_webhook import _INVOICE_SUB, _failed
    uid, post, t = _assinou_pelo_webhook(webhook, "carcanc")
    assert post(_failed(uid, f"evt_cc_{uid}", t + 5), subs={_INVOICE_SUB: _sub(status="past_due")}).status_code == 200
    _relogio_passou(uid, expira_ha=timedelta(days=2), atraso_ha=timedelta(days=2),
                    coberto_ate=_agora() + timedelta(days=5))
    assert insight_aviso_aderiu(uid)
    assert post(_deleted(uid, "sub_b6", t + 10, "cancellation_requested")).status_code == 200
    assert not insight_aviso_aderiu(uid) and _retiradas(uid) == ["cancelamento"]


def test_deleted_da_migracao_cartao_para_pix_nao_retira(webhook):
    """`_stripe_cancel` agenda `cancel_at_period_end`; no `fim` chega o `deleted` e o Pix começa ali."""
    from core.services.cartao_recusado_por_pix import MARCA
    uid, post, t = _assinou_pelo_webhook(webhook, "migra")
    fim = _agora()
    sql("update plan_grants set ends_at = %s where user_id = %s and source = 'stripe'", (fim, uid))
    upsert_grant(uid, "pix", f"pix:{uid}:migra", "pro", fim, fim + timedelta(days=30), _v(), f"evt_migra_{uid}")
    assert post(_deleted(uid, "sub_b6", t + 10, "cancellation_requested", MARCA)).status_code == 200
    assert db.get_auth_user(uid)["plan"] == "pro"
    assert insight_aviso_aderiu(uid) and insight_email_permitido(uid) and _retiradas(uid) == []


# ── 9. INSERT sem adesão ──────────────────────────────────────────────────────

def test_insert_sem_adesao_com_marca_forjada_fica_nula():
    uid = int(uuid.uuid4().int % 900_000_000) + 1
    db.ensure_user(uid)
    sql("insert into auth_accounts (user_id, email, password_hash, plan, insight_aviso_coberto_ate)"
        " values (%s, %s, 'x', 'pro', '2099-01-01')", (uid, f"forja-{uuid.uuid4().hex[:8]}@test.local"))
    assert flags(uid)["coberto"] is None
