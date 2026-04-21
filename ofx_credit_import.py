# ofx_credit_import.py
"""
Parser de OFX de fatura de cartão de crédito.

Diferenças em relação ao extrato bancário (ofx_import.py):
  - Arquivo OFX contém tags CREDITCARDMSGSRSV1 / CCSTMTRS / CCACCTFROM
  - Valores negativos = gastos (despesa); positivos = estornos/pagamentos
  - Extrai CREDITLIMIT, AVAILBAL além de LEDGERBAL
  - Detecta parcelamentos pelo padrão "NN/NN" no memo (ex: "AMAZON 02/03")
  - Agrupa parcelamentos por group_id linkando com parcelas anteriores já importadas
"""
import io
import hashlib
import re
from collections import Counter
from datetime import datetime, date
from decimal import Decimal

from ofxparse import OfxParser
from utils_date import _tz
from utils_text import normalize_text, contains_word, LOCAL_RULES
from db import import_credit_ofx_bulk, list_user_category_rules


# ─────────────────────────────────────────────────────────────────────────────
# Extratores de campos específicos de cartão de crédito
# ─────────────────────────────────────────────────────────────────────────────

def _extract_credit_limit(ofx_bytes: bytes) -> Decimal | None:
    """Extrai <CREDITLIMIT> do OFX — limite total do cartão."""
    s = ofx_bytes.decode("utf-8", errors="ignore")
    m = re.search(r"<CREDITLIMIT>\s*([-0-9.]+)", s, re.I)
    if m:
        try:
            return Decimal(m.group(1))
        except Exception:
            pass
    return None


def _extract_available_credit(ofx_bytes: bytes) -> Decimal | None:
    """
    Extrai crédito disponível do OFX.
    Tenta <AVAILCREDIT> (campo explícito) antes de <AVAILBAL><BALAMT>.
    """
    s = ofx_bytes.decode("utf-8", errors="ignore")
    m = re.search(r"<AVAILCREDIT>\s*([-0-9.]+)", s, re.I)
    if m:
        try:
            return Decimal(m.group(1))
        except Exception:
            pass
    m = re.search(r"<AVAILBAL>.*?<BALAMT>\s*([-0-9.]+)", s, re.I | re.S)
    if m:
        try:
            return Decimal(m.group(1))
        except Exception:
            pass
    return None


def _extract_ledger_balance(ofx_bytes: bytes) -> Decimal | None:
    """
    Extrai <LEDGERBAL><BALAMT> do OFX.
    Na fatura, esse valor é geralmente negativo (você deve esse valor ao banco).
    Usamos o valor absoluto como total da fatura.
    """
    s = ofx_bytes.decode("utf-8", errors="ignore")
    m = re.search(r"<LEDGERBAL>.*?<BALAMT>\s*([-0-9.]+)", s, re.I | re.S)
    if m:
        try:
            return Decimal(m.group(1))
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Detecção de parcelamento no memo
# ─────────────────────────────────────────────────────────────────────────────

def _parse_installment(memo: str) -> tuple[int, int] | None:
    """
    Detecta padrão de parcelamento no memo OFX.

    Exemplos aceitos:
      "AMAZON MARKETPLACE 02/03"  → (2, 3)
      "SPOTIFY 1/12"              → (1, 12)
      "IFOOD 003/010"             → (3, 10)

    Retorna None se não detectar ou se os valores forem inválidos.
    """
    m = re.search(r"\b0*(\d{1,2})/0*(\d{1,2})\b", memo)
    if m:
        no = int(m.group(1))
        total = int(m.group(2))
        if 1 <= no <= total <= 60 and total > 1:
            return no, total
    return None


# Memos que indicam pagamento de fatura — não devem ser importados como compras
_PAYMENT_MEMO_RE = re.compile(
    r"\b(pagamento\s+recebido|payment\s+received|pag\.?\s*recebido|bill\s+payment)\b",
    re.IGNORECASE,
)


