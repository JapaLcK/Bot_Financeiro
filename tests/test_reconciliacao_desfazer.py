"""Desfazer uma fusão devolve o gasto à Carteira e a sombra do banco à timeline.

Sentido A: o manual veio antes, o import propôs o casamento e o usuário
confirmou (`confirm_reconciliation` apagou a sombra). Sentido B (manual depois
da importação OF) não funde sozinho: vira pendência e fica SEPARADO, coberto
pelo teste abaixo. No sentido A o cenário é "gastei 50 no mercado" × MERCADO
-50 com o banco em 1000 → 950.
"""
from __future__ import annotations

from datetime import timedelta

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, of_tx_pendente, saldo_bruto,
    sincroniza, tx, uid_pro, ultimo_launch,
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
    rep = db.import_open_finance_launches(uid, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid, of_tx_pendente(uid))
    return conexao


def _desfaz_e_confere(uid, conexao):
    of_tx = _of_tx(uid)
    assert _estado(of_tx)["reconciliation_status"] == "confirmed"
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


def test_manual_criado_depois_do_import_vira_pendencia_e_o_delete_nao_apaga_a_sombra(uid_pro, ia_fora):
    """Decisão "Lançamentos Manuais Exclusivos para Dinheiro": um lançamento
    manual criado depois da importação OF NUNCA funde sozinho — vira pendência
    (`propose_manual_reconciliation`).

    A tx OF continua na PRÓPRIA sombra, com `match` = o manual; o manual fica
    lançamento separado debitando a Carteira. Apagar o manual devolve a
    Carteira sem apagar a sombra: a FK `on delete set null` zera o `match` e a
    pendência vira órfã (fora do aviso, que exige o join com o manual)."""
    conexao = conecta_banco(uid_pro, "950.00", [tx(uid_pro, "-50.00", today_tz(), "MERCADO")])
    assert db.import_open_finance_launches(uid_pro, conexao)["inserted"] == 1
    assert _sombras(uid_pro) == 1
    assert consolidado(uid_pro) == (950.0, 0.0)

    # o dono lança o MESMO gasto à mão depois: pendência, nada funde em silêncio
    manda(uid_pro, "gastei 50 no mercado")
    manual_id = ultimo_launch(uid_pro)

    sombra = _estado(_of_tx(uid_pro))["imported_launch_id"]
    assert _sombras(uid_pro) == 1
    assert _estado(_of_tx(uid_pro)) == {"imported_launch_id": sombra, "match_launch_id": manual_id,
                                        "reconciliation_status": "pending"}
    # sombra (delta 0) + manual (-50): o consolidado some os dois
    assert consolidado(uid_pro) == (900.0, -50.0)

    # apagar o manual: a Carteira volta, a sombra fica, a pendência vira órfã
    db.delete_launch_and_rollback(uid_pro, manual_id)
    assert consolidado(uid_pro) == (950.0, 0.0)
    assert _sombras(uid_pro) == 1
    assert _estado(_of_tx(uid_pro)) == {"imported_launch_id": sombra, "match_launch_id": None,
                                        "reconciliation_status": "pending"}
    assert db.reconciliation_summary(uid_pro)["pending_count"] == 0


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


def test_desfazer_reaproveita_sombra_que_sobreviveu(uid_pro, ia_fora):
    """Dado histórico: uma sombra pode sobreviver com a transação já fundida (a
    antiga fusão reversa engolia a recusa do delete, `except: pass`). Desfazer
    reusa essa sombra em vez de criar outra — o cenário agora chega na fusão
    por confirmação e semeia a sobrevivente com o mesmo `external_id`."""
    funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    sobrevivente = _q("""insert into launches(user_id, tipo, valor, categoria, alvo, criado_em, efeitos,
                                              source, external_id, posted_at, currency)
                         values (%s,'despesa',50,'outros','MERCADO',now(),'{"delta_conta": 0}',
                                 'open_finance',%s,%s,'BRL') returning id""",
                      (uid_pro, f"of-tx-{uid_pro}-1", today_tz()))[0]["id"]

    r = db.undo_reconciliation(uid_pro, of_tx)

    assert r == {"ok": True, "changed": True, "launch_id": sobrevivente}
    assert _sombras(uid_pro) == 1
    assert consolidado(uid_pro) == (900.0, -50.0)


def test_fusao_antiga_sem_match_aparece_e_desfaz(uid_pro, ia_fora):
    """Dado antigo: `auto_merged` com imported = X e `match_launch_id` nulo.
    `funde_a` chega na fusão via confirmação (status 'confirmed'); o update
    regride o status para simular a linha legítima de dados antigos."""
    funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    _q("update open_finance_transactions set match_launch_id=null, "
       "reconciliation_status='auto_merged' where id=%s returning id", (of_tx,))

    assert [r["of_tx_id"] for r in db.list_reconciliations(uid_pro)] == [of_tx]
    assert db.undo_reconciliation(uid_pro, of_tx)["changed"] is True
    assert consolidado(uid_pro) == (900.0, -50.0)
    assert _sombras(uid_pro) == 1


def test_fusao_com_match_em_outro_lancamento_nao_desfaz(uid_pro, ia_fora):
    """imported = X e match = Y ≠ X: estado incoerente, não é fusão a desfazer."""
    funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    db.add_launch_and_update_balance(uid_pro, "despesa", 5, "outro", None)
    y = ultimo_launch(uid_pro)
    _q("update open_finance_transactions set match_launch_id=%s where id=%s returning id", (y, of_tx))
    antes = consolidado(uid_pro)

    assert of_tx not in [r["of_tx_id"] for r in db.list_reconciliations(uid_pro)]
    assert db.undo_reconciliation(uid_pro, of_tx)["changed"] is False
    assert consolidado(uid_pro) == antes
    assert _sombras(uid_pro) == 0
