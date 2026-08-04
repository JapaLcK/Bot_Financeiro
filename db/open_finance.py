import re
import unicodedata
from decimal import Decimal
from datetime import datetime, timedelta
from uuid import NAMESPACE_OID, uuid5

from psycopg.types.json import Jsonb

from utils_date import _tz, add_months, billing_period_for_close_day

from .accounts import delete_launch_and_rollback
from .cards import (
    add_imported_credit_purchase,
    extract_installment_info,
    get_or_create_open_finance_card,
    remove_single_credit_transaction,
)
from .connection import get_conn
from .users import ensure_user


def _rollback_imported_of(rows: list[dict]) -> None:
    """Reverte os artefatos importados de um conjunto de transações OF (launches + fatura).

    Cada `row` traz: user_id, imported_launch_id, imported_credit_tx_id, launch_source.
    - Cartão: `undo_credit_transaction` (ajusta o total da fatura).
    - Launch: só apaga se for do OF (`source='open_finance'`); se foi auto-merge num lançamento
      MANUAL, preserva o manual (só desvincula — a OF tx some depois de qualquer jeito).
    Cada função gerencia própria conexão; falhas são engolidas pra não travar a limpeza.
    """
    for r in rows:
        uid = r.get("user_id")
        ctx = r.get("imported_credit_tx_id")
        if ctx:
            try:
                # remoção ÚNICA: apagar 1 parcela não pode cascatear o parcelamento inteiro.
                remove_single_credit_transaction(uid, ctx)
            except Exception:
                pass
        lid = r.get("imported_launch_id")
        if lid and (r.get("launch_source") == "open_finance"):
            try:
                delete_launch_and_rollback(uid, lid)
            except Exception:
                pass


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

            cur.execute(
                """
                select c.institution_name, i.id, i.name, i.type, i.subtype, i.balance
                from open_finance_investments i
                join open_finance_connections c on c.id = i.connection_id
                where c.user_id=%s
                order by i.balance desc nulls last, i.id
                """,
                (user_id,),
            )
            investments = cur.fetchall()

    return {
        "connections": connections,
        "accounts": accounts,
        "transactions": transactions,
        "investments": investments,
    }


def count_open_finance_connections(user_id: int, provider: str = "pluggy") -> int:
    """Quantos bancos o usuário tem conectados (default: só Pluggy reais). Pro gate por nº de bancos."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if provider:
                cur.execute(
                    "select count(*) as n from open_finance_connections where user_id=%s and provider=%s",
                    (user_id, provider),
                )
            else:
                cur.execute(
                    "select count(*) as n from open_finance_connections where user_id=%s",
                    (user_id,),
                )
            return cur.fetchone()["n"]


def list_open_finance_user_ids() -> list[int]:
    """user_ids distintos com pelo menos 1 banco Pluggy conectado (pros ticks proativos)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select distinct user_id from open_finance_connections where provider='pluggy'")
            return [r["user_id"] for r in cur.fetchall()]


