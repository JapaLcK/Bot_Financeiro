"""Compra/parcela do banco não se apaga nem se antecipa (B1 do PR 0 do sinal). Banco real.

Com o sinal certo a parcela do OF tem `is_refund=false` e fica ao alcance de `anticipate_installment`
e `undo_installment_group`. Sem guarda, antecipar apaga a linha e debita a Carteira, mas o sync
seguinte reimporta a parcela (a FK solta o espelho): a fatura volta a R$ 100 e a Carteira fica
com -R$ 100. Desfazer o grupo é revertido em silêncio no sync. Mesmo `pode` do v2
(`PODE_CARTAO_SQL`: nada de `apagar` na linha do banco).

CONTROLES NEGATIVOS (rodados na entrega, ver o relato): tirar o `raise CompraDoBanco` de
`anticipate_installment`, de `undo_installment_group` ou de `undo_credit_transaction` deixa
vermelho, respectivamente, os testes de antecipar, de desfazer o grupo e de desfazer a compra.
CONTROLE POSITIVO: a compra manual segue antecipável e desfazível.
"""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from core.services.ai_chat.tools.launches import _delete_launch_execute
from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
from tests._of_cash_helpers import q
from tests.test_credit_transactions_endpoints import _auth, _csrf_headers
from tests.test_of_cartao_sinal import faturas, rodar  # noqa: F401 (fixture)

META = {"creditCardMetadata": {"installmentNumber": 1, "totalInstallments": 3, "totalAmount": 300}}
PARCELA = ("p1", 100, "Shopping", "Loja parcelada", META)
COMPRA = ("c1", 70, "Shopping", "Loja")


def estado(uid):
    """(linhas de cartão, faturas, lançamentos da Carteira): o que antecipar/desfazer mexeria."""
    n = q("select count(*) as n from credit_transactions where user_id=%s", (uid,), True)[0]["n"]
    c = q("select count(*) as n from launches where user_id=%s", (uid,), True)[0]["n"]
    return n, faturas(uid), c


def gid(uid):
    return str(q("select group_id from credit_transactions where user_id=%s", (uid,), True)[0]["group_id"])


def ct_id(uid):
    return q("select id from credit_transactions where user_id=%s", (uid,), True)[0]["id"]


def recusa(uid, rodar, tx, acao):
    """sync -> ação recusada, sem efeito -> sync: nada muda (a linha não volta nem some)."""
    rodar(uid, [tx])
    antes = estado(uid)
    with pytest.raises(db.CompraDoBanco):
        acao()
    assert estado(uid) == antes
    rodar(uid, [tx])
    assert estado(uid) == antes
    return antes


def test_antecipar_parcela_do_banco(uid_pro, rodar):
    rodar(uid_pro, [PARCELA])
    g = gid(uid_pro)
    assert recusa(uid_pro, rodar, PARCELA, lambda: db.anticipate_installment(uid_pro, g)) \
        == (1, [(100, "open")], 0)


def test_desfazer_grupo_do_banco(uid_pro, rodar):
    rodar(uid_pro, [PARCELA])
    g = gid(uid_pro)
    assert recusa(uid_pro, rodar, PARCELA, lambda: db.undo_installment_group(uid_pro, g)) \
        == (1, [(100, "open")], 0)


def test_desfazer_parcela_do_banco_pelo_id_da_compra(uid_pro, rodar):
    rodar(uid_pro, [PARCELA])
    c = ct_id(uid_pro)
    recusa(uid_pro, rodar, PARCELA, lambda: db.undo_credit_transaction(uid_pro, c))


def test_desfazer_compra_unica_do_banco(uid_pro, rodar):
    rodar(uid_pro, [COMPRA])
    c = ct_id(uid_pro)
    assert recusa(uid_pro, rodar, COMPRA, lambda: db.undo_credit_transaction(uid_pro, c)) \
        == (1, [(70, "open")], 0)


def test_controle_positivo_compra_manual_segue_antecipavel_e_desfazivel(uid_pro):
    card = db.create_card(uid_pro, "Manual", closing_day=10, due_day=17)
    db.add_credit_purchase_installments(uid_pro, card, 300, "outros", "tv", date.today(), 3)
    g = gid(uid_pro)
    assert db.anticipate_installment(uid_pro, g)["anticipated_installment_no"] == 1
    assert db.undo_installment_group(uid_pro, g)["removed_count"] == 2
    tx, _due, _bill = db.add_credit_purchase(
        user_id=uid_pro, card_id=card, valor=50.0, categoria="outros", nota="x", purchased_at=date.today())
    assert db.undo_credit_transaction(uid_pro, tx)["mode"] == "single"


# ── os canais respondem com a frase do produto, não com "não achei" nem erro genérico ──

def test_app_responde_409_com_a_frase(uid_pro, rodar):
    rodar(uid_pro, [PARCELA])
    g, c = gid(uid_pro), ct_id(uid_pro)
    client = TestClient(dashboard.app)
    _auth(client, uid_pro)
    h = _csrf_headers(client)
    for resp in (client.post(f"/installments/{uid_pro}/{g}/anticipate", headers=h),
                 client.delete(f"/installments/{uid_pro}/{g}", headers=h),
                 client.delete(f"/credit-transactions/{uid_pro}/{c}", headers=h)):
        assert resp.status_code == 409, resp.text
        assert "vem do seu banco" in resp.json()["detail"]
    assert estado(uid_pro) == (1, [(100, "open")], 0)


def test_whatsapp_e_ia_respondem_com_a_frase(uid_pro, ia_fora, rodar):
    rodar(uid_pro, [PARCELA])
    g, c = gid(uid_pro), ct_id(uid_pro)
    curto = g.replace("-", "")[:8]
    for resposta in (manda(uid_pro, f"apagar parcelamento pc{curto}"), manda(uid_pro, f"apagar CC{c}"),
                     _delete_launch_execute(uid_pro, {"launch_id": f"PC{curto}"}),
                     _delete_launch_execute(uid_pro, {"launch_id": str(c)})):
        assert "vem do seu banco" in resposta, resposta
    assert estado(uid_pro) == (1, [(100, "open")], 0)
