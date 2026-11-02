"""Dívida herdada da guarda `tests/test_log_dono_na_coluna.py` (issue #657).

RATCHET por (arquivo, função, classe) -> ocorrências, sem número de linha para
não quebrar a cada edição vizinha. Só pode DIMINUIR: consertou um log, a
contagem cai e o teste reprova até a entrada ser diminuída (ou removida, se
chegou a 0). Para reescrever a lista a partir da árvore, da raiz do repositório:

    PYTHONPATH=. .venv/bin/python tests/test_log_dono_na_coluna.py --regenera

O comando recusa chave nova e contagem maior. Entrada nova aqui à mão passa no
CI: a política é da revisão de diff, como em `tests/_max_lines_baseline.py`.
Base: a árvore de 6a7ef412 (2026-11-02), com o PR-8a (C3a, HTTP de topo + auth) aplicado.

Sem prefixo `test_` de propósito: o pytest não coleta este arquivo.
"""
DIVIDA: dict[tuple[str, str, str], int] = {
    ('adapters/discord/discord_bot.py', '_handle_message', 'LOG_MSG_NULL'): 1,
    ('adapters/discord/discord_bot.py', 'on_message', 'EVT_COL_RAW'): 1,
    ('adapters/discord/discord_bot.py', 'on_message', 'LOG_MSG_NULL'): 1,
    ('core/admin_dashboard.py', '_decrypt_admin_field', 'LOG_MSG_NULL'): 1,
    ('core/admin_dashboard.py', 'set_account_plan', 'EVT_COL_RAW'): 1,
    ('core/reports/reports_daily.py', '_periodic_reports_discord', 'LOG_MSG_NULL'): 1,
    ('core/services/email_service.py', '_log_email_event', 'EVT_PII_NULL'): 1,
    ('core/services/email_service.py', 'send_founder_email_once', 'LOG_MSG_NULL'): 1,
    ('db/accounts.py', 'carteira_exibida', 'LOG_EXTRA_RAW'): 1,
    ('db/bills.py', 'mark_bill_paid', 'LOG_MSG_NULL'): 1,
    ('db/checkout_funnel.py', '_record', 'LOG_MSG_NULL'): 1,
    ('db/rendimento_contratado.py', 'ler', 'LOG_MSG_NULL'): 2,
    ('db/signup_quiz.py', 'boas_vindas_da_conta', 'LOG_MSG_NULL'): 1,
    ('db/users.py', 'link_platform_identity', 'LOG_MSG_NULL'): 1,
    ('db_support.py', 'register_auth_user_impl', 'LOG_PII_NULL'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_new_session', 'EVT_COL_RAW'): 1,
    ('frontend/finance_bot_websocket_custom.py', '_notify_completed', 'LOG_MSG_NULL'): 1,
    ('frontend/finance_bot_websocket_custom.py', 'billing_cancel_change', 'LOG_MSG_NULL'): 1,
    ('frontend/finance_bot_websocket_custom.py', 'billing_change_plan', 'LOG_MSG_NULL'): 1,
    ('frontend/finance_bot_websocket_custom.py', 'billing_create_checkout', 'LOG_MSG_NULL'): 1,
    ('frontend/finance_bot_websocket_custom.py', 'create_launch_route', 'LOG_MSG_NULL'): 1,
    ('frontend/routes/ai_chat.py', 'turno', 'LOG_MSG_NULL'): 1,
}
