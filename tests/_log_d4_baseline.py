"""Dívida herdada da guarda `tests/test_log_d4_guarda.py` (D4 da issue #657).

RATCHET por (arquivo, função, classe) -> ocorrências, sem número de linha. Só pode DIMINUIR. Para
reescrever a lista a partir da árvore, da raiz do repositório:

    PYTHONPATH=. .venv/bin/python tests/test_log_d4_guarda.py --regenera

O comando recusa chave nova e contagem maior. Base: a árvore de 64647d1a (2026-11-09) com o PR-9c1.
"""

DIVIDA: dict[tuple[str, str, str], int] = {
    ('frontend/finance_bot_websocket_custom.py', '_apply_prospect_attribution', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_apply_referral_attribution', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_fire_email', 'PRINT_CRU'): 2,
    ('frontend/finance_bot_websocket_custom.py', 'billing_change_plan', 'PRINT_CRU'): 1,
    ('frontend/finance_bot_websocket_custom.py', 'billing_webhook', 'PRINT_CRU'): 12,
    ('frontend/finance_bot_websocket_custom.py', 'websocket_endpoint', 'PRINT_CRU'): 1,
}