def list_pluggy_item_ids(user_id: int | None = None) -> list[str]:
    """Item ids Pluggy ativos (todos, ou de um usuário). Usado no refresh periódico."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if user_id is None:
                cur.execute("select provider_item_id from open_finance_connections where provider='pluggy'")
            else:
                cur.execute(
                    "select provider_item_id from open_finance_connections where provider='pluggy' and user_id=%s",
                    (user_id,),
                )
            return [r["provider_item_id"] for r in cur.fetchall() if r["provider_item_id"]]


def list_connections_needing_reconnect(user_id: int | None = None, within_days: int = 7) -> list[dict]:
    """Conexões em erro OU com consentimento vencendo em `within_days` dias.

    Base pra um aviso proativo de 'reconectar/renovar' (P1 #5/#6). Sem isso, o dado
    para de atualizar em silêncio — pior que não ter dado.
    """
    sql = """
        select id, user_id, provider_item_id, institution_name, status,
               consent_expires_at, last_sync_at
        from open_finance_connections
        where provider = 'pluggy'
          and (
            upper(coalesce(status, '')) in ('ERROR', 'LOGIN_ERROR', 'OUTDATED', 'WAITING_USER_INPUT')
            or (consent_expires_at is not null
                and consent_expires_at <= now() + make_interval(days => %s))
          )
    """
    params: list = [within_days]
    if user_id is not None:
        sql += " and user_id = %s"
        params.append(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


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


def save_open_finance_investments(connection_id: int, investments: list[dict]) -> dict:
    """Grava (upsert) os investimentos OF — inclui Caixinha (CDB). Espelho, não vira pocket ainda."""
    now = datetime.now(_tz())
    count = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for inv in investments:
                if not inv.get("provider_investment_id"):
                    continue
                cur.execute(
                    """
                    insert into open_finance_investments (
                        connection_id, provider_investment_id, name, type, subtype,
                        currency, balance, raw, updated_at
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (connection_id, provider_investment_id)
                    do update set name=excluded.name, type=excluded.type, subtype=excluded.subtype,
                                  currency=excluded.currency, balance=excluded.balance,
                                  raw=excluded.raw, updated_at=excluded.updated_at
                    """,
                    (connection_id, inv["provider_investment_id"], inv.get("name"), inv.get("type"),
                     inv.get("subtype"), inv.get("currency") or "BRL", inv.get("balance") or Decimal("0"),
                     Jsonb(inv.get("raw") or {}), now),
                )
                count += 1
        conn.commit()
    return {"investments_synced": count}


def get_open_finance_connection_by_item_id(provider_item_id: str, provider: str = "pluggy") -> dict | None:
    """Localiza a conexão pelo item da Pluggy (o webhook só traz o item, não o user)."""
    item_id = (provider_item_id or "").strip()
    if not item_id:
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, user_id, provider, provider_item_id, status, institution_name
                from open_finance_connections
                where provider=%s and provider_item_id=%s
                limit 1
                """,
                (provider, item_id),
            )
            return cur.fetchone()


def delete_open_finance_transactions(
    provider_item_id: str, transaction_ids: list[str], provider: str = "pluggy"
) -> int:
    """Remove transações OF pelo provider_transaction_id (evento transactions/deleted da Pluggy).

    P0 (integridade): antes de apagar o espelho OF, REVERTE o que foi importado —
    o launch (Fase 1) e a transação de fatura (Fase 1a). Sem isso, deletar uma transação
    no banco deixava launch/fatura órfãos, inflando gastos pra sempre.
    """
    item_id = (provider_item_id or "").strip()
    ids = [str(t).strip() for t in (transaction_ids or []) if str(t).strip()]
    if not item_id or not ids:
        return 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id, c.user_id, t.imported_launch_id, t.imported_credit_tx_id,
                       l.source as launch_source
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                left join launches l on l.id = t.imported_launch_id
                where c.provider = %s and c.provider_item_id = %s
                  and t.provider_transaction_id = any(%s)
                """,
                (provider, item_id, ids),
            )
            rows = cur.fetchall()

    if not rows:
        return 0

    _rollback_imported_of(rows)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "delete from open_finance_transactions where id = any(%s)",
                ([r["id"] for r in rows],),
            )
            deleted = cur.rowcount
        conn.commit()

    return deleted


def save_open_finance_sync(connection_id: int, accounts: list[dict]) -> dict:
    """
    Grava contas + transações reais puxadas da Pluggy nas tabelas OF.
    `accounts` é uma lista de dicts já normalizados, cada um com uma lista `transactions`.
    Idempotente: usa os mesmos ON CONFLICT (connection_id, provider_account_id) e
    (account_id, provider_transaction_id) do fluxo mock. NÃO toca no saldo manual (isso é Fase 1).
    """
    now = datetime.now(_tz())
    account_count = 0
    transaction_count = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            for account in accounts:
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
                        account.get("subtype"),
                        account.get("currency") or "BRL",
                        account.get("balance") or Decimal("0"),
                        Jsonb(account.get("raw") or {}),
                        now,
                    ),
                )
                account_db_id = cur.fetchone()["id"]
                account_count += 1

                for tx in account.get("transactions", []):
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
                            account_db_id,
                            tx["provider_transaction_id"],
                            tx["description"],
                            tx["amount"],
                            tx["transaction_date"],
                            tx.get("category"),
                            Jsonb(tx.get("raw") or {}),
                        ),
                    )
                    transaction_count += 1

            cur.execute(
                """
                update open_finance_connections
                set last_sync_at=%s, updated_at=%s
                where id=%s
                """,
                (now, now, connection_id),
            )
        conn.commit()

    return {"accounts_synced": account_count, "transactions_synced": transaction_count}


# ──────────────────────────────────────────────────────────────────────────────
# Fase 1 — import OF → launches (analytics) + saldo consolidado
# ──────────────────────────────────────────────────────────────────────────────

# Movimento interno = dinheiro indo pra poupança/caixinha/conta própria, NÃO gasto.
# Baseado na taxonomia real do Pluggy (GET /categories). Caixinha do Nubank é um CDB:
# aparece na conta como "Automatic investment"/"Fixed income" (aplicação/resgate).
# NÃO inclui "Proceeds interests and dividends" (isso é RENDA de investimento).
_OF_INTERNAL_CATEGORIES = (
    "automatic investment",   # aplicação automática (Caixinha do Nubank)
    "fixed income",           # CDB que lastreia a caixinha
    "same person transfer",   # transferência entre contas próprias (+ variantes por prefixo)
    "transfer - internal",
)
# Fallback por descrição, pra bancos sem a categoria enriquecida (Pro) do Pluggy.
_OF_INTERNAL_KEYWORDS = (
    "aplicacao", "aplicação", "resgate", "caixinha",
    "poupanca", "poupança", "cofrinho",
)


def classify_open_finance_launch(amount, category: str | None = None, description: str | None = None) -> dict:
    """Puro: decide tipo/valor/is_internal a partir da transação OF (amount já assinado).

    Caixinha do banco (aplicação/resgate) e transferência entre contas próprias viram
    movimento interno — ficam fora do cálculo de gastos/"sobrou" (igual `deposito_caixinha`).
    """
    v = Decimal(str(amount))
    tipo = "despesa" if v < 0 else "receita"
    cat = (category or "").strip().lower()
    desc = (description or "").lower()
    is_internal = (
        any(cat == c or cat.startswith(c) for c in _OF_INTERNAL_CATEGORIES)
        or any(k in desc for k in _OF_INTERNAL_KEYWORDS)
    )
    return {"tipo": tipo, "valor": abs(v), "is_internal_movement": is_internal}


# ──────────────────────────────────────────────────────────────────────────────
# Fase 2 — reconciliação OF ↔ manual (não duplicar)
# ──────────────────────────────────────────────────────────────────────────────

# Descrições manuais genéricas (sem estabelecimento) — casam por valor+data mesmo
# sem bater o nome. Normalizadas (sem acento).
_GENERIC_MERCHANTS = {
    "", "almoco", "comida", "gasto", "compra", "lanche", "jantar", "cafe",
    "diversos", "outros", "conta", "boleto", "pix",
}

RECON_AMOUNT_TOL = Decimal("0.05")
RECON_DATE_WINDOW = 3  # dias (janela de graça: o OF chega 0-2 dias depois)


def _normalize_merchant(s) -> str:
    text = unicodedata.normalize("NFKD", str(s or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def merchant_similarity(a, b) -> bool:
    """Puro: dois estabelecimentos são 'parecidos'? Substring ou token (>=3) em comum."""
    na, nb = _normalize_merchant(a), _normalize_merchant(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta = {t for t in na.split() if len(t) >= 3}
    tb = {t for t in nb.split() if len(t) >= 3}
    return bool(ta & tb)


def _is_generic_merchant(s) -> bool:
    return _normalize_merchant(s) in _GENERIC_MERCHANTS


def pick_reconciliation_match(valor, tx_date, description, candidates) -> dict:
    """Puro. Decide se a transação OF casa com um lançamento manual.

    candidates: [{id, valor, ref_date(date), alvo, nota}]. Retorna {launch_id, verdict}:
      'auto' = mesclar sozinho (alto-confiança), 'ask' = perguntar (ambíguo), 'none' = sem match.
    """
    target = Decimal(str(valor))
    elig = []
    for c in candidates:
        try:
            cv = Decimal(str(c["valor"]))
        except (KeyError, TypeError, ValueError):
            continue
        if abs(cv - target) > RECON_AMOUNT_TOL:
            continue
        cd = c.get("ref_date")
        if cd is None or abs((cd - tx_date).days) > RECON_DATE_WINDOW:
            continue
        merchant = f"{c.get('alvo') or ''} {c.get('nota') or ''}"
        similar = merchant_similarity(description, merchant) or _is_generic_merchant(c.get("alvo"))
        elig.append({"id": c["id"], "diff": abs((cd - tx_date).days), "similar": similar})

    if not elig:
        return {"launch_id": None, "verdict": "none"}

    elig.sort(key=lambda e: (e["diff"], not e["similar"]))
    best = elig[0]
    # auto só quando há UM candidato claro, data apertada e estabelecimento bate (ou é genérico)
    if len(elig) == 1 and best["diff"] <= 1 and best["similar"]:
        return {"launch_id": best["id"], "verdict": "auto"}
    # 2+ candidatos, data folgada ou nome diverge → perguntar, sugerindo o melhor
    return {"launch_id": best["id"], "verdict": "ask"}


def _find_manual_candidates(cur, user_id: int, tipo: str, valor, tx_date) -> list[dict]:
    """Lançamentos manuais/OFX (não-OF) elegíveis a casar, ainda não vinculados a nenhuma OF tx."""
    cur.execute(
        """
        select id, valor, coalesce(posted_at, criado_em::date) as ref_date, alvo, nota
        from launches
        where user_id = %s
          and tipo = %s
          and coalesce(source, 'manual') <> 'open_finance'
          and is_internal_movement = false
          and abs(valor - %s) <= %s
          and coalesce(posted_at, criado_em::date) between %s and %s
          and not exists (
              select 1 from open_finance_transactions o where o.imported_launch_id = launches.id
          )
        """,
        (user_id, tipo, Decimal(str(valor)), RECON_AMOUNT_TOL,
         tx_date - timedelta(days=RECON_DATE_WINDOW), tx_date + timedelta(days=RECON_DATE_WINDOW)),
    )
    return cur.fetchall()


def import_open_finance_launches(user_id: int, connection_id: int | None = None) -> dict:
    """Importa transações OF (ainda não importadas) de contas BANK como `launches`.

    Modelo híbrido (Design Y): a transação vira lançamento pra alimentar sobrou/timeline/
    budgets, mas com `delta_conta=0` — NÃO move `accounts.balance` (o saldo do banco já é
    contado à parte, autoritativo, em `get_consolidated_balance`). Idempotente via
    `on conflict (user_id, source, external_id)` + filtro `imported_launch_id is null`.
    Cartão de crédito (type != BANK) fica de fora por ora.
    """
    ensure_user(user_id)
    inserted = 0
    auto_merged = 0
    pending = 0
    skipped_non_bank = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id as of_tx_id, t.provider_transaction_id, t.description,
                       t.amount, t.transaction_date, t.category, a.type as account_type
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                  and t.imported_launch_id is null
                  and (%s::bigint is null or c.id = %s)
                order by t.transaction_date, t.id
                """,
                (user_id, connection_id, connection_id),
            )
            rows = cur.fetchall()

            for r in rows:
                if (r["account_type"] or "").upper() != "BANK":
                    skipped_non_bank += 1
                    continue

                cls = classify_open_finance_launch(r["amount"], r["category"], r["description"])

                # Reconciliação (Fase 2): gasto/receita não-interno tenta casar com manual.
                verdict, match_id = "none", None
                if not cls["is_internal_movement"]:
                    candidates = _find_manual_candidates(
                        cur, user_id, cls["tipo"], cls["valor"], r["transaction_date"]
                    )
                    pick = pick_reconciliation_match(
                        cls["valor"], r["transaction_date"], r["description"], candidates
                    )
                    verdict, match_id = pick["verdict"], pick["launch_id"]

                if verdict == "auto":
                    # Alto-confiança: mescla no lançamento manual — NÃO cria OF launch (1 real = 1 linha).
                    if r["category"]:
                        cur.execute(
                            "update launches set categoria=%s "
                            "where id=%s and (categoria is null or categoria in ('outros',''))",
                            (r["category"], match_id),
                        )
                    cur.execute(
                        "update open_finance_transactions "
                        "set imported_launch_id=%s, match_launch_id=%s, reconciliation_status='auto_merged' "
                        "where id=%s",
                        (match_id, match_id, r["of_tx_id"]),
                    )
                    auto_merged += 1
                    continue

                # Sem match ('none') ou ambíguo ('ask'): cria o OF launch.
                efeitos = {
                    "delta_conta": 0,  # analytics-only: não mexe no saldo manual
                    "open_finance": {"provider_transaction_id": r["provider_transaction_id"]},
                }
                cur.execute(
                    """
                    insert into launches(
                        user_id, tipo, valor, categoria, alvo, nota, criado_em, efeitos,
                        source, external_id, posted_at, currency, imported_at, is_internal_movement
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
                    on conflict (user_id, source, external_id) do nothing
                    returning id
                    """,
                    (
                        user_id, cls["tipo"], cls["valor"], (r["category"] or "outros"),
                        r["description"], None, r["transaction_date"], Jsonb(efeitos),
                        "open_finance", r["provider_transaction_id"], r["transaction_date"], "BRL",
                        cls["is_internal_movement"],
                    ),
                )
                got = cur.fetchone()
                if got:
                    launch_id = got["id"]
                    inserted += 1
                else:
                    cur.execute(
                        "select id from launches where user_id=%s and source='open_finance' and external_id=%s",
                        (user_id, r["provider_transaction_id"]),
                    )
                    ex = cur.fetchone()
                    launch_id = ex["id"] if ex else None

                if launch_id is not None:
                    status = "pending" if verdict == "ask" else "imported"
                    cur.execute(
                        "update open_finance_transactions "
                        "set imported_launch_id=%s, match_launch_id=%s, reconciliation_status=%s where id=%s",
                        (launch_id, (match_id if verdict == "ask" else None), status, r["of_tx_id"]),
                    )
                    if verdict == "ask":
                        pending += 1

        conn.commit()

    return {
        "inserted": inserted,
        "auto_merged": auto_merged,
        "pending": pending,
        "skipped_non_bank": skipped_non_bank,
    }


