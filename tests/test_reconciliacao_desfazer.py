"""Desfazer uma fusão devolve o gasto à Carteira e a sombra do banco à timeline.

Sentido A: o manual veio antes e o import fundiu. Sentido B: o banco veio antes
e o manual novo fundiu (`reconcile_manual_launch` apagou a sombra). Nos dois o
cenário é "gastei 50 no mercado" × MERCADO -50 com o banco em 1000 → 950.
"""
from __future__ import annotations

from datetime import timedelta

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, saldo_bruto, sincroniza, tx, uid_pro,
    ultimo_launch,
)
from tests.test_reconciliacao_resolver import _estado, _gasto_do_mes, _q, pendencia


def _of_tx(uid):
    return _q("""select o.id from open_finance_transactions o
                   join open_finance_accounts a on a.id=o.account_id
                   join open_finance_connections c on c.id=a.connection_id
                  where c.user_id=%s and o.provider_transaction_id=%s""",
              (uid, f"of-tx-{uid}-1"))[0]["id"]


def _sombras(uid):
    return _q("select count(*) as n from launches where user_id=%s and source='open_finance' "
              "and external_id=%s", (uid, f"of-tx-{uid}-1"))[0]["n"]


def funde_a(uid):
    conexao = conecta_banco(uid, "1000.00")
    manda(uid, "gastei 50 no mercado")
    sincroniza(conexao, uid, "950.00", [tx(uid, "-50.00", today_tz(), "MERCADO")])
    assert db.import_open_finance_launches(uid, conexao)["auto_merged"] == 1
    return conexao


def funde_b(uid):
    conexao = conecta_banco(uid, "950.00", [tx(uid, "-50.00", today_tz(), "MERCADO")])
    assert db.import_open_finance_launches(uid, conexao)["inserted"] == 1
    manda(uid, "gastei 50 no mercado")
    assert _sombras(uid) == 0, "a fusão reversa não aconteceu"
    return conexao


def _desfaz_e_confere(uid, conexao):
    of_tx = _of_tx(uid)
    assert _estado(of_tx)["reconciliation_status"] == "auto_merged"
    assert consolidado(uid) == (950.0, 0.0)
    assert _gasto_do_mes(uid) == 50.0

    r = db.undo_reconciliation(uid, of_tx)

    assert r["changed"] is True
    assert _estado(of_tx) == {"imported_launch_id": r["launch_id"], "match_launch_id": None,
                              "reconciliation_status": "imported"}
    assert consolidado(uid) == (900.0, -50.0)
    assert _gasto_do_mes(uid) == 100.0
    assert _sombras(uid) == 1

    assert db.undo_reconciliation(uid, of_tx)["changed"] is False
    assert _sombras(uid) == 1
    assert consolidado(uid) == (900.0, -50.0)

    # o sync seguinte não refunde nem duplica
    rep = db.import_open_finance_launches(uid, conexao)
    assert (rep["inserted"], rep["auto_merged"]) == (0, 0), rep
    assert consolidado(uid) == (900.0, -50.0)
    assert _sombras(uid) == 1


def test_desfazer_fusao_do_import(uid_pro, ia_fora):
    _desfaz_e_confere(uid_pro, funde_a(uid_pro))


def test_desfazer_fusao_reversa(uid_pro, ia_fora):
    _desfaz_e_confere(uid_pro, funde_b(uid_pro))


def test_desfazer_confirmada(uid_pro, ia_fora):
    _, of_tx, manual, _ = pendencia(uid_pro)
    db.confirm_reconciliation(uid_pro, of_tx)
    assert consolidado(uid_pro) == (113.88, 0.0)

    r = db.undo_reconciliation(uid_pro, of_tx)

    assert r["changed"] is True
    assert _estado(of_tx)["reconciliation_status"] == "imported"
    assert consolidado(uid_pro) == (112.88, -1.0)
    assert _sombras(uid_pro) == 1


def test_desfazer_com_conexao_pausada(uid_pro, ia_fora):
    conexao = funde_a(uid_pro)
    _q("update open_finance_connections set status='PAUSED' where id=%s returning id", (conexao,))
    assert consolidado(uid_pro) == (-50.0, -50.0)

    assert db.undo_reconciliation(uid_pro, _of_tx(uid_pro))["changed"] is True

    assert consolidado(uid_pro) == (-50.0, -50.0)
    assert saldo_bruto(uid_pro) == -50
    assert _sombras(uid_pro) == 1


def test_fusao_historica_credito_em_conta_fica_intacta(uid_pro, ia_fora):
    """Fusão antiga semeada (alvo nulo, receita 73,38): nem import nem manual
    novo a reescrevem — este PR não mexe em fusão histórica."""
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "1000.00")
    db.add_launch_and_update_balance(uid_pro, "receita", 73.38, None, None)
    manual = ultimo_launch(uid_pro)
    of_tx = _q("""insert into open_finance_transactions
                    (account_id, provider_transaction_id, description, amount, transaction_date,
                     imported_launch_id, match_launch_id, reconciliation_status)
                  select a.id, 'hist', 'Crédito em conta', 73.38, %s, %s, %s, 'auto_merged'
                    from open_finance_accounts a where a.connection_id=%s returning id""",
               (hoje - timedelta(days=3), manual, manual, conexao))[0]["id"]
    antes = _estado(of_tx)

    sincroniza(conexao, uid_pro, "980.00", [tx(uid_pro, "-20.00", hoje, "CINEMA")])
    db.import_open_finance_launches(uid_pro, conexao)
    manda(uid_pro, "recebi 73,38 do fulano")

    assert _estado(of_tx) == antes
