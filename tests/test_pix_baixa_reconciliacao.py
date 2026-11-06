"""#1267: `reconciliar_grant` (morte entre revogar e reabrir + Pix real antes da retentativa).
Cada teste prova uma guarda; removê-la deixa o teste vermelho."""
from __future__ import annotations

import core.services.asaas as asaas
import core.services.pix_baixa_desfeita as m
from _dreno_pix_helpers import entregar
from db.pix_effects import registrar_efeito
from db.plan_grants import revoke_grant
from test_pix_baixa_desfeita import _ativos, _morre_antes_de_reabrir, _paga, externo  # noqa: F401


def test_morte_get_pendente_com_received_nao_concede(user_id, externo, monkeypatch):
    """Guarda `pagamento_confirmado`."""
    cob, _ = _morre_antes_de_reabrir(externo, monkeypatch, user_id)
    externo["get_status"] = "PENDING"
    entregar("PAYMENT_RECEIVED", cob)
    assert _ativos(user_id) == []


def test_motivo_diferente_nao_roda_o_executor(user_id, externo, monkeypatch):
    """Guarda do `revoked_reason`."""
    cob = _paga(user_id)
    registrar_efeito(cob["asaas_payment_id"], "baixa_desfeita", "evt_x")
    revoke_grant(user_id, "pix", str(cob["id"]), "asaas:PAYMENT_REFUNDED", 2_000_000_000)
    externo["get_status"] = "RECEIVED"
    chamadas = []
    monkeypatch.setitem(m.EXECUTORES, "grant", lambda *a: chamadas.append(1))
    entregar("PAYMENT_RECEIVED", cob)
    assert chamadas == []


def test_estorno_na_janela_morta_nao_reconcede(user_id, externo, monkeypatch):
    """Guarda do status da cobrança (`refunded` não é paid)."""
    cob, _ = _morre_antes_de_reabrir(externo, monkeypatch, user_id)
    entregar("PAYMENT_REFUNDED", cob)
    externo["get_status"] = "RECEIVED"
    entregar("PAYMENT_RECEIVED", cob)
    assert _ativos(user_id) == []


def test_compra_normal_nao_consulta_o_asaas(user_id, externo, monkeypatch):
    """Ordem das checagens: o marcador vem antes do GET."""
    n = []
    real = asaas.buscar_pagamento
    monkeypatch.setattr(asaas, "buscar_pagamento", lambda p: n.append(p) or real(p))
    _paga(user_id)
    assert n == []


def test_get_falha_no_received_nao_perde(user_id, externo, monkeypatch):
    from core.services.pix_drain import drenar_evento

    cob, _ = _morre_antes_de_reabrir(externo, monkeypatch, user_id)
    externo["get_erro"] = RuntimeError("fora")
    eid = entregar("PAYMENT_RECEIVED", cob)
    assert _ativos(user_id) == []
    externo["get_erro"], externo["get_status"] = None, "RECEIVED"
    drenar_evento(eid)
    assert len(_ativos(user_id)) == 1
