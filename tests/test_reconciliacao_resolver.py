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
    assert of_tx not in [r["of_tx_id"] for r in db.list_reconciliations(uid_pro)]
    assert db.reconciliation_summary(uid_pro)["pending_count"] == 0
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


# ── duas pendências no MESMO X: lista e resumo seguem uma regra só ─────────

def _lista_e_contagem(uid):
    pend = [r["of_tx_id"] for r in db.list_reconciliations(uid) if r["status"] == "pending"]
    return sorted(pend), db.reconciliation_summary(uid)["pending_count"]


def _duas_no_mesmo_x(uid):
    hoje = today_tz()
    conexao = conecta_banco(uid, "114.88")
    manda(uid, "Gastei 1 real com a barbara")
    manual = ultimo_launch(uid)
    sincroniza(conexao, uid, "112.88", [
        tx(uid, "-1.00", hoje, "COMPRA CARTAO 4412 XPTO", ident="1"),
        tx(uid, "-1.00", hoje, "COMPRA CARTAO 9981 ABCD", ident="2"),
    ])
    assert db.import_open_finance_launches(uid, conexao)["pending"] == 2
    ids = [r["id"] for r in _q("""select o.id from open_finance_transactions o
                                    join open_finance_accounts a on a.id=o.account_id
                                    join open_finance_connections c on c.id=a.connection_id
                                   where c.user_id=%s and o.match_launch_id=%s order by o.id""",
                                (uid, manual))]
    assert len(ids) == 2
    return conexao, manual, ids


def test_duas_pendencias_no_mesmo_x_lista_e_contagem_batem(uid_pro, ia_fora):
    _, _, ids = _duas_no_mesmo_x(uid_pro)
    assert _lista_e_contagem(uid_pro) == (ids, 2)
    assert db.reconciliation_summary(uid_pro)["delta_se_confirmar"] == 1, "X conta uma vez só"


def test_confirmar_a_primeira_resolve_a_irma(uid_pro, ia_fora):
    _, manual, (primeira, irma) = _duas_no_mesmo_x(uid_pro)
    sombra_irma = _estado(irma)["imported_launch_id"]

    db.confirm_reconciliation(uid_pro, primeira)

    assert _lista_e_contagem(uid_pro) == ([], 0)
    assert _estado(irma) == {"imported_launch_id": sombra_irma, "match_launch_id": None,
                             "reconciliation_status": "imported"}
    assert consolidado(uid_pro) == (112.88, 0.0)


def test_conexao_pausada_some_da_lista_e_da_contagem(uid_pro, ia_fora):
    conexao, _, _ = _duas_no_mesmo_x(uid_pro)
    _q("update open_finance_connections set status='PAUSED' where id=%s returning id", (conexao,))
    assert _lista_e_contagem(uid_pro) == ([], 0)


# ── casos MISTOS no mesmo usuário: só a não acionável sai ─────────────────

def _segundo_banco_com_pendencia(uid):
    """Outra conta (outra identidade) com "gastei 50 no mercado" pendente."""
    item = db.save_pluggy_open_finance_item(uid, {
        "id": f"item-2-{uid}", "connector": {"id": 77, "name": "Inter"}, "status": "UPDATED"})
    manda(uid, "gastei 50 no mercado")
    db.save_open_finance_sync(item["id"], [{
        "provider_account_id": f"acc-2-{uid}", "name": "Inter Conta", "type": "BANK",
        "currency": "BRL", "balance": 500, "raw": {},
        "transactions": [tx(uid, "-50.00", today_tz(), "COMPRA CARTAO 9981 ABCD", ident="b2")]}])
    assert db.import_open_finance_launches(uid, item["id"])["pending"] == 1
    of_tx = _q("select id from open_finance_transactions where provider_transaction_id=%s",
               (f"of-tx-{uid}-b2",))[0]["id"]
    return item["id"], of_tx


def test_acionavel_e_pausada_no_mesmo_usuario(uid_pro, ia_fora):
    _, acionavel, _, _ = pendencia(uid_pro)
    conexao2, pausada = _segundo_banco_com_pendencia(uid_pro)
    assert _lista_e_contagem(uid_pro) == (sorted([acionavel, pausada]), 2)

    _q("update open_finance_connections set status='PAUSED' where id=%s returning id", (conexao2,))

    assert _lista_e_contagem(uid_pro) == ([acionavel], 1)


def test_acionavel_e_x_ocupado_no_mesmo_usuario(uid_pro, ia_fora):
    _, acionavel, _, _ = pendencia(uid_pro)
    _, ocupada = _segundo_banco_com_pendencia(uid_pro)
    x = _estado(ocupada)["match_launch_id"]
    _q("""insert into open_finance_transactions
            (account_id, provider_transaction_id, description, amount, transaction_date,
             imported_launch_id, match_launch_id, reconciliation_status)
          select account_id, 'ocupa-x', 'OUTRA', -50, transaction_date, %s, %s, 'auto_merged'
            from open_finance_transactions where id=%s returning id""", (x, x, ocupada))

    assert _lista_e_contagem(uid_pro) == ([acionavel], 1)
