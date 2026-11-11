"""Dívida herdada da guarda `tests/test_log_d4_guarda.py` (D4 da issue #657).

RATCHET por (arquivo, função, classe) -> ocorrências, sem número de linha. Só pode DIMINUIR. Para
reescrever a lista a partir da árvore, da raiz do repositório:

    PYTHONPATH=. .venv/bin/python tests/test_log_d4_guarda.py --regenera

O comando recusa chave nova e contagem maior. Vazia desde o PR-9c (303b110b + 9c2): ponto novo reprova e
se conserta no código; o `--regenera` só confirma que ela segue vazia.
"""

DIVIDA: dict[tuple[str, str, str], int] = {
}
