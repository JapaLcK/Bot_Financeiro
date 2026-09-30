"""Receita/despesa de movimentação interna sai como "entrada"/"saída" no WhatsApp.

O saque em espécie do Open Finance espelhado na Carteira (`_credita`,
db/open_finance_cash.py) grava `tipo='receita'` + `is_internal_movement`, e o
depósito grava `'despesa'`. Os totais já os deixam de fora; a LINHA da listagem
dizia "receita"/"despesa" — dinheiro que só mudou de lugar lido como ganho/gasto.
Decisão do dono: rotular pela direção.

Pela conversa (`handle_incoming`), com estado real no Postgres, e com a linha
comum na MESMA listagem: ela é o controle POSITIVO (continua "receita").

Controle NEGATIVO: `_rotulo_interno` (core/handlers/launches.py) devolvendo
sempre None → os dois testes ficam vermelhos.
"""
from __future__ import annotations

import db
from tests.test_tipo_legado_no_dashboard import _hoje_as
from tests.test_tipo_legado_na_cauda import _diga, uid_wa  # noqa: F401


def _base(uid, categoria_interna="transferencia_interna") -> None:
    """Receita comum 80, saque em espécie 200 (receita interna) e depósito 30
    (despesa interna) — a forma que `_credita` grava."""
    db.add_launch_and_update_balance(
        uid, "receita", 80, "freela", None, categoria="rendimentos", criado_em=_hoje_as(11),
    )
    db.add_launch_and_update_balance(
        uid, "receita", 200, "Saque em dinheiro", None, categoria=categoria_interna,
        is_internal_movement=True, criado_em=_hoje_as(10),
    )
    db.add_launch_and_update_balance(
        uid, "despesa", 30, "Depósito em dinheiro", None, categoria=categoria_interna,
        is_internal_movement=True, criado_em=_hoje_as(9),
    )


def test_lista_do_dia_rotula_interno_pela_direcao(uid_wa):
    _base(uid_wa)
    lista = _diga(uid_wa, "lancamentos de hoje")
    assert "• entrada • R$ 200,00" in lista, lista
    assert "• saída • R$ 30,00" in lista, lista
    assert "• receita • R$ 80,00" in lista, lista          # positivo: a comum não muda
    assert "• receita • R$ 200,00" not in lista, lista
    # totais intocados
    assert "💰 Receitas: R$ 80,00" in lista, lista
    assert "🔁 R$ 230,00 em movimentação interna" in lista, lista


def test_lista_da_categoria_rotula_interno_pela_direcao(uid_wa):
    """`_listar_categoria`, a outra porta. A comum na MESMA categoria é o positivo
    (artificial de propósito: é o que põe as duas na mesma resposta)."""
    _base(uid_wa, categoria_interna="rendimentos")
    lista = _diga(uid_wa, "liste os lancamentos em rendimentos")
    assert "• entrada • R$ 200,00" in lista, lista
    assert "• saída • R$ 30,00" in lista, lista
    assert "• receita • R$ 80,00" in lista, lista
    assert "• receita • R$ 200,00" not in lista, lista


def test_ultimos_n_sem_data_marca_interno_e_o_tira_dos_totais(uid_wa):
    """Ramo SEM data (`db.list_launches`), que não trazia `is_internal_movement`:
    o guard do rodapé nunca descartava nada e o saque entrava em "Receitas".
    NEGATIVO: tirar a coluna do SELECT de `db.list_launches` → vermelho.
    POSITIVO: a receita comum fica com 💰 e dentro do total."""
    _base(uid_wa)
    lista = _diga(uid_wa, "meus lancamentos")
    linhas = lista.splitlines()
    saque = next(l for l in linhas if "Saque em dinheiro" in l)
    deposito = next(l for l in linhas if "Depósito em dinheiro" in l)
    freela = next(l for l in linhas if "freela" in l)
    assert saque.startswith("🔁"), saque
    assert deposito.startswith("🔁"), deposito
    # o 🔁 sozinho não diz a direção: sem o rótulo as duas linhas eram iguais
    assert "• entrada • R$ 200,00" in saque, saque
    assert "• saída • R$ 30,00" in deposito, deposito
    # positivo: a linha comum segue o formato de sempre, sem rótulo extra
    assert freela.startswith("💰"), freela
    assert freela.endswith("• R$ 80,00 • freela [#1]") and "entrada" not in freela, freela
    assert "💰 Receitas: R$ 80,00" in lista, lista
    assert "💸 Gastos" not in lista, lista   # a única despesa é o depósito interno


def test_tool_do_chat_devolve_interno_como_entrada_saida(uid_wa):
    """`list_recent_launches` (chat do Piggy) pelo `_dispatch_tool` do runner —
    o JSON que o modelo recebe. Com "receita" cru, o saque em espécie era lido
    como ganho. NEGATIVO: `"tipo": r["tipo"]` de volta em
    core/services/ai_chat/tools/launches.py → vermelho. POSITIVO: a comum
    continua "receita"."""
    import json

    from core.services.ai_chat.runner import _dispatch_tool

    _base(uid_wa)
    conteudo, terminal = _dispatch_tool(uid_wa, "list_recent_launches", {"limit": 10})
    assert terminal is None
    tipos = {l["alvo"]: l["tipo"] for l in json.loads(conteudo)["launches"]}
    assert tipos == {"freela": "receita", "Saque em dinheiro": "entrada",
                     "Depósito em dinheiro": "saída"}, tipos


def test_interno_sem_nota_nem_alvo_nao_imprime_o_tipo_cru(uid_wa):
    """Fallback `descricao = tipo` do ramo sem data. NEGATIVO: `descricao = tipo`
    de volta → a linha termina em "• receita [#1]"."""
    db.add_launch_and_update_balance(
        uid_wa, "receita", 50, None, None, categoria="transferencia_interna",
        is_internal_movement=True, criado_em=_hoje_as(10),
    )
    linha = next(l for l in _diga(uid_wa, "meus lancamentos").splitlines() if "R$ 50,00" in l)
    assert "receita" not in linha, linha
    assert linha.startswith("🔁") and "• entrada •" in linha, linha
