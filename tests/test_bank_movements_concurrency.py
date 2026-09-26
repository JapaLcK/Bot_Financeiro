"""Ordem de bloqueios das declarações versus exclusão de lançamentos."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import psycopg
import pytest
import db
import db.bank_movements as movements
import db.investments as investments_db
from tests._espera_lock import _esperar_backend_travado
from tests.test_bank_movements import _bank, _sync, _tx, _deposit


def _ordered_pair(monkeypatch, first, second, *, esperar_travado=False):
    """Primeiro já detém conta quando o segundo começa; sem trava pós-launch.

    `esperar_travado=True`: o primeiro, segurando o lock, só segue quando algum
    backend deste database estiver esperando lock (o segundo chegou na trava), ou
    depois de 3 s. Sem isso o primeiro podia commitar antes de o segundo chegar lá,
    e a "corrida" virava duas chamadas sequenciais."""
    locked, other_started = Event(), Event()
    original = movements._lock_user
    first_thread = []
    def lock(cur, uid):
        original(cur, uid)
        if first_thread and current_thread().ident == first_thread[0] and not locked.is_set():
            locked.set()
            assert other_started.wait(5)
            if esperar_travado:
                _esperar_backend_travado(3)
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


def _captura(fn):
    """Devolve o resultado OU a exceção: a asserção do teste decide qual é prevista."""
    def run():
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — quem julga é o teste
            return exc
    return run


def _so_recusas_previstas(*resultados):
    for r in resultados:
        if isinstance(r, Exception):
            assert isinstance(r, db.InvestmentMovementNotLast) or (
                isinstance(r, ValueError) and str(r) == "INV_NOT_ZERO"), repr(r)


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
            return db.delete_launch_and_rollback(user_id, previous)
    take = lambda: withdraw(user_id, "viagem", 10)
    a, b = _ordered_pair(monkeypatch, _captura(undo if undo_first else take),
                         _captura(take if undo_first else undo), esperar_travado=True)
    _so_recusas_previstas(a, b)
    observed = float(assets(user_id)[0]["balance"])
    if kind == "pocket":
        assert observed == 70
    else:
        # Saque antes: o desfazer deixa de ser o último movimento e é recusado.
        desfazer = a if undo_first else b
        assert isinstance(desfazer, db.InvestmentMovementNotLast) is not undo_first
        assert observed == (90 if undo_first else 70)
    assert db.get_balance(user_id) == 0


def _cdb_da_carteira(user_id, resgate):
    """Conta 0 → aporte 100 na carteira (sem banco) → resgate; devolve o launch."""
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    db.create_investment(user_id, "cdb", 0.01, "yearly")
    db.investment_deposit_from_account(user_id, "cdb", 100)
    if resgate is None:
        return db.investment_withdraw_to_account(user_id, "cdb", withdraw_all=True)[0]
    return db.investment_withdraw_to_account(user_id, "cdb", resgate)[0]


def _total(user_id):
    return float(db.get_balance(user_id)) + sum(float(i["balance"]) for i in db.list_investments(user_id))


@pytest.mark.parametrize("undo_first", [True, False], ids=["undo_first", "withdraw_first"])
def test_desfazer_e_resgate_na_carteira_serializam(user_id, monkeypatch, undo_first):
    r1 = _cdb_da_carteira(user_id, 20)
    undo = _captura(lambda: db.delete_launch_and_rollback(user_id, r1))
    take = _captura(lambda: db.investment_withdraw_to_account(user_id, "cdb", 10))
    a, b = _ordered_pair(monkeypatch, undo if undo_first else take,
                         take if undo_first else undo, esperar_travado=True)
    _so_recusas_previstas(a, b)
    desfazer = a if undo_first else b
    assert isinstance(desfazer, db.InvestmentMovementNotLast) is not undo_first
    assert _total(user_id) == 100
    assert float(db.list_investments(user_id)[0]["balance"]) == (90 if undo_first else 70)


@pytest.mark.parametrize("undo_first", [True, False], ids=["undo_first", "delete_first"])
def test_desfazer_e_apagar_investimento_serializam(user_id, monkeypatch, undo_first):
    r = _cdb_da_carteira(user_id, None)
    undo = _captura(lambda: db.delete_launch_and_rollback(user_id, r))
    apaga = _captura(lambda: db.delete_investment(user_id, "cdb"))
    a, b = _ordered_pair(monkeypatch, undo if undo_first else apaga,
                         apaga if undo_first else undo, esperar_travado=True)
    _so_recusas_previstas(a, b)
    desfazer, apagar = (a, b) if undo_first else (b, a)
    if undo_first:
        assert not isinstance(desfazer, Exception)
        assert isinstance(apagar, ValueError) and str(apagar) == "INV_NOT_ZERO"
        assert float(db.list_investments(user_id)[0]["balance"]) == 100
        assert db.get_balance(user_id) == 0
    else:
        assert isinstance(desfazer, db.InvestmentMovementNotLast)
        assert db.list_investments(user_id) == []
        assert db.get_balance(user_id) == 100


def test_desfazer_e_accrue_all_nao_deadlockam(user_id, monkeypatch):
    """Ordem das travas: accrue_all pega investimento → lotes; o desfazer tem de
    pegar investimento ANTES dos lotes (a guarda), senão os dois se esperam."""
    r = _cdb_da_carteira(user_id, 20)
    segurando, original = Event(), investments_db.accrue_investment_db

    def pausa(cur, uid, inv_id, today=None):
        if not segurando.is_set():
            segurando.set()
            _esperar_backend_travado(3)
        return original(cur, uid, inv_id, today=today)

    monkeypatch.setattr(investments_db, "accrue_investment_db", pausa)

    def desfazer():
        assert segurando.wait(5)
        return db.delete_launch_and_rollback(user_id, r)

    with ThreadPoolExecutor(max_workers=2) as pool:
        acc = pool.submit(_captura(lambda: db.accrue_all_investments(user_id)))
        und = pool.submit(_captura(desfazer))
        resultados = acc.result(timeout=15), und.result(timeout=15)
    assert not any(isinstance(x, psycopg.errors.DeadlockDetected) for x in resultados), resultados
    assert not any(isinstance(x, Exception) for x in resultados), resultados
    assert float(db.list_investments(user_id)[0]["balance"]) == 100


def test_predicado_de_sombra_nao_arredonda_nem_inclui_fatura():
    assert movements.uses_bank_movement_lock("open_finance", {"delta_conta": 0})
    assert not movements.uses_bank_movement_lock("open_finance", {})
    assert not movements.uses_bank_movement_lock("open_finance", {"delta_conta": "0.0001"})
    assert not movements.uses_bank_movement_lock("manual", {"delta_conta": -100, "bill_id": 1})
