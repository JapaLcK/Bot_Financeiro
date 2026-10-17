"""Congelamento (45 dias) da declaração de fluxo JÁ casada: nunca prende pendência nem impede correção.

Só declaração casada congela. Sem par, a idade não a concilia, não a tira do "a conferir" nem a pula
do reconcile. Congelada, a própria prova segue revalidada (o banco apagar/corrigir a transação
desvincula) e o usuário apaga, edita texto e escolhe par pelo modal como em qualquer outra.
Não existe hoje caminho de produto para DESFAZER uma confirmação (nem do aporte): o que o congelamento
garante é que ele não é pior que o das recentes (`test_confirmar_outro_par_...` compara as duas).
Controle: voltar a filtrar a congelada do reconcile deixa vermelho `test_banco_corrigindo_...`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import db
import db.bank_conferencia as conferencia
import db.bank_movements as movements
from core.services.cashflow_snapshot import carregar
from db.bank_declaracoes import congelada
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank
from tests.test_declaracao_fluxo_reconcilia import _decls, decl, declara, n_linhas, sincroniza, tx
from utils_date import _tz, today_tz

ANTIGA = 50  # dias; passa dos 45 do congelamento


def _casada_antiga(uid, dias=ANTIGA):
    cid, _ = _bank(uid)
    lid = declara(uid, criado_em=datetime.now(_tz()) - timedelta(days=dias))
    sincroniza(cid, uid, tx(days=-dias))
    d = decl(lid)
    assert d["matched_transaction_id"]
    return cid, lid, d["matched_transaction_id"]


def _reconcilia(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        movements.reconcile_bank_movements(cur, uid)
        conn.commit()


def test_so_casada_congela_pendente_nunca(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id, criado_em=datetime.now(_tz()) - timedelta(days=100))
    assert not congelada(_decls(user_id)[0], today_tz())
    # segue a conferir: resumo, lista do modal e Previsão
    assert movements.bank_movement_summary(user_id)["pending_count"] == 1
    assert [m["launch_id"] for m in conferencia.list_bank_movements(user_id)] == [lid]
    motivos = carregar(user_id, today_tz(), today_tz() + timedelta(days=30)).motivos
    assert ("declaracao_bancaria_a_conferir", lid) in [(m.codigo, m.origem_id) for m in motivos]
    # e o reconcile não a pula: a transação antiga chega e ela casa
    sincroniza(cid, user_id, tx(days=-100))
    assert decl(lid)["matched_transaction_id"]


def test_congelada_valida_segue_casada_em_varias_rodadas(user_id):
    _, lid, tx_id = _casada_antiga(user_id)
    assert congelada(_decls(user_id)[0], today_tz())
    for _ in range(2):
        _reconcilia(user_id)
        d = decl(lid)
        assert d["matched_transaction_id"] == tx_id and not d["requires_review"]


def test_apagar_o_lancamento_congelado(user_id):
    _, lid, tx_id = _casada_antiga(user_id)
    db.delete_launch_and_rollback(user_id, lid)
    assert decl(lid) is None
    assert q("select imported_launch_id i from open_finance_transactions where id=%s", (tx_id,))["i"] is not None
    assert n_linhas(user_id, 50) == 1  # a transação já voltou como sombra
    db.import_open_finance_launches(user_id)
    assert n_linhas(user_id, 50) == 1  # e o import depois não duplica


def test_editar_categoria_e_descricao_do_congelado(user_id):
    _, lid, tx_id = _casada_antiga(user_id)
    assert db.update_launch_fields(user_id, lid, categoria="lazer", alvo="cinema", exigir_pode=True)
    assert decl(lid)["matched_transaction_id"] == tx_id


@pytest.mark.parametrize("dias", [ANTIGA, 5])
def test_confirmar_outro_par_se_comporta_igual_congelada_ou_recente(user_id, dias):
    """Casada e congelada (50) ou casada e recente (5): o modal não lista a casada e confirmar OUTRA
    transação recusa igual nos dois (trocar o par passa por apagar a transação ou o lançamento)."""
    cid, lid, tx_id = _casada_antiga(user_id, dias)
    assert conferencia.list_bank_movements(user_id) == []
    sincroniza(cid, user_id, tx(txid="t2", days=-dias))
    with pytest.raises(ValueError, match="MOVEMENT_ALREADY_MATCHED"):
        conferencia.confirm_bank_movement(user_id, lid, q(
            "select id from open_finance_transactions where provider_transaction_id='t2'")["id"])
    conferencia.confirm_bank_movement(user_id, lid, tx_id)  # idempotente na própria prova


def test_banco_apagando_a_transacao_desvincula_e_um_novo_par_religa(user_id):
    cid, lid, _ = _casada_antiga(user_id)
    db.delete_open_finance_transactions(f"decl-{user_id}", ["t1"])
    assert decl(lid)["matched_transaction_id"] is None
    assert not congelada(_decls(user_id)[0], today_tz())  # sem par, não é congelada
    sincroniza(cid, user_id, tx(txid="novo", days=-ANTIGA))
    assert decl(lid)["matched_transaction_id"]


def test_banco_corrigindo_a_transacao_desvincula_mesmo_congelada(user_id):
    cid, lid, _ = _casada_antiga(user_id)
    sincroniza(cid, user_id, tx(-52.30, days=-ANTIGA))
    d = decl(lid)
    assert d["matched_transaction_id"] is None and d["requires_review"]


def _sync_cru(cid, *txs):
    db.save_open_finance_sync(cid, [{"provider_account_id": f"account-{cid}", "name": "C", "type": "BANK",
                                     "currency": "BRL", "balance": 1, "transactions": list(txs)}])


def test_congelada_que_perde_a_prova_nao_escolhe_par_na_mesma_passada(user_id):
    """A leitura dessa passada usa o `desde` das ATIVAS (aqui -47): de duas candidatas elegíveis para a
    congelada (b em -49, fora; c em -45, dentro) só uma é lida, e ela ligaria com unicidade falsa."""
    cid, _ = _bank(user_id)
    f = declara(user_id, criado_em=datetime.now(_tz()) - timedelta(days=46))
    sincroniza(cid, user_id, tx(days=-46, txid="a"))
    assert decl(f)["matched_transaction_id"]
    declara(user_id, 70, criado_em=datetime.now(_tz()) - timedelta(days=44))  # ativa: fixa o desde
    _sync_cru(cid, tx(days=-46, txid="a"), tx(days=-49, txid="b"), tx(days=-45, txid="c"))
    assert decl(f)["matched_transaction_id"]  # ainda a prova dela: "a" não mudou
    _sync_cru(cid, tx(-52.30, days=-46, txid="a"), tx(days=-49, txid="b"), tx(days=-45, txid="c"))
    d = decl(f)
    assert d["matched_transaction_id"] is None and d["requires_review"]
    _reconcilia(user_id)  # já sem par, f não é congelada: leitura completa e duas candidatas = ambíguo
    assert decl(f)["matched_transaction_id"] is None
