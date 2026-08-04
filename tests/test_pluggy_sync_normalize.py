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
from db import (
    classify_open_finance_launch,
    credit_day_from_iso,
    detect_bill_increase,
    detect_recurring_income_matches,
    merchant_similarity,
    pick_reconciliation_match,
)


def test_detect_recurring_income_salary():
    # salário ~mesmo valor, ~mesmo dia, em 3 meses distintos → todos recorrentes.
    credits = [
        {"id": 1, "valor": Decimal("6500"), "date": date(2026, 6, 5)},
        {"id": 2, "valor": Decimal("6500"), "date": date(2026, 7, 5)},
        {"id": 3, "valor": Decimal("6520"), "date": date(2026, 8, 6)},
        {"id": 9, "valor": Decimal("250"), "date": date(2026, 7, 12)},  # pix avulso, não recorre
    ]
    ids = set(detect_recurring_income_matches(credits))
    assert ids == {1, 2, 3}


def test_detect_recurring_income_ignores_same_month():
    credits = [
        {"id": 1, "valor": Decimal("100"), "date": date(2026, 8, 1)},
        {"id": 2, "valor": Decimal("100"), "date": date(2026, 8, 2)},  # mesmo mês → não conta
    ]
    assert detect_recurring_income_matches(credits) == []


def test_detect_bill_increase():
    exp = [
        {"merchant": "Enel Luz", "valor": Decimal("180"), "date": date(2026, 6, 10)},
        {"merchant": "ENEL LUZ", "valor": Decimal("185"), "date": date(2026, 7, 10)},
        {"merchant": "Enel luz", "valor": Decimal("216"), "date": date(2026, 8, 10)},  # +18% vs média
        {"merchant": "iFood", "valor": Decimal("50"), "date": date(2026, 7, 1)},
        {"merchant": "iFood", "valor": Decimal("52"), "date": date(2026, 8, 1)},  # estável
    ]
    flagged = detect_bill_increase(exp)
    merchants = {f["merchant"].lower() for f in flagged}
    assert "enel luz" in merchants
    assert not any("ifood" in m for m in merchants)


def _cand(id, valor, ref_date, alvo=None, nota=None):
    return {"id": id, "valor": valor, "ref_date": ref_date, "alvo": alvo, "nota": nota}


def test_merchant_similarity():
    assert merchant_similarity("iFood *Restaurante", "iFood") is True
    assert merchant_similarity("NETFLIX.COM", "netflix") is True
    assert merchant_similarity("Uber", "iFood") is False
    assert merchant_similarity("", "iFood") is False


def test_reconciliation_auto_merge_same_day_similar_merchant():
    # manual "iFood 47,90" seg; OF "iFood" mesmo dia, mesmo valor → mescla sozinho.
    r = pick_reconciliation_match(
        Decimal("47.90"), date(2026, 7, 20), "iFood",
        [_cand(10, Decimal("47.90"), date(2026, 7, 20), alvo="iFood")],
    )
    assert r == {"launch_id": 10, "verdict": "auto"}


def test_reconciliation_auto_merge_generic_manual_description():
    # manual genérico "almoço 50" sem estabelecimento → casa por valor+data.
    r = pick_reconciliation_match(
        Decimal("50"), date(2026, 7, 20), "Restaurante Sabor",
        [_cand(11, Decimal("50"), date(2026, 7, 21), alvo="almoço")],
    )
    assert r["verdict"] == "auto"
    assert r["launch_id"] == 11


def test_reconciliation_ask_when_merchant_differs():
    # valor e data batem, mas estabelecimento claramente diverge → perguntar.
    r = pick_reconciliation_match(
        Decimal("100"), date(2026, 7, 20), "Posto Shell",
        [_cand(12, Decimal("100"), date(2026, 7, 20), alvo="Farmácia São Paulo")],
    )
    assert r["verdict"] == "ask"
    assert r["launch_id"] == 12


def test_reconciliation_ask_when_multiple_candidates():
    r = pick_reconciliation_match(
        Decimal("30"), date(2026, 7, 20), "Padaria",
        [_cand(1, Decimal("30"), date(2026, 7, 20), alvo="Padaria"),
         _cand(2, Decimal("30"), date(2026, 7, 19), alvo="Padaria")],
    )
    assert r["verdict"] == "ask"


def test_reconciliation_none_when_out_of_window_or_value():
    assert pick_reconciliation_match(
        Decimal("47.90"), date(2026, 7, 20), "iFood",
        [_cand(1, Decimal("47.90"), date(2026, 7, 30), alvo="iFood")],  # 10 dias fora
    )["verdict"] == "none"
    assert pick_reconciliation_match(
        Decimal("47.90"), date(2026, 7, 20), "iFood",
        [_cand(1, Decimal("99.00"), date(2026, 7, 20), alvo="iFood")],  # valor diferente
    )["verdict"] == "none"


def test_extract_installment_info():
    from db import extract_installment_info
    assert extract_installment_info({"creditCardMetadata": {"installmentNumber": 3, "totalInstallments": 12}}) == (3, 12)
    # à vista (total 1 ou ausente) → sem parcela
    assert extract_installment_info({"creditCardMetadata": {"installmentNumber": 1, "totalInstallments": 1}}) == (None, None)
    assert extract_installment_info({"creditCardMetadata": {}}) == (None, None)
    assert extract_installment_info({}) == (None, None)
    assert extract_installment_info(None) == (None, None)


def test_credit_day_from_iso():
    assert credit_day_from_iso("2026-08-15", 1) == 15
    assert credit_day_from_iso("2026-08-28T00:00:00.000Z", 1) == 28
    assert credit_day_from_iso(None, 10) == 10
    assert credit_day_from_iso("lixo", 7) == 7
    assert credit_day_from_iso("2026-08-00", 5) == 5  # dia 0 inválido → default


def test_classify_expense_and_income():
    assert classify_open_finance_launch(-47.90, "Restaurants", "iFood") == {
        "tipo": "despesa", "valor": Decimal("47.90"), "is_internal_movement": False
    }
    assert classify_open_finance_launch(6500, "Salary", "Salário")["tipo"] == "receita"


def test_classify_caixinha_automatic_investment_is_internal():
    # Caixinha do Nubank = "Automatic investment" na conta corrente → interno, não gasto.
    r = classify_open_finance_launch(-200, "Automatic investment", "Aplicação Caixinha Viagem")
    assert r["is_internal_movement"] is True
    assert r["tipo"] == "despesa"


def test_classify_same_person_transfer_is_internal():
    r = classify_open_finance_launch(-500, "Same person transfer - PIX", "Pix para mim mesmo")
    assert r["is_internal_movement"] is True


def test_classify_investment_income_is_not_internal():
    # Rendimento de investimento é RENDA, não movimento interno.
    r = classify_open_finance_launch(12.34, "Proceeds interests and dividends", "Rendimento CDB")
    assert r["is_internal_movement"] is False
    assert r["tipo"] == "receita"


def test_classify_internal_by_description_fallback():
    # Sem categoria enriquecida (Pro), cai no fallback por descrição.
    r = classify_open_finance_launch(-150, None, "RESGATE POUPANCA")
    assert r["is_internal_movement"] is True


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
