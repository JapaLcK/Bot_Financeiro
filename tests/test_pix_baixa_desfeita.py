"""#1267: `PAYMENT_RECEIVED_IN_CASH_UNDONE` (baixa manual desfeita).

Revoga o acesso, alerta e REABRE a cobrança; pagamento confirmado depois (o GET
ao Asaas diz RECEIVED) concede de novo sem repetir Purchase/e-mail. Banco real,
pelo `drenar_evento`; só o mundo externo é mockado (`externo["get_status"]`).
Controles: tirar o `delete` do grant (`reabrir_baixa`) ou o `do update` do upsert
deixa vermelho o teste do Pix real; tirar a guarda do marcador, o RECEIVED
reentregue; tirar o GET do `desfazer_baixa`, o UNDONE pós-Pix.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, evento, ler, mundo_externo, nova_cobranca
from _pix_checkout_helpers import asaas_falso, vendavel  # noqa: F401 — fixtures
from db.connection import get_conn
from db.plan_grants import list_grants, upsert_grant

UNDONE = "PAYMENT_RECEIVED_IN_CASH_UNDONE"
_EXTRAS = [{"price": "price_cad_a", "url": "https://drive.test/a", "nome": "Caderno A",
            "valor_cents": 1990}]


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _pix(uid):
    return [g for g in list_grants(uid) if g["source"] == "pix"]


def _ativos(uid):
    return [g for g in _pix(uid) if g["status"] == "active"]


def _plano(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select plan from auth_accounts where user_id = %s", (uid,))
        return cur.fetchone()["plan"]


def _paga(uid, **kw):
    conta(uid, "free", None)
    cob = nova_cobranca(uid, **kw)
    entregar("PAYMENT_RECEIVED", cob)
    return cob


def _pix_real(externo, cob):
    """O Asaas passa a dizer RECEIVED e o RECEIVED chega (de novo)."""
    externo["get_status"] = "RECEIVED"
    return entregar("PAYMENT_RECEIVED", cob)


def test_a_paid_vira_pending_e_revoga(user_id, externo):
    cob = _paga(user_id)
    assert len(_ativos(user_id)) == 1 and _plano(user_id) != "free"
    externo["alerta"].clear()

    eid = entregar(UNDONE, cob)
    linha = ler(cob["id"])
    assert evento(eid)["attempts"] == 0 and evento(eid)["processed_at"] is not None
    assert linha["status"] == "pending"
    assert linha["paid_at"] is None and linha["access_starts_at"] is None
    assert linha["access_expires_at"] is None
    assert _ativos(user_id) == [] and len(_pix(user_id)) == 1  # a linha do grant fica, revogada
    assert _plano(user_id) == "free"
    ef = efeitos(cob["asaas_payment_id"])
    assert "revoke" not in ef and "baixa_desfeita" in ef and "grant" not in ef
    assert len(externo["alerta"]) == 1 and "revogado, cobrança reaberta" in externo["alerta"][0]


def test_b_caderno_pendente_nao_fecha_como_estornado(user_id, externo):
    cob = _paga(user_id, extras=_EXTRAS)
    entregar(UNDONE, cob)
    assert ler(cob["id"])["status"] == "pending"
    from core.services.pix_extras import conferir_entrega
    with pytest.raises(RuntimeError):  # pendente no Asaas: nem entrega nem fecha estornado; o job retenta
        conferir_entrega(user_id, f"pix:{cob['id']}", "price_cad_a")


def test_c_undone_reentregue_revoga_uma_vez_e_nao_bloqueia_o_pix(user_id, externo):
    cob = _paga(user_id)
    entregar(UNDONE, cob)
    entregar(UNDONE, cob)
    assert len(_pix(user_id)) == 1 and _ativos(user_id) == []
    _pix_real(externo, cob)
    assert len(_ativos(user_id)) == 1


def test_d_undone_antes_do_received_nao_concede(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar(UNDONE, cob)
    externo["alerta"].clear()
    entregar("PAYMENT_RECEIVED", cob)  # GET PENDING
    assert _ativos(user_id) == [] and _plano(user_id) == "free"
    assert externo["ga4"] == 0 and externo["email"] == 0
    assert len(externo["alerta"]) == 1


def test_undone_e_received_real_reativam_o_mesmo_grant(user_id, externo):
    cob = _paga(user_id)
    antes = _pix(user_id)[0]
    entregar(UNDONE, cob)
    _pix_real(externo, cob)
    (grant,) = _pix(user_id)
    assert grant["id"] == antes["id"] and grant["status"] == "active"
    assert grant["revoked_reason"] is None
    assert grant["starts_at"] > antes["starts_at"] and grant["ends_at"] > antes["ends_at"]
    assert ler(cob["id"])["status"] == "paid" and _plano(user_id) != "free"
    assert (externo["ga4"], externo["capi"], externo["email"]) == (1, 1, 1)


def test_ciclo_duplo_de_baixa_termina_com_grant_ativo(user_id, externo):
    cob = _paga(user_id)
    entregar(UNDONE, cob)
    _pix_real(externo, cob)
    externo["get_status"] = "PENDING"
    entregar(UNDONE, cob)
    assert ler(cob["id"])["status"] == "pending" and _ativos(user_id) == []
    _pix_real(externo, cob)
    assert len(_ativos(user_id)) == 1 and ler(cob["id"])["status"] == "paid"
    assert (externo["ga4"], externo["capi"], externo["email"]) == (1, 1, 1)


def test_undone_reentregue_depois_do_pix_real_mantem_o_acesso(user_id, externo):
    cob = _paga(user_id)
    entregar(UNDONE, cob)
    _pix_real(externo, cob)
    externo["alerta"].clear()
    entregar(UNDONE, cob)  # GET agora diz RECEIVED
    assert len(_ativos(user_id)) == 1 and ler(cob["id"])["status"] == "paid"
    assert len(externo["alerta"]) == 1 and "ignorado" in externo["alerta"][0]


def test_received_da_baixa_reentregue_nao_concede(user_id, externo):
    cob = _paga(user_id)
    entregar(UNDONE, cob)
    entregar("PAYMENT_RECEIVED", cob)  # GET PENDING
    assert _ativos(user_id) == [] and ler(cob["id"])["status"] == "pending"


def test_refunded_depois_do_undone_bloqueia_o_received(user_id, externo):
    cob = _paga(user_id)
    entregar(UNDONE, cob)
    entregar("PAYMENT_REFUNDED", cob)
    _pix_real(externo, cob)
    assert _ativos(user_id) == [] and "revoke" in efeitos(cob["asaas_payment_id"])


def test_undone_depois_de_refunded_nao_reabre(user_id, externo):
    cob = _paga(user_id)
    entregar("PAYMENT_REFUNDED", cob)
    entregar(UNDONE, cob)
    assert ler(cob["id"])["status"] == "refunded" and _ativos(user_id) == []
    assert "baixa_desfeita" not in efeitos(cob["asaas_payment_id"])


def test_parcial_e_undone_vai_a_refunded(user_id, externo):
    cob = _paga(user_id)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    entregar(UNDONE, cob)
    assert ler(cob["id"])["status"] == "refunded" and _ativos(user_id) == []
    assert {"revoke", "baixa_desfeita"} <= efeitos(cob["asaas_payment_id"])


def test_outra_cobranca_ativa_vira_expired_e_o_pix_concede(user_id, externo):
    cob = _paga(user_id)
    outra = nova_cobranca(user_id)  # paid não ocupa o índice de ativa
    eid = entregar(UNDONE, cob)
    assert evento(eid)["processed_at"] is not None and evento(eid)["attempts"] == 0
    assert ler(cob["id"])["status"] == "expired" and ler(outra["id"])["status"] == "pending"
    assert _ativos(user_id) == []
    _pix_real(externo, cob)
    assert len(_ativos(user_id)) == 1 and ler(cob["id"])["status"] == "paid"


def test_get_que_levanta_deixa_o_evento_aberto_sem_mudar_nada(user_id, externo):
    cob = _paga(user_id)
    externo["get_erro"] = RuntimeError("asaas fora")
    eid = entregar(UNDONE, cob)
    assert evento(eid)["attempts"] == 1 and evento(eid)["processed_at"] is None
    assert ler(cob["id"])["status"] == "paid" and len(_ativos(user_id)) == 1
    assert "baixa_desfeita" not in efeitos(cob["asaas_payment_id"])


def test_morte_entre_a_revogacao_e_a_reabertura_converge(user_id, externo, monkeypatch):
    import core.services.pix_baixa_desfeita as m

    cob = _paga(user_id)
    real = m.reabrir_baixa

    def _morre(*a, **kw):
        raise RuntimeError("morreu")

    monkeypatch.setattr(m, "reabrir_baixa", _morre)
    eid = entregar(UNDONE, cob)
    assert evento(eid)["attempts"] == 1
    assert _ativos(user_id) == [] and ler(cob["id"])["status"] == "paid"

    monkeypatch.setattr(m, "reabrir_baixa", real)
    from core.services.pix_drain import drenar_evento
    drenar_evento(eid)
    assert evento(eid)["processed_at"] is not None
    assert ler(cob["id"])["status"] == "pending" and _ativos(user_id) == []


def test_outro_grant_ativo_do_cliente_continua(user_id, externo):
    cob = _paga(user_id)
    upsert_grant(user_id, "admin", "mimo", "pro_max", datetime.now(timezone.utc),
                 datetime.now(timezone.utc) + timedelta(days=30), 1)
    entregar(UNDONE, cob)
    assert _ativos(user_id) == []
    assert [g for g in list_grants(user_id) if g["source"] == "admin" and g["status"] == "active"]
    assert _plano(user_id) != "free"


def test_checkout_depois_do_undone_emite_cobranca_nova(user_id, externo, asaas_falso, vendavel):
    from core.services.pix_checkout import criar_checkout

    cob = _paga(user_id)
    entregar(UNDONE, cob)
    r = criar_checkout(user_id, plan_stored="pro_max", cpf_cnpj="12345678901",
                       nome="Fulano", email="f@x.com")
    assert r and r["public_token"] != cob["public_token"]  # cobrança nova, não a velha reaproveitada
    assert ler(cob["id"])["status"] in ("canceled", "expired")  # a velha sem QR sai do caminho


@pytest.mark.parametrize("motivo", ["asaas:PAYMENT_REFUNDED", "admin:revogado"])
def test_grant_pix_revogado_por_outro_motivo_nao_revive_no_upsert(user_id, externo, motivo):
    from db.plan_grants import revoke_grant

    conta(user_id, "free", None)
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "pix", "99", "pro_max", agora, agora + timedelta(days=30), 1)
    assert revoke_grant(user_id, "pix", "99", motivo, 2)
    upsert_grant(user_id, "pix", "99", "pro_max", agora, agora + timedelta(days=30), 3)
    assert _ativos(user_id) == []


def test_alerta_diz_a_verdade_por_estado(user_id, externo):
    conta(user_id, "free", None)
    pend = nova_cobranca(user_id)  # pending: nada a reabrir
    entregar(UNDONE, pend)
    assert ler(pend["id"])["status"] == "pending"
    assert "nada concedido" in externo["alerta"][-1] and "reaberta" not in externo["alerta"][-1]


def test_alerta_do_parcial_diz_estornada(user_id, externo):
    cob = _paga(user_id)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    externo["alerta"].clear()
    entregar(UNDONE, cob)
    assert "estornada" in externo["alerta"][-1] and "reaberta" not in externo["alerta"][-1]


def _orfa(user_id):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set user_id = null where id = %s", (cob["id"],))
        conn.commit()
    entregar("PAYMENT_RECEIVED", cob)
    assert ler(cob["id"])["status"] == "paid_orphan"
    return cob


def test_orfa_desfeita_vai_a_expired_e_pix_real_volta_a_paid_orphan(user_id, externo):
    cob = _orfa(user_id)
    entregar(UNDONE, cob)
    assert ler(cob["id"])["status"] == "expired"
    _pix_real(externo, cob)
    assert ler(cob["id"])["status"] == "paid_orphan"


def test_orfa_received_da_baixa_reentregue_consulta_o_asaas(user_id, externo):
    cob = _orfa(user_id)
    entregar(UNDONE, cob)
    externo["alerta"].clear()
    entregar("PAYMENT_RECEIVED", cob)  # GET PENDING: nao volta a paid_orphan
    assert ler(cob["id"])["status"] == "expired"
    assert any("depois da baixa desfeita" in a for a in externo["alerta"])


@pytest.mark.parametrize("estado", ["paid", "paid_orphan", "refunded_partial"])
def test_undone_de_outro_pagamento_nao_toca_na_cobranca_paga(user_id, externo, estado):
    cob = _orfa(user_id) if estado == "paid_orphan" else _paga(user_id)
    if estado == "refunded_partial":
        entregar("PAYMENT_PARTIALLY_REFUNDED", cob)
    antes = (ler(cob["id"])["status"], [g["id"] for g in _ativos(user_id)], _plano(user_id))
    externo["get_status"] = "PENDING"
    externo["alerta"].clear()
    entregar(UNDONE, cob, payment_id="pay_outro_x", referencia=cob["external_reference"])
    assert (ler(cob["id"])["status"], [g["id"] for g in _ativos(user_id)], _plano(user_id)) == antes
    assert "baixa_desfeita" not in efeitos(cob["asaas_payment_id"])
    assert "baixa_desfeita" not in efeitos("pay_outro_x")
    assert "nada alterado" in externo["alerta"][-1]


def test_undone_do_mesmo_pagamento_continua_reabrindo(user_id, externo):
    cob = _paga(user_id)
    entregar(UNDONE, cob, payment_id=cob["asaas_payment_id"], referencia=cob["external_reference"])
    assert ler(cob["id"])["status"] == "pending" and _ativos(user_id) == []


def test_alerta_so_cita_stripe_se_o_cancelamento_foi_registrado(user_id, externo):
    from db.pix_effects import registrar_efeito

    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set stripe_subscription_id = 'sub_x' where id = %s",
                    (cob["id"],))
        conn.commit()
    eid = entregar(UNDONE, cob)
    assert "segue agendado" not in externo["alerta"][-1]
    registrar_efeito(cob["asaas_payment_id"], "stripe_cancel", eid)
    entregar(UNDONE, cob)
    assert "segue agendado" in externo["alerta"][-1]


def _undone_registrado_antes(cob):
    """UNDONE com versão BAIXA, gravado na outbox e ainda não drenado."""
    import uuid
    from _dreno_pix_helpers import _versao
    from db.webhook_outbox import registrar_evento

    eid = f"evt_{uuid.uuid4().hex[:16]}"
    registrar_evento(eid, UNDONE, {"id": eid, "event": UNDONE, "payment": {
        "id": cob["asaas_payment_id"], "externalReference": cob["external_reference"],
        "value": 499.0, "status": "RECEIVED_IN_CASH_UNDONE"}}, next(_versao))
    return eid


def test_undone_de_versao_menor_que_a_do_grant_ainda_revoga(user_id, externo):
    """Codex A: RECEIVED reentregue (versão MAIOR) drenado antes do UNDONE."""
    from core.services.pix_drain import drenar_evento

    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    eid = _undone_registrado_antes(cob)
    entregar("PAYMENT_RECEIVED", cob)  # grant nasce com versão > a do UNDONE
    externo["get_status"] = "PENDING"
    drenar_evento(eid)
    assert ler(cob["id"])["status"] == "pending" and _ativos(user_id) == []
    assert _plano(user_id) == "free"
    _pix_real(externo, cob)  # Pix real depois do UNDONE concede
    assert len(_ativos(user_id)) == 1 and ler(cob["id"])["status"] == "paid"


def _morre_antes_de_reabrir(externo, monkeypatch, user_id):
    import core.services.pix_baixa_desfeita as m

    cob = _paga(user_id)
    real = m.reabrir_baixa
    monkeypatch.setattr(m, "reabrir_baixa", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    eid = entregar(UNDONE, cob)
    assert _ativos(user_id) == [] and ler(cob["id"])["status"] == "paid"
    monkeypatch.setattr(m, "reabrir_baixa", real)
    return cob, eid


def test_pix_real_depois_da_morte_e_antes_da_retentativa_concede(user_id, externo, monkeypatch):
    """Codex B, retentativa do UNDONE com o GET já pago."""
    from core.services.pix_drain import drenar_evento

    cob, eid = _morre_antes_de_reabrir(externo, monkeypatch, user_id)
    externo["get_status"] = "RECEIVED"
    externo["ga4"] = externo["email"] = 0
    drenar_evento(eid)
    assert len(_ativos(user_id)) == 1 and ler(cob["id"])["status"] == "paid"
    assert _plano(user_id) != "free" and (externo["ga4"], externo["email"]) == (0, 0)


def test_pix_real_depois_da_morte_concede_no_proprio_received(user_id, externo, monkeypatch):
    """Codex B, RECEIVED antes da retentativa; a retentativa depois não desfaz."""
    from core.services.pix_drain import drenar_evento

    cob, eid = _morre_antes_de_reabrir(externo, monkeypatch, user_id)
    _pix_real(externo, cob)
    assert len(_ativos(user_id)) == 1
    drenar_evento(eid)
    assert len(_ativos(user_id)) == 1 and ler(cob["id"])["status"] == "paid"
    assert (externo["ga4"], externo["email"]) == (1, 1)


def test_undone_velho_sem_marcador_nao_reconcede(user_id, externo):
    """Sem marcador, grant revogado pelo UNDONE-reason à mão: nada volta."""
    from db.plan_grants import revoke_grant

    cob = _paga(user_id)
    revoke_grant(user_id, "pix", str(cob["id"]), "asaas:" + UNDONE, 2_000_000_000)
    externo["get_status"] = "RECEIVED"
    entregar(UNDONE, cob)
    assert _ativos(user_id) == []
