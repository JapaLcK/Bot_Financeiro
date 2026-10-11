"""Faturas do cartão que o banco fechou (`of_card_bills`, vindas de `/bills` da Pluggy).

Só GRAVA — nenhuma tela lê ainda (só o export LGPD, db/privacy.py). Sem user_id: o dono é conta -> conexão, e quem ler
filtra por `join open_finance_accounts a ... join open_finance_connections c ...
where c.user_id = %s` (como `of_recurring_payments`, ver db/of_recurring.py).

ponytail: fatura que some da Pluggy fica gravada (o upsert nunca apaga).
"""
from datetime import date

from psycopg.types.json import Jsonb

from .connection import get_conn
from .of_snapshots import _numero


def _data(v):
    """`YYYY-MM-DD` (com ou sem hora) ou None."""
    try:
        return date.fromisoformat(v[:10]) if isinstance(v, str) else None
    except ValueError:
        return None


def salvar_faturas_do_banco(connection_id: int, por_conta: dict[str, list]) -> None:
    """Upsert por (conta, id da fatura), numa transação. `por_conta` é
    `{provider_account_id: faturas da Pluggy}`. Fatura sem id, sem `dueDate` válido
    ou com `totalAmount` que não é número é pulada. Conta com itens e nenhum
    válido é pulada com uma linha de log (formato mudou); as outras contas gravam."""
    linhas = []
    for pid, itens in por_conta.items():
        validas = []
        for b in itens:
            if not isinstance(b, dict):
                continue
            bid, venc, total = b.get("id"), _data(b.get("dueDate")), _numero(b.get("totalAmount"))
            if isinstance(bid, str) and bid.strip() and venc and total is not None:
                moeda = b.get("totalAmountCurrencyCode")
                validas.append((pid, bid, venc, _data(b.get("billClosingDate")), total,
                                moeda if isinstance(moeda, str) else None, Jsonb(b)))
        if itens and not validas:
            print(f"[of_card_bills] conta={pid} sem nenhuma fatura válida de {len(itens)}", flush=True)
        linhas += validas
    with get_conn() as conn, conn.cursor() as cur:
        for pid, *resto in linhas:
            # account_id só dentro da conexão travada pelo item: o id da Pluggy não é único entre usuários.
            cur.execute(
                "insert into of_card_bills (account_id, provider_bill_id, due_date, closing_date,"
                " total_amount, currency, raw)"
                " select a.id, %s, %s, %s, %s, %s, %s from open_finance_accounts a"
                " where a.connection_id = %s and a.provider_account_id = %s"
                " on conflict (account_id, provider_bill_id) do update set due_date = excluded.due_date,"
                " closing_date = excluded.closing_date, total_amount = excluded.total_amount,"
                " currency = excluded.currency, raw = excluded.raw, updated_at = now()",
                (*resto, connection_id, pid))
        conn.commit()
