"""PR-B (ADR 0006), G-flag: `DECLARACAO_BANCO` / `DECLARACAO_BANCO_USER_IDS`. Nasce desligada, fail-closed, sem cache.

Controle negativo: `ligada` devolvendo sempre True deixa vermelhos `test_ligada` (todos os casos de False) e
`test_desligada_por_padrao`. Positivo: os casos de True da mesma tabela e a conversa ligada em
`test_declaracao_b_conversa.py`.
"""
from __future__ import annotations

import pytest

from core.handlers import declaracao_banco as decl
from core.handlers import forma_pagamento as fp


@pytest.mark.parametrize("flag,ids,uid,esperado", [
    (None, None, 42, False), ("", None, 42, False), ("0", None, 42, False), ("talvez", None, 42, False),
    ("false", "42", 42, False),
    ("1", None, 42, True), ("TRUE", None, 42, True), (" on ", None, 42, True), ("yes", None, 42, True),
    ("1", "", 42, False),                      # presente e vazia = NINGUÉM (fail-closed; só ausente libera todos)
    ("1", "7,42", 42, True), ("1", " 42 , x", 42, True),
    ("1", "7,43", 42, False),                  # lista que não tem o usuário
    ("1", ",", 42, False), ("1", "abc", 42, False), ("1", "  ", 42, False),  # lista sem id válido desliga TODOS
    ("1", "42", None, False),                  # nunca levanta
])
def test_ligada(monkeypatch, flag, ids, uid, esperado):
    for nome, valor in (("DECLARACAO_BANCO", flag), ("DECLARACAO_BANCO_USER_IDS", ids)):
        if valor is None:
            monkeypatch.delenv(nome, raising=False)
        else:
            monkeypatch.setenv(nome, valor)
    assert decl.ligada(uid) is esperado


def test_lista_sem_id_avisa_uma_vez_por_processo(monkeypatch, caplog):
    decl._avisa_lista_sem_id.cache_clear()
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    monkeypatch.setenv("DECLARACAO_BANCO_USER_IDS", "")
    with caplog.at_level("WARNING"):
        assert not decl.ligada(1) and not decl.ligada(2)
    assert len([r for r in caplog.records if "sem id válido" in r.message]) == 1


def test_desligada_por_padrao():
    """O `autouse` do conftest limpa as duas variáveis: um `.env` com a flag não vira os testes de recusa."""
    assert decl.ligada(42) is False


def test_lida_a_cada_chamada_sem_cache(monkeypatch):
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    assert decl.ligada(42)
    monkeypatch.setenv("DECLARACAO_BANCO", "0")
    assert not decl.ligada(42)


@pytest.mark.parametrize("decisao,ligada,esperado", [
    (fp.BANCO, True, True), (fp.BANCO, False, False),
    (fp.CARTEIRA, True, False), (fp.PERGUNTA, True, False), (fp.MISTO, True, False),
])
def test_declara_so_a_decisao_banco_com_a_flag(monkeypatch, decisao, ligada, esperado):
    if ligada:
        monkeypatch.setenv("DECLARACAO_BANCO", "1")
    assert decl.declara(42, decisao) is esperado
