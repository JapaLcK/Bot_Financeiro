# handlers/credit.py
"""
Handlers de comandos relacionados a crédito, cartões e faturas.
Responsável por interpretar mensagens do usuário e chamar as funções de DB.
"""

import re
from utils_date import extract_date_from_text, now_tz
from db import (
    create_card, list_cards, get_card_id_by_name, set_default_card, get_default_card_id,
    add_credit_purchase, add_credit_purchase_installments, add_credit_refund,
    get_open_bill_summary, get_next_bill_summary, close_bill, pay_bill_amount,
)
from utils_text import parse_money, normalize_text
from db import get_memorized_category
from ai_router import classify_category_with_gpt



async def handle_credit_commands(message) -> bool:
    """
    Tenta tratar comandos de crédito/cartões/faturas.
    Retorna True se algum comando foi tratado; False caso contrário.
    """
    t = message.content.strip()
    t_low = t.lower().strip()
    user_id = message.author.id

    # ---- cole aqui os if t_low.startswith(...) que eu te mandei ----
    # return True quando tratar
    # no final:
    return False
