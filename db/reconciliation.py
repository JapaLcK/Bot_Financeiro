"""Reconciliação OF × lançamento manual: confirmar, rejeitar e desfazer.

Estados de `open_finance_transactions` que importam aqui:
  pending     imported = sombra, match = X  (o usuário ainda não decidiu)
  confirmed   imported = match = X          (o usuário confirmou)
  auto_merged imported = match = X          (o import ou a fusão reversa juntaram)
  imported    imported = sombra, match null (sem par, ou par rejeitado/desfeito)

Toda escrita é uma transação só, na ordem do resto do módulo de Open Finance
(`accounts` → transação OF → vínculo): a mesma do sync, então as duas se
serializam em vez de se cruzarem. Nenhuma função trava o lançamento X.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from psycopg import errors as pg_errors

from utils_date import today_tz

from .bank_movements import _lock_user, is_of_shadow
from .connection import get_conn
from .open_finance import (
    MERGED_WALLET_DELTA_SQL, PENDING_RECONCILIATION_SQL, _insert_of_shadow,
    classify_open_finance_launch, merged_wallet_delta_params,
)

# Fonte única: quem desfaz e quem lista leem daqui (CLAUDE.md §0.7).
FUSED_STATUSES = ("auto_merged", "confirmed")


class ReconciliationConflict(Exception):
    """Corrida com outra escrita (sync, delete do lançamento): tente de novo."""


def _locked_tx(cur, user_id, of_tx_id):
    _lock_user(cur, user_id)
    cur.execute(
        """select o.id, o.provider_transaction_id, o.description, o.amount,
                  o.transaction_date, o.transacted_at, o.category,
                  o.imported_launch_id, o.match_launch_id, o.reconciliation_status
             from open_finance_transactions o
             join open_finance_accounts a on a.id = o.account_id
             join open_finance_connections c on c.id = a.connection_id
            where o.id = %s and c.user_id = %s
            for update of o""",
        (of_tx_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        raise LookupError("RECONCILIATION_NOT_FOUND")
    return row


def _write(user_id, of_tx_id, fn):
    """Trava, lê, deixa `fn` escrever e commita. Deadlock e FK violada por um
    delete concorrente de X viram conflito (409), nunca 500."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            result = fn(cur, _locked_tx(cur, user_id, of_tx_id))
            conn.commit()
            return result
    except (pg_errors.DeadlockDetected, pg_errors.ForeignKeyViolation) as exc:
        raise ReconciliationConflict(str(exc)) from exc


def confirm_reconciliation(user_id: int, of_tx_id: int) -> dict:
    """O usuário confirma que a transação pendente é o lançamento X."""
    def fn(cur, o):
        x = o["match_launch_id"]
        if o["reconciliation_status"] in FUSED_STATUSES and x and o["imported_launch_id"] == x:
            return {"ok": True, "changed": False, "launch_id": x}
        if o["reconciliation_status"] != "pending":
            raise ValueError("NOT_PENDING")
        cur.execute("select 1 from launches where id=%s and user_id=%s", (x, user_id))
        if not x or not cur.fetchone():
            raise ValueError("MATCH_NOT_FOUND")
        cur.execute("select 1 from open_finance_transactions where imported_launch_id=%s and id<>%s",
                    (x, o["id"]))
        if cur.fetchone():
            raise ValueError("ALREADY_LINKED")
        cur.execute(
            """update open_finance_transactions
                  set imported_launch_id=%s, reconciliation_status='confirmed'
                where id=%s""",
            (x, o["id"]))
        shadow_id = o["imported_launch_id"]
        if shadow_id and shadow_id != x:
            cur.execute("select source, efeitos from launches where id=%s and user_id=%s",
                        (shadow_id, user_id))
            shadow = cur.fetchone()
            if shadow and isinstance(shadow["efeitos"], dict) and is_of_shadow(
                    shadow["source"], shadow["efeitos"].get("delta_conta")):
                cur.execute("delete from launches where id=%s and user_id=%s", (shadow_id, user_id))
        return {"ok": True, "changed": True, "launch_id": x}
    return _write(user_id, of_tx_id, fn)


