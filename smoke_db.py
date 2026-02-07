import os
from decimal import Decimal
from dotenv import load_dotenv
load_dotenv()


from db import (
    init_db, ensure_user, get_balance,
    add_launch_and_update_balance, list_launches,
    create_pocket, list_pockets, pocket_deposit_from_account, pocket_withdraw_to_account, delete_pocket,
    create_investment_db, list_investments, investment_deposit_from_account, investment_withdraw_to_account,
    delete_launch_and_rollback,
)

USER_ID = 999999  # um id fake pra teste

def money(x):
    return Decimal(str(x))

def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg} | esperado={b} obtido={a}")

def run():
    if not os.getenv("DATABASE_URL"):
        raise SystemExit("Faltou DATABASE_URL no ambiente.")

    init_db()
    ensure_user(USER_ID)

    # saldo inicial
    bal = get_balance(USER_ID)
    assert_eq(bal, money("0"), "Saldo inicial")

    # receita 1000
    l1, bal = add_launch_and_update_balance(USER_ID, "receita", 1000, None, "salario")
    assert_eq(bal, money("1000"), "Depois receita 1000")

    # despesa 120
    l2, bal = add_launch_and_update_balance(USER_ID, "despesa", 120, None, "mercado")
    assert_eq(bal, money("880"), "Depois despesa 120")

    # cria caixinha
    launch_cp, pocket_id, pocket_name = create_pocket(USER_ID, "viagem")
    assert pocket_name.lower() == "viagem"

    # deposita 300 na caixinha
    l3, bal_acc, bal_pocket, _ = pocket_deposit_from_account(USER_ID, "viagem", 300, "teste deposito")
    assert_eq(bal_acc, money("580"), "Conta após depositar 300 na caixinha")
    assert_eq(bal_pocket, money("300"), "Caixinha após depositar 300")

    # saca 100 da caixinha
    l4, bal_acc, bal_pocket, _ = pocket_withdraw_to_account(USER_ID, "viagem", 100, "teste saque")
    assert_eq(bal_acc, money("680"), "Conta após sacar 100 da caixinha")
    assert_eq(bal_pocket, money("200"), "Caixinha após sacar 100")

    # cria investimento (1% ao mês)
    li, inv_id, inv_name = create_investment_db(USER_ID, "CDB Nubank", rate=0.01, period="monthly", nota="teste")
    assert inv_name.lower() == "cdb nubank"

    # aplica 200 no investimento
    l5, bal_acc, bal_inv, _ = investment_deposit_from_account(USER_ID, "CDB Nubank", 200, "apliquei 200")
    assert_eq(bal_acc, money("480"), "Conta após aplicar 200")
    assert_eq(bal_inv, money("200"), "Invest após aplicar 200")

    # retira 50 do investimento
    l6, bal_acc, bal_inv, _ = investment_withdraw_to_account(USER_ID, "CDB Nubank", 50, "retirei 50")
    assert_eq(bal_acc, money("530"), "Conta após retirar 50")
    assert_eq(bal_inv, money("150"), "Invest após retirar 50")

    # rollback de um lançamento (apagar e reverter)
    delete_launch_and_rollback(USER_ID, int(l6))
    bal = get_balance(USER_ID)
    # se removeu o saque de 50, conta volta pra 480
    assert_eq(bal, money("480"), "Conta após rollback do saque (delete_launch_and_rollback)")

    # zera caixinha e apaga
    pocket_withdraw_to_account(USER_ID, "viagem", 200, "zerar")
    # agora pode apagar
    launch_del, canon = delete_pocket(USER_ID, "viagem")
    assert canon.lower() == "viagem"

    print("✅ SMOKE DB OK")

if __name__ == "__main__":
    run()
