"""Quem pode soltar o vínculo de uma caixinha OF, e o que acontece com o saldo.

Decisão do dono (O3):
  1. caixinha CRIADA pelo sync (`source='open_finance'`) não se desvincula — ela é
     espelho, e a posição volta no sync seguinte de qualquer jeito;
  2. meta MANUAL que perde o vínculo zera o espelho (nome/emoji/meta ficam), senão o
     mesmo dinheiro conta duas vezes: no pocket e na renda fixa do banco, que volta a
     listar a posição assim que o `of_investment_id` some.
"""
import pytest

import db
from db import get_conn
from test_of_caixinha_autoimport import _pockets, _save, _seed_connection

CDB = {"id": "cdb-vinc", "name": "CDB Banco Inter", "type": "FIXED_INCOME",
       "subtype": "CDB", "balance": 1000.0}


def _of_id(conn_id: int, provider_id: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from open_finance_investments "
                "where connection_id=%s and provider_investment_id=%s",
                (conn_id, provider_id),
            )
            return cur.fetchone()["id"]


def _meta_manual(user_id: int, nome: str = "Viagem") -> int:
    """Meta do usuário (source='manual'), sem rendimento pra o total não andar sozinho."""
    _, pocket_id, _ = db.create_pocket(user_id, nome, interest_enabled=False)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update pockets set emoji='✈️', target_amount=5000 where id=%s and user_id=%s",
                (pocket_id, user_id),
            )
            cur.execute("update accounts set balance=2000 where user_id=%s", (user_id,))
        conn.commit()
    return pocket_id


def _total(user_id: int) -> float:
    """Patrimônio que a tela soma: renda fixa do banco + saldo das caixinhas."""
    fixo = sum(g["balance"] for g in db.list_of_fixed_income(user_id))
    caixas = sum(float(p["balance"] or 0) for p in _pockets(user_id).values())
    return round(fixo + caixas, 2)


def test_caixinha_do_sync_nao_desvincula(user_id):
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx-auto", "name": "Caixinha Viagem", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 500.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    auto = _pockets(user_id)["Caixinha Viagem"]

    with pytest.raises(ValueError, match="OF_POCKET_READONLY"):
        db.bind_pocket_to_caixinha(user_id, auto["id"], None)

    depois = _pockets(user_id)["Caixinha Viagem"]
    assert depois["of_investment_id"] == auto["of_investment_id"]   # vínculo intacto
    assert float(depois["balance"]) == 500.0                        # e o espelho também


def test_meta_manual_desvincula_e_o_total_nao_muda(user_id):
    """Controle POSITIVO da recusa acima (manual continua podendo desvincular) e a
    prova de que o espelho zerado fecha a dupla contagem."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    pocket_id = _meta_manual(user_id)
    assert db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc")) is True
    db.sync_open_finance_caixinhas(conn_id, user_id)          # espelha os 1000 do banco
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 1000.0
    antes = _total(user_id)

    assert db.bind_pocket_to_caixinha(user_id, pocket_id, None) is True

    p = _pockets(user_id)["Viagem"]
    assert p["of_investment_id"] is None
    assert float(p["balance"]) == 0.0          # espelho some com o vínculo
    assert p["emoji"] == "✈️"                   # o que é do usuário fica
    assert float(p["target_amount"]) == 5000.0
    assert _total(user_id) == antes == 1000.0  # sem zerar daria 2000: dobra


def test_desvincular_nao_deixa_o_saldo_antigo_ressuscitar(user_id):
    """O saldo do pocket é recomposto dos lotes no depósito seguinte. Se os lotes de
    antes do vínculo ficassem abertos, o `balance=0` duraria até o próximo aporte."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    pocket_id = _meta_manual(user_id)
    db.pocket_deposit_from_account(user_id, "Viagem", 300.0)     # lote de 300, pré-vínculo
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    db.bind_pocket_to_caixinha(user_id, pocket_id, None)

    db.pocket_deposit_from_account(user_id, "Viagem", 50.0)
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 50.0  # só o aporte novo


def test_roubar_a_caixinha_de_um_pocket_do_sync_e_recusado(user_id):
    """Trocar a meta de uma caixinha do sync deixaria o pocket automático órfão —
    mesma recusa do desvincular."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx-auto", "name": "Caixinha Viagem", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 500.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    auto = _pockets(user_id)["Caixinha Viagem"]
    outra = _meta_manual(user_id, "Outra meta")

    with pytest.raises(ValueError, match="OF_POCKET_READONLY"):
        db.bind_pocket_to_caixinha(user_id, outra, auto["of_investment_id"])

    pk = _pockets(user_id)
    assert pk["Caixinha Viagem"]["of_investment_id"] == auto["of_investment_id"]
    assert pk["Outra meta"]["of_investment_id"] is None


def test_auto_cura_limpa_a_do_banco_e_nao_toca_na_manual_zerada(user_id):
    """As duas metades do mesmo delete: ele continua alcançando a caixinha do sync que
    o banco zerou, e não alcança a meta manual que o desvincular acabou de zerar."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB, {"id": "cx-auto", "name": "Caixinha Viagem", "type": "FIXED_INCOME",
                          "subtype": "CDB", "balance": 500.0}])
    pocket_id = _meta_manual(user_id)
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    db.bind_pocket_to_caixinha(user_id, pocket_id, None)          # manual zerada

    _save(conn_id, [CDB, {"id": "cx-auto", "name": "Caixinha Viagem", "type": "FIXED_INCOME",
                          "subtype": "CDB", "balance": 0.0}])      # banco esvaziou a do sync
    res = db.sync_open_finance_caixinhas(conn_id, user_id)

    assert res["caixinhas_cleaned"] == 1
    pk = _pockets(user_id)
    assert "Caixinha Viagem" not in pk        # a do banco saiu
    assert float(pk["Viagem"]["balance"]) == 0.0   # a do usuário ficou, zerada
