"""Reconciliação OF × lançamento manual: confirmar, rejeitar e desfazer.

Estados de `open_finance_transactions` que importam aqui:
  pending     imported = sombra, match = X  (o usuário ainda não decidiu)
  confirmed   imported = match = X          (o usuário confirmou)
  auto_merged imported = X, match = X       (o import juntou, ou a antiga fusão
                                             reversa, em dado anterior ao #498;
                                             dado antigo pode ter match null)
  imported    imported = sombra, match null (sem par, ou par rejeitado/desfeito)

Toda escrita é uma transação só, na ordem do resto do módulo de Open Finance
(`accounts` → lançamento → transação OF). O apagar da fundida usa a mesma
ordem e chama `_desfaz` (apagar desfaz a junção, P3).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from psycopg import errors as pg_errors

from utils_date import _tz, today_tz

from .bank_movements import _lock_user, delete_if_shadow
from .connection import TIPO_CANON_SQL, get_conn
from .open_finance_categories import categoria_pigbank, garantir_no_catalogo
from .open_finance_cash import INTERNOS, INTERNOS_SQL, RESERVADO_SQL, cash_internal_tx_ids, internos_de
from .open_finance import (
    ACTIONABLE_PENDING_SQL, MERGED_WALLET_DELTA_SQL, PENDING_RECONCILIATION_SQL, _insert_of_shadow,
    actionable_pending_params, classify_open_finance_launch, merged_wallet_delta_params,
)

# Fonte única: quem desfaz e quem lista leem daqui (CLAUDE.md §0.7).
FUSED_STATUSES = ("auto_merged", "confirmed")


class ReconciliationConflict(Exception):
    """Corrida com outra escrita (sync, delete do lançamento): tente de novo."""


def _apply_bank_fields(cur, user_id, launch_id, row, cls):
    """Banco manda na representação; delta_conta e o original não mudam no sync."""
    instant = row["transacted_at"] or datetime.combine(row["transaction_date"], time(12), _tz())
    cur.execute(
        """update launches set valor=%s, tipo=%s, posted_at=%s, criado_em=%s,
               efeitos = coalesce(efeitos, '{}'::jsonb)
                 || case when coalesce(source,'manual') <> 'open_finance'
                              and not coalesce(efeitos ? 'of_original', false)
                         then jsonb_build_object('of_original', jsonb_build_object(
                              'valor', valor, 'tipo', tipo, 'posted_at', posted_at,
                              'criado_em', criado_em, 'time_known', efeitos->'time_known'))
                         else '{}'::jsonb end
                 || jsonb_build_object('time_known', %s::boolean)
             where user_id=%s and id=%s and (
                   (valor,tipo,posted_at,criado_em) is distinct from (%s,%s,%s,%s)
                   or efeitos->'time_known' is distinct from to_jsonb(%s::boolean)
                   or (coalesce(source,'manual') <> 'open_finance'
                       and not coalesce(efeitos ? 'of_original', false)))
             returning id""",
        (cls["valor"], cls["tipo"], row["transaction_date"], instant,
         row["transacted_at"] is not None, user_id, launch_id,
         cls["valor"], cls["tipo"], row["transaction_date"], instant,
         row["transacted_at"] is not None))
    return cur.fetchone() is not None


def _restore_original(cur, user_id, launch_id):
    """Saída da fusão restaura só campos bancários; edições e efeitos permanecem."""
    cur.execute(
        """update launches set valor=(efeitos->'of_original'->>'valor')::numeric,
               tipo=efeitos->'of_original'->>'tipo',
               posted_at=(efeitos->'of_original'->>'posted_at')::date,
               criado_em=(efeitos->'of_original'->>'criado_em')::timestamptz,
               efeitos=(efeitos - 'of_original' - 'time_known')
                 || case when efeitos->'of_original'->>'time_known' is not null
                         then jsonb_build_object('time_known', efeitos->'of_original'->'time_known')
                         else '{}'::jsonb end
             where user_id=%s and id=%s and efeitos ? 'of_original'
               and coalesce(source,'manual') <> 'open_finance'""", (user_id, launch_id))


def _locked_tx(cur, user_id, of_tx_id):
    _lock_user(cur, user_id)
    # Mesma ordem do apagar: accounts → lançamentos → espelho; relê depois da trava.
    cur.execute(
        """select l.id from launches l
             join open_finance_transactions o
               on l.id in (o.imported_launch_id, o.match_launch_id)
             join open_finance_accounts a on a.id=o.account_id
             join open_finance_connections c on c.id=a.connection_id
            where l.user_id=%s and c.user_id=%s and o.id=%s
            order by l.id for update of l""", (user_id, user_id, of_tx_id))
    cur.fetchall()
    cur.execute(
        """select o.id, o.provider_transaction_id, o.description, o.amount,
                  o.transaction_date, o.transacted_at, o.category,
                  a.provider_account_id, c.provider,
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
        taken = cur.fetchone()
        cur.execute(f"select 1 from launches where id=%s and user_id=%s and {RESERVADO_SQL.format(t='launches')}",
                    (x, user_id))
        # ou X, ou a própria transação, é do saque em espécie (db/open_finance_cash.py)
        if taken or cur.fetchone() or o["id"] in cash_internal_tx_ids(cur, user_id):
            raise ValueError("ALREADY_LINKED")
        cur.execute(
            """update open_finance_transactions
                  set imported_launch_id=%s, reconciliation_status='confirmed'
                where id=%s""",
            (x, o["id"]))
        _apply_bank_fields(cur, user_id, x, o,
                           classify_open_finance_launch(o["amount"], o["category"], o["description"]))
        # As outras pendências em X NÃO são tocadas: enquanto X está ocupado elas
        # saem da lista sozinhas (`ACTIONABLE_PENDING_SQL`) e voltam se o usuário
        # desfizer esta — gravar nelas tiraria a reversibilidade.
        shadow_id = o["imported_launch_id"]
        if shadow_id and shadow_id != x:
            delete_if_shadow(cur, user_id, shadow_id)
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


