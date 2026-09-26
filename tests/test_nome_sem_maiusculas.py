"""#596: renomear caixinha para o nome de outra é 400, não 500.

O `unique(user_id, name)` recusa o nome EXATO de outra caixinha do usuário, e a
UniqueViolation subia crua do `update pockets` do `update_pocket_meta`.
"""
from fastapi.testclient import TestClient

import db
from conftest import promote_to_pro


def _renomeia(uid, pid, nome):
    from tests.test_pockets_endpoints import _auth, _csrf_headers
    import frontend.finance_bot_websocket_custom as dashboard

    client = TestClient(dashboard.app)
    _auth(client, uid)
    return client.patch(f"/pockets/{uid}/{pid}/meta", json={"name": nome},
                        headers=_csrf_headers(client))


def test_renomear_caixinha_para_nome_exato_de_outra_da_400(user_id):
    promote_to_pro(user_id)
    db.create_pocket(user_id, "Viagem")
    _, pid, _ = db.create_pocket(user_id, "Reserva")
    resp = _renomeia(user_id, pid, "Viagem")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Já existe uma caixinha com esse nome."
    assert sorted(p["name"] for p in db.list_pockets(user_id, accrue=False)) == ["Reserva", "Viagem"]


def test_renomear_caixinha_para_nome_livre_da_200(user_id):
    """Positivo: a recusa é só para nome de OUTRA caixinha."""
    promote_to_pro(user_id)
    db.create_pocket(user_id, "Viagem")
    _, pid, _ = db.create_pocket(user_id, "Reserva")
    assert _renomeia(user_id, pid, "Carro").status_code == 200
    assert sorted(p["name"] for p in db.list_pockets(user_id, accrue=False)) == ["Carro", "Viagem"]
