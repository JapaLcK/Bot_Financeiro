from datetime import date
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
