"""Identidade do provedor, compartilhada por sombras BANK e compras CREDIT."""
from __future__ import annotations

import json


def external_id(row):
    # JSON evita ambiguidade de delimitadores e não usa o id local da conexão.
    return json.dumps([row["provider"], row["provider_account_id"],
                       row["provider_transaction_id"]], separators=(",", ":"))


def existing_import(cur, user_id, row, *, credit=False, include_fused=False):
    """Reutiliza legado só quando sua procedência é inequívoca. Não repara colisões."""
    table = "credit_transactions" if credit else "launches"
    link = "imported_credit_tx_id" if credit else "imported_launch_id"
    key = external_id(row)
    cur.execute(
        f"""select distinct l.id, l.external_id from {table} l
              left join open_finance_transactions t on t.{link}=l.id
              left join open_finance_accounts a on a.id=t.account_id
              left join open_finance_connections c on c.id=a.connection_id
             where l.user_id=%s and (l.source='open_finance' or %s)
               and ((l.source='open_finance' and l.external_id in (%s,%s))
                    or (c.user_id=%s and c.provider=%s and a.provider_account_id=%s
                        and t.provider_transaction_id=%s))""",
        (user_id, include_fused, key, row["provider_transaction_id"], user_id, row["provider"],
         row["provider_account_id"], row["provider_transaction_id"]),
    )
    candidates = cur.fetchall()
    found = []
    for candidate in candidates:
        cur.execute(
            f"""select distinct c.provider, a.provider_account_id, t.provider_transaction_id
                  from open_finance_transactions t
                  join open_finance_accounts a on a.id=t.account_id
                  join open_finance_connections c on c.id=a.connection_id
                 where c.user_id=%s and t.{link}=%s""", (user_id, candidate["id"]))
        identities = {external_id(r) for r in cur.fetchall()}
        if len(identities) > 1:
            raise ValueError("OF_IDENTITY_AMBIGUOUS")
        if not identities and candidate["external_id"] == key:
            identities = {key}
        if not identities:
            # Uma sombra legada sem backlink só é adotada se o espelho prova a conta.
            cur.execute(
                """select distinct c.provider, a.provider_account_id, t.provider_transaction_id
                     from open_finance_transactions t
                     join open_finance_accounts a on a.id=t.account_id
                     join open_finance_connections c on c.id=a.connection_id
                    where c.user_id=%s and t.provider_transaction_id=%s""",
                (user_id, row["provider_transaction_id"]))
            identities = {external_id(r) for r in cur.fetchall()}
            if len(identities) != 1:
                raise ValueError("OF_IDENTITY_AMBIGUOUS")
        if identities == {key}:
            found.append(candidate["id"])
    if len(found) > 1:
        raise ValueError("OF_IDENTITY_AMBIGUOUS")
    return found[0] if found else None


# Alias a/c: só transfere autoridade se a transação está presente no novo espelho.
LATEST_TRANSACTION_SQL = """not exists (
    select 1 from open_finance_accounts a2
    join open_finance_connections c2 on c2.id=a2.connection_id
    join open_finance_transactions t2 on t2.account_id=a2.id
    where c2.user_id=c.user_id and c2.provider=c.provider
      and a2.provider_account_id=a.provider_account_id and c2.id>c.id
      and t2.provider_transaction_id={tx}.provider_transaction_id)"""


def bind_import(cur, user_id, row, imported_id, *, credit=False):
    """Transfere o backlink na reconexão; desconectar o espelho velho não apaga a linha."""
    link = "imported_credit_tx_id" if credit else "imported_launch_id"
    # CREDIT leu os candidatos antes de tomar accounts; revalida a autoridade agora.
    cur.execute(
        f"""select t.id from open_finance_transactions t
              join open_finance_accounts a on a.id=t.account_id
              join open_finance_connections c on c.id=a.connection_id
             where c.user_id=%s and t.id=%s and {LATEST_TRANSACTION_SQL.format(tx='t')}""",
        (user_id, row["of_tx_id"]))
    if not cur.fetchone():
        return
    cur.execute(
        f"""select t.id, t.reconciliation_status, t.match_launch_id
              from open_finance_transactions t
              join open_finance_accounts a on a.id=t.account_id
              join open_finance_connections c on c.id=a.connection_id
             where c.user_id=%s and t.{link}=%s and t.id<>%s
             order by c.id desc, t.id desc for update of t""",
        (user_id, imported_id, row["of_tx_id"]))
    previous = cur.fetchall()
    status = previous[0]["reconciliation_status"] if previous else "imported"
    match = previous[0]["match_launch_id"] if previous else None
    if previous:
        cur.execute(
            f"""update open_finance_transactions t set {link}=null, match_launch_id=null,
                       reconciliation_status='imported'
                  from open_finance_accounts a join open_finance_connections c on c.id=a.connection_id
                 where c.user_id=%s and t.account_id=a.id and t.id=any(%s)""",
            (user_id, [r["id"] for r in previous]))
    cur.execute(
        f"""update open_finance_transactions t set {link}=%s,
                   reconciliation_status=%s, match_launch_id=%s
              from open_finance_accounts a join open_finance_connections c on c.id=a.connection_id
             where c.user_id=%s and t.account_id=a.id and t.id=%s""",
        (imported_id, status, match, user_id, row["of_tx_id"]))


def assert_unambiguous_links(cur, user_id, *, credit=False):
    """Detecta vínculos colididos antes de corrigir valores; não escolhe uma conta."""
    link = "imported_credit_tx_id" if credit else "imported_launch_id"
    cur.execute(
        f"""select t.{link} from open_finance_transactions t
              join open_finance_accounts a on a.id=t.account_id
              join open_finance_connections c on c.id=a.connection_id
             where c.user_id=%s and t.{link} is not null
             group by t.{link}
            having count(distinct (c.provider,a.provider_account_id,t.provider_transaction_id))>1
             limit 1""", (user_id,))
    if cur.fetchone():
        raise ValueError("OF_IDENTITY_AMBIGUOUS")
