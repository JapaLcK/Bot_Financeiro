from datetime import date, timedelta
from decimal import Decimal

import db
import db.investments as investments_db


def test_accrue_investment_db_cdi_sem_novas_datas_publicadas_nao_avanca_last_date(user_id):
    inv_id = None
    original_balance = Decimal("1000")
    original_last_date = date(2026, 4, 14)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investments(user_id, name, balance, rate, period, last_date)
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (user_id, "CDB CDI Teste", original_balance, Decimal("1.16"), "cdi", original_last_date),
            )
            inv_id = cur.fetchone()["id"]
        conn.commit()

    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                original_fetch = db._get_cdi_daily_map
                db._get_cdi_daily_map = lambda _cur, start, end: {}
                try:
                    new_bal = db.accrue_investment_db(cur, user_id, inv_id, today=date(2026, 4, 16))
                    assert new_bal == original_balance
                finally:
                    db._get_cdi_daily_map = original_fetch
            conn.rollback()

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select balance, last_date from investments where id=%s and user_id=%s", (inv_id, user_id))
                row = cur.fetchone()

        assert row["balance"] == original_balance
        assert row["last_date"] == original_last_date
    finally:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from investments where id=%s and user_id=%s", (inv_id, user_id))
            conn.commit()


def test_accrue_investment_db_cdi_avanca_ate_ultima_data_publicada(user_id):
    inv_id = None
    original_balance = Decimal("1000")
    original_last_date = date(2026, 4, 14)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investments(user_id, name, balance, rate, period, last_date)
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (user_id, "CDB CDI Datas Publicadas", original_balance, Decimal("1.16"), "cdi", original_last_date),
            )
            inv_id = cur.fetchone()["id"]
        conn.commit()

    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                original_fetch = db._get_cdi_daily_map
                db._get_cdi_daily_map = lambda _cur, start, end: {
                    date(2026, 4, 15): 0.05,
                    date(2026, 4, 17): 0.06,
                }
                try:
                    new_bal = db.accrue_investment_db(cur, user_id, inv_id, today=date(2026, 4, 20))
                finally:
                    db._get_cdi_daily_map = original_fetch
            conn.commit()

        expected = Decimal(str(1000 * (1 + 0.05 / 100 * 1.16) * (1 + 0.06 / 100 * 1.16)))
        assert abs(new_bal - expected) < Decimal("0.000001")

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("select balance, last_date from investments where id=%s and user_id=%s", (inv_id, user_id))
                row = cur.fetchone()

        assert abs(row["balance"] - expected) < Decimal("0.000001")
        assert row["last_date"] == date(2026, 4, 17)
    finally:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from investments where id=%s and user_id=%s", (inv_id, user_id))
            conn.commit()


def test_accrue_investment_db_cdi_spread_composto(user_id):
    inv_id = None
    original_balance = Decimal("1000")
    original_last_date = date(2026, 4, 14)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investments(user_id, name, balance, rate, period, last_date)
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (user_id, "CDB CDI Spread", original_balance, Decimal("0.025"), "cdi_spread", original_last_date),
            )
            inv_id = cur.fetchone()["id"]
        conn.commit()

    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                original_fetch = db._get_cdi_daily_map
                db._get_cdi_daily_map = lambda _cur, start, end: {
                    date(2026, 4, 15): 0.05,
                    date(2026, 4, 16): 0.06,
                }
                try:
                    new_bal = db.accrue_investment_db(cur, user_id, inv_id, today=date(2026, 4, 17))
                finally:
                    db._get_cdi_daily_map = original_fetch
            conn.commit()

        spread_daily = (1 + 0.025) ** (1 / 252) - 1
        expected = Decimal(str(1000 * (1 + 0.05 / 100) * (1 + spread_daily) * (1 + 0.06 / 100) * (1 + spread_daily)))
        assert abs(new_bal - expected) < Decimal("0.000001")
    finally:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from investments where id=%s and user_id=%s", (inv_id, user_id))
            conn.commit()


def test_accrue_investment_db_selic_spread_composto(user_id):
    inv_id = None
    original_balance = Decimal("1000")
    original_last_date = date(2026, 4, 14)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investments(user_id, name, balance, rate, period, last_date)
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (user_id, "Tesouro Selic Spread", original_balance, Decimal("0.0007"), "selic_spread", original_last_date),
            )
            inv_id = cur.fetchone()["id"]
        conn.commit()

    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                original_fetch = investments_db._get_selic_daily_map
                investments_db._get_selic_daily_map = lambda _cur, start, end: {
                    date(2026, 4, 15): 0.05,
                    date(2026, 4, 16): 0.06,
                }
                try:
                    new_bal = db.accrue_investment_db(cur, user_id, inv_id, today=date(2026, 4, 17))
                finally:
                    investments_db._get_selic_daily_map = original_fetch
            conn.commit()

        spread_daily = (1 + 0.0007) ** (1 / 252) - 1
        expected = Decimal(str(1000 * (1 + 0.05 / 100) * (1 + spread_daily) * (1 + 0.06 / 100) * (1 + spread_daily)))
        assert abs(new_bal - expected) < Decimal("0.000001")
    finally:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from investments where id=%s and user_id=%s", (inv_id, user_id))
            conn.commit()


