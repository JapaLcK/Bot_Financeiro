from decimal import Decimal
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from utils_date import _tz

from .connection import get_conn
from .users import ensure_user


MOCK_OPEN_FINANCE_INSTITUTIONS = {
    "nubank": {
        "id": "mock-nubank",
        "name": "Nubank",
        "connector_id": "612",
    },
    "itau": {
        "id": "mock-itau",
        "name": "Itaú",
        "connector_id": "601",
    },
    "bradesco": {
        "id": "mock-bradesco",
        "name": "Bradesco",
        "connector_id": "603",
    },
}


def _mock_open_finance_institution(key: str | None = None) -> dict:
    normalized = (key or "nubank").strip().lower()
    normalized = normalized.replace("ú", "u").replace("ã", "a")
    normalized = normalized.replace(" ", "")
    return MOCK_OPEN_FINANCE_INSTITUTIONS.get(normalized, MOCK_OPEN_FINANCE_INSTITUTIONS["nubank"])


def create_mock_open_finance_connection(user_id: int, institution_key: str | None = None) -> dict:
    """
    Simula o fluxo Pluggy/Open Finance para desenvolvimento.
    Nao altera o saldo manual do PigBank; salva dados importados em tabelas separadas.
    """
    ensure_user(user_id)
    institution = _mock_open_finance_institution(institution_key)
    provider_item_id = f"mock-pluggy-{user_id}-{institution['id']}"
    now = datetime.now(_tz())
    today = now.date()

    raw_connection = {
        "provider": "mock_pluggy",
        "connectorId": institution["connector_id"],
        "environment": "sandbox",
        "products": ["ACCOUNTS", "TRANSACTIONS", "CREDIT_CARDS"],
    }

    account_specs = [
        {
            "provider_account_id": f"{provider_item_id}-checking",
            "name": f"{institution['name']} Conta",
            "type": "CHECKING_ACCOUNT",
            "subtype": "CONTA_CORRENTE",
            "balance": Decimal("4320.75"),
            "transactions": [
                ("tx-salary", "Salário", Decimal("6500.00"), today - timedelta(days=6), "receita"),
                ("tx-market", "Mercado", Decimal("-184.32"), today - timedelta(days=4), "alimentação"),
                ("tx-pix", "Pix recebido", Decimal("250.00"), today - timedelta(days=3), "transferência"),
                ("tx-uber", "Uber", Decimal("-38.90"), today - timedelta(days=1), "transporte"),
            ],
        },
        {
            "provider_account_id": f"{provider_item_id}-card",
            "name": f"{institution['name']} Cartão",
            "type": "CREDIT_CARD",
            "subtype": "CARTAO_CREDITO",
            "balance": Decimal("-845.90"),
            "transactions": [
                ("tx-card-ifood", "iFood", Decimal("-72.40"), today - timedelta(days=5), "alimentação"),
                ("tx-card-streaming", "Streaming", Decimal("-39.90"), today - timedelta(days=2), "assinaturas"),
                ("tx-card-pharmacy", "Farmácia", Decimal("-56.12"), today - timedelta(days=1), "saúde"),
            ],
        },
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into open_finance_connections (
                    user_id, provider, provider_item_id, status, institution_id,
                    institution_name, consent_url, consent_expires_at, last_sync_at, raw, updated_at
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (user_id, provider, provider_item_id)
                do update set status = excluded.status,
                              institution_name = excluded.institution_name,
                              consent_url = excluded.consent_url,
                              consent_expires_at = excluded.consent_expires_at,
                              last_sync_at = excluded.last_sync_at,
                              raw = excluded.raw,
                              updated_at = excluded.updated_at
                returning id, provider_item_id, status, institution_name, consent_url, last_sync_at
                """,
                (
                    user_id,
                    "mock_pluggy",
                    provider_item_id,
                    "ACTIVE",
                    institution["id"],
                    institution["name"],
                    f"https://mock.pluggy.local/connect/{provider_item_id}",
                    now + timedelta(minutes=30),
                    now,
                    Jsonb(raw_connection),
                    now,
                ),
            )
            connection = cur.fetchone()
            connection_id = connection["id"]

            account_count = 0
            transaction_count = 0

            for account in account_specs:
                account_raw = {
                    "connectorId": institution["connector_id"],
                    "institution": institution["name"],
                    "mock": True,
                }
                cur.execute(
                    """
                    insert into open_finance_accounts (
                        connection_id, provider_account_id, name, type,
                        subtype, currency, balance, raw, updated_at
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (connection_id, provider_account_id)
                    do update set name = excluded.name,
                                  type = excluded.type,
                                  subtype = excluded.subtype,
                                  currency = excluded.currency,
                                  balance = excluded.balance,
                                  raw = excluded.raw,
                                  updated_at = excluded.updated_at
                    returning id
                    """,
                    (
                        connection_id,
                        account["provider_account_id"],
                        account["name"],
                        account["type"],
                        account["subtype"],
                        "BRL",
                        account["balance"],
                        Jsonb(account_raw),
                        now,
                    ),
                )
                account_id = cur.fetchone()["id"]
                account_count += 1

                for tx_id, description, amount, transaction_date, category in account["transactions"]:
                    provider_transaction_id = f"{account['provider_account_id']}-{tx_id}"
                    tx_raw = {
                        "mock": True,
                        "providerItemId": provider_item_id,
                        "accountId": account["provider_account_id"],
                    }
                    cur.execute(
                        """
                        insert into open_finance_transactions (
                            account_id, provider_transaction_id, description,
                            amount, transaction_date, category, raw
                        )
                        values (%s,%s,%s,%s,%s,%s,%s)
                        on conflict (account_id, provider_transaction_id)
                        do update set description = excluded.description,
                                      amount = excluded.amount,
                                      transaction_date = excluded.transaction_date,
                                      category = excluded.category,
                                      raw = excluded.raw
                        """,
                        (
                            account_id,
                            provider_transaction_id,
                            description,
                            amount,
                            transaction_date,
                            category,
                            Jsonb(tx_raw),
                        ),
                    )
                    transaction_count += 1

        conn.commit()

    return {
        "connection": connection,
        "accounts_synced": account_count,
        "transactions_synced": transaction_count,
    }


def get_open_finance_snapshot(user_id: int, limit: int = 8) -> dict:
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, provider, provider_item_id, status, institution_name, last_sync_at
                from open_finance_connections
                where user_id=%s
                order by updated_at desc, id desc
                """,
                (user_id,),
            )
            connections = cur.fetchall()

            cur.execute(
                """
                select c.id as connection_id, c.institution_name, a.id, a.name, a.type,
                       a.subtype, a.currency, a.balance, a.updated_at
                from open_finance_accounts a
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id=%s
                order by c.updated_at desc, a.type, a.name
                """,
                (user_id,),
            )
            accounts = cur.fetchall()

            cur.execute(
                """
                select c.institution_name, a.name as account_name, t.id, t.description,
                       t.amount, t.transaction_date, t.category
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id=%s
                order by t.transaction_date desc, t.id desc
                limit %s
                """,
                (user_id, limit),
            )
            transactions = cur.fetchall()

    return {
        "connections": connections,
        "accounts": accounts,
        "transactions": transactions,
    }


def save_pluggy_open_finance_item(user_id: int, item: dict) -> dict:
    ensure_user(user_id)

    if not isinstance(item, dict):
        raise ValueError("Item Pluggy inválido.")

    item_id = item.get("id") or item.get("itemId")
    if not item_id:
        raise ValueError("Item Pluggy sem id.")
    item_id = str(item_id)

    connector = item.get("connector") or {}
    institution_id = (
        connector.get("id")
        or item.get("connectorId")
        or item.get("institutionId")
        or "pluggy"
    )
    institution_name = (
        connector.get("name")
        or connector.get("institutionName")
        or item.get("connectorName")
        or item.get("institutionName")
        or item.get("name")
        or "Banco conectado"
    )
    status = item.get("status") or item.get("executionStatus") or "UPDATING"
    now = datetime.now(_tz())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into open_finance_connections (
                    user_id, provider, provider_item_id, status, institution_id,
                    institution_name, consent_url, consent_expires_at, last_sync_at, raw, updated_at
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (user_id, provider, provider_item_id)
                do update set status = excluded.status,
                              institution_id = excluded.institution_id,
                              institution_name = excluded.institution_name,
                              last_sync_at = excluded.last_sync_at,
                              raw = excluded.raw,
                              updated_at = excluded.updated_at
                returning id, provider, provider_item_id, status, institution_name, last_sync_at
                """,
                (
                    user_id,
                    "pluggy",
                    item_id,
                    str(status).upper(),
                    str(institution_id),
                    str(institution_name),
                    None,
                    None,
                    now,
                    Jsonb(item),
                    now,
                ),
            )
            connection = cur.fetchone()
        conn.commit()

    return connection


def update_pluggy_open_finance_item_status(provider_item_id: str, status: str, raw: dict | None = None) -> int:
    item_id = (provider_item_id or "").strip()
    if not item_id:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update open_finance_connections
                set status=%s,
                    raw=coalesce(%s, raw),
                    updated_at=now()
                where provider='pluggy' and provider_item_id=%s
                """,
                (status, Jsonb(raw) if raw is not None else None, item_id),
            )
            updated = cur.rowcount
        conn.commit()

    return updated


def disconnect_open_finance_connection(user_id: int, connection_id: int | None = None) -> int:
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            if connection_id is None:
                cur.execute("delete from open_finance_connections where user_id=%s", (user_id,))
            else:
                cur.execute(
                    "delete from open_finance_connections where user_id=%s and id=%s",
                    (user_id, connection_id),
                )
            deleted = cur.rowcount
        conn.commit()

    return deleted
