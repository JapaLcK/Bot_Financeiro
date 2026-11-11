"""PL-06 B6, §12 a §14 do plano: expiração natural guarda a adesão por 1 hora; ato explícito retira na hora.

Testes M1 a M14 do §13.5 e as decisões P10 (b) e P11 (a) do §14, pelos caminhos reais: varredura de grants
(`reprojetar_grants_recentes`), webhook do Stripe, dreno do Pix, admin, rotas.

O RELÓGIO: o gatilho compara com o `now()` do Postgres, que nenhum relógio falso de Python alcança, e o repo
não tem padrão para congelá-lo. Por isso "o tempo passou" é mover as datas para trás numa transação com
`session_replication_role = replica` (o gatilho não roda nela, que é o que acontece quando ninguém escreve).
A fronteira exata de 60 minutos usa a mesma transação para mover a marca e escrever o pagamento: `now()` é o
instante de início da transação, então "marca = now() - 60 min" fica exato, sem `sleep`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import db
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from _billing_grants_helpers import garantir_system_event_logs
from _insights_adesao_apoio import aderir, ambiente, cliente, eventos, flags, get, patch, sql  # noqa: F401
from conftest import usuario_pagante
from core.services.billing_access import recompute_entitlement, reprojetar_grants_recentes
from db.plan_grants import upsert_grant
from db.reports import insight_aviso_aderiu, insight_email_permitido
from test_family_access import wh  # noqa: F401 — fixture
from test_insights_adesao_direito import _assinante, _assinou_pelo_webhook, _relogio_passou, _sub, webhook  # noqa: F401

pytestmark = pytest.mark.usefixtures("ambiente")
_V = [2_000_000]


def _agora():
    return datetime.now(timezone.utc)


def _v():
    _V[0] += 1
    return _V[0]


def _retiradas(uid):
    return [e["details"].get("motivo") for e in eventos(uid) if e["event"] == "insight_aviso_retirada"]


# ── Pix pela varredura real (M1 a M4) ─────────────────────────────────────────

def _assinante_pix():
    garantir_system_event_logs()
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan = 'free', plan_expires_at = null, plan_selected_at = now(),"
        " stripe_customer_id = null where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    upsert_grant(uid, "pix", f"pix:{uid}:1", "pro", _agora() - timedelta(days=30), _agora() + timedelta(days=1), _v(),
                 f"evt_pix1_{uid}")
    recompute_entitlement(uid)
    aderir(uid)
    return uid


def _pix_venceu_e_a_varredura_rodou(uid, minutos):
    """O grant venceu há `minutos`; a varredura de 60 s grava `free` agora."""
    venceu = _agora() - timedelta(minutes=minutos)
    sql("update plan_grants set ends_at = %s where user_id = %s", (venceu, uid))
    _relogio_passou(uid, expira_ha=timedelta(minutes=minutos), coberto_ate=venceu)
    assert reprojetar_grants_recentes(venceu - timedelta(minutes=1)) >= 1
    db_support.invalidate_auth_user_cache(uid)
    assert db.get_auth_user(uid)["plan"] == "free", "a varredura não gravou a perda"
    return venceu


def _paga_pix(uid):
    upsert_grant(uid, "pix", f"pix:{uid}:2", "pro", _agora(), _agora() + timedelta(days=30), _v(), f"evt_pix2_{uid}")
    recompute_entitlement(uid)
    db_support.invalidate_auth_user_cache(uid)


def test_M1_pix_varredura_guarda_a_marca_no_vencimento_e_pagar_em_20_min_mantem():
    uid = _assinante_pix()
    venceu = _pix_venceu_e_a_varredura_rodou(uid, 5)
    assert insight_aviso_aderiu(uid), "a varredura retirou na hora: a margem virou 60 s"
    assert abs((flags(uid)["coberto"] - venceu).total_seconds()) < 2, "a marca ficou no horário da varredura"
    assert _retiradas(uid) == []
    _relogio_passou(uid, coberto_ate=_agora() - timedelta(minutes=20))   # passaram 20 min da perda
    _paga_pix(uid)
    assert insight_aviso_aderiu(uid) and insight_email_permitido(uid)
    assert flags(uid)["coberto"] > _agora() + timedelta(days=29)


@pytest.mark.parametrize("minutos,fica", [(59, True), (61, False), (75, False)])
def test_M2_M3_pix_pago_perto_do_limite(minutos, fica):
    uid = _assinante_pix()
    _pix_venceu_e_a_varredura_rodou(uid, 5)
    _relogio_passou(uid, coberto_ate=_agora() - timedelta(minutes=minutos))
    _paga_pix(uid)
    assert insight_aviso_aderiu(uid) is fica
    assert _retiradas(uid) == ([] if fica else ["direito"])


def test_M3_pix_pago_75_min_depois_sem_varredura_no_meio():
    """Sem escrita nenhuma no intervalo: quem limpa é a escrita do pagamento (passo 4)."""
    uid = _assinante_pix()
    venceu = _agora() - timedelta(minutes=75)
    sql("update plan_grants set ends_at = %s where user_id = %s", (venceu, uid))
    _relogio_passou(uid, expira_ha=timedelta(minutes=75), coberto_ate=venceu)
    _paga_pix(uid)
    assert not insight_aviso_aderiu(uid) and _retiradas(uid) == ["direito"]


@pytest.mark.parametrize("segundos,fica", [(0, True), (1, False)])
def test_M2_fronteira_exata_de_60_minutos(segundos, fica):
    """`now() > marca + margem` retira: 60 min exatos ficam, 60 min + 1 s saem. Uma transação só, sem `sleep`."""
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan_expires_at = now() + interval '1 day' where user_id = %s", (uid,))
    aderir(uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local session_replication_role = replica")
        cur.execute("update auth_accounts set plan = 'free', plan_expires_at = null,"
                    " insight_aviso_coberto_ate = now() - interval '60 minutes' - make_interval(secs => %s)"
                    " where user_id = %s", (segundos, uid))
        cur.execute("set local session_replication_role = origin")
        cur.execute("update auth_accounts set plan = 'pro', plan_expires_at = now() + interval '30 days'"
                    " where user_id = %s", (uid,))
        conn.commit()
    assert insight_aviso_aderiu(uid) is fica


def test_M4_durante_a_margem_o_portao_fecha_e_a_tela_oferece_desligar():
    uid = _assinante_pix()
    _pix_venceu_e_a_varredura_rodou(uid, 5)
    assert insight_aviso_aderiu(uid) and not insight_email_permitido(uid)
    r = cliente(uid).get(f"/settings/{uid}/notifications")
    assert r.status_code == 402 and r.json()["detail"]["insight_alert_email_enabled"] is True


# ── Stripe (M5 a M8, P11) ─────────────────────────────────────────────────────

def _deleted(uid, sub, t, reason=None, comment=None):
    obj = {"id": sub, "metadata": {"finbot_user_id": str(uid)}}
    detalhes = {k: v for k, v in (("reason", reason), ("comment", comment)) if v}
    if detalhes:
        obj["cancellation_details"] = detalhes
    return {"type": "customer.subscription.deleted", "id": f"evt_del_{t}", "created": t, "data": {"object": obj}}


def test_M5_cancelamento_imediato_retira_na_hora(webhook):
    uid, post, t = _assinou_pelo_webhook(webhook, "m5")
    assert post(_deleted(uid, "sub_b6", t + 10, "cancellation_requested")).status_code == 200
    assert not insight_aviso_aderiu(uid) and _retiradas(uid) == ["cancelamento"]


def test_M5_controle_sem_reason_nao_retira(webhook):
    uid, post, t = _assinou_pelo_webhook(webhook, "m5b")
    assert post(_deleted(uid, "sub_b6", t + 10)).status_code == 200
    assert insight_aviso_aderiu(uid) and _retiradas(uid) == []


def test_M6_cancelamento_de_quem_foi_para_o_pix_nao_retira(webhook):
    """`cartao_recusado_por_pix` cancela a assinatura de cartão de quem pagou Pix: a pessoa segue coberta."""
    from core.services.cartao_recusado_por_pix import MARCA
    uid, post, t = _assinou_pelo_webhook(webhook, "m6")
    upsert_grant(uid, "pix", f"pix:{uid}:m6", "pro", _agora(), _agora() + timedelta(days=60), _v(), f"evt_pixm6_{uid}")
    assert post(_deleted(uid, "sub_b6", t + 10, "cancellation_requested", MARCA)).status_code == 200
    assert db.get_auth_user(uid)["plan"] == "pro"
    assert insight_aviso_aderiu(uid) and insight_email_permitido(uid) and _retiradas(uid) == []


def test_M6_troca_de_assinatura_nao_retira(webhook):
    from _billing_grants_helpers import evt_checkout
    uid, post, t = _assinou_pelo_webhook(webhook, "m6b")
    assert post(evt_checkout(uid, "sub_b6_nova", t + 5, f"cs_nova_{uid}"), subs={"sub_b6_nova": _sub()}).status_code == 200
    assert post(_deleted(uid, "sub_b6", t + 10, "cancellation_requested")).status_code == 200
    assert insight_aviso_aderiu(uid) and _retiradas(uid) == []


@pytest.mark.parametrize("minutos,fica", [(30, True), (120, False)])
def test_M7_payment_failed_e_natural_a_margem_conta_do_fim_da_carencia(webhook, minutos, fica):
    from _billing_grants_helpers import evt_checkout
    uid, post, t = _assinou_pelo_webhook(webhook, f"m7{minutos}")
    fim = _agora() - timedelta(minutes=minutos)
    _relogio_passou(uid, expira_ha=timedelta(days=8), coberto_ate=fim)   # carência acabou há `minutos`
    assert post(_deleted(uid, "sub_b6", t + 10, "payment_failed")).status_code == 200
    assert post(evt_checkout(uid, "sub_b6_volta", t + 20, f"cs_volta_{uid}"), subs={"sub_b6_volta": _sub()}).status_code == 200
    assert db.get_auth_user(uid)["plan_expires_at"] > _agora()
    assert insight_aviso_aderiu(uid) is fica
    assert _retiradas(uid) == ([] if fica else ["direito"])


def test_M8_P10_contestacao_no_stripe_retira_na_hora(webhook):
    uid, post, t = _assinou_pelo_webhook(webhook, "m8")
    assert post(_deleted(uid, "sub_b6", t + 10, "payment_disputed")).status_code == 200
    assert not insight_aviso_aderiu(uid) and _retiradas(uid) == ["contestacao"]


def test_P11_cancelamento_ao_fim_do_periodo(webhook):
    """Avisos até o fim; no fim retira sem margem; desfazer antes do fim não muda nada."""
    uid, post, t = _assinou_pelo_webhook(webhook, "p11")
    sub_cancelando = dict(_sub(), cancel_at_period_end=True)
    upd = {"type": "customer.subscription.updated", "id": f"evt_upd_{t}", "created": t + 5,
           "data": {"object": {"id": "sub_b6", "metadata": {"finbot_user_id": str(uid)}, "cancel_at_period_end": True}}}
    assert post(upd, subs={"sub_b6": sub_cancelando}).status_code == 200
    assert insight_aviso_aderiu(uid) and insight_email_permitido(uid), "até o fim do período os avisos seguem"
    desfaz = {**upd, "id": f"evt_upd2_{t}", "created": t + 6,
              "data": {"object": {**upd["data"]["object"], "cancel_at_period_end": False}}}
    assert post(desfaz, subs={"sub_b6": _sub()}).status_code == 200
    assert insight_aviso_aderiu(uid) and _retiradas(uid) == []
    sql("update plan_grants set ends_at = now() - interval '1 minute' where user_id = %s", (uid,))   # o período acabou
    assert post(_deleted(uid, "sub_b6", t + 10, "cancellation_requested")).status_code == 200
    assert not insight_aviso_aderiu(uid) and _retiradas(uid) == ["cancelamento"]


# ── P10: estorno do Pix pelo dreno real ───────────────────────────────────────

@pytest.fixture()
def externo(monkeypatch):
    from _dreno_pix_helpers import mundo_externo
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _pix_pago_pelo_dreno(uid):
    from _dreno_pix_helpers import entregar, nova_cobranca
    sql("update auth_accounts set plan = 'free', plan_expires_at = null, plan_selected_at = now(),"
        " stripe_customer_id = null where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    cobranca = nova_cobranca(uid)
    entregar("PAYMENT_RECEIVED", cobranca)
    db_support.invalidate_auth_user_cache(uid)
    assert db.get_auth_user(uid)["plan"] == "pro_max"
    aderir(uid)
    return cobranca


def test_P10_estorno_total_retira_na_hora_e_pagar_30_min_depois_nao_devolve(user_id, externo):
    from _dreno_pix_helpers import entregar
    from _billing_grants_helpers import conta
    conta(user_id, "free", None)
    cobranca = _pix_pago_pelo_dreno(user_id)
    entregar("PAYMENT_REFUNDED", cobranca)
    assert not insight_aviso_aderiu(user_id) and _retiradas(user_id) == ["estorno"]
    upsert_grant(user_id, "pix", f"pix:{user_id}:volta", "pro", _agora(), _agora() + timedelta(days=30), _v(),
                 f"evt_volta_{user_id}")
    recompute_entitlement(user_id)
    assert not insight_aviso_aderiu(user_id), "estorno não tem a margem: precisa ligar de novo"


def test_P10_estorno_parcial_que_preserva_o_direito_nao_retira(user_id, externo):
    from _dreno_pix_helpers import entregar
    from _billing_grants_helpers import conta
    conta(user_id, "free", None)
    cobranca = _pix_pago_pelo_dreno(user_id)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert insight_aviso_aderiu(user_id) and insight_email_permitido(user_id) and _retiradas(user_id) == []


# ── downgrade, admin, ato da pessoa, coluna protegida, selo (M9 a M13) ────────

def test_M9_downgrade_pela_troca_de_preco_retira_na_hora(webhook, monkeypatch):
    from _billing_grants_helpers import evt_paid
    from test_billing_webhook_lifecycle import _fake_sub
    uid, post, t = _assinou_pelo_webhook(webhook, "m9")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_ESSENCIAL_MENSAL", "price_essencial_b6")
    assert post(evt_paid(uid, "sub_b6", t + 10),
                subs={"sub_b6": _fake_sub("active", price_id="price_essencial_b6")}).status_code == 200
    assert db.get_auth_user(uid)["plan"] == "essencial"
    assert not insight_aviso_aderiu(uid) and _retiradas(uid) == ["downgrade"]


def test_M9_admin_para_essencial_dentro_da_margem_retira(user_id):
    from core.admin_dashboard import set_account_plan
    _assinante(user_id)
    _relogio_passou(user_id, expira_ha=timedelta(minutes=20), coberto_ate=_agora() - timedelta(minutes=20))
    set_account_plan("essencial", 12, user_id=user_id)
    assert not insight_aviso_aderiu(user_id) and _retiradas(user_id) == ["admin"]


def test_M10_admin_para_free_com_plano_vigente_retira_na_hora(user_id):
    from core.admin_dashboard import set_account_plan
    _assinante(user_id)
    set_account_plan("free", user_id=user_id)
    assert not insight_aviso_aderiu(user_id) and _retiradas(user_id) == ["admin"]


def test_M11_desligar_agora_e_reset_dentro_da_margem_retiram(user_id):
    from db.users import _hash_password
    _assinante(user_id)
    sql("update auth_accounts set password_hash = %s where user_id = %s", (_hash_password("Senha-B6-forte!9"), user_id))
    _relogio_passou(user_id, expira_ha=timedelta(minutes=20), coberto_ate=_agora() - timedelta(minutes=20))
    assert patch(user_id, insight_alert_email_enabled=False).json() == {"ok": True, "insight_alert_email_enabled": False}
    assert not insight_aviso_aderiu(user_id)
    uid = usuario_pagante("pro")
    aderir(uid)
    sql("update auth_accounts set password_hash = %s where user_id = %s", (_hash_password("Senha-B6-forte!9"), uid))
    _relogio_passou(uid, expira_ha=timedelta(minutes=20), coberto_ate=_agora() - timedelta(minutes=20))
    assert cliente(uid).post("/settings/reset", json={"password": "Senha-B6-forte!9"}).status_code == 200
    assert not insight_aviso_aderiu(uid)


@pytest.mark.parametrize("valor", ["2099-01-01", None])
def test_M12_escrita_direta_da_marca_vira_o_valor_calculado(user_id, valor):
    _assinante(user_id, dias=10)
    antes = flags(user_id)["coberto"]
    sql("update auth_accounts set insight_aviso_coberto_ate = %s where user_id = %s", (valor, user_id))
    assert flags(user_id)["coberto"] == antes
    sem = usuario_pagante("pro")
    sql("update auth_accounts set insight_aviso_coberto_ate = %s where user_id = %s", ("2099-01-01", sem))
    assert flags(sem)["coberto"] is None


def test_M13_selo_de_carencia_sem_adesao_e_nulo():
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan = 'free', last_payment_status = 'past_due', past_due_since = now(),"
        " plan_selected_at = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    d = get(uid)
    assert d["insight_alert_email_available"] is False and d["insight_alert_email_paused_notice"] is None


# ── família (M14) ─────────────────────────────────────────────────────────────

def test_M14_titular_cancela_e_o_membro_paga_a_propria_em_20_min_fica(wh):
    from test_family_access import _entra, _sub_stripe, _titular
    from test_billing_webhook_lifecycle import _post
    client, fake = wh
    owner = _titular("pro_max", sub="sub_fam_b6")
    membro = _entra(owner)
    sql("update auth_accounts set plan_selected_at = now() where user_id = %s", (membro,))
    db_support.invalidate_auth_user_cache(membro)
    aderir(membro)
    upd = {"type": "customer.subscription.updated", "id": f"evt_fam_{membro}", "created": 1_950_000_000,
           "data": {"object": {"id": "sub_fam_b6", "metadata": {"finbot_user_id": str(owner)}}}}
    assert _post(client, fake, upd, subs={"sub_fam_b6": _sub_stripe("active", cancel=True)}).status_code == 200
    db_support.invalidate_auth_user_cache(membro)
    assert db.get_auth_user(membro)["plan"] == "free", "o membro saiu na hora"
    assert insight_aviso_aderiu(membro), "perda do membro é natural (ato do titular)"
    _relogio_passou(membro, coberto_ate=_agora() - timedelta(minutes=20))
    upsert_grant(membro, "pix", f"pix:{membro}:m14", "pro", _agora(), _agora() + timedelta(days=30), _v(), f"evt_m14_{membro}")
    recompute_entitlement(membro)
    assert insight_aviso_aderiu(membro) and insight_email_permitido(membro)


@pytest.mark.parametrize("segundos,fica", [(0, True), (1, False)])
def test_M2_fronteira_exata_numa_escrita_de_perda(segundos, fica):
    """A outra comparação da fronteira (passo 7): a escrita que GRAVA a perda, com a marca a 60 min exatos."""
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan_expires_at = now() + interval '1 day' where user_id = %s", (uid,))
    aderir(uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local session_replication_role = replica")
        cur.execute("update auth_accounts set plan_expires_at = now() - interval '60 minutes' - make_interval(secs => %s),"
                    " insight_aviso_coberto_ate = now() - interval '60 minutes' - make_interval(secs => %s)"
                    " where user_id = %s", (segundos, segundos, uid))
        cur.execute("set local session_replication_role = origin")
        cur.execute("update auth_accounts set plan = 'free', plan_expires_at = null where user_id = %s", (uid,))
        conn.commit()
    # A coluna, não a adesão viva: na leitura, o `now()` já passou da fronteira.
    assert (flags(uid)["aderiu"] is not None) is fica
