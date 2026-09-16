"""Ids e fixture compartilhados pelos dois arquivos do `pluggy_items_lock`.

Sem prefixo `test_` de propósito, como `tests/_corpo_json_helpers.py` e
`tests/_system_event_log_helpers.py`: o pytest não coleta este arquivo. Ele
existe porque `tests/test_of_items_lock_teto.py` (o teto da conexão dedicada) e
`tests/test_of_items_lock_vaga.py` (a vaga do `_lock_slots()` e o desfecho do
`except`) saíram do mesmo arquivo e precisam dos MESMOS ids e da MESMA fixture.
Duas cópias seriam dois testes medindo coisas diferentes achando que medem a
mesma (CLAUDE.md §0.7).

A fixture mora AQUI e não num `conftest.py` de propósito: ela é `autouse`, e num
conftest passaria a apagar `OF_SYNC_LOCK_WAIT_MS` da suíte inteira. Importada por
nome, vale só nos arquivos que a importam.

O que NÃO subiu para cá: `_espia_connect` (só o arquivo do teto usa),
`_CortaOPrimeiroExecute` e `_vagas` (só o da vaga). Helper de uso único fica onde
é usado (CLAUDE.md §0.2).
"""
from __future__ import annotations

import pytest

# Ids de laboratório. Advisory lock é de SESSÃO e some no `conn.close()`: estes
# arquivos não gravam linha nenhuma, então não há limpeza a fazer (e nunca um
# `delete ... like`, CLAUDE.md §0).
ITENS = ["teste_teto_items_lock_a", "teste_teto_items_lock_b"]


@pytest.fixture(autouse=True)
def sem_env_de_espera(monkeypatch):
    """A env é o knob sob teste: quem quiser um valor, faz `setenv` explícito."""
    monkeypatch.delenv("OF_SYNC_LOCK_WAIT_MS", raising=False)