def confirm_reconciliation(user_id: int, of_tx_id: int) -> dict:
    """Usuário confirma que a OF tx pendente é a MESMA do candidato: funde.

    Apaga o OF launch (delta_conta=0, não mexe no saldo) e revincula a OF tx no lançamento
    manual. Resultado: 1 transação real = 1 linha.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select o.imported_launch_id, o.match_launch_id, o.reconciliation_status
                from open_finance_transactions o
                join open_finance_accounts a on a.id = o.account_id
                join open_finance_connections c on c.id = a.connection_id
                where o.id = %s and c.user_id = %s
                """,
                (of_tx_id, user_id),
            )
            row = cur.fetchone()

    if not row or row["reconciliation_status"] != "pending" or not row["match_launch_id"]:
        return {"ok": False, "reason": "not_pending"}

    of_launch_id = row["imported_launch_id"]
    manual_id = row["match_launch_id"]
    if of_launch_id:
        try:
            delete_launch_and_rollback(user_id, of_launch_id)
        except Exception:
            pass

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update open_finance_transactions "
                "set imported_launch_id=%s, reconciliation_status='auto_merged' where id=%s",
                (manual_id, of_tx_id),
            )
        conn.commit()
    return {"ok": True, "merged_into": manual_id}


def reject_reconciliation(user_id: int, of_tx_id: int) -> dict:
    """Usuário diz que são DIFERENTES: mantém os dois lançamentos, limpa o estado pendente."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update open_finance_transactions o
                set reconciliation_status='imported', match_launch_id=null
                from open_finance_accounts a, open_finance_connections c
                where o.account_id = a.id and a.connection_id = c.id
                  and o.id = %s and c.user_id = %s and o.reconciliation_status = 'pending'
                """,
                (of_tx_id, user_id),
            )
            n = cur.rowcount
        conn.commit()
    return {"ok": n > 0}


