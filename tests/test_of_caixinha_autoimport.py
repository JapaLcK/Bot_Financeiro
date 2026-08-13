"""Testes do auto-import de caixinhas do Open Finance (db.sync_open_finance_caixinhas).

Cobre: auto-create só pra investimento com CARA de caixinha (CDB comum fica fora),
dedup por nome, espelho do saldo do banco, e o guard que impede o accrual de pocket
tocar no saldo espelhado.
"""
import pytest

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


def test_zero_balance_caixinha_not_imported(user_id):
    # Reserva/fundo com saldo 0 (ex.: Nubank "Reserva Planejada" vazia) não vira caixinha.
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cxz", "name": "Reserva Planejada", "type": "MUTUAL_FUND",
                     "subtype": "INVESTMENT_FUND", "balance": 0.0}])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 0
    assert "Reserva Planejada" not in _pockets(user_id)


def test_phantom_zero_of_caixinha_is_cleaned(user_id):
    # Fantasma já criada (saldo 0) é removida pela auto-cura no próximo sync.
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cxp", "name": "Reserva X", "type": "MUTUAL_FUND",
                     "subtype": "INVESTMENT_FUND", "balance": 0.0}])
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from open_finance_investments where connection_id=%s and provider_investment_id='cxp'", (conn_id,))
            of_id = cur.fetchone()["id"]
            cur.execute(
                "insert into pockets(user_id,name,balance,source,of_investment_id,interest_enabled) "
                "values(%s,'Reserva X',0,'open_finance',%s,false)", (user_id, of_id))
        conn.commit()
    assert "Reserva X" in _pockets(user_id)          # existe antes
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_cleaned"] == 1
    assert "Reserva X" not in _pockets(user_id)      # removida


def test_of_pocket_is_readonly_for_deposit_and_withdraw(user_id):
    # Caixinha do banco (OF) não aceita aporte/resgate pelo Pig — read-only.
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx-ro", "name": "Caixinha Viagem", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 500.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update accounts set balance=1000 where user_id=%s", (user_id,))
            if cur.rowcount == 0:
                cur.execute("insert into accounts(user_id,name,balance) values(%s,'Carteira',1000)", (user_id,))
        conn.commit()
    with pytest.raises(ValueError, match="OF_POCKET_READONLY"):
        db.pocket_deposit_from_account(user_id, "Caixinha Viagem", 100.0)
    with pytest.raises(ValueError, match="OF_POCKET_READONLY"):
        db.pocket_withdraw_to_account(user_id, "Caixinha Viagem", 100.0)


def test_candidates_hide_unlinked_zero_balance(user_id):
    # Nubank devolve toda posição de CDB via OF, inclusive caixinhas já esvaziadas
    # (saldo 0). A tela de vínculo não deve listar essas — só as com saldo > 0.
    conn_id = _seed_connection(user_id)
    _save(conn_id, [
        {"id": "cx-live", "name": "CDB - NU FINANCEIRA S.A.", "type": "FIXED_INCOME",
         "subtype": "CDB", "balance": 500.0},
        {"id": "cx-zero", "name": "CDB - NU FINANCEIRA S.A.", "type": "FIXED_INCOME",
         "subtype": "CDB", "balance": 0.0},
    ])
    cands = db.list_caixinha_candidates(user_id)
    ids = {c["of_investment_id"] for c in cands}
    live = _of_id(conn_id, "cx-live")
    zero = _of_id(conn_id, "cx-zero")
    assert live in ids       # com saldo aparece
    assert zero not in ids   # zerada e sem vínculo some da lista


def test_candidates_keep_linked_zero_balance(user_id):
    # Caixinha vinculada a uma meta permanece na lista mesmo zerada, pra
    # permitir o desvínculo pela UI.
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx-drained", "name": "Caixinha Viagem", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 0.0}])
    of_id = _of_id(conn_id, "cx-drained")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into pockets(user_id,name,balance,source,of_investment_id,interest_enabled) "
                "values(%s,'Minha meta',0,'open_finance',%s,false)", (user_id, of_id))
        conn.commit()
    ids = {c["of_investment_id"] for c in db.list_caixinha_candidates(user_id)}
    assert of_id in ids


def _of_id(conn_id: int, provider_investment_id: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from open_finance_investments "
                "where connection_id=%s and provider_investment_id=%s",
                (conn_id, provider_investment_id))
            return cur.fetchone()["id"]


def test_accrual_does_not_touch_of_pocket_balance(user_id):
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx4", "name": "Cofrinho férias", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 2000.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    # accrue roda várias vezes; saldo espelhado não pode mudar (juros interno OFF)
    for _ in range(3):
        pk = _pockets(user_id)
    assert float(pk["Cofrinho férias"]["balance"]) == 2000.0
