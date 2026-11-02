"""Plano Família, PR 2 — acesso e propagação (grant source='family'), Postgres real.

Tudo passa pelo caminho de produção: o webhook Stripe (`POST /billing/webhook`), o dreno do Pix
(`entregar`), `accept_invite`/`remove_member`/`set_seat_status` e `recompute_entitlement` de verdade.
Só o Stripe/GA4/e-mail são falsos.

Controles negativos medidos (um a um, resto do grupo verde; restaurado por backup + cmp) estão na
tabela do relato do PR; os positivos do grupo são os testes `*_segue*`/`*_mantem*`/`*_volta*`.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import db
from _billing_grants_helpers import conta, garantir_system_event_logs, ler
from _dreno_pix_helpers import entregar, mundo_externo, nova_cobranca
from db import family as fam
from db import family_grants as fg
from db.connection import get_conn
from db.plan_grants import list_grants, upsert_grant
from test_billing_webhook_lifecycle import _FakeStripe, _fake_sub, _post
import frontend.finance_bot_websocket_custom as dashboard
from core.services import billing_access, family_access as fa
from _billing_grants_helpers import FakeStripeSubs, set_customer

_V = [1_700_000_000]


def _v() -> int:
    _V[0] += 1
    return _V[0]


def _agora():
    return datetime.now(timezone.utc)


def _u(plan="free", dias=None, status="") -> int:
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    conta(uid, plan, _agora() + timedelta(days=dias) if dias else None, status)
    return uid


def _titular(plan="pro_max", dias=30, sub=None) -> int:
    """Titular com assinatura Stripe vigente (grant de verdade) e grupo criado."""
    uid = _u(plan, dias, "active")
    upsert_grant(uid, "stripe", sub or f"sub_{uid}", plan, _agora() - timedelta(days=1),
                 _agora() + timedelta(days=dias), _v())
    fam.create_group(uid)
    return uid


def _entra(owner, membro=None) -> int:
    membro = membro or _u()
    fam.accept_invite(fam.create_invite(owner), membro)
    return membro


def _fam(uid, status="active"):
    return [g for g in list_grants(uid) if g["source"] == "family" and g["status"] == status]


def _plano(uid) -> str:
    return ler(uid)["plan"]


def _sql(q, args=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, args)
        r = cur.fetchall() if cur.description else None
        conn.commit()
        return r


@pytest.fixture()
def wh(monkeypatch):
    garantir_system_event_logs()
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    fake = _FakeStripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return TestClient(dashboard.app), fake


def _evt(tipo, owner, sub="sub_f", **obj):
    return {"type": tipo, "id": f"evt_{uuid.uuid4().hex[:10]}", "created": 1_900_000_000 + _v() % 1000,
            "data": {"object": {"id": sub, "metadata": {"finbot_user_id": str(owner)}, **obj}}}


def _sub_stripe(status="active", cancel=False):
    s = _fake_sub(status)
    s["cancel_at_period_end"] = cancel
    return s


# ── concessão e saída ────────────────────────────────────────────────────────

def test_membro_sem_plano_recebe_o_tier_do_titular_e_volta_a_free_ao_sair():
    owner = _titular("pro_max")
    m = _entra(owner)
    assert _plano(m) == "pro_max"
    g = _fam(m)[0]
    assert g["external_ref"].startswith("family_member:")
    assert abs((g["ends_at"] - ler(owner)["plan_expires_at"]).total_seconds()) < 2
    fam.remove_member(owner, m)
    assert _plano(m) == "free" and not _fam(m)
    assert _plano(owner) == "pro_max"


def test_membro_com_individual_maior_fica_com_o_maior_e_ao_sair_volta_para_a_individual():
    owner = _titular("pro")  # Plus
    m = _u("pro_max", 100, "active")
    upsert_grant(m, "stripe", f"sub_ind_{m}", "pro_max", _agora() - timedelta(days=1),
                 _agora() + timedelta(days=100), _v())
    _entra(owner, m)
    assert _plano(m) == "pro_max"          # o individual maior vence o familiar

    owner2 = _titular("pro_max")
    m2 = _u("essencial", 100, "active")
    upsert_grant(m2, "stripe", f"sub_ind_{m2}", "essencial", _agora() - timedelta(days=1),
                 _agora() + timedelta(days=100), _v())
    _entra(owner2, m2)
    assert _plano(m2) == "pro_max"         # o familiar maior vence o individual
    fam.remove_member(owner2, m2)
    r = ler(m2)
    assert r["plan"] == "essencial"        # volta para a individual, que nunca foi tocada
    assert (r["plan_expires_at"] - _agora()).days >= 99
    assert [g["status"] for g in list_grants(m2) if g["source"] == "stripe"] == ["active"]


def test_convite_sem_cobertura_do_titular_nao_concede():
    owner = _u("free")
    fam.create_group(owner)
    with pytest.raises(fam.FamilyError, match="coverage_required"):  # decisão do dono: sem cobertura, não ocupa vaga
        _entra(owner)
    assert not _sql("select 1 from family_members where role='member' and group_id=%s", (fam.get_group(owner)["id"],))


def test_convite_aceito_com_titular_em_atraso_nao_concede():
    owner = _titular("pro_max")
    conta(owner, "pro_max", _agora() + timedelta(days=30), "past_due")
    m = _u()
    with pytest.raises(fam.FamilyError, match="coverage_required"):
        fam.accept_invite(fam.create_invite(owner), m)
    assert not _fam(m) and _plano(m) == "free"


# ── perda imediata dos membros ───────────────────────────────────────────────

def test_cancel_at_period_end_tira_dos_membros_na_hora_e_o_titular_segue(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m1, m2 = _entra(owner), _entra(owner)
    assert _plano(m1) == _plano(m2) == "pro_max"
    r = _post(client, fake, _evt("customer.subscription.updated", owner),
              subs={"sub_f": _sub_stripe("active", cancel=True)})
    assert r.status_code == 200, r.text
    assert _plano(m1) == _plano(m2) == "free" and not _fam(m1) and not _fam(m2)
    # positivo: o titular segue com o tier até o fim do período
    assert _plano(owner) == "pro_max" and ler(owner)["plan_expires_at"] > _agora()


def test_reativacao_recria_os_grants_sem_duplicar(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    _post(client, fake, _evt("customer.subscription.updated", owner),
          subs={"sub_f": _sub_stripe("active", cancel=True)})
    assert _plano(m) == "free"
    for _ in range(2):  # a reentrega do desfazer não duplica
        r = _post(client, fake, _evt("customer.subscription.updated", owner),
                  subs={"sub_f": _sub_stripe("active", cancel=False)})
        assert r.status_code == 200, r.text
    assert _plano(m) == "pro_max"
    todos = [g for g in list_grants(m) if g["source"] == "family"]
    assert len(todos) == 1 and todos[0]["status"] == "active"


def test_payment_failed_tira_dos_membros_e_invoice_paga_devolve(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    r = _post(client, fake, _evt("invoice.payment_failed", owner, subscription="sub_f", attempt_count=1),
              subs={"sub_f": _sub_stripe("past_due")})
    assert r.status_code == 200, r.text
    assert _plano(m) == "free"
    assert _plano(owner) == "pro_max"      # o titular segue (decisão pré-existente do ramo)
    r = _post(client, fake, _evt("invoice.paid", owner, subscription="sub_f", amount_paid=1990),
              subs={"sub_f": _sub_stripe("active")})
    assert r.status_code == 200, r.text
    # o invoice.paid reescreve o tier do titular (price desconhecido = Plus); o membro o acompanha
    assert _plano(m) == _plano(owner) == "pro" and len(_fam(m)) == 1


def test_subscription_deleted_tira_dos_membros(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    r = _post(client, fake, _evt("customer.subscription.deleted", owner))
    assert r.status_code == 200, r.text
    assert _plano(owner) == "free" and _plano(m) == "free" and not _fam(m)


def test_deleted_de_assinatura_antiga_com_outra_cobrindo_mantem_os_membros(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_nova")
    upsert_grant(owner, "stripe", "sub_velha", "pro_max", _agora() - timedelta(days=40),
                 _agora() + timedelta(days=1), _v())
    m = _entra(owner)
    r = _post(client, fake, _evt("customer.subscription.deleted", owner, sub="sub_velha"))
    assert r.status_code == 200, r.text
    assert _plano(owner) == "pro_max" and _plano(m) == "pro_max"


def test_estorno_pix_tira_dos_membros(user_id, monkeypatch):
    garantir_system_event_logs()
    mundo_externo(monkeypatch)
    owner = user_id
    conta(owner, "free", None, "")
    fam.create_group(owner)
    cobranca = nova_cobranca(owner)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _plano(owner) == "pro_max"
    m = _entra(owner)
    assert _plano(m) == "pro_max"
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)          # positivo: parcial não revoga
    assert _plano(m) == "pro_max" and _plano(owner) == "pro_max"
    entregar("PAYMENT_REFUNDED", cobranca)
    assert _plano(owner) == "free" and _plano(m) == "free" and not _fam(m)


def test_pix_renovado_estende_o_acesso_dos_membros(user_id, monkeypatch):
    garantir_system_event_logs()
    mundo_externo(monkeypatch)
    conta(user_id, "free", None, "")
    fam.create_group(user_id)
    c1 = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", c1)
    m = _entra(user_id)
    antes = _fam(m)[0]["ends_at"]
    c2 = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", c2)
    assert _fam(m)[0]["ends_at"] > antes + timedelta(days=300)
    assert len(_fam(m)) == 1


# ── vagas extras ─────────────────────────────────────────────────────────────

def _grupo_cheio():
    owner = _titular("pro_max")
    gid = fam.get_group(owner)["id"]
    _sql("insert into family_seat_purchases (group_id, source, external_ref, amount_cents, status, event_version)"
         " values (%s,'pix','vaga-1',1000,'paid',10)", (gid,))
    return owner, _entra(owner), _entra(owner), _entra(owner)  # titular + 3 = 4 = base 3 + 1 vaga


def test_vaga_estornada_com_grupo_cheio_corta_exatamente_o_mais_recente():
    owner, m1, m2, m3 = _grupo_cheio()
    assert {_plano(m) for m in (m1, m2, m3)} == {"pro_max"}
    assert fg.set_seat_status("pix", "vaga-1", "refunded", 11) is True
    assert [x["user_id"] for x in fam.list_members(owner) if x["role"] == "member"] == [m1, m2]
    assert _plano(m3) == "free" and not _fam(m3)
    assert _plano(m1) == _plano(m2) == "pro_max" and _plano(owner) == "pro_max"
    assert _sql("select left_reason from family_members where user_id=%s", (m3,))[0]["left_reason"] == "seat_lost"


def test_vaga_estornada_sem_excesso_ou_evento_velho_nao_corta():
    owner, m1, m2, m3 = _grupo_cheio()
    assert fg.set_seat_status("pix", "vaga-1", "refunded", 5) is False   # versão velha: não se aplica
    assert len(fam.list_members(owner)) == 4 and _plano(m3) == "pro_max"
    fam.remove_member(owner, m3)                                          # grupo já cabe sem a vaga
    assert fg.set_seat_status("pix", "vaga-1", "refunded", 12) is True
    assert [x["user_id"] for x in fam.list_members(owner) if x["role"] == "member"] == [m1, m2]


# ── quem não é pagante ───────────────────────────────────────────────────────

def _membro():
    owner = _titular("pro_max")
    return owner, _entra(owner)


def test_membro_family_nao_entra_no_dunning_nem_no_status_de_pagamento(user_id):
    from db import dunning
    _, m = _membro()
    assert ler(m)["last_payment_status"] in ("", None, "inactive") and not dunning.claim_past_due_since(m)


def test_grants_para_precificar_ignora_o_family():
    from db.pix_charges_saga import grants_para_precificar
    _, m = _membro()
    assert grants_para_precificar(m) == []


def test_e_primeira_assinatura_ignora_o_family():
    from db.plan_grants import e_primeira_assinatura
    _, m = _membro()
    assert e_primeira_assinatura(m, "stripe", "sub_x") is True
    upsert_grant(m, "legacy", f"legacy:{m}", "pro", _agora(), _agora() + timedelta(days=5), _v())
    assert e_primeira_assinatura(m, "stripe", "sub_x") is False   # positivo: grant que não é family conta


def test_janela_de_acesso_do_pix_proprio_nao_emenda_no_family():
    from core.services.pix_drain_effects import janela_de_acesso
    _, m = _membro()
    ini, _ = janela_de_acesso({"user_id": m, "credit_cents": 0, "stripe_subscription_id": None,
                               "stripe_period_end_at": None, "duration_days": 365})
    assert ini < _agora() + timedelta(minutes=1)


def test_remarketing_e_pagamento_por_outro_caminho_ignoram_o_family():
    from db import remarketing
    from core.services.payment_reminder import _pago_por_outro_caminho
    owner, m = _membro()
    assert _pago_por_outro_caminho(m) is False
    fam.remove_member(owner, m)
    assert remarketing.ja_comprou(m) is False
    for tabela, col in (("checkout_funnel_events", "user_id"), ("affiliate_commissions", "referred_user_id")):
        assert _sql(f"select 1 from {tabela} where {col}=%s", (m,)) == []


def test_cobertura_vencida_do_titular_nao_concede():
    owner = _titular("pro_max")
    _sql("update auth_accounts set plan_expires_at = now() - interval '1 minute' where user_id=%s", (owner,))
    assert fa._cobertura(owner) is None
    with pytest.raises(fam.FamilyError, match="coverage_required"):
        _entra(owner)


def test_admin_set_plan_preserva_o_grant_family():
    from core.admin_dashboard import set_account_plan
    owner = _titular("pro_max")
    m = _entra(owner)
    set_account_plan("essencial", 1, user_id=m)
    assert len(_fam(m)) == 1                                     # não revogado
    set_account_plan("free", 1, user_id=m)
    assert len(_fam(m)) == 1
    from core.services.billing_access import recompute_entitlement
    recompute_entitlement(m)
    assert _plano(m) == "pro_max"


# ── isolamento ───────────────────────────────────────────────────────────────

def test_operar_o_titular_a_nao_toca_membros_de_b():
    a, b = _titular("pro_max"), _titular("pro_max")
    ma, mb = _entra(a), _entra(b)
    assert fa.revoke_members(a, "teste") == 1
    assert _plano(ma) == "free" and _plano(mb) == "pro_max" and len(_fam(mb)) == 1
    with pytest.raises(fam.FamilyError):
        fam.remove_member(a, mb)
    assert fa.sync_from_owner(a) == 0 and fa.retomar(a) == 1 and fa.enforce_capacity(a) == []
    assert len(_fam(mb)) == 1 and _plano(mb) == "pro_max"
    fam.dissolve_group(a)                                        # dissolver A revoga só os de A
    assert _plano(ma) == "free" and _plano(mb) == "pro_max"


# ── PR 2, rodada do Tester: a classe "o entitlement do titular mudou e os membros não souberam" ──────

def test_admin_set_plan_free_no_titular_derruba_os_membros():
    from core.admin_dashboard import set_account_plan
    owner, m = _membro()
    set_account_plan("essencial", 1, user_id=owner)               # positivo: rebaixar mantém, no tier novo
    assert _plano(m) == "essencial" and len(_fam(m)) == 1
    set_account_plan("free", 1, user_id=owner)
    assert _plano(owner) == "free" and _plano(m) == "free" and not _fam(m)


def test_exclusao_da_conta_do_titular_libera_os_membros():
    from db.privacy import delete_user_data
    owner, m = _membro()
    delete_user_data(owner)
    assert not _fam(m) and _plano(m) == "free"


def test_exclusao_de_um_membro_nao_toca_o_titular_nem_os_outros():
    from db.privacy import delete_user_data
    owner, m = _membro()
    m2 = _entra(owner)
    delete_user_data(m)
    assert _plano(owner) == "pro_max" and _plano(m2) == "pro_max" and len(_fam(m2)) == 1


def test_merge_de_titular_ou_membro_com_acesso_vigente_e_recusado():
    """Por que o merge não precisa liberar membros: `_origem_presa` recusa conta com plano pago vigente."""
    from db.users import MergeRefused, merge_users
    owner, m = _membro()
    for origem in (owner, m):
        with pytest.raises(MergeRefused):
            merge_users(origem, _u())


def test_estorno_de_pix_antigo_com_stripe_cobrindo_mantem_os_membros(user_id, monkeypatch):
    from core.services.billing_access import recompute_entitlement
    garantir_system_event_logs()
    mundo_externo(monkeypatch)
    conta(user_id, "free", None, "")
    fam.create_group(user_id)
    c = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", c)
    upsert_grant(user_id, "stripe", "sub_x", "pro_max", _agora() - timedelta(days=1),
                 _agora() + timedelta(days=60), _v())
    recompute_entitlement(user_id)
    m = _entra(user_id)
    entregar("PAYMENT_REFUNDED", c)
    assert _plano(user_id) == "pro_max" and _plano(m) == "pro_max"


def test_updated_de_assinatura_antiga_cancelando_com_outra_cobrindo_mantem_os_membros(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_nova")
    upsert_grant(owner, "stripe", "sub_velha", "pro_max", _agora() - timedelta(days=40),
                 _agora() + timedelta(days=5), _v())
    m = _entra(owner)
    r = _post(client, fake, _evt("customer.subscription.updated", owner, sub="sub_velha"),
              subs={"sub_velha": _sub_stripe("active", cancel=True)})
    assert r.status_code == 200 and _plano(m) == "pro_max"


def test_updated_com_cancelamento_agendado_da_unica_assinatura_tira_os_membros(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    r = _post(client, fake, _evt("customer.subscription.updated", owner),
              subs={"sub_f": _sub_stripe("active", cancel=True)})
    assert r.status_code == 200 and _plano(m) == "free" and _plano(owner) == "pro_max"


def test_updated_de_quem_nao_tem_grupo_nao_chama_o_stripe(wh):
    client, fake = wh
    sem_grupo = _u("pro_max", 30, "active")                       # o fake levanta KeyError no retrieve
    assert _post(client, fake, _evt("customer.subscription.updated", sem_grupo)).status_code == 200
    assert _post(client, fake, _evt("customer.subscription.updated", _u())).status_code == 200


def test_updated_com_assinatura_inexistente_nao_vira_5xx_mas_falha_transitoria_sim(wh):
    client, fake = wh
    owner = _titular("pro_max")
    m = _entra(owner)

    def falha(codigo):
        class E(Exception):
            code = codigo
        def retrieve(_):
            raise E("x")
        return retrieve

    fake.Subscription.retrieve = staticmethod(falha("resource_missing"))
    assert _post(client, fake, _evt("customer.subscription.updated", owner)).status_code == 200
    assert _plano(m) == "pro_max"
    fake.Subscription.retrieve = staticmethod(falha(None))        # positivo: o transitório continua propagando
    try:
        r = _post(client, fake, _evt("customer.subscription.updated", owner))
        assert r.status_code >= 500
    except Exception as exc:
        assert getattr(exc, "code", "x") is None


def test_membro_com_plano_legado_sem_grant_mantem_o_plano_ao_sair():
    owner = _titular("pro_max")
    m = _u("pro_max", 200, "active")                              # plano pago, nenhum grant
    assert not list_grants(m)
    _entra(owner, m)
    fam.remove_member(owner, m)
    r = ler(m)
    assert r["plan"] == "pro_max" and (r["plan_expires_at"] - _agora()).days >= 199


# ── suspensão persistida e isolamento por membro (2ª passada do Tester) ──────

def test_cancelamento_agendado_persiste_apos_recompute_do_titular_e_reativacao_devolve(wh):
    from core.services.billing_access import recompute_entitlement
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    _post(client, fake, _evt("customer.subscription.updated", owner),
          subs={"sub_f": _sub_stripe("active", cancel=True)})
    recompute_entitlement(owner, origem="varredura")   # a varredura diária / admin não religa
    assert _plano(m) == "free" and not _fam(m)
    assert _plano(owner) == "pro_max"
    with pytest.raises(fam.FamilyError, match="coverage_required"):  # convite novo: sem cobertura, nem entra
        fam.accept_invite(fam.create_invite(owner), _u())
    _post(client, fake, _evt("customer.subscription.updated", owner),
          subs={"sub_f": _sub_stripe("active", cancel=False)})
    assert _plano(m) == "pro_max"


def test_payment_failed_persiste_apos_recompute_e_invoice_paga_devolve(wh):
    from core.services.billing_access import recompute_entitlement
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    _post(client, fake, _evt("invoice.payment_failed", owner, subscription="sub_f", attempt_count=1),
          subs={"sub_f": _sub_stripe("past_due")})
    recompute_entitlement(owner)
    assert _plano(m) == "free" and not _fam(m)
    _post(client, fake, _evt("invoice.paid", owner, subscription="sub_f", amount_paid=1990),
          subs={"sub_f": _sub_stripe("active")})
    assert _plano(m) == _plano(owner) and len(_fam(m)) == 1


def _limpa_past_due(owner):
    _sql("update auth_accounts set last_payment_status = 'active' where user_id = %s", (owner,))


def test_so_a_suspensao_segura_os_membros_com_o_titular_de_volta_a_active(wh):
    # Discrimina a suspensão: o status volta a active (sem pagamento novo) e só `is_suspended` segura.
    from core.services.billing_access import recompute_entitlement
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    _post(client, fake, _evt("invoice.payment_failed", owner, subscription="sub_f", attempt_count=1),
          subs={"sub_f": _sub_stripe("past_due")})
    assert fg.is_suspended(owner) and _plano(m) == "free"
    _limpa_past_due(owner)
    recompute_entitlement(owner)
    assert not fa._cobertura(owner) and _plano(m) == "free" and not _fam(m)  # a suspensão recusa o Stripe


def test_invoice_paid_antigo_reentregue_nao_devolve_os_membros_mas_o_novo_sim(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    T = 1_950_000_000
    paid = _evt("invoice.paid", owner, subscription="sub_f", amount_paid=1990)
    paid["created"] = T
    falha = _evt("invoice.payment_failed", owner, subscription="sub_f", attempt_count=1)
    falha["created"] = T + 100
    _post(client, fake, falha, subs={"sub_f": _sub_stripe("past_due")})
    assert fg.is_suspended(owner) and _plano(m) == "free"
    _post(client, fake, paid, subs={"sub_f": _sub_stripe("active")})   # entregue atrasado: ciclo ANTERIOR à falha
    assert fg.is_suspended(owner)
    novo = _evt("invoice.paid", owner, subscription="sub_f", amount_paid=1990)
    novo["created"] = T + 200
    _post(client, fake, novo, subs={"sub_f": _sub_stripe("active")})
    assert not fg.is_suspended(owner) and _plano(m) == _plano(owner) and len(_fam(m)) == 1


def test_payment_failed_mantem_membros_se_outro_grant_cobre_e_suspende_se_nao(wh):
    client, fake = wh
    com_pix, sem = _titular("pro_max", sub="sub_f"), _titular("pro_max", sub="sub_g")
    m1, m2 = _entra(com_pix), _entra(sem)
    upsert_grant(com_pix, "pix", "pix-cobre", "pro_max", _agora() - timedelta(days=1),
                 _agora() + timedelta(days=60), _v())
    _post(client, fake, _evt("invoice.payment_failed", com_pix, subscription="sub_f", attempt_count=1),
          subs={"sub_f": _sub_stripe("past_due")})
    _post(client, fake, _evt("invoice.payment_failed", sem, sub="sub_g", subscription="sub_g", attempt_count=1),
          subs={"sub_g": _sub_stripe("past_due")})
    assert _plano(m1) == "pro_max" and fg.is_suspended(com_pix)  # marcador: o Stripe encerra, o Pix cobre
    assert _plano(m2) == "free" and fg.is_suspended(sem)


def test_payment_failed_com_grant_legacy_suspende_e_com_admin_vigente_mantem(wh):
    client, fake = wh
    leg, adm = _titular("pro_max", sub="sub_f"), _titular("pro_max", sub="sub_g")
    m1, m2 = _entra(leg), _entra(adm)
    upsert_grant(leg, "legacy", "legacy-x", "pro_max", _agora() - timedelta(days=1),
                 _agora() + timedelta(days=60), _v())
    upsert_grant(adm, "admin", "admin-x", "pro_max", _agora() - timedelta(days=1),
                 _agora() + timedelta(days=60), _v())
    _post(client, fake, _evt("invoice.payment_failed", leg, subscription="sub_f", attempt_count=1),
          subs={"sub_f": _sub_stripe("past_due")})
    _post(client, fake, _evt("invoice.payment_failed", adm, sub="sub_g", subscription="sub_g", attempt_count=1),
          subs={"sub_g": _sub_stripe("past_due")})
    assert _plano(m1) == "free" and fg.is_suspended(leg)
    assert _plano(m2) == "pro_max" and fg.is_suspended(adm)


def test_membro_que_lanca_no_recompute_nao_derruba_titular_nem_os_outros(monkeypatch):
    owner = _titular("pro_max")
    m1, m2 = _entra(owner), _entra(owner)
    real = billing_access.recompute_entitlement

    def quebra(uid, **kw):
        if uid == m1:
            raise RuntimeError("boom")
        return real(uid, **kw)
    monkeypatch.setattr(fa, "recompute_entitlement", quebra)                  # concessão (grant_member)
    monkeypatch.setattr(billing_access, "recompute_entitlement", quebra)      # revogação (recompute_users)
    assert fa.sync_from_owner(owner) == 1 and _plano(m2) == "pro_max"
    assert fa.revoke_members(owner, "t") == 2 and _plano(m2) == "free"       # revogação


def test_falha_na_familia_nao_derruba_o_recompute_do_titular(monkeypatch):
    from core.services.billing_access import recompute_entitlement
    owner = _titular("pro_max")
    _entra(owner)

    def boom(_owner):
        raise RuntimeError("boom")
    monkeypatch.setattr(fa, "sync_from_owner", boom)
    assert recompute_entitlement(owner) is not None   # não lança; o titular segue com o plano dele
    assert _plano(owner) == "pro_max"


def test_exclusao_com_recompute_pos_commit_falhando_nao_lanca(monkeypatch):
    from db.privacy import delete_user_data
    owner, m = _membro()

    def boom(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(billing_access, "recompute_entitlement", boom)
    delete_user_data(owner)   # não lança
    assert not _sql("select 1 from users where id = %s", (owner,))


# ── rodada 2 da revisão: janela do grant, fatura avulsa, grupo vazio, corrida, admin no membro ──────────

def test_pix_com_starts_at_futuro_nao_cobre_o_titular_em_atraso(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    upsert_grant(owner, "pix", "pix-futuro", "pro_max", _agora() + timedelta(days=10),
                 _agora() + timedelta(days=40), _v())            # renovação agendada, ainda não começou
    _post(client, fake, _evt("invoice.payment_failed", owner, subscription="sub_f", attempt_count=1),
          subs={"sub_f": _sub_stripe("past_due")})
    assert fg.is_suspended(owner) and _plano(m) == "free" and not fa._cobertura(owner)
    upsert_grant(owner, "pix", "pix-vigente", "pro_max", _agora() - timedelta(days=1),
                 _agora() + timedelta(days=40), _v())            # positivo: o Pix vigente cobre
    assert fa._cobertura(owner)


def test_fatura_avulsa_falhando_nao_tira_os_membros_de_titular_com_assinatura_saudavel(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    r = _post(client, fake, _evt("invoice.payment_failed", owner, attempt_count=1))   # sem `subscription`
    assert r.status_code == 200 and not fg.is_suspended(owner) and _plano(m) == "pro_max" and len(_fam(m)) == 1


def test_updated_em_grupo_sem_membros_suspende_e_a_reativacao_libera_os_convidados_futuros(wh):
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")                     # grupo criado, 0 membros
    _post(client, fake, _evt("customer.subscription.updated", owner),
          subs={"sub_f": _sub_stripe("active", cancel=True)})
    assert fg.is_suspended(owner)
    m, token = _u(), fam.create_invite(owner)
    with pytest.raises(fam.FamilyError, match="coverage_required"):  # durante a suspensão o aceite é recusado
        fam.accept_invite(token, m)
    _post(client, fake, _evt("customer.subscription.updated", owner),
          subs={"sub_f": _sub_stripe("active", cancel=False)})
    fam.accept_invite(token, m)                                  # o mesmo convite vale de novo
    assert not fg.is_suspended(owner) and _plano(m) == "pro_max"


def _espera_bloqueio_de_lock(timeout=10):
    """Espera (poll) até existir um backend bloqueado em lock de linha: determinístico, sem sleep fixo."""
    import time
    fim = time.time() + timeout
    while time.time() < fim:
        if _sql("select 1 from pg_stat_activity where wait_event_type = 'Lock' and datname = current_database()"):
            return
        time.sleep(0.02)
    raise AssertionError("nenhum backend bloqueado em lock")


def test_saida_do_membro_no_meio_do_grant_nao_recria_o_grant(monkeypatch):
    import threading, time
    owner = _titular("pro_max")
    m = _entra(owner)
    fam.remove_member(owner, m)
    # volta ao grupo como membro novo-ish: simula a corrida com um segundo membro que sai durante o grant
    m2 = _u()
    fam.accept_invite(fam.create_invite(owner), m2)
    fa._revogar(owner, "preparo")                                # zera o grant para o grant_member recriar
    real, disparou, ts = fg.event_version_now, [], []

    def hook():
        if not disparou:
            disparou.append(1)
            t = threading.Thread(target=lambda: fam.remove_member(owner, m2))
            t.start(); ts.append(t)
            _espera_bloqueio_de_lock()                           # a saída está parada no lock que este grant segura
        return real()
    monkeypatch.setattr(fg, "event_version_now", hook)
    fa.grant_member(owner, m2)
    ts[0].join()
    assert not _fam(m2) and _plano(m2) == "free"


def test_admin_baixando_o_plano_individual_do_membro_nao_tira_a_familia():
    from core.admin_dashboard import set_account_plan
    owner = _titular("pro_max")
    m = _entra(owner)
    set_account_plan("essencial", 1, user_id=m)
    assert _plano(m) == "pro_max"                                # positivo: o maior tier (família) segue
    outro = _u("essencial", 30, "active")
    set_account_plan("free", 1, user_id=outro)
    assert _plano(outro) == "free"                               # positivo: quem não é membro rebaixa normal


def test_suspensao_que_commita_durante_o_grant_em_voo_vence_o_grant(monkeypatch):
    import threading, time
    owner = _titular("pro_max")
    m = _entra(owner)
    fa._revogar(owner, "preparo")                                # sem grant, ainda não suspenso: grant_member vai criar
    t = threading.Thread(target=lambda: fa.grant_member(owner, m))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s for update", (owner,))
        t.start(); time.sleep(0.5)                               # o grant passou da cobertura e espera o lock
        cur.execute("update family_groups set members_suspended_at = now() where owner_user_id = %s", (owner,))
        conn.commit()
    t.join()
    assert fg.is_suspended(owner) and not _fam(m) and _plano(m) == "free"


def test_recompute_do_membro_que_falhou_na_revogacao_e_corrigido_pela_varredura(monkeypatch):
    from core.services.billing_access import reprojetar_grants_recentes
    owner = _titular("pro_max")
    m = _entra(owner)
    real = billing_access.recompute_entitlement
    monkeypatch.setattr(billing_access, "recompute_entitlement", lambda uid, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    fa.revoke_members(owner, "teste")                            # revogou o grant, o recompute caiu
    assert _plano(m) == "pro_max"
    monkeypatch.setattr(billing_access, "recompute_entitlement", real)
    reprojetar_grants_recentes(None)                             # varredura diária
    assert _plano(m) == "free"


def test_cancelamento_agendado_com_pix_curto_cobrindo_mantem_e_ao_vencer_o_pix_os_membros_perdem(wh):
    from core.services.billing_access import recompute_entitlement
    client, fake = wh
    owner = _titular("pro_max", sub="sub_f")
    m = _entra(owner)
    upsert_grant(owner, "pix", f"pix-{owner}", "pro_max", _agora() - timedelta(days=1), _agora() + timedelta(days=3), _v())
    _post(client, fake, _evt("customer.subscription.updated", owner),
          subs={"sub_f": _sub_stripe("active", cancel=True)})
    assert _plano(m) == "pro_max"  # positivo: o Pix vigente segue cobrindo
    _sql("update plan_grants set ends_at = now() - interval '1 minute' where user_id = %s and source = 'pix'", (owner,))
    recompute_entitlement(owner)
    assert _plano(owner) == "pro_max" and _plano(m) == "free"  # Stripe pago não volta a cobrir os membros
    recompute_entitlement(owner)
    assert _plano(m) == "free" and not _fam(m)
    _post(client, fake, _evt("customer.subscription.updated", owner),
          subs={"sub_f": _sub_stripe("active", cancel=False)})
    assert _plano(m) == "pro_max"  # reativação devolve


# ── revisão independente do PR 2: varredura como evento, ex_ref, ordem de lock, conexão fora do lock ─────────


@pytest.mark.parametrize("recompute_cai", [True, False])
def test_membro_com_stripe_proprio_mais_curto_que_sai_volta_ao_dele_pela_varredura(monkeypatch, recompute_cai):
    """Sem a retentativa como evento, a varredura recusa encurtar a data e o membro fica com a família."""
    owner = _titular("pro_max", 30)
    m = _u("pro", 10, "active"); set_customer(m, f"cus_{m}")
    fim = _agora() + timedelta(days=10)
    upsert_grant(m, "stripe", f"sub_m{m}", "pro", _agora() - timedelta(days=20), fim, _v())
    fam.accept_invite(fam.create_invite(owner), m)
    assert _plano(m) == "pro_max"
    real = billing_access.recompute_entitlement
    if recompute_cai:
        monkeypatch.setattr(billing_access, "recompute_entitlement",
                            lambda uid, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    fam.remove_member(owner, m)                                  # o recompute imediato cai (ou não)
    monkeypatch.setattr(billing_access, "recompute_entitlement", real)
    assert _plano(m) == ("pro_max" if recompute_cai else "pro")
    viva = {"id": f"sub_m{m}", "status": "active", "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(sys.modules, "stripe", FakeStripeSubs(ativa=viva))
    billing_access.reprojetar_grants_recentes(None)
    assert _plano(m) == "pro" and ler(m)["plan_expires_at"] <= fim + timedelta(seconds=5)


def test_ex_membro_com_stripe_proprio_vivo_e_renovacao_perdida_e_reparado_pela_varredura(monkeypatch):
    """A varredura precisa rodar ANTES do evento: só ela consulta o Stripe e repara o grant próprio vencido."""
    owner = _titular("pro_max", 30)
    m = _u("pro", 5, "active"); set_customer(m, f"cus_{m}")
    upsert_grant(m, "stripe", f"sub_m{m}", "pro", _agora() - timedelta(days=25), _agora() + timedelta(days=5), _v())
    fam.accept_invite(fam.create_invite(owner), m)
    fam.remove_member(owner, m)
    _sql("update plan_grants set ends_at = now() - interval '1 hour' where user_id = %s and source = 'stripe'", (m,))
    _sql("update auth_accounts set plan = 'pro', plan_expires_at = now() + interval '1 hour' where user_id = %s", (m,))
    fim = _agora() + timedelta(days=25)
    viva = {"id": f"sub_m{m}", "status": "active", "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(sys.modules, "stripe", FakeStripeSubs(ativa=viva))
    billing_access.reprojetar_grants_recentes(None)
    assert _plano(m) == "pro" and ler(m)["plan_expires_at"] > _agora() + timedelta(days=24)


def test_ex_membro_com_stripe_fora_do_ar_nao_e_rebaixado_pela_passada_de_evento(monkeypatch):
    """Sem a consulta ao Stripe a varredura segura; a passada "evento" não pode reduzir por conta própria."""
    owner = _titular("pro_max", 30)
    m = _u("pro", 5, "active"); set_customer(m, f"cus_{m}")
    upsert_grant(m, "stripe", f"sub_m{m}", "pro", _agora() - timedelta(days=25), _agora() + timedelta(days=5), _v())
    fam.accept_invite(fam.create_invite(owner), m)
    fam.remove_member(owner, m)
    _sql("update plan_grants set ends_at = now() - interval '1 hour' where user_id = %s and source = 'stripe'", (m,))
    _sql("update auth_accounts set plan = 'pro', plan_expires_at = now() + interval '1 hour' where user_id = %s", (m,))
    monkeypatch.setitem(sys.modules, "stripe", FakeStripeSubs(erro=True))
    billing_access.reprojetar_grants_recentes(None)
    assert _plano(m) == "pro"


def test_grant_member_revalida_a_cobertura_independente_depois_do_lock(monkeypatch):
    """Invariante (também vale na main): Pix estornado antes do lock não cobre a família suspensa."""
    owner = _titular("pro_max", 30); m = _entra(owner)
    fa._revogar(owner, "preparo")
    fg.set_suspended(owner, True)
    upsert_grant(owner, "pix", f"pix-{owner}", "pro_max", _agora() - timedelta(days=1), _agora() + timedelta(days=5), _v())
    real_lock = fam._lock_group

    def lock(cur, o):                                            # Pix estornado entre a checagem de cobertura e o lock
        _sql("update plan_grants set status = 'revoked', revoked_at = now() where user_id = %s and source = 'pix'", (o,))
        return real_lock(cur, o)
    monkeypatch.setattr(fam, "_lock_group", lock)
    assert fa.grant_member(owner, m) is False and _plano(m) == "free" and not _fam(m)


def test_dissolver_ou_suspender_a_nao_revoga_o_grant_do_ex_membro_que_esta_em_b():
    a, b = _titular("pro_max", 30), _titular("pro_max", 30)
    m = _entra(a)
    fam.remove_member(a, m)
    _entra(b, m)
    assert _plano(m) == "pro_max" and len(_fam(m)) == 1
    fa.revoke_members(a, "x")                                    # suspensão/revogação de A
    assert _plano(m) == "pro_max" and len(_fam(m)) == 1
    fam.dissolve_group(a)
    assert _plano(m) == "pro_max" and len(_fam(m)) == 1
    fam.dissolve_group(b)                                        # positivo: o grupo certo derruba
    assert _plano(m) == "free" and not _fam(m)


def _bloqueia_no_lock_do_grupo(owner, chamada):
    """Segura o lock do grupo numa conexão, dispara `chamada` numa thread e devolve se ela esperou o lock."""
    import threading
    t = threading.Thread(target=chamada)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s for update", (owner,))
        t.start()
        try:  # determinístico: a thread aparece bloqueada em lock no Postgres, não "ainda viva após um sleep"
            _espera_bloqueio_de_lock(timeout=5)
            esperou = True
        except AssertionError:
            esperou = False
        conn.commit()
    t.join()
    return esperou


def test_revogar_os_grants_do_titular_espera_o_lock_do_grupo():
    owner = _titular("pro_max"); _entra(owner)
    assert _bloqueia_no_lock_do_grupo(owner, lambda: fg.revoke_owner_grants(owner, "t"))
    # positivo: depois do lock a revogação acontece
    assert not _sql("select 1 from plan_grants where source = 'family' and status = 'active'"
                    " and external_ref in (select 'family_member:' || id from family_members where group_id ="
                    " (select id from family_groups where owner_user_id = %s))", (owner,))


def test_liberar_o_titular_excluido_espera_o_lock_do_grupo():
    owner = _titular("pro_max"); _entra(owner)

    def libera():
        with get_conn() as conn, conn.cursor() as cur:
            fg.release_user(cur, owner, "family_owner_deleted")
            conn.commit()
    assert _bloqueia_no_lock_do_grupo(owner, libera)


def test_grant_em_voo_depois_da_revogacao_nao_concede(monkeypatch):
    """A cobertura lida antes do lock é revalidada depois dele: titular que perdeu a cobertura e teve a revogação
    commitada no meio não deixa o grant em voo ressuscitar o membro."""
    owner = _titular("pro_max", 30)
    m = _u()
    real, feito = fa._cobertura, []

    def cob(o):
        c = real(o)
        if o != owner or feito:
            return c
        feito.append(1)
        _sql("update plan_grants set status='revoked', revoked_at=now() where user_id=%s", (o,))
        _sql("update auth_accounts set plan='free', plan_expires_at=null where user_id=%s", (o,))
        fa._revogar(o, "family_owner_sem_cobertura")
        return c
    monkeypatch.setattr(fa, "_cobertura", cob)
    fam.accept_invite(fam.create_invite(owner), m)
    assert not _fam(m) and _plano(m) == "free"


def test_sem_grant_ex_membro_com_assinatura_viva_segue_pro(monkeypatch):
    """Veredito `sem_grant` (assinatura Stripe viva sem grant nosso) segura: a passada "evento" dos revogados
    family não pode rebaixar."""
    owner = _titular("pro_max", 30)
    m = _u("pro", 5, "active"); set_customer(m, f"cus_{m}")
    upsert_grant(m, "stripe", f"sub_old{m}", "pro", _agora() - timedelta(days=25), _agora() + timedelta(days=5), _v())
    fam.accept_invite(fam.create_invite(owner), m)
    fam.remove_member(owner, m)
    _sql("update plan_grants set ends_at = now() - interval '1 hour' where user_id = %s and source = 'stripe'", (m,))
    _sql("update auth_accounts set plan = 'pro', plan_expires_at = now() + interval '1 hour' where user_id = %s", (m,))
    fim = _agora() + timedelta(days=25)
    viva = {"id": f"sub_new{m}", "status": "active", "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(sys.modules, "stripe", FakeStripeSubs(ativa=viva))
    billing_access.reprojetar_grants_recentes(None)
    assert _plano(m) == "pro"


def test_grant_member_nao_abre_segunda_conexao_com_o_lock_do_grupo(monkeypatch):
    owner = _titular("pro_max")
    m = _entra(owner)
    fa._revogar(owner, "preparo")
    fg.set_suspended(owner, True)                                # suspenso: só Pix/admin cobre (precisa de list_grants)
    upsert_grant(owner, "pix", f"pix-{owner}", "pro_max", _agora() - timedelta(days=1), _agora() + timedelta(days=5), _v())
    em_lock, chamadas = [], []
    real_lock, real_list = fam._lock_group, fa.list_grants

    def lock(cur, o):
        r = real_lock(cur, o); em_lock.append(1); return r
    monkeypatch.setattr(fam, "_lock_group", lock)
    monkeypatch.setattr(fa, "list_grants", lambda *a, **k: (chamadas.append(bool(em_lock)), real_list(*a, **k))[1])
    assert fa.grant_member(owner, m) is True and em_lock       # positivo: o Pix cobre e o grant sai
    assert chamadas and not any(chamadas)                        # nenhuma list_grants depois do lock


def test_aceite_cujo_grant_lanca_depois_do_commit_nao_propaga_e_o_membro_fica(monkeypatch):
    owner = _titular("pro_max")
    m = _u()
    monkeypatch.setattr(fa, "grant_member", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    gid = fam.accept_invite(fam.create_invite(owner), m)         # não lança
    assert gid and [x["user_id"] for x in fam.list_members(owner) if x["user_id"] == m]
