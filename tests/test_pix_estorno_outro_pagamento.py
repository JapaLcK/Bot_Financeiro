"""#1435: estorno/chargeback do pagamento DUPLICADO não revoga a compra boa.

O RECEIVED duplicado é retido por `conferir_valor` e nunca grava o id; o REFUNDED dele caía
em `external_reference`, achava a cobrança boa e a revogava. Caminho real: `entregar` + dreno.
Positivo: o estorno do pagamento BOM segue revogando. Sem id gravado, o ramo de `pending`
sem id (`test_pix_estorno_antes_do_pagamento.py`) segue em `canceled`.
"""
from __future__ import annotations

import uuid

import pytest

from _dreno_pix_helpers import efeitos, entregar, evento, ler
from _billing_grants_helpers import conta
from test_afiliado_area import _afiliado
from test_afiliado_pagantes import _indicado, _linhas, _pix
from test_pix_dreno_estorno import _grants_ativos, externo  # noqa: F401


def _cenario(externo):
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    ok = cobranca["asaas_payment_id"]
    dup = f"pay_{uuid.uuid4().hex[:12]}"
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_RECEIVED", cobranca, payment_id=dup)
    externo["alerta"].clear()
    return c, ind, cobranca, ok, dup


def _intacta(c, ind, cobranca, ok, dup):
    linha = ler(cobranca["id"])
    assert (linha["status"], linha["asaas_payment_id"]) == ("paid", ok)
    assert len(_grants_ativos(ind.uid)) == 1
    assert not {"revoke", "afiliado_estorno", "afiliado_estorno_parcial"} & (efeitos(ok) | efeitos(dup))
    assert [r["refund_status"] for r in _linhas(c)] == ["none"]


@pytest.mark.parametrize("tipo", ["PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED"])
def test_estorno_do_duplicado_nao_mexe_na_compra_boa(externo, tipo):
    c, ind, cobranca, ok, dup = _cenario(externo)
    eid = entregar(tipo, cobranca, payment_id=dup)
    _intacta(c, ind, cobranca, ok, dup)
    assert evento(eid)["processed_at"] is not None
    assert len(externo["alerta"]) == 1 and "outro pagamento" in externo["alerta"][0]


@pytest.mark.parametrize("tipo", ["PAYMENT_CHARGEBACK_REQUESTED", "PAYMENT_REFUND_DENIED"])
def test_chargeback_do_duplicado_so_alerta(externo, tipo):
    c, ind, cobranca, ok, dup = _cenario(externo)
    eid = entregar(tipo, cobranca, payment_id=dup)
    _intacta(c, ind, cobranca, ok, dup)
    assert evento(eid)["processed_at"] is not None
    assert len(externo["alerta"]) == 1 and "outro pagamento" in externo["alerta"][0]


def test_positivo_estorno_do_pagamento_bom_ainda_revoga(externo):
    c, ind, cobranca, ok, dup = _cenario(externo)
    entregar("PAYMENT_REFUNDED", cobranca)
    assert ler(cobranca["id"])["status"] == "refunded"
    assert _grants_ativos(ind.uid) == [] and "revoke" in efeitos(ok)
    assert [r["refund_status"] for r in _linhas(c)] == ["total"]


def test_positivo_pending_sem_id_gravado_segue_a_canceled(user_id, externo):
    """Sem `asaas_payment_id` na cobrança não há 'outro pagamento': o ramo antigo vale."""
    from db.connection import get_conn
    from _dreno_pix_helpers import nova_cobranca
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    with get_conn() as conn:
        conn.execute("update pix_charges set asaas_payment_id = null where id = %s", (cobranca["id"],))
        conn.commit()
    entregar("PAYMENT_REFUNDED", {**cobranca, "asaas_payment_id": None},
             payment_id=f"pay_{uuid.uuid4().hex[:12]}")
    assert ler(cobranca["id"])["status"] == "canceled"
