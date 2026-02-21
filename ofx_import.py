# ofx_import.py
import io
import hashlib
from datetime import datetime, date, time
from decimal import Decimal
import re
from ofxparse import OfxParser

from utils_date import _tz
from utils_text import normalize_text, LOCAL_RULES, contains_word
from db import set_balance, import_ofx_launches_bulk, list_category_rules


def _infer_category(memo: str, rules: list[tuple[str, str]]) -> str:
    memo_norm = normalize_text(memo or "")
    if not memo_norm:
        return "outros"

    for kw, cat in rules:
        kw_norm = normalize_text(kw)
        if kw_norm and (contains_word(memo_norm, kw_norm) or kw_norm in memo_norm):
            return cat

    for keywords, c in LOCAL_RULES:
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if kw_norm and (contains_word(memo_norm, kw_norm) or kw_norm in memo_norm):
                return c

    return "outros"

def _extract_ledger_balance(ofx_bytes: bytes) -> Decimal | None:
    """
    Pega o saldo final do extrato (LEDGERBAL/BALAMT).
    """
    s = ofx_bytes.decode("utf-8", errors="ignore")
    m = re.search(r"<LEDGERBAL>.*?<BALAMT>\s*([-0-9.]+)", s, re.I | re.S)
    if not m:
        return None
    return Decimal(m.group(1))


def import_ofx_bytes(user_id: int, ofx_bytes: bytes, filename: str | None = None) -> dict:
    """
    Lê OFX, transforma em launches, e chama o DB bulk idempotente.
    Retorna um report pronto pro bot responder.
    """
    if not ofx_bytes:
        raise ValueError("OFX vazio.")

    file_hash = hashlib.sha256(ofx_bytes).hexdigest()

    ofx = OfxParser.parse(io.BytesIO(ofx_bytes))

    # statement pode estar em caminhos diferentes dependendo do OFX
    stmt = None
    if getattr(ofx, "account", None) and getattr(ofx.account, "statement", None):
        stmt = ofx.account.statement
    elif getattr(ofx, "statement", None):
        stmt = ofx.statement
    else:
        raise ValueError("OFX sem statement.")

    bank_id = getattr(getattr(ofx, "account", None), "routing_number", None)  # pode vir None
    acct_id = getattr(getattr(ofx, "account", None), "account_id", None)
    acct_type = getattr(getattr(ofx, "account", None), "account_type", None)

    # alguns parsers não preenchem routing_number; no seu OFX vem via tags (BANKID etc)
    # não é essencial pro seu modelo (1 conta só), mas guardamos no log se existir.

    txs = getattr(stmt, "transactions", []) or []

    # período
    dt_start = getattr(stmt, "start_date", None)
    dt_end = getattr(stmt, "end_date", None)
    if isinstance(dt_start, datetime):
        dt_start = dt_start.date()
    if isinstance(dt_end, datetime):
        dt_end = dt_end.date()

    launches_rows = []
    tz = _tz()
    
    # fallback: se statement não der período, calcular pelo min/max
    min_d = None
    max_d = None

    rules = list_category_rules(user_id)
    ledger_balance = _extract_ledger_balance(ofx_bytes)

    for trn in txs:
        # FITID
        fitid = getattr(trn, "id", None) or getattr(trn, "fitid", None)
        if not fitid:
            # sem FITID é raro, mas se acontecer: rejeita (pra não deduplicar errado)
            raise ValueError("Transação OFX sem FITID (não vou importar sem dedupe).")

        # data
        d = getattr(trn, "date", None) or getattr(trn, "dtposted", None) or getattr(trn, "posted", None)
        if isinstance(d, datetime):
            posted_at = d.date()
        elif isinstance(d, date):
            posted_at = d
        else:
            # se vier string, tenta o básico (mas normalmente ofxparse já converte)
            raise ValueError("DTPOSTED inválido no OFX.")

        if min_d is None or posted_at < min_d:
            min_d = posted_at
        if max_d is None or posted_at > max_d:
            max_d = posted_at

        # amount
        amount = getattr(trn, "amount", None)
        if amount is None:
            raise ValueError("Transação OFX sem TRNAMT.")
        amount = Decimal(str(amount))

        # memo/descrição
        memo = (getattr(trn, "memo", None) or getattr(trn, "payee", None) or getattr(trn, "name", None) or "").strip()
        if not memo:
            memo = "(sem memo)"

        trn_type = (getattr(trn, "type", None) or getattr(trn, "trntype", None) or "").upper().strip()

        # tipo/valor/delta
        # Regra: usa sinal do TRNAMT, mas se o banco vier com TRNTYPE=DEBIT e TRNAMT positivo,
        # a gente força como despesa (isso evita diferenças tipo a sua de R$ 18,10).
        is_debit = trn_type in {"DEBIT", "DIRECTDEBIT", "PAYMENT", "FEE", "CHECK", "ATM", "POS"}

        signed = amount
        if signed > 0 and is_debit:
            signed = -signed

        if signed < 0:
            tipo = "despesa"
            valor = (-signed)
            delta = -valor
        else:
            tipo = "receita"
            valor = signed
            delta = +valor
                
        categoria = _infer_category(memo, rules)

        # criado_em: usar a data do movimento (meio-dia) pra ordenar bem
        criado_em = datetime.combine(posted_at, time(12, 0), tzinfo=tz)

        launches_rows.append({
            "tipo": tipo,
            "valor": valor,
            "delta": delta,
            "categoria": categoria,
            "nota": memo,
            "external_id": str(fitid),
            "posted_at": posted_at,
            "criado_em": criado_em,
            "currency": "BRL",
            "ofx_meta": {
                "memo": memo,
                "amount_signed": float(amount),
                "posted_at": posted_at.isoformat(),
                "filename": filename,
            },
        })

    if dt_start is None:
        dt_start = min_d
    if dt_end is None:
        dt_end = max_d

    result = import_ofx_launches_bulk(
        user_id,
        launches_rows,
        file_hash=file_hash,
        bank_id=str(bank_id) if bank_id else None,
        acct_id=str(acct_id) if acct_id else None,
        acct_type=str(acct_type) if acct_type else None,
        dt_start=dt_start,
        dt_end=dt_end,
    )

    result["filename"] = filename
    result["ledger_balance"] = ledger_balance

    if ledger_balance is not None:
        # força o saldo do bot a bater com o saldo final do banco no OFX
        new_bal = set_balance(user_id, ledger_balance)
        result["new_balance"] = new_bal
        result["reconciled"] = True
    else:
        result["reconciled"] = False

    return result