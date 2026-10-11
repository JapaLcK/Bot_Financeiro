"""Faturas do cartão que o banco fechou (`of_card_bills`, vindas de `/bills` da Pluggy).

Nenhuma tela lê a tabela; quem a lê é `quitar_pelo_banco` (abaixo), que grava em `credit_bills.paid_amount` do
cartão do banco o que o banco já recebeu (o export LGPD, db/privacy.py, também). Sem user_id: o dono é conta ->
conexão, e quem ler filtra por `join open_finance_accounts a ... join open_finance_connections c ...
where c.user_id = %s` (como `of_recurring_payments`, ver db/of_recurring.py).

ponytail: fatura que some da Pluggy fica gravada (o upsert nunca apaga), mas não entra na quitação: só vale a fatura
gravada NESTE sync (frescor por fatura, ver `quitar_pelo_banco`).
"""
from datetime import date
from decimal import Decimal

from psycopg.types.json import Jsonb

from utils_date import today_tz

from .connection import get_conn
from .of_snapshots import _numero

TOL = Decimal("0.05")

# Única definição de "fatura do banco" do cartão (isolamento: cartão, conta e conexão do mesmo user_id).
FATURAS_DO_CARTAO_SQL = """
select a.id as account_id, a.provider_account_id, b.provider_bill_id, b.due_date, b.closing_date, b.total_amount, b.currency,
       b.raw->'payments' as payments, b.raw->'financeCharges' as charges
  from credit_cards cc
  join open_finance_accounts a on a.id = cc.open_finance_account_id
  join open_finance_connections c on c.id = a.connection_id
  join of_card_bills b on b.account_id = a.id
 where cc.id = %(card)s and cc.user_id = %(uid)s and c.user_id = %(uid)s
 order by b.due_date"""

# Compras do cartão com a fatura do banco a que pertencem (espelho da conta ATUAL; o mais novo por compra).
COMPRAS_POR_FATURA_SQL = """
select distinct on (ct.id) ct.bill_id, ct.valor, o.raw->'creditCardMetadata'->>'billId' as fatura_banco
  from credit_cards cc
  join open_finance_accounts a on a.id = cc.open_finance_account_id
  join open_finance_connections c on c.id = a.connection_id
  join open_finance_transactions o on o.account_id = a.id
  join credit_transactions ct on ct.id = o.imported_credit_tx_id
                              and ct.user_id = cc.user_id and ct.card_id = cc.id
 where cc.id = %(card)s and cc.user_id = %(uid)s and c.user_id = %(uid)s
 order by ct.id, o.id desc"""


def _data(v):
    """`YYYY-MM-DD` (com ou sem hora) ou None."""
    try:
        return date.fromisoformat(v[:10]) if isinstance(v, str) else None
    except ValueError:
        return None


def salvar_faturas_do_banco(connection_id: int, por_conta: dict[str, list]) -> set[tuple[str, str]]:
    """Upsert por (conta, id da fatura), numa transação. `por_conta` é
    `{provider_account_id: faturas da Pluggy}`. Fatura sem id, sem `dueDate` válido
    ou com `totalAmount` que não é número é pulada. Conta com itens e nenhum
    válido é pulada com uma linha de log (formato mudou); as outras contas gravam.
    Devolve os pares (provider_account_id, provider_bill_id) das faturas válidas gravadas NESTA chamada: só elas são
    frescas, e é só nelas que `quitar_pelo_banco` pode confiar (se levanta, nada foi gravado). Fatura inválida ou
    que sumiu da Pluggy deixa a linha velha na tabela, mas fora do conjunto."""
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
    return {(pid, bid) for pid, bid, *_ in linhas}


def _soma(itens, absoluto=False):
    """Soma `amount` de uma lista da Pluggy; None se algum item não é número (nunca lê como 0).
    `absoluto`: soma dos módulos, e sinais misturados (pagamento e estorno na lista) também dão None."""
    if itens is None:
        return Decimal(0)
    if not isinstance(itens, list):
        return None
    liq = bruto = Decimal(0)
    for i in itens:
        v = _numero(i.get("amount")) if isinstance(i, dict) else None
        if v is None:
            return None
        liq, bruto = liq + v, bruto + abs(v)
    return (bruto if abs(liq) == bruto else None) if absoluto else liq


