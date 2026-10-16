"""Conferência manual das declarações bancárias (o modal) e migração do legado.

O automático e os privados (`_lock_user`, `_bind`...) moram em `db/bank_movements.py`. Eles são
importados DENTRO de cada função, na chamada, e não no topo: assim `monkeypatch.setattr(db.bank_movements,
"_lock_user", ...)` alcança também estas funções (o teste de corrida depende disso)."""
from __future__ import annotations

from .bank_declaracoes import janela, projeta
from .connection import get_conn


def list_bank_movements(user_id):
    from .bank_movements import _declarations, _eligible, _free, _transactions
    with get_conn() as conn, conn.cursor() as cur:
        declarations = _declarations(cur, user_id)
        transactions = _transactions(cur, user_id)
    used = {d["matched_transaction_id"] for d in declarations if d["matched_transaction_id"]}
    dates = [t["transaction_date"] for t in transactions]
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
                                      for t in janela(transactions, dates, d)
                                      if t["id"] not in used and _free(t) and _eligible(d,t,manual=True)]})
    return result


def confirm_bank_movement(user_id, launch_id, transaction_id):
    from .bank_movements import _bind, _declarations, _eligible, _free, _lock_user, _transactions
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
        projeta(cur, user_id, d, tx)
        conn.commit()


def migrate_legacy_bank_movements(cur):
    """Legado vira revisão, nunca confirmação pela idade; JSON ruim não derruba boot."""
    from .bank_movements import _amount, _money
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