def reconcile_manual_launch(user_id: int, launch_id: int) -> dict:
    """Reconciliação REVERSA (P0 #3): usuário criou um lançamento manual; se já existe um OF
    launch gêmeo (importado antes), funde — apaga o OF launch e revincula a OF tx no manual.

    Chamar logo após criar um lançamento manual (bot/web). Best-effort e idempotente.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, tipo, valor, coalesce(posted_at, criado_em::date) as ref_date,
                       alvo, nota, coalesce(source,'manual') as source, is_internal_movement
                from launches where id=%s and user_id=%s
                """,
                (launch_id, user_id),
            )
            m = cur.fetchone()
            if not m or m["source"] == "open_finance" or m["is_internal_movement"]:
                return {"ok": False, "reason": "not_manual"}

            cur.execute(
                """
                select l.id, l.valor, coalesce(l.posted_at, l.criado_em::date) as ref_date,
                       o.id as of_tx_id, o.description as of_desc
                from launches l
                join open_finance_transactions o on o.imported_launch_id = l.id
                where l.user_id=%s and coalesce(l.source,'') = 'open_finance'
                  and l.tipo=%s and l.is_internal_movement = false
                  and abs(l.valor - %s) <= %s
                  and coalesce(l.posted_at, l.criado_em::date) between %s and %s
                  and o.reconciliation_status in ('imported','pending')
                """,
                (user_id, m["tipo"], m["valor"], RECON_AMOUNT_TOL,
                 m["ref_date"] - timedelta(days=RECON_DATE_WINDOW),
                 m["ref_date"] + timedelta(days=RECON_DATE_WINDOW)),
            )
            of_rows = cur.fetchall()

    if not of_rows:
        return {"ok": True, "matched": False}

    manual_desc = f"{m['alvo'] or ''} {m['nota'] or ''}"
    candidates = [
        {"id": r["id"], "valor": r["valor"], "ref_date": r["ref_date"], "alvo": r["of_desc"], "nota": None}
        for r in of_rows
    ]
    pick = pick_reconciliation_match(m["valor"], m["ref_date"], manual_desc, candidates)
    if pick["verdict"] != "auto":
        return {"ok": True, "matched": False, "verdict": pick["verdict"]}

    of_launch_id = pick["launch_id"]
    of_tx_id = next(r["of_tx_id"] for r in of_rows if r["id"] == of_launch_id)
    try:
        delete_launch_and_rollback(user_id, of_launch_id)
    except Exception:
        pass
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update open_finance_transactions "
                "set imported_launch_id=%s, match_launch_id=%s, reconciliation_status='auto_merged' where id=%s",
                (launch_id, launch_id, of_tx_id),
            )
        conn.commit()
    return {"ok": True, "matched": True, "merged_of_launch": of_launch_id}


