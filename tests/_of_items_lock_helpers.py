"""Ids compartilhados pelos dois arquivos do `pluggy_items_lock`.

Sem prefixo `test_` de propósito, como `tests/_corpo_json_helpers.py` e
`tests/_system_event_log_helpers.py`: o pytest não coleta este arquivo. Ele
existe porque `tests/test_of_items_lock_teto.py` (o teto da conexão dedicada) e
`tests/test_of_items_lock_vaga.py` (a vaga do `_lock_slots()` e o desfecho do
`except`) saíram do mesmo arquivo e precisam dos MESMOS ids. Duas cópias seriam
dois testes medindo coisas diferentes achando que medem a mesma (CLAUDE.md §0.7).

TINHA também uma fixture `autouse` apagando `OF_SYNC_LOCK_WAIT_MS`, e ela SAIU:
medida, ela era inerte. Os 8 testes dos dois arquivos ficam verdes sem ela em 11
valores da env (1, 50, 200, 400, 999, 1000, 2000, 60000, 0, -5, `abacaxi`) —
quem depende do valor faz `setenv` explícito, e
`test_kwargs_do_teto_chegam_no_connect_real` compara contra `_lock_wait_ms()`
nos DOIS lados da igualdade, então acompanha qualquer env. Fixture que nenhum
teste consegue ver vermelha é a que o terceiro arquivo esquece de importar e
fica verde e silencioso, que é o modo do #427: não protege, só parece.

O que NÃO subiu para cá: `_espia_connect` (só o arquivo do teto usa),
`_CortaOPrimeiroExecute` e `_vagas` (só o da vaga). Helper de uso único fica onde
é usado (CLAUDE.md §0.2).
"""
from __future__ import annotations

# Ids de laboratório. Advisory lock é de SESSÃO e some no `conn.close()`: estes
# arquivos não gravam linha nenhuma, então não há limpeza a fazer (e nunca um
# `delete ... like`, CLAUDE.md §0).
ITENS = ["teste_teto_items_lock_a", "teste_teto_items_lock_b"]
