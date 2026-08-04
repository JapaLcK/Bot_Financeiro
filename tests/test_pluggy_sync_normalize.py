"""Testes das funções puras de normalização do sync Pluggy (Fase 0).

Não tocam banco nem rede — só a conversão dado-cru-Pluggy → formato interno.
"""

from datetime import date
from decimal import Decimal

from core.services.pluggy_sync import (
    normalize_pluggy_account,
    normalize_pluggy_transaction,
)


def test_transaction_debit_positive_amount_becomes_negative():
    tx = normalize_pluggy_transaction(
        {"id": "t1", "description": "Mercado", "amount": 184.32, "type": "DEBIT", "date": "2026-07-20"}
    )
    assert tx["amount"] == Decimal("-184.32")
    assert tx["provider_transaction_id"] == "t1"
    assert tx["transaction_date"] == date(2026, 7, 20)


def test_transaction_credit_negative_amount_becomes_positive():
    tx = normalize_pluggy_transaction(
        {"id": "t2", "description": "Salário", "amount": -6500, "type": "CREDIT", "date": "2026-07-01T00:00:00.000Z"}
    )
    assert tx["amount"] == Decimal("6500")
    assert tx["transaction_date"] == date(2026, 7, 1)


def test_transaction_already_signed_is_preserved():
    tx = normalize_pluggy_transaction(
        {"id": "t3", "description": "Uber", "amount": -38.90, "type": "DEBIT", "date": "2026-07-19"}
    )
    assert tx["amount"] == Decimal("-38.90")


def test_transaction_missing_fields_have_fallbacks():
    tx = normalize_pluggy_transaction({"id": "t4", "amount": "10"})
    assert tx["description"] == "Transação"
    assert tx["category"] is None
    assert tx["amount"] == Decimal("10")


def test_account_credit_positive_balance_becomes_negative():
    acc = normalize_pluggy_account(
        {"id": "a1", "name": "Cartão", "type": "CREDIT", "subtype": "CREDIT_CARD", "balance": 845.90, "currencyCode": "BRL"}
    )
    assert acc["balance"] == Decimal("-845.90")
    assert acc["type"] == "CREDIT"
    assert acc["provider_account_id"] == "a1"


def test_account_bank_balance_preserved_and_name_fallback():
    acc = normalize_pluggy_account(
        {"id": "a2", "type": "BANK", "subtype": "CHECKING_ACCOUNT", "balance": 4320.75}
    )
    assert acc["balance"] == Decimal("4320.75")
    assert acc["name"] == "BANK"  # sem name/marketingName → cai no type
    assert acc["currency"] == "BRL"


def test_account_prefers_marketing_name():
    acc = normalize_pluggy_account(
        {"id": "a3", "name": "Conta", "marketingName": "Nu Conta", "type": "BANK", "balance": 0}
    )
    assert acc["name"] == "Nu Conta"