def import_open_finance_credit(user_id: int, connection_id: int | None = None) -> dict:
    """Importa transações de contas de CRÉDITO do OF pra máquina de faturas (opção a).

    Auto-cria/vincula um cartão PigBank por conta de crédito e lança cada transação em
    `credit_transactions` (dedup por external_id), gravando o back-link `imported_credit_tx_id`.
    Assim compras de cartão entram na fatura, não no saldo — sem furar o "sobrou".
    """
    ensure_user(user_id)
    card_cache: dict[int, int] = {}
    links: list[tuple[int, int]] = []
    inserted = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id as of_tx_id, t.provider_transaction_id, t.description,
                       t.amount, t.transaction_date, t.category, t.raw as tx_raw,
                       a.id as of_account_id, a.name as account_name, a.raw as account_raw
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s
                  and t.imported_credit_tx_id is null
                  and upper(a.type) = 'CREDIT'
                  and (%s::bigint is null or c.id = %s)
                order by t.transaction_date, t.id
                """,
                (user_id, connection_id, connection_id),
            )
            rows = cur.fetchall()

    for r in rows:
        of_acc_id = r["of_account_id"]
        if of_acc_id not in card_cache:
            card_cache[of_acc_id] = get_or_create_open_finance_card(
                user_id, of_acc_id, r["account_name"], r["account_raw"]
            )
        # Parcelas (#11): cada parcela é uma tx separada; marca nº/total e agrupa por compra.
        inst_no, inst_total = extract_installment_info(r["tx_raw"])
        group_id = None
        if inst_total:
            # Heurística da doc Pluggy: cartão + estabelecimento + nº parcelas + valor total.
            # Sem o totalAmount, duas compras diferentes iguais (mesmo merchant/parcelas) colidiam.
            meta = (r["tx_raw"] or {}).get("creditCardMetadata") or {}
            total_amount = meta.get("totalAmount")
            # P2: adiciona o MÊS DE ORIGEM estimado (data da parcela menos nº parcela-1) pra separar
            # compras idênticas feitas em meses distintos — sem quebrar o agrupamento das parcelas de
            # UMA compra: todas back-calculam pro mesmo mês (parcela 1 em M, parcela 2 em M+1 → M).
            origin_ym = ""
            if inst_no and r["transaction_date"] is not None:
                oy, om = add_months(
                    r["transaction_date"].year, r["transaction_date"].month, -(int(inst_no) - 1)
                )
                origin_ym = f"{oy:04d}-{om:02d}"
            key = "|".join([
                str(card_cache[of_acc_id]),
                (r["description"] or "").strip().lower(),
                str(inst_total),
                str(total_amount) if total_amount is not None else "",
                origin_ym,
            ])
            group_id = uuid5(NAMESPACE_OID, key)
        tx_id, created = add_imported_credit_purchase(
            user_id, card_cache[of_acc_id], r["amount"], r["category"],
            r["transaction_date"], r["provider_transaction_id"],
            installment_no=inst_no, installments_total=inst_total, group_id=group_id,
        )
        if created:
            inserted += 1
        if tx_id is not None:
            links.append((r["of_tx_id"], tx_id))

    if links:
        with get_conn() as conn:
            with conn.cursor() as cur:
                for of_tx_id, credit_tx_id in links:
                    cur.execute(
                        "update open_finance_transactions set imported_credit_tx_id=%s where id=%s",
                        (credit_tx_id, of_tx_id),
                    )
            conn.commit()

    return {"inserted": inserted, "linked": len(links), "cards": len(card_cache)}


def _get_or_create_open_bill_cur(cur, user_id: int, card_id: int, ref_date) -> int | None:
    """Como get_or_create_open_bill, mas usando o cursor da transação em curso (sem abrir
    conexão aninhada, que arriscaria lock/inconsistência no meio de um update). Retorna o
    bill_id da fatura que contém ref_date (reabrindo se estava paga/fechada)."""
    cur.execute("select closing_day, user_id from credit_cards where id=%s limit 1", (card_id,))
    card = cur.fetchone()
    if not card or int(card["user_id"]) != int(user_id):
        return None
    period_start, period_end = billing_period_for_close_day(ref_date, int(card["closing_day"]))
    cur.execute(
        """
        insert into credit_bills (user_id, card_id, period_start, period_end, total, status)
        values (%s,%s,%s,%s,0,'open')
        on conflict (card_id, period_start, period_end) do update set user_id = excluded.user_id
        returning id, status
        """,
        (user_id, card_id, period_start, period_end),
    )
    row = cur.fetchone()
    bill_id = int(row["id"])
    if (row.get("status") or "").lower() in ("paid", "closed"):
        cur.execute("update credit_bills set status='open' where id=%s", (bill_id,))
    return bill_id


def sync_imported_open_finance_updates(user_id: int, connection_id: int | None = None) -> dict:
    """Propaga CORREÇÕES da Pluggy (transactions/updated) pros registros já importados.

    Sem isso, uma correção de valor/data/categoria atualizava só o espelho OF — o launch,
    a credit_transaction e o total da fatura ficavam com o valor velho. Mexe apenas em
    registros DO OF (source=open_finance); nunca sobrescreve lançamento manual auto-mesclado.
    """
    ensure_user(user_id)
    launches_updated = 0
    credit_updated = 0

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1) Launches próprios do OF (conta BANK)
            cur.execute(
                """
                select o.amount, o.transaction_date, o.category, o.description,
                       l.id as launch_id, l.valor as cur_valor, l.categoria as cur_cat,
                       l.tipo as cur_tipo, l.is_internal_movement as cur_internal,
                       coalesce(l.posted_at, l.criado_em::date) as cur_date
                from open_finance_transactions o
                join open_finance_accounts a on a.id = o.account_id
                join open_finance_connections c on c.id = a.connection_id
                join launches l on l.id = o.imported_launch_id
                where c.user_id=%s and (%s::bigint is null or c.id=%s)
                  and upper(a.type)='BANK' and coalesce(l.source,'')='open_finance'
                """,
                (user_id, connection_id, connection_id),
            )
            for r in cur.fetchall():
                cls = classify_open_finance_launch(r["amount"], r["category"], r["description"])
                new_cat = r["category"] or "outros"
                changed = (
                    Decimal(str(r["cur_valor"])) != cls["valor"]
                    or r["cur_tipo"] != cls["tipo"]
                    or (r["cur_cat"] or "") != new_cat
                    or bool(r["cur_internal"]) != cls["is_internal_movement"]
                    or r["cur_date"] != r["transaction_date"]
                )
                if changed:
                    cur.execute(
                        """
                        update launches set valor=%s, tipo=%s, categoria=%s,
                               is_internal_movement=%s, posted_at=%s
                        where id=%s
                        """,
                        (cls["valor"], cls["tipo"], new_cat, cls["is_internal_movement"],
                         r["transaction_date"], r["launch_id"]),
                    )
                    launches_updated += 1

            # 2) Transações de cartão (ajusta o total da fatura pela diferença)
            cur.execute(
                """
                select o.amount, o.transaction_date, o.category,
                       ct.id as ct_id, ct.valor as cur_valor, ct.is_refund as cur_refund,
                       ct.categoria as cur_cat, ct.purchased_at as cur_date, ct.bill_id,
                       ct.card_id
                from open_finance_transactions o
                join open_finance_accounts a on a.id = o.account_id
                join open_finance_connections c on c.id = a.connection_id
                join credit_transactions ct on ct.id = o.imported_credit_tx_id
                where c.user_id=%s and (%s::bigint is null or c.id=%s)
                  and upper(a.type)='CREDIT'
                """,
                (user_id, connection_id, connection_id),
            )
            for r in cur.fetchall():
                amt = Decimal(str(r["amount"]))
                new_valor = -amt  # convenção canônica assinada (compra +, estorno -)
                new_refund = amt > 0
                new_cat = r["category"]
                changed = (
                    Decimal(str(r["cur_valor"])) != new_valor
                    or bool(r["cur_refund"]) != new_refund
                    or (r["cur_cat"] or None) != (new_cat or None)
                    or r["cur_date"] != r["transaction_date"]
                )
                if changed:
                    old_valor = Decimal(str(r["cur_valor"]))
                    old_bill_id = r["bill_id"]
                    new_bill_id = old_bill_id
                    # P1: se a data mudou de ciclo de fatura, a transação precisa MIGRAR de fatura.
                    # Sem isso, ela mantinha o bill_id antigo e o gasto continuava preso no mês/fatura
                    # velha (dashboard e analytics agrupam por credit_bills.period_end).
                    if r["cur_date"] != r["transaction_date"] and r["card_id"] is not None:
                        resolved = _get_or_create_open_bill_cur(
                            cur, user_id, r["card_id"], r["transaction_date"]
                        )
                        if resolved is not None:
                            new_bill_id = resolved
                    cur.execute(
                        "update credit_transactions set valor=%s, is_refund=%s, categoria=%s, "
                        "purchased_at=%s, bill_id=%s where id=%s",
                        (new_valor, new_refund, new_cat, r["transaction_date"], new_bill_id, r["ct_id"]),
                    )
                    if new_bill_id == old_bill_id:
                        # mesma fatura: ajusta só pela diferença de valor (fatura foi `total += valor`).
                        cur.execute(
                            "update credit_bills set total = total + %s where id=%s and user_id=%s",
                            (new_valor - old_valor, old_bill_id, user_id),
                        )
                    else:
                        # migrou de fatura: remove o valor antigo da fatura velha e soma o novo na nova.
                        cur.execute(
                            "update credit_bills set total = total - %s where id=%s and user_id=%s",
                            (old_valor, old_bill_id, user_id),
                        )
                        cur.execute(
                            "update credit_bills set total = total + %s where id=%s and user_id=%s",
                            (new_valor, new_bill_id, user_id),
                        )
                    credit_updated += 1

        conn.commit()

    return {"launches_updated": launches_updated, "credit_updated": credit_updated}


# ──────────────────────────────────────────────────────────────────────────────
# P2 — inteligência: salário (Fase 5) e anomalia de conta (Fase 6), via dados OF
# ──────────────────────────────────────────────────────────────────────────────

def detect_recurring_income_matches(credits, value_tol_pct=Decimal("0.10"), day_tol=3) -> list:
    """Puro. credits: [{id, valor, date}]. Retorna os ids que se repetem em MESES distintos
    (mesmo ~valor ±10%, ~dia ±3) — candidatos a renda recorrente / salário."""
    out = []
    for i, c in enumerate(credits):
        cv, cd = Decimal(str(c["valor"])), c["date"]
        for j, o in enumerate(credits):
            if i == j:
                continue
            od = o["date"]
            if (cd.year, cd.month) == (od.year, od.month):
                continue
            ov = Decimal(str(o["valor"]))
            hi = max(cv, ov)
            if hi > 0 and abs(cv - ov) / hi <= value_tol_pct and abs(cd.day - od.day) <= day_tol:
                out.append(c["id"])
                break
    return out


def detect_open_finance_salary(user_id: int, months: int = 4) -> dict | None:
    """Acha o crédito OF mais recente que parece salário (renda recorrente).

    Base pra confirmação proativa (Fase 5). O envio WhatsApp fica dormente até ter template
    Meta (igual o lembrete de boleto). Retorna {launch_id, valor, date} ou None.
    """
    ensure_user(user_id)
    since = datetime.now(_tz()).date() - timedelta(days=months * 31)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, valor, coalesce(posted_at, criado_em::date) as date
                from launches
                where user_id=%s and coalesce(source,'')='open_finance' and tipo='receita'
                  and is_internal_movement=false
                  and coalesce(posted_at, criado_em::date) >= %s
                order by coalesce(posted_at, criado_em::date)
                """,
                (user_id, since),
            )
            credits = cur.fetchall()

    ids = set(detect_recurring_income_matches(
        [{"id": c["id"], "valor": c["valor"], "date": c["date"]} for c in credits]
    ))
    recurring = [c for c in credits if c["id"] in ids]
    if not recurring:
        return None
    recurring.sort(key=lambda c: (c["date"], c["valor"]))
    cand = recurring[-1]
    return {"launch_id": cand["id"], "valor": cand["valor"], "date": cand["date"]}


