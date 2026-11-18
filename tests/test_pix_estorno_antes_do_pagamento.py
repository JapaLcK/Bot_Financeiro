"""#1437 R3.3: o `PAYMENT_REFUNDED` do MESMO id antes do pagamento leva a cobrança `draft`, `creating`,
`pending` ou `canceling` a `canceled` e apaga o QR (`db/pix_estornos.estorno_no_dreno`). Linha sem id
também cancela, menos em `creating`: ali a emissão está em voo, e o estorno é de OUTRO pagamento.

Sem isso a cobrança ficava `pending` com um QR já pago e estornado: o checkout devolvia o QR morto e
a recompra pedia `DELETE` no Asaas. `canceled`, e não `refunded`: `refunded` prova que houve `paid`, e
o afiliado gravaria pagante (`afiliado_pix.estornar`). O `RECEIVED` atrasado segue barrado pela D3 e
pela condição `sem_estorno` da transição (`canceled` é origem de pagamento).

CONTROLE NEGATIVO: sem a transição em `estorno_no_dreno`, os três primeiros ficam vermelhos.
POSITIVO: o estorno de OUTRO id na mesma cobrança não a cancela, e o pagamento dela concede.
"""
from __future__ import annotations

import uuid

import pytest

from _billing_grants_helpers import conta
from _dreno_pix_helpers import entregar, ler, nova_cobranca
from _pix_checkout_helpers import _linhas as _cobrancas, asaas_falso, vendavel  # noqa: F401
from core.services.pix_checkout import criar_checkout
from db.connection import get_conn
from db.schema import BACKFILL_AFILIADO_PAGAMENTOS_SQL
from test_afiliado_area import _afiliado
from test_afiliado_pagantes import _indicado, _linhas, _pix
from test_pix_dreno_estorno import _grants_ativos, externo  # noqa: F401


def test_estornada_antes_do_pagamento_vai_a_canceled_sem_qr(user_id, externo):
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    with get_conn() as conn:
        conn.execute("update pix_charges set qr_payload_enc = 'qr-de-teste' where id = %s", (cobranca["id"],))
        conn.commit()
    entregar("PAYMENT_REFUNDED", cobranca)
    linha = ler(cobranca["id"])
    assert (linha["status"], linha["qr_payload_enc"], linha["canceled_at"] is not None) == ("canceled", None, True)


def test_recompra_depois_do_estorno_cria_cobranca_nova_sem_delete_remoto(user_id, externo, asaas_falso, vendavel):
    primeira = criar_checkout(user_id, plan_stored="pro_max", cpf_cnpj="12345678901", nome="Fulano", email="f@x.com")
    (cobranca,) = _cobrancas(user_id)
    entregar("PAYMENT_REFUNDED", cobranca)
    asaas_falso["ordem"].clear()
    segunda = criar_checkout(user_id, plan_stored="pro_max", cpf_cnpj="12345678901", nome="Fulano", email="f@x.com")
    assert segunda["public_token"] != primeira["public_token"]
    assert not [o for o in asaas_falso["ordem"] if o.startswith("delete:")], asaas_falso["ordem"]
    assert ler(cobranca["id"])["status"] == "canceled"


def test_received_atrasado_do_id_estornado_nao_concede_nem_grava_pagante(externo):
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    entregar("PAYMENT_REFUNDED", cobranca)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert ler(cobranca["id"])["status"] == "canceled" and _grants_ativos(ind.uid) == []
    assert (externo["ga4"], externo["capi"], externo["email"]) == (0, 0, 0)
    with get_conn() as conn:
        conn.execute(BACKFILL_AFILIADO_PAGAMENTOS_SQL)
        conn.commit()
    assert _linhas(c) == []


def test_estorno_de_outro_id_nao_cancela_a_cobranca(user_id, externo):
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_REFUNDED", cobranca, payment_id=f"pay_{uuid.uuid4().hex[:12]}")
    assert ler(cobranca["id"])["status"] == "pending"
    entregar("PAYMENT_RECEIVED", cobranca)
    assert ler(cobranca["id"])["status"] == "paid" and len(_grants_ativos(user_id)) == 1


def test_estorno_de_outro_pagamento_nao_cancela_a_emissao_em_voo(user_id, externo):
    """O caso do Tester: a reemissão está em `creating` sem id (o `POST` pode já ter criado o pagamento
    novo no Asaas, e o `attach` perdeu a corrida) e chega o estorno de OUTRO pagamento com a nossa
    referência. Cancelar deixaria o pagamento novo vivo e órfão; a linha fica para a saga."""
    from test_pix_matriz_estado_evento import montar

    cobranca = montar("creating", user_id)
    entregar("PAYMENT_REFUNDED", cobranca)
    assert ler(cobranca["id"])["status"] == "creating"


@pytest.mark.parametrize("estado", ["draft", "pending"])
def test_estorno_em_linha_sem_id_fora_da_emissao_cancela(user_id, externo, estado):
    """POSITIVO do anterior: sem id e fora de `creating` (o `draft` do #819, e um `pending` sem id
    montado à mão) o estorno com a nossa referência segue cancelando."""
    from test_pix_matriz_estado_evento import montar

    cobranca = montar(estado, user_id)
    with get_conn() as conn:
        conn.execute("update pix_charges set asaas_payment_id = null where id = %s", (cobranca["id"],))
        conn.commit()
    entregar("PAYMENT_REFUNDED", cobranca)
    assert ler(cobranca["id"])["status"] == "canceled"


@pytest.mark.parametrize("ate", ["creating", "pending_com_id_novo"])
def test_checkout_que_avanca_entre_a_leitura_e_o_cancelamento_nao_e_cancelado(user_id, externo, monkeypatch, ate):
    """Thread do Codex no #1595. O dreno leu a cobrança em `draft` sem id; entre essa leitura e o
    `UPDATE` de cancelamento, o checkout a leva a `creating` (emissão em voo) ou já a `pending` com o
    id NOVO. As condições "id igual" e "sem id fora de `creating`" valem no próprio `UPDATE`: o
    dicionário lido antes não decide nada. Cancelar aqui deixaria o pagamento novo vivo no Asaas."""
    import db.pix_charges as pix_charges
    from db.pix_charges_saga import attach_pagamento
    from test_pix_matriz_estado_evento import montar

    cobranca = montar("draft", user_id)  # sem id na linha; o evento traz o id de um pagamento anterior
    transicionar, novo = pix_charges.transicionar, f"pay_{uuid.uuid4().hex[:12]}"

    def checkout_no_meio(charge_id, **kw):  # o cancelamento de `estorno_no_dreno`, já com o dicionário lido
        if kw.get("para") == "canceled":
            assert transicionar(charge_id, de="draft", para="creating")
            if ate != "creating":
                assert attach_pagamento(charge_id, novo)
        return transicionar(charge_id, **kw)

    monkeypatch.setattr(pix_charges, "transicionar", checkout_no_meio)
    entregar("PAYMENT_REFUNDED", cobranca)
    linha = ler(cobranca["id"])
    assert (linha["status"], linha["asaas_payment_id"]) == (
        ("creating", None) if ate == "creating" else ("pending", novo))
