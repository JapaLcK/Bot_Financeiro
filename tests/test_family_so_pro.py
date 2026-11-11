"""Plano Família: só o titular Pro (valor 'pro_max'; 'pro' armazenado é o Plus) cria família; o membro recebe Plus.
Postgres real, HTTP nos dois prefixos (fixture `w` de test_api_v2_familia).
Controle negativo: tirar o filtro de tier de `_decidir_cobertura` deixa `test_titular_sem_pro_*` vermelho;
trocar TIER_TO_STORED_PLAN["plus"] por cob[0] no grant_member deixa `test_membro_recebe_plus_*` vermelho.
"""
from datetime import timedelta

import pytest

from _apoio_casa import meta
from _billing_grants_helpers import conta
from core.admin_dashboard import set_account_plan
from core.services import family_access as fa
from core.services.billing_access import recompute_entitlement
from core.services.plan_service import get_plan_tier
from db import family as fam
from db.plan_grants import upsert_grant
from test_api_v2_familia_dados import w as wd  # noqa: F401 (fixture: família A com consentimento do MA1)
from test_api_v2_familia import (  # noqa: F401 (w é fixture)
    _agora, _grants_family, _usuario, _V, aceitar, convite, erro, w)


@pytest.mark.parametrize("plano", ["pro", "plus", "essencial"])
def test_titular_sem_pro_nao_cria_nem_convida_nem_cobre(w, plano):
    novo = _usuario(plano)
    erro(w.chamar(novo, "POST", "/familia"), 402, "family_coverage_required")
    assert fa._cobertura(novo) is None
    fam.create_group(novo)  # grupo criado direto: o convite também é recusado
    erro(w.chamar(novo, "POST", "/familia/convites"), 402, "family_coverage_required")


def test_titular_pro_cria_convida_e_o_membro_recebe_plus(w):
    novo = _usuario("pro_max")
    assert w.chamar(novo, "POST", "/familia").status_code == 201
    membro = _usuario("free")
    assert aceitar(w, membro, convite(w, novo)).status_code == 200
    (g,) = _grants_family(membro)
    assert g["plan_stored"] == "pro" and get_plan_tier(membro) == "plus"  # 'pro' armazenado = Plus
    assert get_plan_tier(novo) == "pro"
    assert abs(g["ends_at"] - fa._cobertura(novo)[1]) < timedelta(seconds=1)  # o fim é o do titular


def test_membro_com_pro_proprio_continua_pro(w):
    novo = _usuario("pro_max")
    w.chamar(novo, "POST", "/familia")
    membro = _usuario("pro_max")
    assert aceitar(w, membro, convite(w, novo)).status_code == 200
    assert get_plan_tier(membro) == "pro" and len(_grants_family(membro)) == 1


def test_trial_pro_cobre(w):
    novo = _usuario("pro_max")
    conta(novo, "pro_max", _agora() + timedelta(days=7), "trialing")
    assert w.chamar(novo, "POST", "/familia").status_code == 201


def test_downgrade_revoga_e_voltar_ao_pro_religa(w):
    novo = _usuario("pro_max")
    w.chamar(novo, "POST", "/familia")
    membro = _usuario("free")
    assert aceitar(w, membro, convite(w, novo)).status_code == 200
    conta(novo, "pro", _agora() + timedelta(days=30), "active")  # Pro -> Plus
    fa.sync_from_owner(novo)
    assert not _grants_family(membro) and get_plan_tier(membro) == "free"
    conta(novo, "pro_max", _agora() + timedelta(days=30), "active")
    fa.sync_from_owner(novo)
    assert len(_grants_family(membro)) == 1 and get_plan_tier(membro) == "plus"


def test_aceite_apos_downgrade_e_recusado_e_nao_consome_o_convite(w):
    novo = _usuario("pro_max")
    w.chamar(novo, "POST", "/familia")
    t = convite(w, novo)
    conta(novo, "pro", _agora() + timedelta(days=30), "active")  # Plus
    u = _usuario("free")
    erro(aceitar(w, u, t), 402, "family_coverage_required")
    assert not _grants_family(u)
    conta(novo, "pro_max", _agora() + timedelta(days=30), "active")
    assert aceitar(w, u, t).status_code == 200


def test_pix_pro_sobre_stripe_plus_cobre(w):
    novo = _usuario("pro")  # Stripe Plus
    erro(w.chamar(novo, "POST", "/familia"), 402, "family_coverage_required")
    _V[0] += 1
    upsert_grant(novo, "pix", f"pix_{novo}", "pro_max", _agora() - timedelta(days=1), _agora() + timedelta(days=30), _V[0])
    recompute_entitlement(novo)
    assert w.chamar(novo, "POST", "/familia").status_code == 201


# ── titular que deixa de ser Pro: leitura do membro e casa fecham (402); vínculo e consentimento ficam ──────────

def _para_plus(uid):
    conta(uid, "pro", _agora() + timedelta(days=30), "active")  # o que o invoice.paid de um price Plus projeta


def _para_essencial(uid):
    set_account_plan("essencial", user_id=uid)


def _para_pro(uid):
    conta(uid, "pro_max", _agora() + timedelta(days=30), "active")


def _vinculos(w):
    from db.connection import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select (select count(*) from family_members where left_at is null) as m,"
                    " (select count(*) from family_consents where member_user_id = %s) as c", (w.ma1,))
        return cur.fetchone()


@pytest.mark.parametrize("rebaixa", [_para_plus, _para_essencial])
def test_titular_rebaixado_perde_leitura_do_membro_e_a_casa_e_volta_com_o_pro(wd, rebaixa):
    w = wd
    ref = meta(w, w.ta, "Reforma")
    for rec in ("agregados", "lancamentos"):
        assert w.ler(w.ta, w.ma1, rec).status_code == 200  # positivo: o titular Pro lê
    assert w.chamar(w.ma2, "GET", "/familia/casa").status_code == 200
    antes = _vinculos(w)
    rebaixa(w.ta)
    for rec in ("agregados", "lancamentos"):
        erro(w.ler(w.ta, w.ma1, rec), 402, "family_coverage_required")
    corpo = {"nome": "x", "alvo": "10"}
    for quem in (w.ta, w.ma1):  # titular e membro (ma1 tem plano próprio, Essencial)
        erro(w.chamar(quem, "GET", "/familia/casa"), 402, "family_coverage_required")
        erro(w.chamar(quem, "POST", "/familia/casa/metas", json=corpo), 402, "family_coverage_required")
        erro(w.chamar(quem, "POST", f"/familia/casa/metas/{ref}/movimentos",
                      json={"tipo": "somar", "valor": "1"}), 402, "family_coverage_required")
    assert _vinculos(w) == antes  # nada foi removido
    _para_pro(w.ta)
    assert w.ler(w.ta, w.ma1).status_code == 200
    assert w.chamar(w.ma1, "GET", "/familia/casa").status_code == 200
    assert w.chamar(w.ta, "POST", "/familia/casa/metas", json=corpo).status_code == 201


def test_membro_continua_gerindo_as_proprias_permissoes_com_titular_rebaixado(wd):
    _para_essencial(wd.ta)
    assert wd.chamar(wd.ma1, "GET", "/familia").json()["permissoes"] == ["agregados", "lancamentos"]