def detect_bill_increase(expenses, threshold_pct=Decimal("0.15")) -> list:
    """Puro. expenses: [{merchant, valor, date}]. Agrupa por estabelecimento; se aparece em
    >=2 meses e o mais recente subiu >= threshold vs a média anterior → flag de aumento."""
    from collections import defaultdict
    groups: dict = defaultdict(list)
    for e in expenses:
        key = _normalize_merchant(e["merchant"])
        if key:
            groups[key].append(e)
    out = []
    for items in groups.values():
        by_month = {}
        for e in items:
            by_month[(e["date"].year, e["date"].month)] = e  # último do mês vence
        if len(by_month) < 2:
            continue
        ordered = [by_month[k] for k in sorted(by_month)]
        latest = ordered[-1]
        prior = ordered[:-1]
        avg_prior = sum(Decimal(str(e["valor"])) for e in prior) / len(prior)
        lv = Decimal(str(latest["valor"]))
        if avg_prior > 0 and (lv - avg_prior) / avg_prior >= threshold_pct:
            out.append({
                "merchant": latest["merchant"],
                "old": avg_prior,
                "new": lv,
                "pct": (lv - avg_prior) / avg_prior * 100,
            })
    return out


def detect_open_finance_bill_increase(user_id: int, months: int = 4) -> list:
    """Detecta contas que aumentaram usando dados OF (Fase 6) — pega até conta que o usuário
    nem cadastrou como recorrente. Complementa `_detect_recurring_increase` (só olha manual)."""
    ensure_user(user_id)
    since = datetime.now(_tz()).date() - timedelta(days=months * 31)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select alvo, nota, valor, coalesce(posted_at, criado_em::date) as date
                from launches
                where user_id=%s and coalesce(source,'')='open_finance' and tipo='despesa'
                  and is_internal_movement=false
                  and coalesce(posted_at, criado_em::date) >= %s
                """,
                (user_id, since),
            )
            rows = cur.fetchall()
    expenses = [{"merchant": (r["alvo"] or r["nota"] or ""), "valor": r["valor"], "date": r["date"]} for r in rows]
    return detect_bill_increase(expenses)


def get_consolidated_balance(user_id: int) -> dict:
    """Saldo consolidado = saldo manual + soma dos saldos das contas BANK conectadas.

    Cartão (type CREDIT) fica de fora (é dívida, não saldo disponível). Auto-atualiza
    conforme o sync refresca os saldos autoritativos dos bancos.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select coalesce(balance, 0) as b from accounts where user_id=%s", (user_id,))
            row = cur.fetchone()
            manual = row["b"] if row else Decimal("0")

            cur.execute(
                """
                select coalesce(sum(a.balance), 0) as b
                from open_finance_accounts a
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id=%s and upper(a.type) = 'BANK'
                """,
                (user_id,),
            )
            of_bank = cur.fetchone()["b"]

    return {
        "manual": manual,
        "open_finance_bank": of_bank,
        "consolidated": (manual or Decimal("0")) + (of_bank or Decimal("0")),
    }


