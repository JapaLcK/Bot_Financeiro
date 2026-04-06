# core/handlers/balance.py
from __future__ import annotations
import db
from utils_text import fmt_brl


def check(user_id: int) -> str:
    bal = float(db.get_balance(user_id) or 0)
    return f"🏦 **Conta Corrente**: {fmt_brl(bal)}"