def _fracao(pago, total):
    """Parte paga da fatura, de 0 a 1 (teto 1: excedente não vira crédito para a seguinte)."""
    if pago >= total - TOL:
        return Decimal(1)
    return pago / total


def cobertura(faturas: list[dict], hoje: date) -> dict[str, Decimal]:
    """`{provider_bill_id: parte paga}` das faturas FECHADAS do cartão (`faturas` ordenadas por vencimento).

    Fechada = `(closing_date or due_date) < hoje`; fora: aberta, moeda não-BRL (null passa) e total > TOL com
    pagamento ilegível (ou de sinais misturados). Fechada com total <= TOL entra com parte 0 (o banco zerou a
    fatura: o pago das compras dela volta a 0), mas não pontua. Duas regras de leitura do pagamento são pontuadas
    por conta (exatas, cobertas): a "própria" (o pagamento da fatura Y quita Y) e a "N+1" (o de Y+1, sem
    encargos, quita Y; só vizinhas a 20-40 dias).
    Só a "própria" é aplicada; N+1 vencendo ou empate descarta as faturas de valor (conta ambígua não quita), mas as
    zeradas (parte 0) entram sempre: o banco zerar a fatura não depende da regra de leitura do pagamento."""
    fech, zeradas = [], []
    for b in faturas:
        moeda, pay = b["currency"], _soma(b["payments"], absoluto=True)
        if (b["closing_date"] or b["due_date"]) >= hoje or moeda not in (None, "BRL"):
            continue
        if b["total_amount"] <= TOL:
            zeradas.append(b["provider_bill_id"])
        elif pay is not None:
            fech.append((b, b["total_amount"], pay, _soma(b["charges"])))
    prop, nmais = [0, 0], [0, 0]
    for i, (b, total, pay, _) in enumerate(fech):
        prop[0] += abs(pay - total) <= TOL
        prop[1] += pay >= total - TOL
        if i + 1 < len(fech):
            seg, _, pay_s, chg_s = fech[i + 1]
            if chg_s is not None and 20 <= (seg["due_date"] - b["due_date"]).days <= 40:
                ef = pay_s - chg_s
                nmais[0] += abs(ef - total) <= TOL
                nmais[1] += ef >= total - TOL
    # ponytail: o ramo N+1 só pontua. Aplicá-lo exige guardar `pay_s - chg_s` por fatura no laço (a última não tem par):
    # código novo, não interruptor.
    if prop <= nmais:
        if fech:
            print(f"[of_card_bills] conta={faturas[0]['account_id']} regra indefinida", flush=True)
        return dict.fromkeys(zeradas, Decimal(0))
    return {**dict.fromkeys(zeradas, Decimal(0)),
            **{b["provider_bill_id"]: _fracao(pay, total) for b, total, pay, _ in fech}}


def pago_por_fatura_pig(compras: list[dict], frac: dict[str, Decimal]) -> dict[int, Decimal]:
    """`{credit_bill_id: pago}`: Σ valor × parte paga da fatura do banco, só das compras casadas.
    Limite: estorno sem `billId` não entra, e o pago de fatura parcial sai maior (o integral é contido pelo `least` do UPDATE e pelo aparo que vem depois)."""
    pagos: dict[int, Decimal] = {}
    for c in compras:
        f = frac.get(c["fatura_banco"])
        if f is not None:
            pagos[c["bill_id"]] = pagos.get(c["bill_id"], Decimal(0)) + c["valor"] * f
    return {k: v.quantize(Decimal("0.01")) for k, v in pagos.items()}