def reject_reconciliation(user_id: int, of_tx_id: int) -> dict:
    """O usuário diz que são diferentes: os dois lançamentos ficam."""
    def fn(cur, o):
        if o["reconciliation_status"] != "pending":
            return {"ok": True, "changed": False}
        cur.execute(
            """update open_finance_transactions
                  set reconciliation_status='imported', match_launch_id=null
                where id=%s""",
            (o["id"],))
        return {"ok": True, "changed": True}
    return _write(user_id, of_tx_id, fn)


def undo_reconciliation(user_id: int, of_tx_id: int) -> dict:
    """Desfaz uma fusão (automática ou confirmada): recria a sombra do banco e
    solta X, que volta a contar na Carteira. Vale nos dois sentidos — no reverso
    a sombra foi apagada e renasce com o mesmo `external_id` do provedor."""
    def fn(cur, o):
        x = o["match_launch_id"]
        if o["reconciliation_status"] not in FUSED_STATUSES or not x or o["imported_launch_id"] != x:
            return {"ok": True, "changed": False}
        cls = classify_open_finance_launch(o["amount"], o["category"], o["description"])
        shadow_id, _ = _insert_of_shadow(cur, user_id, o, cls)
        if shadow_id is None:
            raise ReconciliationConflict("SHADOW_NOT_CREATED")
        cur.execute(
            """update open_finance_transactions
                  set imported_launch_id=%s, match_launch_id=null, reconciliation_status='imported'
                where id=%s""",
            (shadow_id, o["id"]))
        return {"ok": True, "changed": True, "launch_id": shadow_id}
    return _write(user_id, of_tx_id, fn)


def list_reconciliations(user_id: int) -> list[dict]:
    """Pendências e fusões dos últimos 60 dias, com os dois lados."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select o.id, o.reconciliation_status, o.description, o.amount, o.transaction_date,
                      c.institution_name, l.id as launch_id, l.tipo, l.valor, l.alvo, l.nota,
                      coalesce(l.posted_at, l.criado_em::date) as launch_date
                 from open_finance_transactions o
                 join open_finance_accounts a on a.id = o.account_id
                 join open_finance_connections c on c.id = a.connection_id
                 join launches l on l.id = o.match_launch_id
                where c.user_id = %s and l.user_id = %s
                  and (o.reconciliation_status = 'pending'
                       or (o.reconciliation_status = any(%s)
                           and o.imported_launch_id = o.match_launch_id
                           and o.transaction_date >= %s))
                order by o.transaction_date desc, o.id desc""",
            (user_id, user_id, list(FUSED_STATUSES), today_tz() - timedelta(days=60)),
        )
        rows = cur.fetchall()
    return [{
        "of_tx_id": r["id"], "status": r["reconciliation_status"],
        "bank": {"description": r["description"], "amount": float(r["amount"]),
                 "date": r["transaction_date"].isoformat(), "institution": r["institution_name"]},
        "launch": {"id": r["launch_id"], "tipo": r["tipo"], "valor": float(r["valor"]),
                   "alvo": r["alvo"], "nota": r["nota"], "date": r["launch_date"].isoformat()},
    } for r in rows]


def reconciliation_summary(user_id: int) -> dict:
    """`delta_se_confirmar`: quanto a Carteira exibida mudaria se o usuário
    confirmasse todas as pendências (o "pode ser R$ X" = exibido + isto)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(PENDING_RECONCILIATION_SQL, merged_wallet_delta_params(user_id))
        r = cur.fetchone()
    return {"pending_count": int(r["pending_count"]),
            "delta_se_confirmar": r["delta_se_confirmar"],
            "receita_back": r["receita_back"]}


# Guarda de cobertura da Carteira, sempre pelo MENOR: a fusão já devolvida
# (`MERGED_WALLET_DELTA_SQL`) mais a receita pendente tirada de volta
# (`receita_back` ≤ 0). Despesa pendente já é o menor e não entra.
_GUARD_SQL = (
    f"select (select d from ({MERGED_WALLET_DELTA_SQL}) m)"
    f" + (select receita_back from ({PENDING_RECONCILIATION_SQL}) p) as d"
)


def wallet_guard_delta(cur, user_id: int) -> Decimal:
    cur.execute(_GUARD_SQL, merged_wallet_delta_params(user_id) * 2)
    return cur.fetchone()["d"]


async def wallet_guard_delta_async(cur, user_id: int) -> Decimal:
    await cur.execute(_GUARD_SQL, merged_wallet_delta_params(user_id) * 2)
    return (await cur.fetchone())["d"]
