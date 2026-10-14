"""PR-A: declarar × `save_open_finance_sync` ao mesmo tempo, nas duas ordens de chegada à trava.

Ambos tomam o mutex do usuário primeiro (o `update accounts` do escritor é o `_lock_user` do
sync); sem deadlock e com resultado único: um vínculo, uma linha, Carteira intocada. A barreira
é a de `test_bank_movements_concurrency._ordered_pair` (o segundo só roda com o primeiro
segurando a trava e esperando por ele).
"""
import pytest

import db
from tests.test_bank_movements import _bank, _sync
from tests.test_bank_movements_concurrency import _ordered_pair
from tests.test_declaracao_fluxo_reconcilia import decl, declara, n_linhas, saiu, tx
from tests._patrimonio_helpers import q


@pytest.mark.parametrize("declara_primeiro", [True, False])
def test_declarar_x_sync_um_vinculo_sem_deadlock(user_id, monkeypatch, declara_primeiro):
    cid, _ = _bank(user_id)
    box = {}

    def declarar():
        box["lid"] = declara(user_id)

    def sincronizar():
        _sync(cid, 1000, tx())

    _ordered_pair(monkeypatch, declarar if declara_primeiro else sincronizar,
                  sincronizar if declara_primeiro else declarar, esperar_travado=True)
    db.import_open_finance_launches(user_id)
    d = decl(box["lid"])
    assert d["matched_transaction_id"] and d["confirmation_method"] == "automatic"
    assert n_linhas(user_id, 50) == 1 and saiu(user_id) == 50
    assert q("select count(*) n from launches where user_id=%s and source='open_finance'", (user_id,))["n"] == 0
    assert db.get_balance(user_id) == 0
