"""Declarações bancárias: registrar é diferente de conferir com o extrato."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .bank_declaracoes import congelada, desde_da_leitura, e_fluxo, elegivel_fluxo, janela, projeta_todas, tem_evidencia
from .connection import get_conn
from utils_date import _tz
from utils_text import VALOR_MAX


def _amount(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result.copy_abs() <= VALOR_MAX else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def is_of_shadow(source, delta):
    """Sombra consumível: zero exato, independente da categoria histórica."""
    return source == "open_finance" and _amount(delta) == Decimal(0)


def delete_if_shadow(cur, user_id, launch_id) -> bool:
    """Trava e apaga `launch_id` só se ainda for sombra OF; preserva lançamento
    manual (auto-merge) ou sombra alheia. Chamador precisa estar dentro da
    transação que já trava o usuário (`_lock_user`) — NÃO abre conexão própria."""
    if not launch_id:
        return False
    cur.execute("select source,efeitos from launches where id=%s and user_id=%s for update",
                (launch_id, user_id))
    s = cur.fetchone()
    if s and isinstance(s["efeitos"], dict) and is_of_shadow(s["source"], s["efeitos"].get("delta_conta")):
        cur.execute("delete from launches where id=%s and user_id=%s", (launch_id, user_id))
        return True
    return False


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
    """Mutex por usuário: a linha de `accounts` (sem FK entrante, só serve de trava).

    ORDEM DE LOCK — fonte única. O que protege é ESTE mutex, tomado PRIMEIRO por
    transação que trava mais de uma família entre launches, pockets, investments e
    lotes; os DOIS lados de um par precisam dele. A ordem interna só vale contra quem
    NÃO o toma: `accrue_all_*` (de propósito: o mutex serializaria a leitura do
    dashboard) vai pai → filho, e o reset, que segura accounts, apaga pai antes de
    filho (`_RESET_TABLES`). Seguir "launches → pai → lotes" SEM o mutex reabre o ciclo
    com o reset, que apaga launches por último. Fora: cartão (cartão FOR SHARE → fatura → conta → launches, no pagamento manual).
    Sync/conferência OF usam a mesma ordem, antes de conta OF/transação/vínculo.
    Exceções conhecidas, com acompanhamento em issue separada: (1) cartão: o undo de pagamento
    de fatura só toma o mutex quando o pagamento é financiado/ligado ao banco (Open Finance);
    nesse caso `pay_bill_amount` × undo trava sem deadlock detectável, e o par pagamento de
    fatura que ganha ligação OF depois do preview × sync dá DeadlockDetected quando o undo
    chega primeiro. Sem Open Finance a ordem é cartão (FOR SHARE) → fatura → conta → launches (`pay_bill_amount` segura a
    fatura enquanto outra conexão pede `accounts`) e cruza com reset, merge e delete_user_data;
    `pay_bill_amount` também pode travar sem deadlock detectável contra reset e merge; (2)
    `delete_user_data` apaga Open Finance e crédito ANTES de `accounts` (o mutex vem antes do
    laço de tabelas por usuário, não no topo: T17/T21/T23 modelam uma sessão que comita no meio
    da exclusão), então cruza com reset, merge e sync/conciliação do Open Finance, que fazem
    accounts → Open Finance."""
    cur.execute("select user_id from accounts where user_id=%s for update", (user_id,))


def _transactions(cur, user_id, *, lock=False, desde: date | None = None, ids=()):
    from .open_finance import BANK_ACCOUNTS_SQL, is_credit_card_payment, investment_transfer_kind
    from .open_finance_cash import cash_internal_tx_ids
    cur.execute(
        f"""select t.*, a.id as bank_account_id, a.name as account_name,
                   a.institution_name, l.source as imported_source, l.user_id as imported_user,
                   l.efeitos->>'delta_conta' as imported_delta
              from open_finance_transactions t
              join ({BANK_ACCOUNTS_SQL}) a on a.id=t.account_id
              left join launches l on l.id=t.imported_launch_id
             where (t.imported_launch_id is null or l.user_id=%s)
               -- saque/depósito em espécie já é da Carteira (db/open_finance_cash.py)
               and not t.id = any(%s)
               and (%s::date is null or t.transaction_date >= %s::date or t.id = any(%s))
             order by t.transaction_date, t.id""" + (" for update of t" if lock else ""),
        (user_id, user_id, list(cash_internal_tx_ids(cur, user_id)), desde, desde, list(ids)))
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
        """select d.*, l.alvo, l.nota, l.tipo, l.valor as launch_valor, l.criado_em as launch_criado_em,
                  l.efeitos->'funding_source'->>'label' as bank_label
             from bank_movement_declarations d
             join launches l on l.id=d.launch_id and l.user_id=d.user_id
            where d.user_id=%s order by d.declared_at, d.launch_id""", (user_id,))
    return [dict(r) for r in cur.fetchall()]


def _eligible(declaration, tx, *, manual=False):
    if e_fluxo(declaration):
        return elegivel_fluxo(declaration, tx, manual=manual)
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
              set matched_transaction_id=%s, confirmation_method=%s, confirmed_at=now(),
                  requires_review = requires_review and not %s, manual_only = manual_only or %s
            where user_id=%s and launch_id=%s""",
        (tx["id"], method, e_fluxo(declaration), method == "manual" and e_fluxo(declaration),
         user_id, declaration["launch_id"]))
    cur.execute(
        """update open_finance_transactions t
              set imported_launch_id=%s, match_launch_id=%s, reconciliation_status='bank_movement_confirmed'
             from open_finance_accounts a, open_finance_connections c
            where t.id=%s and a.id=t.account_id and c.id=a.connection_id and c.user_id=%s""",
        (declaration["launch_id"], declaration["launch_id"], tx["id"], user_id))
    if old_id and old_id != declaration["launch_id"]:
        # OF analytics-only: jamais undo, jamais alterar efeitos/lotes do manual.
        delete_if_shadow(cur, user_id, old_id)


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
        if d["account_id"] not in active_accounts and not d["account_ambiguous"] and not e_fluxo(d):
            cur.execute("update bank_movement_declarations set account_ambiguous=true where launch_id=%s and user_id=%s", (d["launch_id"], user_id))
            d["account_ambiguous"] = True
        if not d["matched_transaction_id"] and d["confirmation_method"]:
            cur.execute("update bank_movement_declarations set confirmation_method=null, confirmed_at=null, requires_review=true where launch_id=%s and user_id=%s", (d["launch_id"], user_id))
            d["requires_review"] = True
    # Congelada (fluxo casado há muito): não alarga a janela de leitura nem busca par, mas a PRÓPRIA prova
    # segue revalidada (`ids`): o banco apagar/corrigir a transação desvincula também a antiga.
    hoje = datetime.now(_tz()).date()
    congeladas = {d["launch_id"] for d in declarations if congelada(d, hoje)}
    transactions = _transactions(
        cur, user_id, lock=True,
        desde=desde_da_leitura([d for d in declarations if d["launch_id"] not in congeladas]) or date.max,
        ids=[d["matched_transaction_id"] for d in declarations if d["launch_id"] in congeladas])
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
    dates = [t["transaction_date"] for t in transactions]
    graph = {d["launch_id"]: [t for t in janela(transactions, dates, d) if t["id"] not in occupied
                              and _free(t) and _eligible(d, t) and tem_evidencia(d, t)] for d in pending}
    claims = Counter(t["id"] for choices in graph.values() for t in choices)  # candidato único nos DOIS sentidos
    confirmed = 0
    for d in pending:
        candidates = graph[d["launch_id"]]
        # Fluxo: conta NULL = qualquer conta, então a revisão pós-desconexão (que protege a escolha
        # de conta, ADR 0003) não se aplica; o candidato único nos dois sentidos segue valendo.
        # Congelada que perdeu a prova NESTA passada leu só a janela das ativas: não escolhe par agora
        # (voltaria a ligar com unicidade falsa); no próximo reconcile ela já não é congelada.
        # I7: fluxo só manual (escolhido, trocado ou desfeito) nunca liga sozinho, mas segue em `claims`.
        if (d["requires_review"] and not e_fluxo(d)) or d["account_ambiguous"] or len(candidates) != 1 \
                or d["launch_id"] in congeladas or (e_fluxo(d) and d["manual_only"]):
            continue
        tx = candidates[0]
        if claims[tx["id"]] != 1:
            continue
        _bind(cur, user_id, d, tx, "automatic")
        d["matched_transaction_id"] = tx["id"]
        confirmed += 1
    projeta_todas(cur, user_id, declarations, by_id)  # fluxo: valor/data do lançamento seguem o extrato
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