_NOVO = "greatest(0, least(b.total, v.pago))"   # avaliado na hora da escrita: nunca passa do total
_GRAVA_SQL = f"""
update credit_bills b set
  paid_amount = {_NOVO},
  status = case when b.total > 0 and {_NOVO} >= b.total then 'paid'
                when b.status = 'paid' and (b.total > {_NOVO} or {_NOVO} <= 0) then 'open' else b.status end,
  paid_at = case when b.total > 0 and {_NOVO} >= b.total then coalesce(b.paid_at, now())
                 when b.status = 'paid' and (b.total > {_NOVO} or {_NOVO} <= 0) then null else b.paid_at end
 from unnest(%(ids)s::bigint[], %(pagos)s::numeric[]) as v(id, pago)
 where b.id = v.id and b.user_id = %(uid)s and b.card_id = %(card)s
   and (b.paid_amount is distinct from {_NOVO}
        or (b.total > 0 and {_NOVO} >= b.total and b.status <> 'paid')
        or (b.status = 'paid' and (b.total > {_NOVO} or {_NOVO} <= 0)))"""


# Pago que não cabe no total do Pig (compra casada que saiu, ou o banco que se foi) é aparado nele. Cobre também pago ==
# total com a fatura ainda aberta. Fatura 'paid' com pago <= 0 (zumbi, de qualquer total) reabre como 'open' com paid_at nulo, pela
# convenção de db/cards.py de que 'paid' exige pago > 0 (ramo `status = 'paid' and least(...) <= 0` do where). Roda a cada quitação e no disconnect (db/open_finance.py). Params: (user_id, card_id).
APARAR_PAGO_SQL = """
update credit_bills set paid_amount = least(paid_amount, greatest(total, 0)),
  status = case when total > 0 and paid_amount > 0 then 'paid' when status = 'paid' then 'open' else status end,
  paid_at = case when total > 0 and paid_amount > 0 then coalesce(paid_at, now()) when status = 'paid' then null else paid_at end
 where user_id = %s and card_id = %s
   and (paid_amount > greatest(total, 0) or (total > 0 and paid_amount >= total and status <> 'paid')
        or (status = 'paid' and least(paid_amount, greatest(total, 0)) <= 0))"""


def quitar_pelo_banco(user_id: int, connection_id: int, frescas: set[tuple[str, str]]) -> None:
    """Grava em `credit_bills.paid_amount` o que o banco pagou, por cartão da conexão. Escrita ABSOLUTA
    (idempotente), e depois o aparo do pago que ficou acima do total. Só entram na cobertura as faturas
    cujo par (`provider_account_id`, `provider_bill_id`) está em `frescas` (o que `salvar_faturas_do_banco` gravou
    neste sync): linha velha de `of_card_bills` (fatura que veio inválida ou sumiu da Pluggy) não pode se combinar com
    compras novas. Cartão sem fatura fresca não calcula cobertura. O aparo roda em TODO cartão da conexão. Quem chama segura o `pluggy_item_lock` do item: é ele a trava, sem `for update`."""
    hoje = today_tz()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select cc.id from credit_cards cc"
            " join open_finance_accounts a on a.id = cc.open_finance_account_id"
            " join open_finance_connections c on c.id = a.connection_id"
            " where a.connection_id = %s and c.user_id = %s and cc.user_id = %s",
            (connection_id, user_id, user_id))
        for card in [r["id"] for r in cur.fetchall()]:
            p = {"card": card, "uid": user_id}
            cur.execute(FATURAS_DO_CARTAO_SQL, p)
            frac = cobertura([f for f in cur.fetchall()
                              if (f["provider_account_id"], f["provider_bill_id"]) in frescas], hoje)
            if frac:
                cur.execute(COMPRAS_POR_FATURA_SQL, p)
                pagos = pago_por_fatura_pig(cur.fetchall(), frac)
                if pagos:
                    cur.execute(_GRAVA_SQL, {**p, "ids": list(pagos), "pagos": list(pagos.values())})
            cur.execute(APARAR_PAGO_SQL, (user_id, card))   # compra casada que sumiu deixa pago velho > total
        conn.commit()
