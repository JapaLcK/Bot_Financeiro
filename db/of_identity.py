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
        f"""select distinct l.id, l.external_id, l.source from {table} l
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
            f"""select distinct c.provider, a.provider_account_id, t.provider_transaction_id,
                         t.reconciliation_status
                  from open_finance_transactions t
                  join open_finance_accounts a on a.id=t.account_id
                  join open_finance_connections c on c.id=a.connection_id
                 where c.user_id=%s and t.{link}=%s""", (user_id, candidate["id"]))
        links = cur.fetchall()
        identities = {external_id(r) for r in links}
        exclusive = {external_id(r) for r in links
                     if credit or candidate["source"] == "open_finance"
                     or r["reconciliation_status"] != "bank_movement_confirmed"}
        if len(exclusive) > 1:
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
        if key in identities:
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
        f"""select t.id, t.account_id from open_finance_transactions t
              join open_finance_accounts a on a.id=t.account_id
              join open_finance_connections c on c.id=a.connection_id
             where c.user_id=%s and t.id=%s and {LATEST_TRANSACTION_SQL.format(tx='t')}""",
        (user_id, row["of_tx_id"]))
    current = cur.fetchone()
    if not current:
        return
    cur.execute(
        f"""select t.id, t.account_id, t.reconciliation_status, t.match_launch_id
              from open_finance_transactions t
              join open_finance_accounts a on a.id=t.account_id
              join open_finance_connections c on c.id=a.connection_id
             where c.user_id=%s and t.{link}=%s and t.id<>%s
               and c.provider=%s and a.provider_account_id=%s and t.provider_transaction_id=%s
             order by c.id desc, t.id desc for update of t""",
        (user_id, imported_id, row["of_tx_id"], row["provider"],
         row["provider_account_id"], row["provider_transaction_id"]))
    previous = cur.fetchall()
    status = previous[0]["reconciliation_status"] if previous else "imported"
    match = previous[0]["match_launch_id"] if previous else None
    if previous:
        if not credit:
            for old in previous:
                cur.execute(
                    """update bank_movement_declarations set matched_transaction_id=%s,
                           account_id=case when account_id=%s then %s else account_id end
                        where user_id=%s and launch_id=%s and matched_transaction_id=%s""",
                    (row["of_tx_id"], old["account_id"], current["account_id"],
                     user_id, imported_id, old["id"]))
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
    table = "credit_transactions" if credit else "launches"
    cur.execute(
        f"""select t.{link} from open_finance_transactions t
              join open_finance_accounts a on a.id=t.account_id
              join open_finance_connections c on c.id=a.connection_id
             join {table} l on l.id=t.{link} and l.user_id=c.user_id
             where c.user_id=%s and t.{link} is not null
               and (%s or l.source='open_finance'
                    or t.reconciliation_status is distinct from 'bank_movement_confirmed')
             group by t.{link}
            having count(distinct (c.provider,a.provider_account_id,t.provider_transaction_id))>1
             limit 1""", (user_id, credit))
    if cur.fetchone():
        raise ValueError("OF_IDENTITY_AMBIGUOUS")


def preserve_disconnect_alias(cur, user_id, removing, imported_id, *, credit=False):
    """Sob lock da compra/usuário, mantém a representação no espelho sobrevivente.

    Exclusão de transação pelo provedor não usa este caminho: um alias sem
    vínculo não é evidência de que uma transação excluída continua existindo.
    """
    link = "imported_credit_tx_id" if credit else "imported_launch_id"
    cur.execute(
        f"""select t.id as previous_id, t.{link} as imported_id, t.reconciliation_status, t.match_launch_id,
                   t.account_id as previous_account, alias.id as alias_id, alias.account_id as alias_account
              from open_finance_transactions t
              join open_finance_accounts a on a.id=t.account_id
              join open_finance_connections c on c.id=a.connection_id
              join lateral (
                  select t2.id, t2.account_id from open_finance_transactions t2
                  join open_finance_accounts a2 on a2.id=t2.account_id
                  join open_finance_connections c2 on c2.id=a2.connection_id
                  where c2.user_id=c.user_id and c2.provider=c.provider
                    and a2.provider_account_id=a.provider_account_id and a2.type=a.type
                    and t2.provider_transaction_id=t.provider_transaction_id
                    and not t2.id=any(%s) and upper(c2.status)<>'DELETED'
                    and (t2.{link} is null or t2.{link}=t.{link})
                  order by c2.id desc, t2.id desc limit 1 for update of t2
              ) alias on true
             where c.user_id=%s and t.id=any(%s) and t.{link}=%s""",
        (removing, user_id, removing, imported_id))
    for row in cur.fetchall():
        cur.execute(
            f"""update open_finance_transactions t set {link}=%s,
                       reconciliation_status=%s, match_launch_id=%s
                  from open_finance_accounts a join open_finance_connections c on c.id=a.connection_id
                 where c.user_id=%s and t.account_id=a.id and t.id=%s""",
            (row["imported_id"], row["reconciliation_status"], row["match_launch_id"],
             user_id, row["alias_id"]))
        if not credit:
            cur.execute(
                """update bank_movement_declarations set matched_transaction_id=%s,
                           account_id=case when account_id=%s then %s else account_id end
                    where user_id=%s and launch_id=%s and matched_transaction_id=%s""",
                (row["alias_id"], row["previous_account"], row["alias_account"],
                 user_id, row["imported_id"], row["previous_id"]))