def test_accrue_investment_db_ipca_spread_composto(user_id):
    inv_id = None
    original_balance = Decimal("1000")
    original_last_date = date(2026, 1, 31)

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investments(user_id, name, balance, rate, period, last_date)
                values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (user_id, "Tesouro IPCA Spread", original_balance, Decimal("0.0743"), "ipca_spread", original_last_date),
            )
            inv_id = cur.fetchone()["id"]
        conn.commit()

    try:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                original_fetch = investments_db._get_ipca_monthly_map
                investments_db._get_ipca_monthly_map = lambda _cur, start, end: {
                    date(2026, 2, 1): 0.30,
                    date(2026, 3, 1): 0.40,
                }
                try:
                    new_bal = db.accrue_investment_db(cur, user_id, inv_id, today=date(2026, 4, 1))
                finally:
                    investments_db._get_ipca_monthly_map = original_fetch
            conn.commit()

        spread_monthly = (1 + 0.0743) ** (1 / 12) - 1
        expected = Decimal(str(1000 * (1 + 0.30 / 100) * (1 + spread_monthly) * (1 + 0.40 / 100) * (1 + spread_monthly)))
        assert abs(new_bal - expected) < Decimal("0.000001")
    finally:
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from investments where id=%s and user_id=%s", (inv_id, user_id))
            conn.commit()


def test_aporte_em_investimento_cria_lote_individual(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_investment_db(user_id, "CDB Lote", rate=0.01, period="monthly", nota="teste")

    _, bal_acc, bal_inv, _ = db.investment_deposit_from_account(user_id, "CDB Lote", 200, "aporte")

    assert bal_acc == Decimal("800")
    assert bal_inv == Decimal("200")
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select principal_initial, principal_remaining, balance, status
                from investment_lots
                where user_id=%s and investment_id=(
                    select id from investments where user_id=%s and name='CDB Lote'
                )
                """,
                (user_id, user_id),
            )
            lot = cur.fetchone()

    assert lot["principal_initial"] == Decimal("200")
    assert lot["principal_remaining"] == Decimal("200")
    assert lot["balance"] == Decimal("200")
    assert lot["status"] == "open"


def test_resgate_usa_peps_e_calcula_ir_por_lote(user_id):
    today = date.today()
    db.set_balance(user_id, Decimal("0"))
    _, inv_id, _ = db.create_investment_db(
        user_id,
        "CDB PEPS",
        rate=0.01,
        period="yearly",
        nota="teste",
        tax_profile="regressive_ir_iof",
    )

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investment_lots(
                    user_id, investment_id, principal_initial, principal_remaining,
                    balance, opened_at, last_date, status
                )
                values
                    (%s,%s,500,500,530,%s,%s,'open'),
                    (%s,%s,500,500,510,%s,%s,'open')
                """,
                (
                    user_id, inv_id, today - timedelta(days=800), today,
                    user_id, inv_id, today - timedelta(days=40), today,
                ),
            )
            cur.execute(
                "update investments set balance=1040, last_date=%s where id=%s and user_id=%s",
                (today, inv_id, user_id),
            )
        conn.commit()

    _, bal_acc, bal_inv, _, taxes = db.investment_withdraw_to_account(user_id, "CDB PEPS", 700, "resgate")

    assert bal_acc.quantize(Decimal("0.01")) == Decimal("694.75")
    assert bal_inv.quantize(Decimal("0.01")) == Decimal("340.00")
    assert Decimal(str(taxes["gross"])).quantize(Decimal("0.01")) == Decimal("700.00")
    assert Decimal(str(taxes["ir"])).quantize(Decimal("0.01")) == Decimal("5.25")
    assert Decimal(str(taxes["iof"])).quantize(Decimal("0.01")) == Decimal("0.00")
    assert len(taxes["lots"]) == 2

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select balance, principal_remaining, status
                from investment_lots
                where user_id=%s and investment_id=%s
                order by opened_at, id
                """,
                (user_id, inv_id),
            )
            lots = cur.fetchall()

    assert lots[0]["status"] == "closed"
    assert lots[0]["balance"] == Decimal("0")
    assert lots[1]["status"] == "open"
    assert lots[1]["balance"].quantize(Decimal("0.01")) == Decimal("340.00")
    assert lots[1]["principal_remaining"].quantize(Decimal("0.01")) == Decimal("333.33")


def test_resgate_de_ativo_isento_nao_desconta_ir_iof(user_id):
    today = date.today()
    db.set_balance(user_id, Decimal("0"))
    _, inv_id, _ = db.create_investment_db(
        user_id,
        "LCI Isenta",
        rate=0.01,
        period="yearly",
        nota="teste",
        asset_type="LCI",
        tax_profile="exempt_ir_iof",
    )

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into investment_lots(
                    user_id, investment_id, principal_initial, principal_remaining,
                    balance, opened_at, last_date, status
                )
                values (%s,%s,500,500,530,%s,%s,'open')
                """,
                (user_id, inv_id, today - timedelta(days=10), today),
            )
            cur.execute(
                "update investments set balance=530, last_date=%s where id=%s and user_id=%s",
                (today, inv_id, user_id),
            )
        conn.commit()

    _, bal_acc, bal_inv, _, taxes = db.investment_withdraw_to_account(user_id, "LCI Isenta", 100, "resgate")

    assert bal_acc == Decimal("100")
    assert bal_inv == Decimal("430")
    assert Decimal(str(taxes["ir"])) == Decimal("0.0")
    assert Decimal(str(taxes["iof"])) == Decimal("0.0")
