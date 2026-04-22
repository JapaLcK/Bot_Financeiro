# ofx_import.py
import io
import hashlib
from datetime import datetime, date, time
from decimal import Decimal
import re
from unittest import result
from ofxparse import OfxParser
from utils_date import _tz
from db import set_balance, import_ofx_launches_bulk, get_last_ofx_import_end_date
from db import list_user_category_rules
from utils_text import normalize_text, contains_word, LOCAL_RULES, INTERNAL_MOVEMENT_CATEGORIES


def detect_ofx_type(ofx_bytes: bytes) -> str:
    """
    Detecta se o OFX é de conta bancária ('bank') ou fatura de cartão de crédito ('credit_card').
    Inspeciona as tags SGML/XML do arquivo sem depender do parser.
    Retorna 'unknown' se não for possível determinar.
    """
    text = ofx_bytes.decode("utf-8", errors="ignore").upper()
    # Tags exclusivas de fatura de cartão de crédito no padrão OFX
    if "CREDITCARDMSGSRS" in text or "CCSTMTRS" in text or "CCACCTFROM" in text:
        return "credit_card"
    # Tags exclusivas de conta bancária (corrente/poupança)
    if "BANKMSGSRS" in text or "BANKACCTFROM" in text:
        return "bank"
    return "unknown"


def _extract_ledger_balance(ofx_bytes: bytes) -> Decimal | None:
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

    bank_id = getattr(getattr(ofx, "account", None), "routing_number", None)
    acct_id = getattr(getattr(ofx, "account", None), "account_id", None)
    acct_type = getattr(getattr(ofx, "account", None), "account_type", None)

    txs = getattr(stmt, "transactions", []) or []

    # período
    dt_start = getattr(stmt, "start_date", None)
    dt_end = getattr(stmt, "end_date", None)
    if isinstance(dt_start, datetime):
        dt_start = dt_start.date()
    if isinstance(dt_end, datetime):
        dt_end = dt_end.date()

    tz = _tz()
    launches_rows: list[dict] = []

    min_d: date | None = None
    max_d: date | None = None

    ledger_balance = _extract_ledger_balance(ofx_bytes)

    # carrega regras UMA vez (evita N+1 no Postgres)
    _rules_raw = list_user_category_rules(user_id)
    _rules_norm: list[tuple[str, str]] = []
    for kw, cat in _rules_raw:
        kw_n = normalize_text(kw or "")
        cat_n = normalize_text(cat or "")
        if kw_n and cat_n:
            _rules_norm.append((kw_n, cat_n))

    for trn in txs:
        fitid = getattr(trn, "id", None) or getattr(trn, "fitid", None)
        if not fitid:
            raise ValueError("Transação OFX sem FITID (não vou importar sem dedupe).")

        d = getattr(trn, "date", None) or getattr(trn, "dtposted", None) or getattr(trn, "posted", None)
        if isinstance(d, datetime):
            posted_at = d.date()
        elif isinstance(d, date):
            posted_at = d
        else:
            raise ValueError("DTPOSTED inválido no OFX.")

        if min_d is None or posted_at < min_d:
            min_d = posted_at
        if max_d is None or posted_at > max_d:
            max_d = posted_at

        amount = getattr(trn, "amount", None)
        if amount is None:
            raise ValueError("Transação OFX sem TRNAMT.")
        amount = Decimal(str(amount))

        # texto-base do OFX (muito importante!)
        memo = (
            (getattr(trn, "payee", None) or "")
            + " "
            + (getattr(trn, "name", None) or "")
            + " "
            + (getattr(trn, "memo", None) or "")
        ).strip()
        if not memo:
            memo = "(sem memo)"

        trn_type = (getattr(trn, "type", None) or getattr(trn, "trntype", None) or "").upper().strip()

        # tipo/valor/delta
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

        memo_norm = normalize_text(memo)

        categoria = None

        # B) regras do usuário (em memória)
        for kw_norm, cat_norm in _rules_norm:
            if contains_word(memo_norm, kw_norm) or (kw_norm in memo_norm):
                categoria = cat_norm
                break

        # C) LOCAL_RULES (sem DB)
        if not categoria:
            for keywords, cat2 in LOCAL_RULES:
                cat2_norm = normalize_text(cat2 or "")
                if not cat2_norm:
                    continue
                for kw in keywords:
                    kw2_norm = normalize_text(kw or "")
                    if not kw2_norm:
                        continue
                    if contains_word(memo_norm, kw2_norm) or (kw2_norm in memo_norm):
                        categoria = cat2_norm
                        break
                if categoria:
                    break

        if not categoria:
            categoria = "outros"

        is_internal = normalize_text(categoria) in INTERNAL_MOVEMENT_CATEGORIES

        criado_em = datetime.combine(posted_at, time(12, 0), tzinfo=tz)

        launches_rows.append(
            {
                "tipo": tipo,
                "valor": valor,
                "delta": delta,
                "categoria": categoria,
                "nota": memo,
                "external_id": str(fitid),
                "posted_at": posted_at,
                "criado_em": criado_em,
                "currency": "BRL",
                "is_internal_movement": is_internal,
                "ofx_meta": {
                    "memo": memo,
                    "amount_signed": float(amount),
                    "posted_at": posted_at.isoformat(),
                    "filename": filename,
                },
            }
        )

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

    can_reconcile = False

    try:
        last_dt_end = get_last_ofx_import_end_date(user_id)

        # Só reconcilia se:
        # 1) tiver saldo no OFX
        # 2) tiver dt_end do arquivo
        # 3) esse OFX for o mais recente já importado
        if ledger_balance is not None and dt_end is not None:
            if last_dt_end is None or dt_end >= last_dt_end:
                can_reconcile = True
    except Exception:
        can_reconcile = False

    if can_reconcile:
        new_bal = set_balance(user_id, ledger_balance)
        result["new_balance"] = new_bal
        result["reconciled"] = True
    else:
        result["reconciled"] = False

    return result