def disconnect_open_finance_connection(user_id: int, connection_id: int | None = None) -> int:
    """Desconecta banco(s) e LIMPA o que foi importado (P0 integridade).

    Política: reverte launches OF + transações de fatura, apaga os cartões auto-criados
    que ficaram vazios, e só então remove a conexão (cascade nas contas/transações OF).
    Lançamentos MANUAIS que foram auto-mesclados são preservados (só desvinculados).
    """
    ensure_user(user_id)

    # 1. junta as transações OF importadas + os cartões auto-criados desta conexão.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select t.id, c.user_id, t.imported_launch_id, t.imported_credit_tx_id,
                       l.source as launch_source
                from open_finance_transactions t
                join open_finance_accounts a on a.id = t.account_id
                join open_finance_connections c on c.id = a.connection_id
                left join launches l on l.id = t.imported_launch_id
                where c.user_id = %s and (%s::bigint is null or c.id = %s)
                """,
                (user_id, connection_id, connection_id),
            )
            rows = cur.fetchall()

            cur.execute(
                """
                select cc.id as card_id
                from credit_cards cc
                join open_finance_accounts a on a.id = cc.open_finance_account_id
                join open_finance_connections c on c.id = a.connection_id
                where c.user_id = %s and (%s::bigint is null or c.id = %s)
                """,
                (user_id, connection_id, connection_id),
            )
            card_ids = [r["card_id"] for r in cur.fetchall()]

    # 2. reverte launches/fatura importados.
    _rollback_imported_of(rows)

    # 3. apaga cartões auto-criados que ficaram sem transações + remove a conexão.
    with get_conn() as conn:
        with conn.cursor() as cur:
            for cid in card_ids:
                cur.execute("select count(*) as n from credit_transactions where card_id=%s", (cid,))
                if cur.fetchone()["n"] == 0:
                    cur.execute("delete from credit_cards where id=%s and user_id=%s", (cid, user_id))

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
