"""Testes do auto-import de caixinhas do Open Finance (db.sync_open_finance_caixinhas).

Cobre: auto-create só pra investimento com CARA de caixinha (CDB comum fica fora),
dedup por nome, espelho do saldo do banco, e o guard que impede o accrual de pocket
tocar no saldo espelhado.
"""
import db
from db import get_conn
from db.pockets import accrue_all_pockets
from core.services.pluggy_sync import normalize_pluggy_investment


def _seed_connection(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into open_finance_connections
                   (user_id, provider, provider_item_id, status, institution_id, institution_name)
                   values (%s,'pluggy','test-cx-item','ACTIVE','2','Pluggy Bank')
                   on conflict (user_id, provider, provider_item_id) do update set status='ACTIVE'
                   returning id""",
                (user_id,),
            )
            conn_id = cur.fetchone()["id"]
        conn.commit()
    return conn_id


def _save(conn_id: int, raws: list[dict]):
    db.save_open_finance_investments(conn_id, [normalize_pluggy_investment(r) for r in raws])


def _pockets(user_id: int) -> dict:
    return {p["name"]: p for p in accrue_all_pockets(user_id)}


def test_autocreate_only_caixinha_like(user_id):
    conn_id = _seed_connection(user_id)
    _save(conn_id, [
        {"id": "cx1", "name": "Caixinha Viagem", "type": "FIXED_INCOME", "subtype": "CDB", "balance": 500.0},
        {"id": "cdb1", "name": "CDB Banco Inter", "type": "FIXED_INCOME", "subtype": "CDB", "balance": 9000.0},
        {"id": "ac1", "name": "GGRC11", "type": "EQUITY", "subtype": "REAL_ESTATE_FUND", "balance": 100.0},
    ])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 1  # só a "Caixinha Viagem"

    pk = _pockets(user_id)
    assert "Caixinha Viagem" in pk
    assert "CDB Banco Inter" not in pk           # CDB comum não vira caixinha
    assert "GGRC11" not in pk                     # renda variável não vira caixinha
    cx = pk["Caixinha Viagem"]
    assert cx["source"] == "open_finance"
    assert cx["of_investment_id"] is not None
    assert float(cx["balance"]) == 500.0
    assert cx["interest_enabled"] is False


def test_mirror_updates_balance_on_resync(user_id):
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx2", "name": "Reserva de emergência", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 1000.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Reserva de emergência"]["balance"]) == 1000.0

    # banco aportou: novo saldo → re-sync espelha
    _save(conn_id, [{"id": "cx2", "name": "Reserva de emergência", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 1250.0}])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 0          # não duplica
    assert res["caixinhas_mirrored"] == 1
    assert float(_pockets(user_id)["Reserva de emergência"]["balance"]) == 1250.0


def test_dedup_binds_existing_manual_pocket(user_id):
    # usuário já tem uma caixinha manual "Reserva"
    db.create_pocket(user_id, "Reserva")
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx3", "name": "Reserva", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 777.0}])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 0
    assert res["caixinhas_linked"] == 1           # vinculou na existente, não duplicou

    pk = _pockets(user_id)
    names = [n for n in pk if n.lower() == "reserva"]
    assert len(names) == 1                         # uma só (sem duplicata)
    assert pk["Reserva"]["of_investment_id"] is not None
    assert float(pk["Reserva"]["balance"]) == 777.0


def test_accrual_does_not_touch_of_pocket_balance(user_id):
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx4", "name": "Cofrinho férias", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 2000.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    # accrue roda várias vezes; saldo espelhado não pode mudar (juros interno OFF)
    for _ in range(3):
        pk = _pockets(user_id)
    assert float(pk["Cofrinho férias"]["balance"]) == 2000.0
