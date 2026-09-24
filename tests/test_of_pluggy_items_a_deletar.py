"""As REGRAS de `db.open_finance_state.pluggy_items_a_deletar` — a fonte única
(CLAUDE.md §0.7) do filtro que decide quais items ainda precisam de DELETE na
Pluggy: disconnect, reset e os DOIS pontos da exclusão de conta.

POR QUE ESTE ARQUIVO EXISTE: a função não era nomeada por teste nenhum. O Tester
mediu as duas mutações na PR-C (#539) e as duas ficaram VERDES — apagar a regra
do `PAUSED` dava 466 passed, apagar `provider == "pluggy"` dava 485 passed. Só a
FIAÇÃO estava presa (trocar o corpo por `return []` mata os 4 chamadores); o
CONTEÚDO do filtro não estava. Os testes daqui prendem o conteúdo, e os dois
irmãos de fiação continuam onde estão (`tests/test_account_deletion_pluggy.py`,
`tests/test_account_reset.py`, `tests/test_open_finance_disconnect_route.py`).

Unitário e sem banco de propósito: a função é pura, recebe as linhas prontas.

ASSIMETRIA DELIBERADA, e por isso tem caso nomeado: `status` é normalizado com
`.upper()` e `provider` NÃO. `status` chega do payload da Pluggy (ela é quem
escolhe a caixa); `provider` é literal nosso, escrito em dois inserts do
repositório (`db/open_finance.py:125` e `:770`, ambos com `'pluggy'`), e a irmã
SQL que alimenta o MESMO delete remoto compara igual — `list_pluggy_item_ids`
usa `where provider='pluggy' and upper(coalesce(status,'')) <> 'PAUSED'`
(`db/open_finance.py:416`). Normalizar só aqui faria as duas leituras
divergirem, que é o que o §0.7 proíbe.
"""
from __future__ import annotations

import pytest

from db.open_finance_state import pluggy_items_a_deletar


def _linha(provider="pluggy", item="item-1", status="UPDATED"):
    return {"provider": provider, "provider_item_id": item, "status": status}


# (linha, entra?, por quê)
TABELA = [
    (_linha(), True, "caminho legítimo: pluggy, item id, status vivo"),
    (_linha(status=None), True, "status nulo não é PAUSED"),
    (_linha(status=""), True, "status vazio não é PAUSED"),
    (_linha(status="PAUSED"), False, "PAUSED: item já apagado na Pluggy no trial"),
    (_linha(status="paused"), False, "PAUSED minúsculo: `.upper()` normaliza"),
    (_linha(status="Paused"), False, "PAUSED misto: `.upper()` normaliza"),
    (_linha(provider="bacen"), False, "outro provider não tem item na Pluggy"),
    (_linha(provider=""), False, "provider vazio não é pluggy"),
    (_linha(provider=None), False, "provider nulo não é pluggy"),
    (_linha(provider="PLUGGY"), False, "assimetria: provider compara IGUAL (ver docstring)"),
    (_linha(item=None), False, "sem item id não há o que deletar lá"),
    (_linha(item=""), False, "item id vazio não há o que deletar lá"),
]


@pytest.mark.parametrize("linha,entra,motivo", TABELA, ids=[c[2] for c in TABELA])
def test_regra_por_linha(linha, entra, motivo):
    esperado = [linha["provider_item_id"]] if entra else []
    assert pluggy_items_a_deletar([linha]) == esperado, motivo


def test_vazio_e_none_nao_estouram():
    assert pluggy_items_a_deletar([]) == []
    assert pluggy_items_a_deletar(None) == []


def test_ordena_e_deduplica():
    """O chamador compara CONJUNTOS (1º passe × 2º passe da exclusão): item
    repetido em duas conexões não pode virar dois DELETEs, e a ordem é estável."""
    linhas = [_linha(item="b"), _linha(item="a"), _linha(item="b"),
              _linha(item="z", status="PAUSED")]
    assert pluggy_items_a_deletar(linhas) == ["a", "b"]
