"""Faturas encerram sua transação antes da conferência de declarações BANK."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, local

import psycopg
import pytest
import db
import db.cards as cards
import db.open_finance as of
import db.bank_movements as movements
from tests.test_bank_movements import _bank, _deposit, _tx


def _row(sql, params):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _write(sql, params):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        conn.commit()


def _credit(uid, cid):
    db.save_open_finance_sync(cid, [{"provider_account_id": f"credit-{uid}", "name": "Cartão",
        "type": "CREDIT", "currency": "BRL", "balance": -100,
        "transactions": [_tx(-100, "credit-tx", "Restaurants")]}])
    db.import_open_finance_credit(uid, cid)
    return _row("""select t.id,t.imported_credit_tx_id,ct.card_id,ct.bill_id from open_finance_transactions t
                    join credit_transactions ct on ct.id=t.imported_credit_tx_id where ct.user_id=%s""", (uid,))


@pytest.mark.parametrize("operation", ["sync", "disconnect"])
@pytest.mark.parametrize("with_declaration", [False, True])
def test_pagamento_e_fase_credit_progridem_sem_ciclo(user_id, monkeypatch, operation, with_declaration):
    cid, source = _bank(user_id)
    if with_declaration:
        _deposit(user_id, source)
    db.add_launch_and_update_balance(user_id, "receita", 1000, "saldo", None)
    linked = _credit(user_id, cid)
    if operation == "sync":
        _write("update open_finance_transactions set amount=-200 where id=%s", (linked["id"],))
    else:
        # Cartão vazio com fatura real: DELETE card chega ao cascade de bill.
        _write("delete from credit_transactions where id=%s", (linked["imported_credit_tx_id"],))
    bill_locked, credit_write = Event(), Event()
    phase = local()
    original_payment, original_execute, original_conn = cards.add_launch_and_update_balance, psycopg.Cursor.execute, of.get_conn
    def payment_write(*args, **kwargs):
        bill_locked.set()
        assert credit_write.wait(5)
        return original_payment(*args, **kwargs)
    def execute(cur, query, *args, **kwargs):
        sql = str(query).strip().lower()
        if getattr(phase, "credit", False) and sql.startswith(("update credit_bills", "delete from credit_cards")):
            credit_write.set()
        return original_execute(cur, query, *args, **kwargs)
    @contextmanager
    def bounded(*args, **kwargs):
        with original_conn(*args, **kwargs) as conn:
            conn.execute("set local statement_timeout='1500ms'")
            yield conn
    monkeypatch.setattr(cards, "add_launch_and_update_balance", payment_write)
    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    monkeypatch.setattr(of, "get_conn", bounded)
    def update():
        assert bill_locked.wait(5)
        phase.credit = True
        if operation == "sync":
            return db.sync_imported_open_finance_updates(user_id, cid)
        return db.disconnect_open_finance_connection(user_id, cid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        paid = pool.submit(db.pay_bill_amount, user_id, linked["card_id"], "Cartão", 100, linked["bill_id"])
        changed = pool.submit(update)
        res_pago = paid.result(timeout=10)
        assert res_pago
        assert changed.result(timeout=10)
    # NO CONTRATO NOVO o pagamento da fatura com Open Finance ativo NÃO debita
    # a Carteira Piggy (o débito ocorre no banco): saldo intacto e o lançamento
    # do pagamento carrega `delta_conta: 0` + origem `bank`. O que este teste
    # mede é a ordenação de travas (pagamento × fase credit sem ciclo) — a
    # aritmética do saldo é só o efeito observável do pagamento ter passado.
    assert db.get_balance(user_id) == 1000
    efeitos = _row("select efeitos from launches where id=%s", (res_pago["launch_id"],))["efeitos"]
    assert efeitos["delta_conta"] == 0
    assert efeitos["funding_source"]["kind"] == "bank"
    if operation == "sync":
        bill = _row("select total,paid_amount from credit_bills where id=%s", (linked["bill_id"],))
        assert bill == {"total": 200, "paid_amount": 100}
    else:
        assert _row("select id from credit_cards where id=%s", (linked["card_id"],)) is None
    if with_declaration:
        assert float(db.list_pockets(user_id)[0]["balance"]) == 500
        assert movements.bank_movement_summary(user_id)["pending_count"] == 1


@pytest.mark.parametrize("amount,days", [(-200, 0), (25, 0), (-200, 40)])
def test_credit_commit_e_retry_apos_falha_bank(user_id, monkeypatch, amount, days):
    cid, source = _bank(user_id)
    _deposit(user_id, source)
    linked = _credit(user_id, cid)
    _write("update open_finance_transactions set amount=%s,transaction_date=transaction_date+%s where id=%s", (amount, days, linked["id"]))
    original = movements.reconcile_bank_movements
    def fail(*args):
        raise RuntimeError("falha fase BANK")
    monkeypatch.setattr(movements, "reconcile_bank_movements", fail)
    with pytest.raises(RuntimeError, match="fase BANK"):
        db.sync_imported_open_finance_updates(user_id, cid)
    before = _row("select valor,bill_id from credit_transactions where id=%s", (linked["imported_credit_tx_id"],))
    assert before["valor"] == -amount
    if days:
        assert before["bill_id"] != linked["bill_id"]
        assert _row("select total from credit_bills where id=%s", (linked["bill_id"],))["total"] == 0
    assert movements.bank_movement_summary(user_id)["pending_count"] == 1
    monkeypatch.setattr(movements, "reconcile_bank_movements", original)
    assert db.sync_imported_open_finance_updates(user_id, cid)["credit_updated"] == 0
    assert _row("select valor,bill_id from credit_transactions where id=%s", (linked["imported_credit_tx_id"],)) == before
    assert _row("select total from credit_bills where id=%s", (before["bill_id"],))["total"] == -amount
    assert float(db.list_pockets(user_id)[0]["balance"]) == 500


def test_disconnect_retry_e_swept_out_so_apos_commit(user_id, monkeypatch):
    cid, source = _bank(user_id)
    _deposit(user_id, source)
    linked = _credit(user_id, cid)
    original = movements.reconcile_bank_movements
    def fail(*args):
        raise RuntimeError("falha delete final")
    monkeypatch.setattr(movements, "reconcile_bank_movements", fail)
    swept = []
    with pytest.raises(RuntimeError, match="delete final"):
        db.disconnect_open_finance_connection(user_id, cid, swept_out=swept)
    assert swept == []
    assert _row("select id from open_finance_connections where id=%s", (cid,))
    assert _row("select id from credit_cards where id=%s", (linked["card_id"],)) is None
    monkeypatch.setattr(movements, "reconcile_bank_movements", original)
    assert db.disconnect_open_finance_connection(user_id, cid, swept_out=swept) == 1
    assert swept == [f"decl-{user_id}"]
    assert db.disconnect_open_finance_connection(user_id, cid) == 0
    assert float(db.list_pockets(user_id)[0]["balance"]) == 500


def test_disconnect_preserva_cartao_com_compra_manual(user_id):
    cid, source = _bank(user_id)
    linked = _credit(user_id, cid)
    from datetime import date
    purchase, _, _ = db.add_credit_purchase(user_id, linked["card_id"], 50, "outros", "Manual", date.today())
    db.disconnect_open_finance_connection(user_id, cid)
    card = _row("select open_finance_account_id from credit_cards where id=%s", (linked["card_id"],))
    assert card == {"open_finance_account_id": None}
    assert _row("select valor from credit_transactions where id=%s", (purchase,))["valor"] == 50
