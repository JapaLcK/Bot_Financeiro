"""Provas em centavos e invalidação da origem bancária."""
from datetime import timedelta, datetime
from decimal import Decimal

import pytest
import db
from db.bank_movements import bank_movement_summary, list_bank_movements, confirm_bank_movement
from db.open_finance import pause_open_finance_connection, update_pluggy_open_finance_item_status
from tests.test_bank_movements import _bank, _sync, _tx, _deposit
from utils_date import _tz


@pytest.mark.parametrize("kind", ["pocket", "investment"])
@pytest.mark.parametrize("category", ["Fixed income", "Same person transfer"])
def test_liquido_subcentavos_preserva_lote_e_confere_centavos(user_id, kind, category):
    cid, source = _bank(user_id)
    if kind == "pocket":
        _deposit(user_id, source, 100)
    else:
        db.create_investment(user_id, "viagem", 0.01, "yearly")
        db.investment_deposit_from_account(user_id, "viagem", 100, funding_source=source)
    _sync(cid, 900, _tx(-100, "aporte"))
    today = datetime.now(_tz()).date()
    lots = "pocket_lots" if kind == "pocket" else "investment_lots"
    assets = "pockets" if kind == "pocket" else "investments"
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"update {lots} set balance=120.011,opened_at=%s,last_date=%s where user_id=%s", (today-timedelta(days=40), today, user_id))
        cur.execute(f"update {assets} set balance=120.011 where user_id=%s", (user_id,))
        conn.commit()
    fn = db.pocket_withdraw_to_account if kind == "pocket" else db.investment_withdraw_to_account
    result = fn(user_id, "viagem", None, withdraw_all=True)
    assert Decimal(str(result[4]["net"])) == Decimal("115.511")
    lid = result[0]
    _sync(cid, 1015.50, _tx(Decimal("115.50"), "resgate", category))
    assert list_bank_movements(user_id)[0]["candidates"] == []
    _sync(cid, 1015.51, _tx(Decimal("115.51"), "resgate", category))
    if category == "Same person transfer":
        candidate = list_bank_movements(user_id)[0]["candidates"][0]["id"]
        confirm_bank_movement(user_id, lid, candidate)
    assert bank_movement_summary(user_id)["pending_count"] == 0
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select amount from bank_movement_declarations where launch_id=%s", (lid,))
        assert cur.fetchone()["amount"] == Decimal("115.51")
    assert db.get_balance(user_id) == 0


@pytest.mark.parametrize("event", ["pause", "deleted", "sync_deleted", "transaction_deleted", "disconnect"])
def test_perder_prova_limpa_confirmacao_sem_reverter_declaracao(user_id, event):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    _sync(cid, 500, _tx())
    assert bank_movement_summary(user_id)["pending_count"] == 0
    if event == "pause":
        pause_open_finance_connection(cid)
    elif event == "deleted":
        update_pluggy_open_finance_item_status(f"decl-{user_id}", "DELETED")
    elif event == "sync_deleted":
        from db.open_finance_state import mark_sync_result
        mark_sync_result(cid, ok=False, status="DELETED")
    elif event == "transaction_deleted":
        db.delete_open_finance_transactions(f"decl-{user_id}", ["tx1"])
    else:
        db.disconnect_open_finance_connection(user_id, cid)
    assert bank_movement_summary(user_id)["pending_count"] == 1
    assert float(db.list_pockets(user_id)[0]["balance"]) == 500
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from bank_movement_declarations where launch_id=%s", (lid,))
        row = cur.fetchone()
        assert row["confirmation_method"] is None
        assert row["confirmed_at"] is None
        assert row["requires_review"]


def test_reconexao_exige_escolher_prova_da_nova_conta(user_id):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    _sync(cid, 500, _tx())
    newer = db.save_pluggy_open_finance_item(user_id, {"id": f"new-{user_id}", "status": "UPDATED"})
    db.save_open_finance_sync(newer["id"], [{"provider_account_id": f"account-{cid}", "name": "Nova conta", "type": "BANK", "currency": "BRL", "balance": 500, "transactions": [_tx()]}])
    db.import_open_finance_launches(user_id)
    pending = list_bank_movements(user_id)
    assert len(pending) == 1
    assert len(pending[0]["candidates"]) == 1
    confirm_bank_movement(user_id, lid, pending[0]["candidates"][0]["id"])
    assert bank_movement_summary(user_id)["pending_count"] == 0
    assert float(db.list_pockets(user_id)[0]["balance"]) == 500


def test_post_idempotente_revalida_prova_atual(user_id):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    _sync(cid, 500, _tx())
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select matched_transaction_id from bank_movement_declarations where launch_id=%s", (lid,))
        txid = cur.fetchone()["matched_transaction_id"]
        # Uma correção antes do hook não autoriza repetir confirmação obsoleta.
        cur.execute("update open_finance_transactions set amount=-600 where id=%s", (txid,))
        conn.commit()
    with pytest.raises(ValueError, match="MOVEMENT_INCOMPATIBLE"):
        confirm_bank_movement(user_id, lid, txid)


@pytest.mark.parametrize("value,expected", [("1.005", "1.01"), ("-1.005", "-1.01"), ("NaN", None), ("1e100", None)])
def test_unidade_monetaria_nao_quebra_por_valor_legado(value, expected):
    from db.bank_movements import _money
    assert _money(value) == (Decimal(expected) if expected is not None else None)