def _is_payment_memo(memo: str) -> bool:
    """Retorna True se o memo indica pagamento de fatura anterior (não uma compra)."""
    return bool(_PAYMENT_MEMO_RE.search(memo))


def _memo_base(memo: str) -> str:
    """
    Remove o sufixo de parcelamento do memo para obter o nome base da compra.
    "AMAZON MARKETPLACE 02/03" → "amazon marketplace"
    Usado para linkar parcelas de diferentes faturas ao mesmo group_id.
    """
    base = re.sub(r"\s*0*\d{1,2}/0*\d{1,2}\s*$", "", memo).strip()
    return normalize_text(base)


# ─────────────────────────────────────────────────────────────────────────────
# Categorização (reutiliza mesma lógica do ofx_import.py)
# ─────────────────────────────────────────────────────────────────────────────

def _categorize(memo_norm: str, rules_norm: list[tuple[str, str]]) -> str:
    for kw_norm, cat_norm in rules_norm:
        if contains_word(memo_norm, kw_norm) or (kw_norm in memo_norm):
            return cat_norm
    for keywords, cat in LOCAL_RULES:
        cat_norm = normalize_text(cat or "")
        if not cat_norm:
            continue
        for kw in keywords:
            kw_n = normalize_text(kw or "")
            if not kw_n:
                continue
            if contains_word(memo_norm, kw_n) or (kw_n in memo_norm):
                return cat_norm
    return "outros"


# ─────────────────────────────────────────────────────────────────────────────
# Importação principal
# ─────────────────────────────────────────────────────────────────────────────

