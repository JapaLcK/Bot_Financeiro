"""PR-A não pode mudar o #498: dinheiro puro vira par "pendente" e nunca funde sozinho.

A declaração "paga pelo banco" sai das duas buscas de candidato a par do dinheiro
(`_find_manual_candidates` do importador, `_propose_manual_reconciliation` da ordem inversa).
Controle negativo: tirar o `not exists` de `_find_manual_candidates` deixa vermelho
`test_declarada_nao_vira_par_pendente_no_import`; o de `_propose_*` deixa vermelho
`test_declarada_nao_vira_par_pendente_na_ordem_inversa`. O positivo é o dinheiro puro
(mesmas ordens), que segue pendente.
"""
from __future__ import annotations

import db
from db.reconciliation import reject_reconciliation
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank
from tests.test_declaracao_fluxo_reconcilia import decl, declara, sincroniza, tx


def status_das_tx(uid):
    return q("""select o.provider_transaction_id p, o.reconciliation_status s, o.match_launch_id m
                  from open_finance_transactions o
                  join open_finance_accounts a on a.id = o.account_id
                  join open_finance_connections c on c.id = a.connection_id
                 where c.user_id = %s order by o.id""", (uid,), fetch=True)


def dinheiro(uid, valor=50):
    return db.add_launch_and_update_balance(uid, "despesa", valor, "mercado", "msg", "mercado")[0]


def test_dinheiro_puro_antes_do_extrato_vira_par_pendente(user_id):
    cid, _ = _bank(user_id)
    x = dinheiro(user_id)
    sincroniza(cid, user_id, tx())
    [t] = status_das_tx(user_id)
    assert (t["s"], t["m"]) == ("pending", x)
    assert db.get_balance(user_id) == -50  # a Carteira segue como sempre


def test_dinheiro_puro_depois_do_extrato_vira_par_pendente(user_id):
    cid, _ = _bank(user_id)
    sincroniza(cid, user_id, tx())
    x = dinheiro(user_id)
    assert db.propose_manual_reconciliation(user_id, x)["of_tx_id"] is not None
    [t] = status_das_tx(user_id)
    assert (t["s"], t["m"]) == ("pending", x)


def test_declarada_nao_vira_par_pendente_no_import(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx(txid="a"), tx(txid="b"))  # dois candidatos: nenhum auto
    assert decl(lid)["matched_transaction_id"] is None
    assert all(t["s"] != "pending" and t["m"] is None for t in status_das_tx(user_id))


def test_declarada_nao_vira_par_pendente_na_ordem_inversa(user_id):
    cid, _ = _bank(user_id)
    sincroniza(cid, user_id, tx(txid="a"), tx(txid="b"))
    lid = declara(user_id)
    assert decl(lid)["matched_transaction_id"] is None
    assert db.propose_manual_reconciliation(user_id, lid) == {"ok": True, "of_tx_id": None}
    assert all(t["s"] != "pending" and t["m"] is None for t in status_das_tx(user_id))


def test_par_pendente_do_dinheiro_segura_a_transacao_e_rejeitar_libera(user_id):
    cid, _ = _bank(user_id)
    x = dinheiro(user_id)
    sincroniza(cid, user_id, tx())
    [t] = status_das_tx(user_id)
    assert (t["s"], t["m"]) == ("pending", x)
    lid = declara(user_id)
    assert decl(lid)["matched_transaction_id"] is None  # D não toma a T do par
    tx_id = q("select id from open_finance_transactions where match_launch_id=%s", (x,))["id"]
    reject_reconciliation(user_id, tx_id)
    assert decl(lid)["matched_transaction_id"] is None  # liberar não casa sozinho: é o próximo reconcile
    sincroniza(cid, user_id, tx())
    assert decl(lid)["matched_transaction_id"] == tx_id
    assert q("select count(*) n from launches where user_id=%s and source='open_finance'", (user_id,))["n"] == 0
