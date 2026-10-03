"""Recurring Payments da Pluggy (`of_recurring_payments`) e a marcação do usuário
(`subscription_marks`). Quem monta a lista é `core/services/assinaturas.py`.

`of_recurring_payments` não tem user_id: o dono é a conexão, e toda leitura passa
por `open_finance_connections c ... where c.user_id = %s`.
"""
from .connection import get_conn
from .of_snapshots import _numero


def salvar_recorrencias(connection_id: int, itens: list) -> None:
    """Troca o resultado da conexão pelo da busca nova, numa transação. Item
    malformado é pulado. Grava também as receitas: o filtro é na leitura.

    Lista com itens e nenhum válido levanta (formato mudou): apagar e carimbar
    `recurring_fetched_at` aí consumiria o silêncio do Detetive sem lápide."""
    linhas = []
    for it in itens:
        if not isinstance(it, dict):
            continue
        desc, media, occ = it.get("description"), _numero(it.get("averageAmount")), it.get("occurrences")
        if not (isinstance(desc, str) and desc.strip() and media is not None
                and isinstance(occ, list) and all(isinstance(o, str) for o in occ)):
            continue
        linhas.append((connection_id, desc, media, _numero(it.get("regularityScore")), occ))
    if itens and not linhas:
        raise ValueError("recurring-payments sem nenhum item válido")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from of_recurring_payments where connection_id = %s", (connection_id,))
        if linhas:
            cur.executemany(
                "insert into of_recurring_payments"
                " (connection_id, description, average_amount, regularity_score, occurrences)"
                " values (%s, %s, %s, %s, %s)", linhas)
        # A 1ª busca da conexão silenciosa fica guardada: é dela que sai a lápide.
        cur.execute("update open_finance_connections set recurring_fetched_at = now(),"
                    " recurring_seed_descricoes = case when recurring_seed_silent"
                    " and recurring_seed_descricoes is null then %s else recurring_seed_descricoes end"
                    " where id = %s", ([l[1] for l in linhas], connection_id))
        conn.commit()


# Uma linha por ocorrência casada. O join da transação fica preso às contas do
# MESMO item da recorrência: o id de transação da Pluggy não é único entre usuários.
_SQL_OCORRENCIAS = """
select rp.id rp_id, rp.description,
       t.transaction_date, t.amount, t.category, t.raw->'merchant' merchant,
       t.raw->'creditCardMetadata' cc_meta, a.type account_type, a.name account_name,
       a.raw->>'number' account_number
from of_recurring_payments rp
join open_finance_connections c on c.id = rp.connection_id
join open_finance_accounts a on a.connection_id = rp.connection_id
join open_finance_transactions t on t.account_id = a.id
     and t.provider_transaction_id = any(rp.occurrences)
where c.user_id = %s and upper(coalesce(c.status,'')) not in ('PAUSED','DELETED')
  and rp.average_amount < 0
  and upper(a.currency) = 'BRL'   -- conta em dólar somaria US$ como R$
order by t.transaction_date, t.provider_transaction_id
"""


def ocorrencias_do_usuario(user_id: int) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SQL_OCORRENCIAS, (user_id,))
        return cur.fetchall()


def marcas(user_id: int) -> dict[str, tuple[str, bool]]:
    """`{chave: (status, assinatura_antes)}`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select merchant_key, status, assinatura_antes from subscription_marks"
                    " where user_id = %s", (user_id,))
        return {r["merchant_key"]: (r["status"], r["assinatura_antes"]) for r in cur.fetchall()}


def marcar(user_id: int, chave: str, status: str) -> None:
    """`status='nenhuma'` apaga a marca (é o "desfazer"). Ignorar guarda em
    `assinatura_antes` se a chave estava marcada como assinatura (ignorar de novo
    mantém o que já estava guardado); `assinatura` zera o guardado."""
    with get_conn() as conn, conn.cursor() as cur:
        if status == "nenhuma":
            cur.execute("delete from subscription_marks where user_id = %s and merchant_key = %s",
                        (user_id, chave))
        else:
            cur.execute(
                "insert into subscription_marks (user_id, merchant_key, status) values (%s, %s, %s)"
                " on conflict (user_id, merchant_key) do update set"
                " assinatura_antes = excluded.status = 'ignorar' and (subscription_marks.status = 'assinatura'"
                " or (subscription_marks.status = 'ignorar' and subscription_marks.assinatura_antes)),"
                " status = excluded.status, updated_at = now()",
                (user_id, chave, status))
        conn.commit()


def conexoes_a_silenciar(user_id: int) -> list[int]:
    """Conexões cuja 1ª busca ainda vira lápide: só depois de buscadas."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from open_finance_connections where user_id = %s"
                    " and recurring_seed_silent and recurring_fetched_at is not null", (user_id,))
        return [r["id"] for r in cur.fetchall()]


def descricoes_das_conexoes(user_id: int, ids: list[int]) -> list[str]:
    """As descrições da 1ª busca dessas conexões, sem filtro de status nem de marca:
    a lápide do 1º deploy cobre o que hoje não alerta e pode alertar depois. É a
    foto da 1ª busca, não a atual: com o Detetive desligado a foto muda a cada
    sync, e o que entrou depois do deploy não pode virar lápide."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select unnest(recurring_seed_descricoes) d from open_finance_connections"
                    " where user_id = %s and id = any(%s)", (user_id, list(ids)))
        return [r["d"] for r in cur.fetchall()]


def consumir_silencio(user_id: int, ids: list[int]) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update open_finance_connections set recurring_seed_silent = false"
                    " where user_id = %s and id = any(%s)", (user_id, list(ids)))
        conn.commit()
