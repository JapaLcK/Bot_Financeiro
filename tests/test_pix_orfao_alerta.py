"""#1505: chargeback/MED e parcial de titular EXCLUÍDO (`user_id` nulo) alertam o admin, sem PII.

Dreno real (`entregar`) e banco real; só GA4/e-mail/alerta/Asaas são simulados (`externo["alerta"]`).

CONTROLE NEGATIVO: tirar o `alertar` do ramo de órfão em `_decidir` -> os 3 testes parametrizados de
chargeback/parcial ficam VERMELHOS. POSITIVO: `test_recebido_orfao_so_avisa_pix_orfao` (RECEIVED órfão
segue com exatamente o alerta "Pix órfão" e nenhum de estorno).
"""
from __future__ import annotations

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import entregar, mundo_externo, nova_cobranca
from db.connection import get_conn


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _orfa(uid):
    conta(uid, "free", None)
    cobranca = nova_cobranca(uid)
    entregar("PAYMENT_RECEIVED", cobranca)
    with get_conn() as conn:
        conn.execute("update pix_charges set status = 'paid_orphan', user_id = null where id = %s",
                     (cobranca["id"],))
        conn.commit()
    return cobranca


@pytest.mark.parametrize("tipo", ["PAYMENT_CHARGEBACK_REQUESTED", "PAYMENT_CHARGEBACK_DISPUTE",
                                  "PAYMENT_PARTIALLY_REFUNDED"])
def test_orfao_alerta_chargeback_e_parcial(user_id, externo, tipo):
    cobranca = _orfa(user_id)
    externo["refunds"] = [{"value": 10.0, "status": "DONE"}]
    externo["alerta"].clear()
    entregar(tipo, cobranca)
    assert len(externo["alerta"]) == 1
    msg = externo["alerta"][0]
    assert tipo in msg and f"`{cobranca['id']}`" in msg
    assert "@" not in msg and str(user_id) not in msg.replace(str(cobranca["id"]), "")


def test_recebido_orfao_so_avisa_pix_orfao(user_id, externo):
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    with get_conn() as conn:
        conn.execute("update pix_charges set user_id = null where id = %s", (cobranca["id"],))
        conn.commit()
    entregar("PAYMENT_RECEIVED", cobranca)
    assert len(externo["alerta"]) == 1 and "Pix órfão" in externo["alerta"][0]
    assert "CHARGEBACK" not in externo["alerta"][0]
