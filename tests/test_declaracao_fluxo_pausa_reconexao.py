"""Pausa, retomada e reconexão não deixam a despesa declarada solta (D + sombra = dobra).

Fluxo tem conta NULL ("qualquer conta"): o motivo da ADR 0003 para a revisão pós-desconexão (não
presumir a conta) não existe, então `requires_review` NÃO bloqueia o automático dele, mantidos o
candidato único nos dois sentidos. O aporte não muda: pausa marca a conta como ambígua e ele fica
sem religar (o teste dele guarda isso; não depende só do `requires_review`).
Controle: voltar a checar `requires_review` para fluxo deixa vermelhos os dois de religação; o de duas
transações é o positivo da regra restritiva.
"""
import db
from db.bank_movements import bank_movement_summary, list_bank_movements
from db.open_finance import pause_open_finance_connection
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank, _deposit, _sync, _tx
from tests.test_declaracao_fluxo_reconcilia import decl, declara, n_linhas, saiu, sincroniza, tx


def _retoma(cid):
    """Não há função de produto que tire a conexão de PAUSED (o retorno é reconectar); o estado é posto à mão."""
    q("update open_finance_connections set status='UPDATED' where id=%s returning id", (cid,))


def _reconecta(uid, cid, *txs):
    db.disconnect_open_finance_connection(uid, cid)
    novo = db.save_pluggy_open_finance_item(uid, {"id": f"novo-{uid}", "status": "UPDATED"})
    db.save_open_finance_sync(novo["id"], [{"provider_account_id": f"nova-{uid}", "name": "Nova", "type": "BANK",
                                            "currency": "BRL", "balance": 1000, "transactions": list(txs)}])
    db.import_open_finance_launches(uid)


def test_pausa_e_retomada_religam_com_um_vinculo(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx())
    pause_open_finance_connection(cid)
    d = decl(lid)
    assert d["matched_transaction_id"] is None and d["requires_review"] and saiu(user_id) == 50
    _retoma(cid)
    sincroniza(cid, user_id, tx())
    d = decl(lid)
    assert d["matched_transaction_id"] and not d["requires_review"] and d["confirmation_method"] == "automatic"
    assert n_linhas(user_id, 50) == 1 and saiu(user_id) == 50


def test_reconexao_com_a_mesma_transacao_religa(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx())
    _reconecta(user_id, cid, tx())
    d = decl(lid)
    assert d["matched_transaction_id"] and not d["requires_review"]
    assert n_linhas(user_id, 50) == 1 and saiu(user_id) == 50


def test_reconexao_com_duas_transacoes_candidatas_nao_religa(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx())
    _reconecta(user_id, cid, tx(txid="a"), tx(txid="b"))
    assert decl(lid)["matched_transaction_id"] is None
    assert len(list_bank_movements(user_id)[0]["candidates"]) == 2


def test_correcao_do_banco_continua_desvinculando(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx())
    sincroniza(cid, user_id, tx(-52.30))
    d = decl(lid)
    assert d["matched_transaction_id"] is None and d["requires_review"]


def test_aporte_nao_religa_depois_da_pausa(user_id):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    _sync(cid, 500, _tx())
    assert bank_movement_summary(user_id)["pending_count"] == 0
    pause_open_finance_connection(cid)
    _retoma(cid)
    _sync(cid, 500, _tx())
    assert decl(lid)["matched_transaction_id"] is None and decl(lid)["requires_review"]
