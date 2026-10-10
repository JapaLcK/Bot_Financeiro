"""Fatura paga/fechada que o PR 0 do sinal faz voltar a dever reabre (apontamento do Codex).

`pay_bill_amount` marca `paid` a fatura com total <= 0; trocar o sinal da compra (sync) ou tirar
um estorno/pagamento (remoção) sobe o total sem passar pelo insert que já reabria.
"""
from __future__ import annotations

from decimal import Decimal

import db
import db.cards as cards
from tests._of_cash_helpers import q
from tests.test_of_cartao_sinal import A, B, faturas, linhas, regra_velha, rodar  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401 (fixtures)


def paga(uid):
    """O que o usuário fez: pagar a fatura em aberto pelo caminho de produção."""
    card = q("select id, name from credit_cards where user_id=%s", (uid,), True)[0]
    db.pay_bill_amount(uid, card["id"], card["name"], None)
    return card["id"]


def test_sync_do_sinal_reabre_fatura_legada_paga(uid_pro, rodar, regra_velha):
    with regra_velha():
        rodar(uid_pro, [A])                          # legado: compra de 120 gravada como -120
    card_id = paga(uid_pro)                          # total <= 0 → 'paid'
    assert faturas(uid_pro) == [(-120, "paid")]
    assert cards.get_card_credit_usage(uid_pro, card_id) == 0

    rodar(uid_pro, [A])                              # o código novo corrige o sinal

    assert faturas(uid_pro) == [(120, "open")]
    assert cards.get_card_credit_usage(uid_pro, card_id) == Decimal(120)   # o limite usado conta


def test_sync_que_nao_cria_divida_mantem_a_fatura_paga(uid_pro, rodar):
    """Controle positivo: paga 90, a Pluggy corrige o estorno e o total cai para 80 <= pago."""
    rodar(uid_pro, [A, B])
    paga(uid_pro)
    assert faturas(uid_pro) == [(90, "paid")]
    rodar(uid_pro, [A, ("b", -40, "Transfers", "Estorno loja")])
    assert faturas(uid_pro) == [(80, "paid")]


def test_remover_estorno_reabre_fatura_paga(uid_pro, rodar):
    rodar(uid_pro, [A, B])
    card_id = paga(uid_pro)
    assert faturas(uid_pro) == [(90, "paid")]

    cards.remove_single_credit_transaction(uid_pro, linhas(uid_pro)["b"]["id"])   # total 90 → 120

    assert faturas(uid_pro) == [(120, "open")]
    assert cards.get_card_credit_usage(uid_pro, card_id) == Decimal(30)    # 120 - 90 pagos


def test_remover_compra_mantem_a_fatura_paga(uid_pro, rodar):
    """Controle positivo da remoção: tirar compra baixa o total, a fatura segue paga."""
    rodar(uid_pro, [A, B])
    paga(uid_pro)
    cards.remove_single_credit_transaction(uid_pro, linhas(uid_pro)["a"]["id"])    # total 90 → -30
    assert faturas(uid_pro) == [(-30, "paid")]
