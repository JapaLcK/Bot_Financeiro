from decimal import Decimal

from db import (
    get_balance,
    add_launch_and_update_balance,
    list_launches,
    create_pocket,
    pocket_deposit_from_account,
    pocket_withdraw_to_account,
    create_investment_db,
    investment_deposit_from_account,
    investment_withdraw_to_account,
    delete_launch_and_rollback,
)

def D(x) -> Decimal:
    return Decimal(str(x))

def test_saldo_e_lancamentos(user_id):
    assert get_balance(user_id) == D("0")

    l1, bal = add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    assert bal == D("1000")

    l2, bal = add_launch_and_update_balance(user_id, "despesa", 120, None, "mercado")
    assert bal == D("880")

    rows = list_launches(user_id, limit=10)
    assert len(rows) >= 2


def test_caixinha_deposito_saque(user_id):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_pocket(user_id, "viagem")

    _, bal_acc, bal_pocket, _ = pocket_deposit_from_account(user_id, "viagem", 300, "teste")
    assert bal_acc == D("700")
    assert bal_pocket == D("300")

    _, bal_acc, bal_pocket, _ = pocket_withdraw_to_account(user_id, "viagem", 100, "teste")
    assert bal_acc == D("800")
    assert bal_pocket == D("200")


def test_investimento_aporte_resgate(user_id):
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    create_investment_db(user_id, "CDB Nubank", rate=0.01, period="monthly", nota="teste")

    _, bal_acc, bal_inv, _ = investment_deposit_from_account(user_id, "CDB Nubank", 200, "aporte")
    assert bal_acc == D("800")
    assert bal_inv == D("200")

    _, bal_acc, bal_inv, _ = investment_withdraw_to_account(user_id, "CDB Nubank", 50, "resgate")
    assert bal_acc == D("850")
    assert bal_inv == D("150")


def test_delete_launch_and_rollback(user_id):
    assert get_balance(user_id) == D("0")
    l1, bal = add_launch_and_update_balance(user_id, "receita", 1000, None, "salario")
    l2, bal = add_launch_and_update_balance(user_id, "despesa", 200, None, "mercado")
    assert get_balance(user_id) == D("800")

    delete_launch_and_rollback(user_id, int(l2))
    assert get_balance(user_id) == D("1000")
