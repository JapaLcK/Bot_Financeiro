"""Dívida herdada da guarda `tests/test_log_dono_na_coluna.py` (issue #657).

RATCHET por (arquivo, função, classe) -> ocorrências, sem número de linha para
não quebrar a cada edição vizinha. Só pode DIMINUIR: consertou um log, a
contagem cai e o teste reprova até a entrada ser diminuída (ou removida, se
chegou a 0). Para reescrever a lista a partir da árvore, da raiz do repositório:

    PYTHONPATH=. .venv/bin/python tests/test_log_dono_na_coluna.py --regenera

O comando recusa chave nova e contagem maior. Entrada nova aqui à mão passa no
CI: a política é da revisão de diff, como em `tests/_max_lines_baseline.py`.
Base: a árvore de df4fbd16 (2026-11-02, com o C3a), com o PR-8b (C3b, conta e billing) aplicado.

Sem prefixo `test_` de propósito: o pytest não coleta este arquivo.
"""
DIVIDA: dict[tuple[str, str, str], int] = {
    ('adapters/discord/discord_bot.py', '_handle_message', 'LOG_MSG_NULL'): 1,
    ('adapters/discord/discord_bot.py', 'on_message', 'EVT_COL_RAW'): 1,
    ('adapters/discord/discord_bot.py', 'on_message', 'LOG_MSG_NULL'): 1,
    ('core/reports/reports_daily.py', '_periodic_reports_discord', 'LOG_MSG_NULL'): 1,
}
