"""PR-A: editar e apagar a despesa/receita declarada "paga pelo banco".

Valor e data são do extrato que a confere: editar é BLOQUEADO (apaga e lança de novo); categoria e
descrição editam. Apagar derruba a declaração por cascade e devolve a transação como sombra.
Controle negativo: tirar o ramo `declarada` da guarda de data deixa vermelho
`test_data_bloqueada_sem_par_e_com_par`; tirar `declarada` do `PODE_SQL`, o de categoria/descrição.
O de valor passa nos dois casos (sem o ramo o `pode` vira `[]`, que também recusa): é positivo da recusa.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

import db
from db.accounts import LaunchDateLockedError
from db.lancamentos import NaoEditavel
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank, _deposit
from tests.test_declaracao_fluxo_reconcilia import decl, declara, n_linhas, saiu, sincroniza, tx
from utils_date import today_tz


@pytest.mark.parametrize("com_par", [False, True])
@pytest.mark.parametrize("campo", ["dia", "criado_em"])
def test_data_bloqueada_sem_par_e_com_par(user_id, com_par, campo):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    if com_par:
        sincroniza(cid, user_id, tx())
        assert decl(lid)["matched_transaction_id"]
    antes = q("select criado_em from launches where id=%s", (lid,))["criado_em"]
    novo = today_tz() - timedelta(days=1)
    valor = novo if campo == "dia" else antes - timedelta(days=1)
    esperado = "confere com o extrato" if com_par else "ainda não foi conferido"
    with pytest.raises(LaunchDateLockedError, match=f"{esperado}.*apague e lance de novo"):
        db.update_launch_fields(user_id, lid, **{campo: valor})
    assert q("select criado_em from launches where id=%s", (lid,))["criado_em"] == antes


def test_valor_bloqueado_pela_regra_da_v2(user_id):
    lid = declara(user_id)
    with pytest.raises(NaoEditavel):
        db.update_launch_fields(user_id, lid, valor=Decimal("60"), exigir_pode=True)
    assert q("select valor from launches where id=%s", (lid,))["valor"] == 50


def test_categoria_e_descricao_editam(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx())
    assert db.update_launch_fields(user_id, lid, categoria="lazer", alvo="cinema", nota="filme",
                                   exigir_pode=True)
    r = q("select categoria, alvo, nota from launches where id=%s", (lid,))
    assert (r["categoria"], r["alvo"], r["nota"]) == ("lazer", "cinema", "filme")
    assert decl(lid)["matched_transaction_id"]  # editar texto não desfaz a prova


def test_data_de_aporte_declarado_segue_editavel_como_antes(user_id):
    _, source = _bank(user_id)
    lid = _deposit(user_id, source)
    assert db.update_launch_fields(user_id, lid, dia=today_tz() - timedelta(days=1)) is True


def test_apagar_derruba_a_declaracao_e_a_transacao_volta_como_sombra(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx())
    tx_id = decl(lid)["matched_transaction_id"]
    saldo = db.get_balance(user_id)
    db.delete_launch_and_rollback(user_id, lid)
    assert decl(lid) is None
    assert db.get_balance(user_id) == saldo  # delta 0: nada a devolver
    t = q("select imported_launch_id, match_launch_id from open_finance_transactions where id=%s", (tx_id,))
    assert t["imported_launch_id"] is None and t["match_launch_id"] is None
    assert n_linhas(user_id, 50) == 0 and saiu(user_id) == 0
    db.import_open_finance_launches(user_id)
    assert n_linhas(user_id, 50) == 1 and saiu(user_id) == 50  # só a sombra, uma vez


def test_apagar_declarada_sem_par(user_id):
    _bank(user_id)
    lid = declara(user_id)
    db.delete_launch_and_rollback(user_id, lid)
    assert n_linhas(user_id, 50) == 0
    assert decl(lid) is None
