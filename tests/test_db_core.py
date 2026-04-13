from decimal import Decimal

from db import (
    get_balance,
    add_launch_and_update_balance,
    list_launches,
    get_summary_by_period,
    create_pocket,
    pocket_deposit_from_account,
    pocket_withdraw_to_account,
    create_investment_db,
    investment_deposit_from_account,
    investment_withdraw_to_account,
    delete_launch_and_rollback,
    attempt_whatsapp_phone_link,
    confirm_email_verification,
    create_email_verification,
    get_conn,
    ensure_user,
)
import uuid

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


def test_summary_by_period_soma_receitas_e_despesas(user_id):
    from datetime import date

    add_launch_and_update_balance(user_id, "receita", 500, None, "pix")
    add_launch_and_update_balance(user_id, "despesa", 135, None, "rifa")

    summary = get_summary_by_period(user_id, date.today(), date.today())

    assert summary["receita"] == D("500")
    assert summary["despesa"] == D("135")


def test_attempt_whatsapp_phone_link_aceita_variacao_com_e_sem_nono_digito(user_id):
    email = f"wa-link-{uuid.uuid4().hex[:8]}@example.com"
    other_uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(other_uid)

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into auth_accounts (user_id, email, password_hash, phone_e164, phone_status)
                    values (%s, %s, %s, %s, 'pending')
                    """,
                    (user_id, email, "hash", "556592741873"),
                )
            conn.commit()

        result = attempt_whatsapp_phone_link("5565992741873", current_user_id=other_uid)

        assert result["status"] == "linked"

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select user_id
                    from user_identities
                    where provider = 'whatsapp' and external_id = %s
                    """,
                    ("5565992741873",),
                )
                row = cur.fetchone()

        assert row["user_id"] == user_id
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from users where id = %s", (other_uid,))
            conn.commit()


def test_confirm_email_verification_normaliza_telefone_armazenado_no_codigo():
    email = f"verify-{uuid.uuid4().hex[:8]}@example.com"
    raw_phone = "+55 (65) 99274-1873"
    result = None

    code = create_email_verification(email, "123456", "5565992741873")

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update email_verification_codes
                    set phone_e164 = %s
                    where email = %s and code = %s
                    """,
                    (raw_phone, email, code),
                )
            conn.commit()

        result = confirm_email_verification(email, code)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select phone_e164 from auth_accounts where user_id = %s",
                    (result["user_id"],),
                )
                row = cur.fetchone()

        assert row["phone_e164"] == "5565992741873"
    finally:
        if result is not None:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("delete from users where id = %s", (result["user_id"],))
                conn.commit()
