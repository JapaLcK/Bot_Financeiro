"""Confirmar e rejeitar uma pendência de reconciliação, pelo caminho de produção.

Cenário: "gastei 1 real com a barbara" à mão (Carteira -1) e o banco manda
"COMPRA CARTAO 4412 XPTO" de -1 — descrição que não parece a do manual, então o
import deixa `pending` em vez de fundir. Pendente conta duas vezes: (112.88, -1.0).
"""
from __future__ import annotations

import asyncio

import pytest

import db
import frontend.finance_bot_websocket_custom as dashboard
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, sincroniza, tx, uid_pro, ultimo_launch,
)


def _q(sql, params):
    with db.connection.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        conn.commit()
    return rows


def _gasto_do_mes(uid):
    h = today_tz()
    return asyncio.run(dashboard.get_financial_data(uid, year=h.year, month=h.month))["monthly_expense"]


def pendencia(uid, valor="-1.00", frase="Gastei 1 real com a barbara",
              saldo="113.88", descricao="COMPRA CARTAO 4412 XPTO"):
    """Deixa uma pendência e devolve (conexão, of_tx_id, manual_id, sombra_id)."""
    conexao = conecta_banco(uid, "114.88")
    manda(uid, frase)
    manual_id = ultimo_launch(uid)
    sincroniza(conexao, uid, saldo, [tx(uid, valor, today_tz(), descricao)])
    rep = db.import_open_finance_launches(uid, conexao)
    assert rep["pending"] == 1, rep
    row = _q("""select o.id, o.imported_launch_id from open_finance_transactions o
                  join open_finance_accounts a on a.id=o.account_id
                  join open_finance_connections c on c.id=a.connection_id
                 where c.user_id=%s and o.reconciliation_status='pending'""", (uid,))[0]
    return conexao, row["id"], manual_id, row["imported_launch_id"]


def _estado(of_tx_id):
    return dict(_q("select imported_launch_id, match_launch_id, reconciliation_status "
                   "from open_finance_transactions where id=%s", (of_tx_id,))[0])


def test_confirmar_funde_e_repetir_nao_mexe(uid_pro, ia_fora):
    _, of_tx, manual, sombra = pendencia(uid_pro)
    assert consolidado(uid_pro) == (112.88, -1.0)
    assert _gasto_do_mes(uid_pro) == 2.0

    r = db.confirm_reconciliation(uid_pro, of_tx)

    assert r == {"ok": True, "changed": True, "launch_id": manual}
    assert _estado(of_tx) == {"imported_launch_id": manual, "match_launch_id": manual,
                              "reconciliation_status": "confirmed"}
    assert not _q("select 1 from launches where id=%s", (sombra,)), "a sombra ficou"
    assert consolidado(uid_pro) == (113.88, 0.0)
    assert _gasto_do_mes(uid_pro) == 1.0

    assert db.confirm_reconciliation(uid_pro, of_tx)["changed"] is False
    assert consolidado(uid_pro) == (113.88, 0.0)


def test_confirmar_com_x_ja_vinculado_e_conflito(uid_pro, ia_fora):
    _, of_tx, manual, _ = pendencia(uid_pro)
    # outra transação do banco já ocupa X (ex.: fundida antes por outro caminho)
    outra = _q("""insert into open_finance_transactions
                    (account_id, provider_transaction_id, description, amount, transaction_date,
                     imported_launch_id, match_launch_id, reconciliation_status)
                  select account_id, 'outra', 'OUTRA', -1, transaction_date, %s, %s, 'auto_merged'
                    from open_finance_transactions where id=%s returning id""",
               (manual, manual, of_tx))
    assert outra
    with pytest.raises(ValueError, match="ALREADY_LINKED"):
        db.confirm_reconciliation(uid_pro, of_tx)
    assert _estado(of_tx)["reconciliation_status"] == "pending"


def test_rejeitar_mantem_os_numeros_e_repetir_e_noop(uid_pro, ia_fora):
    _, of_tx, _, sombra = pendencia(uid_pro)
    antes = consolidado(uid_pro), _gasto_do_mes(uid_pro)

    assert db.reject_reconciliation(uid_pro, of_tx) == {"ok": True, "changed": True}
    assert _estado(of_tx) == {"imported_launch_id": sombra, "match_launch_id": None,
                              "reconciliation_status": "imported"}
    assert (consolidado(uid_pro), _gasto_do_mes(uid_pro)) == antes
    assert db.reject_reconciliation(uid_pro, of_tx)["changed"] is False


def test_rejeitar_sobre_fusao_e_noop(uid_pro, ia_fora):
    _, of_tx, manual, _ = pendencia(uid_pro)
    db.confirm_reconciliation(uid_pro, of_tx)
    assert db.reject_reconciliation(uid_pro, of_tx)["changed"] is False
    assert _estado(of_tx)["reconciliation_status"] == "confirmed"
    assert consolidado(uid_pro) == (113.88, 0.0)


def test_pendencia_com_x_apagado_sai_da_lista_e_do_resumo(uid_pro, ia_fora):
    _, of_tx, manual, _ = pendencia(uid_pro)
    assert [r["of_tx_id"] for r in db.list_reconciliations(uid_pro)] == [of_tx]
    assert db.reconciliation_summary(uid_pro)["pending_count"] == 1

    db.delete_launch_and_rollback(uid_pro, manual)

    assert db.list_reconciliations(uid_pro) == []
    assert db.reconciliation_summary(uid_pro)["pending_count"] == 0
    with pytest.raises(ValueError, match="MATCH_NOT_FOUND"):
        db.confirm_reconciliation(uid_pro, of_tx)


def test_transacao_de_outro_usuario_nao_existe(uid_pro, ia_fora):
    _, of_tx, _, _ = pendencia(uid_pro)
    outro = uid_pro + 1
    db.ensure_user(outro)
    for fn in (db.confirm_reconciliation, db.reject_reconciliation, db.undo_reconciliation):
        with pytest.raises(LookupError):
            fn(outro, of_tx)
    assert db.list_reconciliations(outro) == []
    assert _estado(of_tx)["reconciliation_status"] == "pending"
