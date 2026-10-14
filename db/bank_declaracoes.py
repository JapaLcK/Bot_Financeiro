"""Declaração de despesa/receita "paga pelo banco": elegibilidade contra a transação do extrato.

Mesma tabela e mesma rotina de conferência do aporte (`db/bank_movements.py`), regras próprias:
valor quase exato (R$ 0,05), janela de 3 dias e `account_id` NULL = "qualquer conta BANK ativa".
Fluxo = launch despesa/receita COM `account_id` NULL; o NULL fica mesmo depois de casar (preenchê-lo
jogaria a declaração no caminho do aporte, que exige categoria de aplicação).
O vínculo é aditivo: sem par a declaração segue "a conferir" e não expira (ADR 0003).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from decimal import Decimal

from utils_date import _tz

TIPOS_FLUXO = ("despesa", "receita")
FLUXO_CONGELA_DIAS = 45  # fluxo já casado há mais que isso sai da conferência (e do lock de leitura)
MANUAL_TOL_ABS, MANUAL_TOL_PCT = Decimal("5.00"), Decimal("0.10")


def e_fluxo(declaration: dict) -> bool:
    return declaration.get("tipo") in TIPOS_FLUXO and declaration.get("account_id") is None


def _dia(declaration: dict) -> date:
    return declaration["declared_at"].astimezone(_tz()).date()


def elegivel_fluxo(declaration: dict, tx: dict, *, manual: bool = False) -> bool:
    from .bank_movements import _money
    from .open_finance import RECON_AMOUNT_TOL, RECON_DATE_WINDOW, classify_open_finance_launch
    amount, tx_amount = _money(declaration.get("amount")), _money(tx["amount"])
    if not amount or not tx_amount or (amount > 0) != (tx_amount > 0):
        return False
    tol = max(MANUAL_TOL_ABS, MANUAL_TOL_PCT * abs(amount)) if manual else RECON_AMOUNT_TOL
    if abs(tx_amount - amount) > tol:
        return False
    if abs((tx["transaction_date"] - _dia(declaration)).days) > RECON_DATE_WINDOW:
        return False
    # Fluxo e aporte são disjuntos. `economic_kind` já implica `is_internal_movement` (as categorias de
    # aplicação são um subconjunto das internas); fica como defesa em profundidade.
    return not tx.get("economic_kind") and not classify_open_finance_launch(
        tx["amount"], tx.get("category"), tx.get("description"))["is_internal_movement"]


def janela(transactions: list[dict], dates: list[date], declaration: dict) -> list[dict]:
    """Só as transações a <= RECON_DATE_WINDOW dias da declaração. `transactions` vem ordenada por
    `transaction_date` (`_transactions`) e `dates` são as datas dela; mesma regra de data do
    `_eligible` dos dois caminhos, então o conjunto elegível não muda (só o custo: O(D x janela))."""
    from .open_finance import RECON_DATE_WINDOW
    dia, w = _dia(declaration), timedelta(days=RECON_DATE_WINDOW)
    return transactions[bisect_left(dates, dia - w):bisect_right(dates, dia + w)]


def congelada(declaration: dict, hoje: date) -> bool:
    return (e_fluxo(declaration) and declaration["matched_transaction_id"] is not None
            and (hoje - _dia(declaration)).days > FLUXO_CONGELA_DIAS)


def desde_da_leitura(declarations: list[dict]) -> date | None:
    """Primeiro dia de transação que alguma declaração ativa pode alcançar (None = sem declaração)."""
    from .open_finance import RECON_DATE_WINDOW
    return min(map(_dia, declarations)) - timedelta(days=RECON_DATE_WINDOW) if declarations else None
