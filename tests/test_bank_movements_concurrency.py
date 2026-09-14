"""Ordem de bloqueios das declarações versus exclusão de lançamentos."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import pytest
import db
import db.bank_movements as movements
from tests.test_bank_movements import _bank, _sync, _tx, _deposit


def _ordered_pair(monkeypatch, first, second):
    """Primeiro já detém conta quando o segundo começa; sem trava pós-launch."""
    locked, other_started = Event(), Event()
    original = movements._lock_user
    first_thread = []
    def lock(cur, uid):
        original(cur, uid)
        if first_thread and current_thread().ident == first_thread[0] and not locked.is_set():
            locked.set()
            assert other_started.wait(5)
    monkeypatch.setattr(movements, "_lock_user", lock)
    def run_first():
        first_thread.append(current_thread().ident)
        return first()
    def run_second():
        assert locked.wait(5)
        other_started.set()
        return second()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run_first), pool.submit(run_second)
        return a.result(timeout=15), b.result(timeout=15)


@pytest.mark.parametrize("delete_first", [False, True])
@pytest.mark.parametrize("reclassified", [False, True])
def test_conferir_e_excluir_sombra_sem_deadlock(user_id, monkeypatch, delete_first, reclassified):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    _sync(cid, 500, _tx(category="Restaurants" if reclassified else "Same person transfer"))
    db.import_open_finance_launches(user_id)
    if reclassified:
        # Simula a correção do provedor antes da atualização da sombra antiga.
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("update open_finance_transactions set category='Fixed income' where account_id=%s", (source["of_account_id"],))
            conn.commit()
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id,imported_launch_id from open_finance_transactions where account_id=%s", (source["of_account_id"],))
        tx = cur.fetchone()
    def confirm():
        return movements.confirm_bank_movement(user_id, lid, tx["id"])
    def delete():
        try:
            return db.delete_launch_and_rollback(user_id, tx["imported_launch_id"])
        except LookupError as exc:
            assert str(exc) == "NOT_FOUND"
    _ordered_pair(monkeypatch, delete if delete_first else confirm, confirm if delete_first else delete)
    assert movements.bank_movement_summary(user_id)["pending_count"] == 0
    assert float(db.list_pockets(user_id)[0]["balance"]) == 500
    assert db.get_balance(user_id) == 0


@pytest.mark.parametrize("kind", ["pocket", "investment"])
@pytest.mark.parametrize("undo_first", [False, True])
def test_undo_e_saque_mesmo_ativo_equivalem_a_ordem_sequencial(user_id, monkeypatch, kind, undo_first):
    cid, source = _bank(user_id)
    if kind == "pocket":
        _deposit(user_id, source, 100)
        withdraw = db.pocket_withdraw_to_account
        assets = db.list_pockets
    else:
        db.create_investment(user_id, "viagem", 0.01, "yearly")
        db.investment_deposit_from_account(user_id, "viagem", 100, funding_source=source)
        withdraw = db.investment_withdraw_to_account
        assets = db.list_investments
    previous = withdraw(user_id, "viagem", 20)[0]
    def undo():
        if kind == "pocket":
            # O undo de pocket_lot_withdrawals já é recusado pelo contrato atual.
            # A recusa deve preservar o lote, inclusive concorrendo com saque.
            from db.accounts import LaunchUnsafeRollback
            with pytest.raises(LaunchUnsafeRollback, match="pocket_lot_withdrawals"):
                db.delete_launch_and_rollback(user_id, previous)
        else:
            db.delete_launch_and_rollback(user_id, previous)
    take = lambda: withdraw(user_id, "viagem", 10)
    _ordered_pair(monkeypatch, undo if undo_first else take, take if undo_first else undo)
    observed = float(assets(user_id)[0]["balance"])
    if kind == "pocket":
        assert observed == 70
    else:
        # O undo legado restaura o snapshot anterior inteiro: se houve saque
        # posterior, não conserva saldo (comprovado também em 0964d08).
        # Esta regressão mede serialização do MESMO ativo, sem fazer do saldo
        # legado incorreto um requisito nem ampliar a capacidade de undo.
        db.create_investment(user_id, "referencia", 0.01, "yearly")
        db.investment_deposit_from_account(user_id, "referencia", 100, funding_source=source)
        ref_id = withdraw(user_id, "referencia", 20)[0]
        if undo_first:
            db.delete_launch_and_rollback(user_id, ref_id)
            withdraw(user_id, "referencia", 10)
        else:
            withdraw(user_id, "referencia", 10)
            db.delete_launch_and_rollback(user_id, ref_id)
        reference = next(asset for asset in assets(user_id) if asset["name"] == "referencia")
        assert observed == float(reference["balance"])
    assert db.get_balance(user_id) == 0


def test_predicado_de_sombra_nao_arredonda_nem_inclui_fatura():
    assert movements.uses_bank_movement_lock("open_finance", {"delta_conta": 0})
    assert not movements.uses_bank_movement_lock("open_finance", {})
    assert not movements.uses_bank_movement_lock("open_finance", {"delta_conta": "0.0001"})
    assert not movements.uses_bank_movement_lock("manual", {"delta_conta": -100, "bill_id": 1})