def _desfaz(cur, user_id, o, novas: list) -> dict:
    """Miolo do desfazer, sob a trava de `_locked_tx`. Categoria nova vai para
    `novas` (o catálogo é garantido depois do commit, fora da trava)."""
    x = o["imported_launch_id"]
    if (o["reconciliation_status"] not in FUSED_STATUSES or not x
            or o["match_launch_id"] not in (None, x)):
        return {"ok": True, "changed": False}
    cls = classify_open_finance_launch(o["amount"], o["category"], o["description"])
    if o["id"] in cash_internal_tx_ids(cur, user_id):  # par da Carteira (saque/depósito)
        cls["is_internal_movement"] = True
    shadow_id, _ = _insert_of_shadow(cur, user_id, o, cls)
    if shadow_id is None:
        raise ReconciliationConflict("SHADOW_NOT_CREATED")
    _restore_original(cur, user_id, x)
    novas.extend(filter(None, [categoria_pigbank(o["category"])]))
    cur.execute(
        """update open_finance_transactions
              set imported_launch_id=%s, match_launch_id=null, reconciliation_status='imported'
            where id=%s""",
        (shadow_id, o["id"]))
    return {"ok": True, "changed": True, "launch_id": shadow_id}


def undo_reconciliation(user_id: int, of_tx_id: int) -> dict:
    """Desfaz uma fusão (automática ou confirmada): recria a sombra do banco e
    solta X, que volta a contar na Carteira. Vale nos dois sentidos — no reverso
    a sombra foi apagada e renasce com o mesmo `external_id` do provedor."""
    novas = []
    result = _write(user_id, of_tx_id, lambda cur, o: _desfaz(cur, user_id, o, novas))
    garantir_no_catalogo(user_id, novas)  # depois do commit, fora da trava
    return result


def list_reconciliations(user_id: int) -> list[dict]:
    """Pendências acionáveis (mesma regra do resumo) e fusões dos últimos 60 dias."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""select o.id, o.reconciliation_status, o.description, o.amount, o.transaction_date,
                      c.institution_name, l.id as launch_id, {TIPO_CANON_SQL} as tipo, l.valor, l.alvo, l.nota,
                      coalesce(l.posted_at, l.criado_em::date) as launch_date
                 from open_finance_transactions o
                 join open_finance_accounts a on a.id = o.account_id
                 join open_finance_connections c on c.id = a.connection_id
                 join launches l on l.id = coalesce(o.match_launch_id, o.imported_launch_id)
                where c.user_id = %s and l.user_id = %s
                  and (o.id in (select of_tx_id from ({ACTIONABLE_PENDING_SQL}) p)
                       or (o.reconciliation_status = any(%s)
                           and o.imported_launch_id = l.id
                           and o.transaction_date >= %s))
                order by o.transaction_date desc, o.id desc""",
            (user_id, user_id, *actionable_pending_params(cur, user_id), list(FUSED_STATUSES),
             today_tz() - timedelta(days=60)),
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
        cur.execute(PENDING_RECONCILIATION_SQL, actionable_pending_params(cur, user_id))
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
    cur.execute(_GUARD_SQL, (*merged_wallet_delta_params(user_id), *actionable_pending_params(cur, user_id)))
    return cur.fetchone()["d"]


async def wallet_guard_delta_async(cur, user_id: int) -> Decimal:
    await cur.execute(INTERNOS_SQL, (user_id, list(INTERNOS)))  # = `actionable_pending_params`
    caixa = list(internos_de(await cur.fetchall()))
    await cur.execute(_GUARD_SQL, (*merged_wallet_delta_params(user_id) * 2, caixa))
    return (await cur.fetchone())["d"]
