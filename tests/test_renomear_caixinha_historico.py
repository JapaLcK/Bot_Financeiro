"""#608 — renomear a caixinha leva o histórico junto.

`update_pocket_meta` trocava só `pockets.name`; os lançamentos seguiam com o
nome antigo em `alvo` e em `efeitos.*.nome`, e quem lê por nome (histórico,
`delete_pocket`, o desfazer da criação) se perdia. O renome é pela porta real:
PATCH /pockets/{u}/{id}/meta.
"""
from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from db.pockets import TIPOS_HISTORICO_CAIXINHA
from test_pockets_endpoints import _auth, _csrf_headers
from tests._fusao_of_helpers import (  # noqa: F401
    ia_fora, manda, saldo_bruto, soma_delta_conta, uid_pro,
)


def _q(sql: str, params: tuple):
    with db.connection.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.commit()
    return rows


def _pocket(uid: int, nome: str):
    rows = _q("select id, name, balance from pockets where user_id=%s and name=%s", (uid, nome))
    return rows[0] if rows else None


def _launches(uid: int):
    return _q("select id, tipo, alvo, efeitos from launches where user_id=%s order by id", (uid,))


def _total(uid: int) -> Decimal:
    bolsos = _q("select coalesce(sum(balance), 0) as s from pockets where user_id=%s", (uid,))
    return saldo_bruto(uid) + Decimal(str(bolsos[0]["s"]))


def _renomeia(uid: int, antigo: str, novo: str) -> TestClient:
    client = TestClient(dashboard.app)
    _auth(client, uid)
    pid = _pocket(uid, antigo)["id"]
    r = client.patch(f"/pockets/{uid}/{pid}/meta", json={"name": novo},
                     headers=_csrf_headers(client))
    assert r.status_code == 200, r.text
    return client


def test_t1_desfazer_deposito_depois_do_renome_nao_perde_dinheiro(uid_pro, ia_fora):
    # Trava: já passa sem o conserto (o desfazer de depósito é recusado).
    manda(uid_pro, "recebi 1000 salario")
    manda(uid_pro, "criar caixinha viagem")
    r = manda(uid_pro, "guardei 300 na caixinha viagem")
    assert "De onde sai" not in r, r
    assert _pocket(uid_pro, "viagem")["balance"] == Decimal("300")
    dep = [l for l in _launches(uid_pro) if l["tipo"] == "deposito_caixinha"]
    assert len(dep) == 1

    _renomeia(uid_pro, "viagem", "Praia")
    manda(uid_pro, "desfazer")
    manda(uid_pro, "sim")

    assert _total(uid_pro) == Decimal("1000")
    assert _pocket(uid_pro, "Praia")["balance"] == Decimal("300")
    assert any(l["id"] == dep[0]["id"] for l in _launches(uid_pro))
    assert ia_fora == []


def test_t2_historico_da_caixinha_renomeada(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_pocket(user_id, "Viagem")
    db.pocket_deposit_from_account(user_id, "Viagem", 100)

    client = _renomeia(user_id, "Viagem", "Praia")
    r = client.get(f"/pockets/{user_id}/Praia/history")
    assert r.status_code == 200, r.text
    tipos = sorted(h["tipo"] for h in r.json()["history"])
    assert tipos == ["criar_caixinha", "deposito_caixinha"]


def test_t3_desfazer_criacao_depois_do_renome_apaga_a_renomeada(uid_pro, ia_fora):
    manda(uid_pro, "criar caixinha viagem")
    _renomeia(uid_pro, "viagem", "Praia")

    [criacao] = [l for l in _launches(uid_pro) if l["tipo"] == "criar_caixinha"]

    manda(uid_pro, "desfazer")
    manda(uid_pro, "sim")

    assert _pocket(uid_pro, "Praia") is None
    assert not any(l["id"] == criacao["id"] for l in _launches(uid_pro))
    # a guarda da #609 lê alvo E efeitos.create_pocket.nome do lançamento de criação
    assert criacao["alvo"] == "Praia"
    assert criacao["efeitos"]["create_pocket"]["nome"] == "Praia"
    assert ia_fora == []


def test_t4_apagar_criacao_antiga_nao_apaga_a_recriada(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    criacao_id, _, _ = db.create_pocket(user_id, "Viagem")
    client = _renomeia(user_id, "Viagem", "Praia")
    db.create_pocket(user_id, "Viagem")
    db.pocket_deposit_from_account(user_id, "Viagem", 200)

    r = client.delete(f"/launches/{user_id}/{criacao_id}", headers=_csrf_headers(client))
    assert r.status_code == 200, r.text

    assert _total(user_id) == Decimal("1000")
    assert _pocket(user_id, "Viagem")["balance"] == Decimal("200")
    assert _pocket(user_id, "Praia") is None


def test_t6_delete_pocket_depois_do_renome_nao_deixa_orfaos(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.create_pocket(user_id, "Viagem")
    db.pocket_deposit_from_account(user_id, "Viagem", 100)
    _renomeia(user_id, "Viagem", "Praia")
    db.pocket_withdraw_to_account(user_id, "Praia", 100)

    db.delete_pocket(user_id, "Praia")

    assert [l for l in _launches(user_id) if l["tipo"] in TIPOS_HISTORICO_CAIXINHA] == []
    assert saldo_bruto(user_id) == soma_delta_conta(user_id)


def test_t7_renome_nao_toca_historico_de_outro_usuario(user_id, uid_pro):
    for uid in (user_id, uid_pro):
        db.add_launch_and_update_balance(uid, "receita", 1000, None, "seed")
        db.create_pocket(uid, "Viagem")
        db.pocket_deposit_from_account(uid, "Viagem", 100)

    _renomeia(user_id, "Viagem", "Praia")

    outros = [l for l in _launches(uid_pro) if l["tipo"] in TIPOS_HISTORICO_CAIXINHA]
    assert len(outros) == 2
    for l in outros:
        assert l["alvo"] == "Viagem"
        nomes = [v["nome"] for k, v in l["efeitos"].items()
                 if k in ("delta_pocket", "create_pocket") and v]
        assert nomes == ["Viagem"], l["efeitos"]


def test_t8_despesa_com_alvo_igual_ao_nome_nao_e_renomeada(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.add_launch_and_update_balance(user_id, "despesa", 50, "Viagem", "passagem")
    db.create_pocket(user_id, "Viagem")

    _renomeia(user_id, "Viagem", "Praia")

    desp = [l for l in _launches(user_id) if l["tipo"] == "despesa"]
    assert [l["alvo"] for l in desp] == ["Viagem"]
    # positivo: o lançamento de criação da caixinha foi renomeado
    assert [l["alvo"] for l in _launches(user_id) if l["tipo"] == "criar_caixinha"] == ["Praia"]
