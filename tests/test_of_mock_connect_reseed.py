"""POST /open-finance/{user_id}/mock-connect — reseed da falsa com o teto cheio.

P2 do Codex (#749): quem já tem a falsa de uma instituição e depois enche a cota
de bancos REAIS levava 402 ao reexecutar o seed da MESMA instituição — que é
upsert da falsa, não banco novo. A rota passa o id do item falso
(`mock_open_finance_item_id`, fonte única com `create_mock_open_finance_connection`)
ao `_enforce_bank_limit`, com o provider `mock_pluggy`.

CONTROLES (§3), medidos por mutação:
  * negativo — tirar o `new_item_id` da chamada na rota deixa
    `test_reseed_*_passa` vermelho (402 no lugar de 200); o id do item sem o
    user_id deixa `test_reseed_com_a_falsa_do_outro_*` vermelho (o mesmo item em
    dois donos: `AmbiguousItemError`); tirar o `_enforce_bank_limit` deixa
    `test_reseed_*_nova_instituicao_402` e `test_falsa_do_outro_*` vermelhos.
  * positivo — os 200 do reseed provam que o caminho legítimo segue; o 402 da
    instituição NOVA prova que a exceção não virou passe livre.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conftest import _cleanup_user, promote_to_pro
from db import (count_open_finance_connections, ensure_user,
                get_open_finance_connection_by_item_id, mock_open_finance_item_id)
from test_of_mock_connect_gate import (_assert_402, _auth, _conexoes, _semeia_pluggy, _switch,
                                       _url, dashboard, of_routes)


def _post(client, uid, inst):
    return client.post(_url(uid), headers=_auth(client, uid), json={"institution": inst})


def _outro_usuario() -> int:
    outro = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(outro)
    promote_to_pro(outro, "essencial")
    return outro


# ── L1: reseed da mesma instituição não conta como banco novo ─────────────────

def test_reseed_no_limite_passa_e_nova_instituicao_402(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, "1")
    client = TestClient(dashboard.app)
    assert _post(client, user_id, "itau").status_code == 200
    _semeia_pluggy(user_id, 1)  # cota de 1 banco real cheia DEPOIS da falsa

    r = _post(client, user_id, "Itaú")
    assert r.status_code == 200, r.text
    assert _conexoes(user_id, "mock_pluggy") == 1  # upsert, não linha nova

    _assert_402(_post(client, user_id, "bradesco"), 1)
    assert _conexoes(user_id, "mock_pluggy") == 1
    # A falsa segue fora da conta da vaga: só o banco real conta.
    assert count_open_finance_connections(user_id) == 1
    with pytest.raises(HTTPException) as exc:
        asyncio.run(of_routes._enforce_bank_limit(user_id, f"real-{user_id}"))
    assert exc.value.status_code == 402


# ── L2: isolamento — a falsa de outro usuário não abre a exceção ──────────────

def test_falsa_do_outro_nao_abre_excecao_no_limite(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    outro = _outro_usuario()
    _switch(monkeypatch, "1")
    try:
        assert _post(TestClient(dashboard.app), outro, "itau").status_code == 200
        _semeia_pluggy(user_id, 1)
        _assert_402(_post(TestClient(dashboard.app), user_id, "itau"), 1)
        assert _conexoes(user_id, "mock_pluggy") == 0 and _conexoes(outro, "mock_pluggy") == 1
    finally:
        _cleanup_user(outro)


def test_reseed_com_a_falsa_do_outro_na_mesma_instituicao_passa(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    outro = _outro_usuario()
    _switch(monkeypatch, "1")
    try:
        for uid in (outro, user_id):
            assert _post(TestClient(dashboard.app), uid, "itau").status_code == 200
        _semeia_pluggy(user_id, 1)
        r = _post(TestClient(dashboard.app), user_id, "itau")
        assert r.status_code == 200, r.text
        assert _conexoes(user_id, "mock_pluggy") == 1 and _conexoes(outro, "mock_pluggy") == 1
    finally:
        _cleanup_user(outro)


# ── L3: o id do item falso ─────────────────────────────────────────────────────

@pytest.mark.parametrize("chave, sufixo", [
    ("itau", "mock-itau"), ("Itaú", "mock-itau"), (" ITAU ", "mock-itau"),
    ("xpto", "mock-nubank"), (None, "mock-nubank"), ("bradesco", "mock-bradesco"),
])
def test_item_id_normaliza_a_instituicao(chave, sufixo):
    assert mock_open_finance_item_id(42, chave) == f"mock-pluggy-42-{sufixo}"


def test_item_id_e_o_gravado_pela_conexao(user_id, monkeypatch):
    promote_to_pro(user_id, "essencial")
    _switch(monkeypatch, "1")
    assert _post(TestClient(dashboard.app), user_id, "itau").status_code == 200
    item = mock_open_finance_item_id(user_id, "itau")
    assert item == f"mock-pluggy-{user_id}-mock-itau"
    linha = get_open_finance_connection_by_item_id(item, "mock_pluggy")
    assert linha and int(linha["user_id"]) == user_id and linha["provider_item_id"] == item
    assert get_open_finance_connection_by_item_id(item) is None  # o default 'pluggy' não a vê
