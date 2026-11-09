"""Dívida herdada da guarda `tests/test_log_d4_guarda.py` (D4 da issue #657).

RATCHET por (arquivo, função, classe) -> ocorrências, sem número de linha. Só pode DIMINUIR. Para
reescrever a lista a partir da árvore, da raiz do repositório:

    PYTHONPATH=. .venv/bin/python tests/test_log_d4_guarda.py --regenera

O comando recusa chave nova e contagem maior. Base: a árvore de e3ca73e2 (2026-11-09) com o PR-9b.
"""

DIVIDA: dict[tuple[str, str, str], int] = {
    ('frontend/finance_bot_websocket_custom.py', '_account_deletion_worker', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_apply_prospect_attribution', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_apply_referral_attribution', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_ebook_worker', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_engagement', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_fire_email', 'PRINT_CRU'): 2,
    ('frontend/finance_bot_websocket_custom.py', '_investment_accrual', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_login_events_retention', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_news_bot', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_open_finance_proactive', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_open_finance_trial_expiry', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_patrimonio_foto', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_piggy_agents', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_pix_worker', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_plan_grants_reprojection', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_proactive_ai', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_recurring_charger', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_remarketing_worker', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_startup_optional', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_stripe_email_worker', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_table_cleanup', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_wa_bill_reminders', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_wa_daily', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_wa_periodic', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_wa_worker', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', 'billing_change_plan', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', 'billing_webhook', 'PRINT_CRU'): 12,
    ('frontend/finance_bot_websocket_custom.py', 'lifespan', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', 'websocket_endpoint', 'PRINT_CRU'): 1,
}
