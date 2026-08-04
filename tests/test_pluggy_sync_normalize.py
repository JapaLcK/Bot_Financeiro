"""Testes das funções puras de normalização do sync Pluggy (Fase 0).

Não tocam banco nem rede — só a conversão dado-cru-Pluggy → formato interno.
"""

from datetime import date
from decimal import Decimal

from core.services.pluggy import _extract_after_cursor
from core.services.pluggy_sync import (
    normalize_pluggy_account,
    normalize_pluggy_transaction,
)


def test_extract_after_cursor_from_next_querystring():
    nxt = "?accountId=abc-123&after=MjAyMC0xMC0xNVQwMDowMA"
    assert _extract_after_cursor(nxt) == "MjAyMC0xMC0xNVQwMDowMA"


def test_extract_after_cursor_from_full_url():
    nxt = "https://api.pluggy.ai/v2/transactions?accountId=abc&after=CURSOR99&pageSize=500"
    assert _extract_after_cursor(nxt) == "CURSOR99"


def test_extract_after_cursor_none_when_absent_or_empty():
    assert _extract_after_cursor(None) is None
    assert _extract_after_cursor("") is None
    assert _extract_after_cursor("?accountId=abc&pageSize=500") is None


def test_transaction_amount_sign_is_trusted_debit_negative():
    # Boleto real do sandbox: DEBIT já vem negativo — mantém.
    tx = normalize_pluggy_transaction(
        {"id": "t1", "description": "Pagamento de boleto", "amount": -100, "type": "DEBIT", "date": "2026-08-04T18:16:16.839Z"}
    )
    assert tx["amount"] == Decimal("-100")
    assert tx["provider_transaction_id"] == "t1"
    assert tx["transaction_date"] == date(2026, 8, 4)


def test_transaction_credit_card_purchase_stays_negative():
    # REGRESSÃO (bug pego no E2E): compra de cartão vem type=CREDIT com amount negativo.
    # NÃO pode virar positivo, senão compra vira receita.
    tx = normalize_pluggy_transaction(
        {"id": "t2", "description": "NETFLIX.COM", "amount": -55.9, "type": "CREDIT", "date": "2026-08-01"}
    )
    assert tx["amount"] == Decimal("-55.9")


def test_transaction_real_income_stays_positive():
    tx = normalize_pluggy_transaction(
        {"id": "t3", "description": "Salário", "amount": 6500, "type": "CREDIT", "date": "2026-07-01"}
    )
    assert tx["amount"] == Decimal("6500")


def test_transaction_missing_fields_have_fallbacks():
    tx = normalize_pluggy_transaction({"id": "t4", "amount": "10"})
    assert tx["description"] == "Transação"
    assert tx["category"] is None
    assert tx["amount"] == Decimal("10")


def test_account_balance_sign_is_trusted():
    # Cartão real do sandbox já vem negativo (valor devido) — mantém como está.
    acc = normalize_pluggy_account(
        {"id": "a1", "marketingName": "Mastercard Black", "type": "CREDIT", "subtype": "CREDIT_CARD", "balance": -580.9, "currencyCode": "BRL"}
    )
    assert acc["balance"] == Decimal("-580.9")
    assert acc["type"] == "CREDIT"
    assert acc["name"] == "Mastercard Black"
    assert acc["provider_account_id"] == "a1"


def test_account_bank_balance_preserved_and_name_fallback():
    acc = normalize_pluggy_account(
        {"id": "a2", "type": "BANK", "subtype": "CHECKING_ACCOUNT", "balance": 21376.9}
    )
    assert acc["balance"] == Decimal("21376.9")
    assert acc["name"] == "BANK"  # sem name/marketingName → cai no type
    assert acc["currency"] == "BRL"


def test_account_prefers_marketing_name():
    acc = normalize_pluggy_account(
        {"id": "a3", "name": "Conta", "marketingName": "Nu Conta", "type": "BANK", "balance": 0}
    )
    assert acc["name"] == "Nu Conta"