def import_credit_ofx_bytes(
    user_id: int,
    card_id: int,
    ofx_bytes: bytes,
    filename: str | None = None,
) -> dict:
    """
    Importa OFX de fatura de cartão de crédito para credit_transactions.

    Retorna dict com:
      inserted, duplicates, total, dt_start, dt_end, bill_id,
      credit_limit, available_credit, ledger_balance,
      installments_detected, skipped_same_file, filename
    """
    if not ofx_bytes:
        raise ValueError("OFX vazio.")

    file_hash = hashlib.sha256(ofx_bytes).hexdigest()

    # Extrai campos específicos de cartão antes do parse
    credit_limit = _extract_credit_limit(ofx_bytes)
    available_credit = _extract_available_credit(ofx_bytes)
    ledger_balance = _extract_ledger_balance(ofx_bytes)

    ofx = OfxParser.parse(io.BytesIO(ofx_bytes))

    stmt = None
    if getattr(ofx, "account", None) and getattr(ofx.account, "statement", None):
        stmt = ofx.account.statement
    elif getattr(ofx, "statement", None):
        stmt = ofx.statement
    else:
        raise ValueError("OFX sem statement válido.")

    acct_id = getattr(getattr(ofx, "account", None), "account_id", None)
    txs = getattr(stmt, "transactions", []) or []

    # Período da fatura
    dt_start = getattr(stmt, "start_date", None)
    dt_end = getattr(stmt, "end_date", None)
    if isinstance(dt_start, datetime):
        dt_start = dt_start.date()
    if isinstance(dt_end, datetime):
        dt_end = dt_end.date()

    # Regras de categorização do usuário (carregadas uma vez)
    _rules_raw = list_user_category_rules(user_id)
    rules_norm = [
        (normalize_text(kw or ""), normalize_text(cat or ""))
        for kw, cat in _rules_raw
        if kw and cat
    ]

    # ── Pré-conta quantas vezes cada FITID aparece ───────────────────────────
    # O Nubank agrupa a antecipação de parcelas sob um único FITID para todas
    # as transações do lote. Se usarmos o FITID bruto como external_id, todas
    # seriam deduplicas como "já inseridas" após a primeira.
    # Solução: quando um FITID aparece N vezes, usar FITID_1, FITID_2, …, FITID_N.
    fitid_count: Counter = Counter()
    for trn in txs:
        fid = getattr(trn, "id", None) or getattr(trn, "fitid", None)
        if fid:
            fitid_count[str(fid)] += 1

    fitid_seen: Counter = Counter()

    tx_rows: list[dict] = []
    min_d: date | None = None
    max_d: date | None = None
    installments_detected = 0
    skipped_payments = 0

    for trn in txs:
        fitid = getattr(trn, "id", None) or getattr(trn, "fitid", None)
        if not fitid:
            continue  # Mais tolerante na fatura do que no extrato
        fitid_str = str(fitid)

        # Data da transação
        d = (
            getattr(trn, "date", None)
            or getattr(trn, "dtposted", None)
            or getattr(trn, "posted", None)
        )
        if isinstance(d, datetime):
            posted_at = d.date()
        elif isinstance(d, date):
            posted_at = d
        else:
            continue

        if min_d is None or posted_at < min_d:
            min_d = posted_at
        if max_d is None or posted_at > max_d:
            max_d = posted_at

        amount = getattr(trn, "amount", None)
        if amount is None:
            continue
        amount = Decimal(str(amount))

        # Memo
        memo = (
            (getattr(trn, "payee", None) or "")
            + " "
            + (getattr(trn, "name", None) or "")
            + " "
            + (getattr(trn, "memo", None) or "")
        ).strip() or "(sem memo)"

        # No OFX de fatura:
        #   Valores negativos  = gastos (despesa)
        #   Valores positivos  = pagamentos ou estornos/créditos
        trn_type = (getattr(trn, "type", None) or "").upper().strip()
        if amount < 0 or trn_type in {"DEBIT", "DIRECTDEBIT", "FEE", "POS", "CHECK", "ATM"}:
            tipo = "despesa"
        else:
            tipo = "estorno"

        # ── Filtra pagamentos de fatura ──────────────────────────────────────
        # "Pagamento recebido" aparece no OFX quando o cliente pagou a fatura
        # anterior. Não é uma compra — ignorar para não distorcer o total.
        if tipo == "estorno" and _is_payment_memo(memo):
            skipped_payments += 1
            continue

        valor = abs(amount)

        # ── external_id único por transação ─────────────────────────────────
        # Se o FITID aparece mais de uma vez no arquivo (ex: Nubank antecipação),
        # adiciona sufixo _N para garantir unicidade e evitar falsa deduplicação.
        fitid_seen[fitid_str] += 1
        if fitid_count[fitid_str] > 1:
            external_id = f"{fitid_str}_{fitid_seen[fitid_str]}"
        else:
            external_id = fitid_str

        # Categorização
        memo_norm = normalize_text(memo)
        categoria = _categorize(memo_norm, rules_norm)

        # Parcelamento
        installment = _parse_installment(memo)
        inst_no = installment[0] if installment else None
        inst_total = installment[1] if installment else None
        base = _memo_base(memo) if installment else None

        if installment:
            installments_detected += 1

        tx_rows.append({
            "external_id": external_id,
            "posted_at": posted_at,
            "valor": valor,
            "tipo": tipo,
            "categoria": categoria,
            "nota": memo,
            "installment_no": inst_no,
            "installments_total": inst_total,
            "memo_base": base,
        })

    # Preenche período com min/max das transações se não veio no OFX
    if dt_start is None:
        dt_start = min_d
    if dt_end is None:
        dt_end = max_d

    result = import_credit_ofx_bulk(
        user_id=user_id,
        card_id=card_id,
        tx_rows=tx_rows,
        file_hash=file_hash,
        dt_start=dt_start,
        dt_end=dt_end,
        acct_id=str(acct_id) if acct_id else None,
        credit_limit=credit_limit,
        ledger_balance=ledger_balance,
    )

    result.update({
        "filename": filename,
        "credit_limit": credit_limit,
        "available_credit": available_credit,
        "ledger_balance": ledger_balance,
        "installments_detected": installments_detected,
    })

    return result
