"""PR-A2b-1: a borda HTTP das ações de conferência (`/open-finance/{uid}/movements/{confirm,undo}`):
entrada inválida nunca é 500, sem cookie não entra, outro usuário não alcança a declaração nem a transação,
e o cache do dashboard só é invalidado quando a ação valeu."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import _cleanup_user, promote_to_pro
from frontend.routes import shared
from tests._retrato_declaracao import em, retrato, t, tid
from tests.test_bank_movements import _bank
from tests.test_declaracao_a2b_acoes import H, ana, cliente, confirma, desfaz
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza


@pytest.fixture
def outro():
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    yield uid
    _cleanup_user(uid)


INEXISTENTE = 10**12  # id pequeno existe num banco novo (ana() cria launch 1 e T1 1) e cai no ramo idempotente: 200

@pytest.mark.parametrize("body", [
    {}, {"launch_id": 1}, {"launch_id": "x", "transaction_id": 1}, {"launch_id": -1, "transaction_id": -1},
    {"launch_id": 10**30, "transaction_id": 10**30}, {"launch_id": 1.5, "transaction_id": 1},
    {"launch_id": None, "transaction_id": 1}, {"launch_id": 1, "transaction_id": 1, "replace_transaction_id": "a"},
    {"launch_id": INEXISTENTE, "transaction_id": INEXISTENTE, "replace_transaction_id": 10**30},
    {"launch_id": [1], "transaction_id": 1}, {"launch_id": True, "transaction_id": INEXISTENTE},  # True vira 1
])
@pytest.mark.parametrize("path", ["confirm", "undo"])
def test_entrada_invalida_nunca_500(user_id, path, body):
    cid, d, c = ana(user_id)
    antes = retrato(user_id)
    r = c.post(f"/open-finance/{user_id}/movements/{path}", json=body, headers=H)
    assert r.status_code in (404, 409, 422), (r.status_code, r.text)
    assert retrato(user_id) == antes


@pytest.mark.parametrize("qs", ["launch_id=abc", "launch_id=-1", "launch_id=99999999999999999999999", "launch_id=",
                                "launch_id=1&launch_id=2"])
def test_get_launch_id_invalido_nunca_500(user_id, qs):
    cid, d, c = ana(user_id)
    assert c.get(f"/open-finance/{user_id}/movements?{qs}").status_code in (200, 422)


def test_corpo_nao_json(user_id):
    cid, d, c = ana(user_id)
    for path in ("confirm", "undo"):
        r = c.post(f"/open-finance/{user_id}/movements/{path}", content=b"not json",
                   headers={**H, "Content-Type": "application/json"})
        assert r.status_code == 422


def test_sem_cookie_nao_acessa(user_id):
    cid, d, c = ana(user_id)
    anon = TestClient(dashboard.app, raise_server_exceptions=False)
    for path in ("confirm", "undo"):
        r = anon.post(f"/open-finance/{user_id}/movements/{path}", json={"launch_id": d, "transaction_id": 1}, headers=H)
        assert r.status_code in (401, 403), (path, r.status_code)
    assert anon.get(f"/open-finance/{user_id}/movements?launch_id={d}").status_code in (401, 403)


def test_usuario_nao_usa_transacao_nem_declaracao_de_outro(user_id, outro):
    """B tem declaração e banco próprios e tenta ligar/trocar/desfazer com ids de A, e com a declaração de A."""
    _, dA, _ = ana(user_id)
    cidB, _ = _bank(outro)
    cB = cliente(outro)
    dB = declara(outro, criado_em=em(0), alvo="mercado")
    sincroniza(cidB, outro, t("B1", -50, 0, "PADARIA"))
    antesA, antesB = retrato(user_id), retrato(outro)
    tA1, tA2 = tid(user_id, "T1"), tid(user_id, "T2")
    for body in ({"launch_id": dB, "transaction_id": tA1}, {"launch_id": dB, "transaction_id": tA2},
                 {"launch_id": dB, "transaction_id": tA2, "replace_transaction_id": tA1},
                 {"launch_id": dA, "transaction_id": tid(outro, "B1")}):
        assert cB.post(f"/open-finance/{outro}/movements/confirm", json=body, headers=H).status_code == 404, body
    r = cB.post(f"/open-finance/{outro}/movements/undo", json={"launch_id": dA, "transaction_id": tA1}, headers=H)
    assert r.status_code == 404
    assert retrato(user_id) == antesA and retrato(outro) == antesB


def test_historico_de_outro_usuario_nao_lista_a_declaracao(user_id, outro):
    cid, d, c = ana(user_id)
    itens = cliente(outro).get(f"/history/{outro}/list?limit=50").json()["items"]
    assert all(i["id"] != d for i in itens)


def test_rota_invalida_o_cache_do_dashboard_so_quando_a_acao_valeu(user_id, monkeypatch):
    cid, d, c = ana(user_id)
    chamadas = []
    monkeypatch.setattr(shared, "invalidate_dashboard_current_cache", chamadas.append)
    assert confirma(c, user_id, d, "T2").status_code == 409  # já está casada com T1
    assert chamadas == []
    assert desfaz(c, user_id, d, "T1").status_code == 200
    assert confirma(c, user_id, d, "T2").status_code == 200
    assert chamadas == [user_id, user_id]
