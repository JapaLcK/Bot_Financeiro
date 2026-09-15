"""Declarações bancárias: registrar é diferente de conferir com o extrato."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .connection import get_conn
from utils_date import _tz


def _amount(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def is_of_shadow(source, delta):
    """Sombra consumível: zero exato, independente da categoria histórica."""
    return source == "open_finance" and _amount(delta) == Decimal(0)


def uses_bank_movement_lock(source, effects):
    if not isinstance(effects, dict):
        return False
    funding = effects.get("funding_source")
    return (is_of_shadow(source, effects.get("delta_conta"))
            or isinstance(funding, dict) and funding.get("kind") == "bank")


def _money(value):
    amount = _amount(value)
    try:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if amount is not None else None
    except InvalidOperation:
        return None


def _lock_user(cur, user_id):
    # Os writers já travam esta linha. Sync/conferência usam a mesma ordem,
    # antes de conta OF/transação/vínculo; não travam nem reescrevem lotes.
    cur.execute("select user_id from accounts where user_id=%s for update", (user_id,))


def _transactions(cur, user_id, *, lock=False):
    from .open_finance import BANK_ACCOUNTS_SQL, is_credit_card_payment, investment_transfer_kind
    cur.execute(
        f"""select t.*, a.id as bank_account_id, a.name as account_name,
                   a.institution_name, l.source as imported_source, l.user_id as imported_user,
                   l.efeitos->>'delta_conta' as imported_delta
              from open_finance_transactions t
              join ({BANK_ACCOUNTS_SQL}) a on a.id=t.account_id
              left join launches l on l.id=t.imported_launch_id
             where (t.imported_launch_id is null or l.user_id=%s)
             order by t.transaction_date, t.id""" + (" for update of t" if lock else ""), (user_id, user_id))
    rows = []
    for row in cur.fetchall():
        row = dict(row)
        if is_credit_card_payment(row.get("category"), row.get("description")):
            continue
        row["economic_kind"] = investment_transfer_kind(row.get("category"))
        rows.append(row)
    return rows


def _declarations(cur, user_id):
    cur.execute(
        """select d.*, l.alvo, l.tipo, l.efeitos->'funding_source'->>'label' as bank_label
             from bank_movement_declarations d
             join launches l on l.id=d.launch_id and l.user_id=d.user_id
            where d.user_id=%s order by d.declared_at, d.launch_id""", (user_id,))
    return [dict(r) for r in cur.fetchall()]


def _eligible(declaration, tx, *, manual=False):
    from .open_finance import RECON_DATE_WINDOW, classify_open_finance_launch
    amount = _money(declaration.get("amount"))
    if amount is None or _money(tx["amount"]) != amount:
        return False
    if not (manual and (declaration["account_ambiguous"] or declaration["account_id"] is None)) and tx["account_id"] != declaration["account_id"]:
        return False
    if abs((tx["transaction_date"] - declaration["declared_at"].astimezone(_tz()).date()).days) > RECON_DATE_WINDOW:
        return False
    if tx.get("economic_kind"):
        return True
    # Uma transferência genérica pode ter vindo sem a categoria de aplicação.
    # Só a escolha explícita do usuário pode dizer que é a mesma movimentação.
    return manual and classify_open_finance_launch(
        tx["amount"], tx.get("category"), tx.get("description"))["is_internal_movement"]


def _free(tx, declaration_id=None):
    if tx.get("match_launch_id") not in (None, declaration_id):
        return False
    return (tx.get("imported_launch_id") in (None, declaration_id)
            or is_of_shadow(tx.get("imported_source"), tx.get("imported_delta")))


def _bind(cur, user_id, declaration, tx, method):
    old_id = tx.get("imported_launch_id")
    cur.execute(
        """update bank_movement_declarations
              set matched_transaction_id=%s, confirmation_method=%s, confirmed_at=now()
            where user_id=%s and launch_id=%s""",
        (tx["id"], method, user_id, declaration["launch_id"]))
    cur.execute(
        """update open_finance_transactions t
              set imported_launch_id=%s, match_launch_id=%s, reconciliation_status='bank_movement_confirmed'
             from open_finance_accounts a, open_finance_connections c
            where t.id=%s and a.id=t.account_id and c.id=a.connection_id and c.user_id=%s""",
        (declaration["launch_id"], declaration["launch_id"], tx["id"], user_id))
    if old_id and old_id != declaration["launch_id"]:
        # OF analytics-only: jamais undo, jamais alterar efeitos/lotes do manual.
        cur.execute("select source,efeitos from launches where id=%s and user_id=%s for update", (old_id, user_id))
        shadow = cur.fetchone()
        if shadow and isinstance(shadow["efeitos"], dict) and is_of_shadow(shadow["source"], shadow["efeitos"].get("delta_conta")):
            cur.execute("delete from launches where id=%s and user_id=%s", (old_id, user_id))


def reconcile_bank_movements(cur, user_id):
    """Duas ordens de chegada, uma transação e um vínculo exclusivo por prova."""
    _lock_user(cur, user_id)
    declarations = _declarations(cur, user_id)
    if not declarations:
        return 0
    from .open_finance import BANK_ACCOUNTS_SQL
    cur.execute(BANK_ACCOUNTS_SQL, (user_id,))
    active_accounts = {row["id"] for row in cur.fetchall()}
    for d in declarations:
        if d["account_id"] not in active_accounts and not d["account_ambiguous"]:
            cur.execute("update bank_movement_declarations set account_ambiguous=true where launch_id=%s and user_id=%s", (d["launch_id"], user_id))
            d["account_ambiguous"] = True
        if not d["matched_transaction_id"] and d["confirmation_method"]:
            cur.execute("update bank_movement_declarations set confirmation_method=null, confirmed_at=null, requires_review=true where launch_id=%s and user_id=%s", (d["launch_id"], user_id))
            d["requires_review"] = True
    transactions = _transactions(cur, user_id, lock=True)
    by_id = {t["id"]: t for t in transactions}
    occupied = set()
    for d in declarations:
        tx_id = d["matched_transaction_id"]
        if not tx_id:
            continue
        tx = by_id.get(tx_id)
        if tx and _eligible(d, tx, manual=d["confirmation_method"] == "manual") and _free(tx, d["launch_id"]):
            occupied.add(tx_id)
            continue
        cur.execute(
            """update bank_movement_declarations set matched_transaction_id=null,
                       confirmation_method=null, confirmed_at=null, requires_review=true
                 where user_id=%s and launch_id=%s""", (user_id, d["launch_id"]))
        cur.execute(
            """update open_finance_transactions t set imported_launch_id=null,
                       match_launch_id=null, reconciliation_status=null
                  from open_finance_accounts a, open_finance_connections c
                 where t.id=%s and a.id=t.account_id and c.id=a.connection_id and c.user_id=%s
                   and t.imported_launch_id=%s and t.match_launch_id=%s""",
            (tx_id, user_id, d["launch_id"], d["launch_id"]))
        d["matched_transaction_id"] = None
        d["requires_review"] = True
    pending = [d for d in declarations if not d["matched_transaction_id"]]
    graph = {d["launch_id"]: [t for t in transactions if t["id"] not in occupied
                              and _free(t) and _eligible(d, t)] for d in pending}
    confirmed = 0
    for d in pending:
        candidates = graph[d["launch_id"]]
        if d["requires_review"] or d["account_ambiguous"] or len(candidates) != 1:
            continue
        tx = candidates[0]
        if sum(any(t["id"] == tx["id"] for t in choices) for choices in graph.values()) != 1:
            continue
        _bind(cur, user_id, d, tx, "automatic")
        confirmed += 1
    return confirmed


def record_bank_movement(cur, user_id, launch_id, funding_source, amount):
    if not funding_source or funding_source.get("kind") != "bank":
        return
    cur.execute(
        """insert into bank_movement_declarations
                    (launch_id,user_id,account_id,amount,declared_at,account_ambiguous)
             select l.id,l.user_id,a.id,%s,l.criado_em,%s
               from launches l
               left join open_finance_accounts a on a.id::text=%s
                 and exists(select 1 from open_finance_connections c where c.id=a.connection_id and c.user_id=%s)
              where l.id=%s and l.user_id=%s
             on conflict(launch_id) do nothing""",
        (_money(amount), bool(funding_source.get("account_ambiguous")),
         str(funding_source.get("of_account_id")), user_id, launch_id, user_id))
    reconcile_bank_movements(cur, user_id)


def existing_bank_outflow(cur, user_id, account_id, amount):
    """Prova já importada pode autorizar registrar o aporte que reduziu o saldo."""
    candidate = {"amount": -abs(Decimal(str(amount))), "account_id": account_id,
                 "account_ambiguous": False, "declared_at": datetime.now(_tz())}
    declarations = _declarations(cur, user_id)
    used = {d["matched_transaction_id"] for d in declarations if d["matched_transaction_id"]}
    txs = [t for t in _transactions(cur, user_id) if t["id"] not in used and _free(t) and _eligible(candidate, t)]
    if len(txs) != 1:
        return False
    return not any(not d["matched_transaction_id"] and _eligible(d, txs[0]) for d in declarations)


def has_existing_bank_outflow(user_id, account_id, amount):
    with get_conn() as conn, conn.cursor() as cur:
        return existing_bank_outflow(cur, user_id, account_id, amount)


def pending_outflows(cur, user_id):
    cur.execute(
        """select account_id, -sum(amount) as amount from bank_movement_declarations
            where user_id=%s and matched_transaction_id is null and amount<0 and account_id is not null
            group by account_id""", (user_id,))
    return {r["account_id"]: r["amount"] for r in cur.fetchall()}


def bank_movement_summary(user_id):
    with get_conn() as conn, conn.cursor() as cur:
        rows = _declarations(cur, user_id)
    pending = [r for r in rows if not r["matched_transaction_id"]]
    return {"pending_count": len(pending), "patrimony_status": "to_review" if pending else "confirmed",
            "declared_outflows": sum((-r["amount"] for r in pending if r["amount"] is not None and r["amount"]<0), Decimal(0)),
            "declared_inflows": sum((r["amount"] for r in pending if r["amount"] is not None and r["amount"]>0), Decimal(0))}


def list_bank_movements(user_id):
    with get_conn() as conn, conn.cursor() as cur:
        declarations = _declarations(cur, user_id)
        transactions = _transactions(cur, user_id)
    used = {d["matched_transaction_id"] for d in declarations if d["matched_transaction_id"]}
    result = []
    for d in declarations:
        if d["matched_transaction_id"]:
            continue
        result.append({"launch_id": d["launch_id"], "name": d["alvo"], "bank": d["bank_label"],
                       "amount": float(d["amount"]) if d["amount"] is not None else None,
                       "date": d["declared_at"].date().isoformat(),
                       "requires_review": d["requires_review"] or d["account_ambiguous"] or d["account_id"] is None,
                       "candidates": [{"id": t["id"], "description": t["description"], "bank": t["institution_name"],
                                       "amount": float(t["amount"]), "date": t["transaction_date"].isoformat()}
                                      for t in transactions if t["id"] not in used and _free(t) and _eligible(d,t,manual=True)]})
    return result


def confirm_bank_movement(user_id, launch_id, transaction_id):
    with get_conn() as conn, conn.cursor() as cur:
        _lock_user(cur, user_id)
        declarations = _declarations(cur, user_id)
        d = next((d for d in declarations if d["launch_id"] == launch_id), None)
        tx = next((t for t in _transactions(cur, user_id, lock=True) if t["id"] == transaction_id), None)
        if not d or not tx:
            raise LookupError("MOVEMENT_NOT_FOUND")
        if d["matched_transaction_id"] == transaction_id:
            if not _free(tx, launch_id) or not _eligible(d, tx, manual=d["confirmation_method"] == "manual"):
                raise ValueError("MOVEMENT_INCOMPATIBLE")
            return
        if d["matched_transaction_id"] or any(r["matched_transaction_id"] == transaction_id for r in declarations):
            raise ValueError("MOVEMENT_ALREADY_MATCHED")
        if not _free(tx) or not _eligible(d, tx, manual=True):
            raise ValueError("MOVEMENT_INCOMPATIBLE")
        _bind(cur, user_id, d, tx, "manual")
        conn.commit()


def migrate_legacy_bank_movements(cur):
    """Legado vira revisão, nunca confirmação pela idade; JSON ruim não derruba boot."""
    cur.execute(
        """select l.id,l.user_id,l.tipo,l.valor,l.criado_em,l.efeitos
             from launches l where l.efeitos->'funding_source'->>'kind'='bank'
              and l.tipo in ('deposito_caixinha','aporte_investimento','saque_caixinha','resgate_investimento')
              and not exists(select 1 from bank_movement_declarations d where d.launch_id=l.id)""")
    for row in cur.fetchall():
        effects = row["efeitos"] or {}
        source = effects.get("funding_source") or {}
        taxes = effects.get("tax_summary")
        taxes = taxes if isinstance(taxes, dict) else {}
        amount = _amount(row["valor"]) if row["tipo"] in ('deposito_caixinha','aporte_investimento') else _amount(taxes.get("net"))
        if amount is not None and row["tipo"] in ('deposito_caixinha','aporte_investimento'):
            amount = -amount
        cur.execute(
            """insert into bank_movement_declarations(launch_id,user_id,account_id,amount,declared_at,requires_review,account_ambiguous)
                 select %s,%s,a.id,%s,%s,true,(a.id is null)
                   from (select 1) seed left join open_finance_accounts a on a.id::text=%s
                    and exists(select 1 from open_finance_connections c where c.id=a.connection_id and c.user_id=%s)
                 on conflict(launch_id) do nothing""",
            (row["id"],row["user_id"],_money(amount),row["criado_em"],str(source.get("of_account_id")),row["user_id"]))
