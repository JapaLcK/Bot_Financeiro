"""Caixinha OF: o que acontece com o DINHEIRO quando o vínculo sai.

Irmão de `tests/test_of_caixinha_vinculo.py` (quem pode desvincular / quem é
recusado), de onde vêm os helpers. Aqui o saldo: espelho do banco não pode virar
lote (lote é saldo PRÓPRIO, e próprio é sacável), desvincular devolve o que o
usuário depositou, desconectar não deixa fantasma, e a auto-cura não leva a meta
manual junto.

CONTROLE NEGATIVO: `balance=0` no `_unbind_pocket` (ou pular o
`_sync_pocket_from_lots`) deixa vermelho o teste dos R$300; tirar o bloco de
espelhos do `disconnect_open_finance_connection` deixa vermelhos os dois de
desconexão; tirar a guarda do `_ensure_pocket_lots` (db/pockets.py) deixa vermelho
o da edição de meta; tirar o `and p.of_investment_id is null` do backfill de
`db/schema.py` deixa vermelho o do `init_db`; tirar o `balance <= 0` do delete
deixa vermelho o da caixinha do sync com aporte; tirar o filtro `source ==
'open_finance'` deixa vermelho o da manual zerada. O do isolamento só fica vermelho
com as TRÊS guardas de `user_id` do disconnect fora juntas — o porquê está no
docstring dele.
"""
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from db import get_conn
from test_of_caixinha_autoimport import _pockets, _save, _seed_connection
from test_of_caixinha_vinculo import CDB, _auth, _meta_manual, _of_id, _total

def _lotes_abertos(user_id: int, pocket_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from pocket_lots "
                "where user_id=%s and pocket_id=%s and status='open'",
                (user_id, pocket_id),
            )
            return int(cur.fetchone()["n"])


def _carteira(user_id: int) -> float:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s", (user_id,))
            return float(cur.fetchone()["balance"] or 0)


def test_desvincular_devolve_o_saldo_proprio_e_nao_destroi_os_300(user_id):
    """Os R$300 depositados ANTES do vínculo saíram da carteira: são dinheiro do
    usuário, não espelho. Desvincular devolve eles — nem os 1000 do banco (dobra:
    patrimônio 3700) nem zero (some com os 300: patrimônio 2700)."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    pocket_id = _meta_manual(user_id)
    db.pocket_deposit_from_account(user_id, "Viagem", 300.0)     # carteira 2000 → 1700
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 1000.0   # espelho do banco

    db.bind_pocket_to_caixinha(user_id, pocket_id, None)

    assert float(_pockets(user_id)["Viagem"]["balance"]) == 300.0
    assert _carteira(user_id) == 1700.0
    assert _carteira(user_id) + _total(user_id) == 3000.0   # 1700 + 1000 no banco + 300
    # e o saldo devolvido é dos LOTES, não da coluna: o aporte seguinte soma neles
    db.pocket_deposit_from_account(user_id, "Viagem", 50.0)
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 350.0


def test_desconectar_o_banco_apaga_a_caixinha_do_sync_e_o_dinheiro_nao_nasce(user_id):
    """Fantasma: `of_investment_id` vira NULL no delete da conexão (FK set null) e
    sobrava uma caixinha do banco com o último saldo espelhado, que o accrual
    seguinte vira LOTE e o "Sacar" credita na carteira."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx-auto", "name": "Caixinha Nubank", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 800.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Caixinha Nubank"]["balance"]) == 800.0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update accounts set balance=0 where user_id=%s", (user_id,))
        conn.commit()

    db.disconnect_open_finance_connection(user_id, conn_id)

    assert "Caixinha Nubank" not in _pockets(user_id)   # espelho sem banco não fica
    with pytest.raises(LookupError):
        db.pocket_withdraw_to_account(user_id, "Caixinha Nubank", 800.0)
    assert _carteira(user_id) == 0.0                   # 800 do Nubank não viraram meus


def test_desconectar_devolve_a_meta_manual_ao_saldo_proprio(user_id):
    """A manual vinculada não é apagada (é a meta do usuário), mas também não pode
    ficar com o saldo do banco: volta pros lotes dela, como no desvincular."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    pocket_id = _meta_manual(user_id)
    db.pocket_deposit_from_account(user_id, "Viagem", 300.0)     # carteira 2000 → 1700
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)

    db.disconnect_open_finance_connection(user_id, conn_id)

    p = _pockets(user_id)["Viagem"]
    assert p["of_investment_id"] is None
    assert float(p["balance"]) == 300.0                # o próprio, não os 1000
    assert p["emoji"] == "✈️"
    with pytest.raises(ValueError, match="INSUFFICIENT_POCKET"):
        db.pocket_withdraw_to_account(user_id, "Viagem", 1000.0)
    assert _carteira(user_id) == 1700.0


def test_editar_a_meta_vinculada_nao_transforma_o_espelho_em_dinheiro_proprio(user_id):
    """O caminho por onde o espelho virava saldo PRÓPRIO — e sacável.

    `PATCH /pockets/{u}/{p}/meta` chama `_ensure_pocket_lots` sempre que o corpo traz
    `interest_enabled`, e o `saveGoal` (frontend/dashboard.js) manda esse campo em TODA
    edição de meta, inclusive num rename; o modal abre também para caixinha vinculada
    (só Depositar/Sacar ficam escondidos). Sem a guarda, o rename materializava o
    espelho de 1000 em lote aberto, o desvincular/desconectar devolvia esse lote como
    "próprio" e o Sacar creditava na carteira 1000 que estão no banco."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    pocket_id = _meta_manual(user_id)
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 1000.0     # espelho do banco
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update accounts set balance=0 where user_id=%s", (user_id,))
        conn.commit()

    client = TestClient(dashboard.app)
    r = client.patch(
        f"/pockets/{user_id}/{pocket_id}/meta",
        json={"name": "Viagem 2027", "description": None, "target_amount": 5000.0,
              "target_date": None, "emoji": "✈️", "color": None, "status": "active",
              "interest_enabled": False, "interest_rate": 1.0, "clear_target": False},
        headers=_auth(client, user_id),
    )
    assert r.status_code == 200, r.text                # o rename continua funcionando
    assert _pockets(user_id)["Viagem 2027"]["of_investment_id"] is not None
    assert _lotes_abertos(user_id, pocket_id) == 0     # espelho não vira lote

    db.disconnect_open_finance_connection(user_id, conn_id)

    assert float(_pockets(user_id)["Viagem 2027"]["balance"]) == 0.0   # nada era próprio
    with pytest.raises(ValueError, match="INSUFFICIENT_POCKET"):
        db.pocket_withdraw_to_account(user_id, "Viagem 2027", 1000.0)
    assert _carteira(user_id) == 0.0                   # os 1000 estão no banco, não aqui


def test_init_db_do_deploy_seguinte_nao_materializa_o_espelho_em_lote(user_id):
    """O SEGUNDO materializador: o backfill de `pocket_lots` dentro do `init_db()`.

    A guarda do `_ensure_pocket_lots` fecha o caminho do PATCH, mas `init_db()` roda
    em TODO startup (frontend/finance_bot_websocket_custom.py, só pula com
    SKIP_INIT_DB=1) e o `insert into pocket_lots ... select ... where p.balance > 0`
    pegava exatamente o perfil de toda caixinha vinculada: saldo espelhado > 0 e zero
    lotes, porque o sync escreve a coluna e nunca cria lote. Um deploy bastava para o
    espelho virar lote aberto — e desvincular/desconectar devolvia como saldo próprio
    sacável. Medido sem o filtro: carteira 0 → 1800."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB, {"id": "cx-auto", "name": "Caixinha Nubank", "type": "FIXED_INCOME",
                          "subtype": "CDB", "balance": 800.0}])
    pocket_id = _meta_manual(user_id)
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    auto_id = _pockets(user_id)["Caixinha Nubank"]["id"]
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 1000.0
    _, legado, _ = db.create_pocket(user_id, "Legado", interest_enabled=False)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update accounts set balance=0 where user_id=%s", (user_id,))
            # CONTROLE POSITIVO: caixinha SEM vínculo, saldo próprio e sem lote — o
            # estado que o backfill existe para consertar. O filtro restringe; sem
            # este caso, apagar o statement inteiro também passaria.
            cur.execute("update pockets set balance=250 where id=%s and user_id=%s",
                        (legado, user_id))
        conn.commit()

    db.init_db()                                   # o deploy seguinte

    assert _lotes_abertos(user_id, pocket_id) == 0     # espelho não vira lote
    assert _lotes_abertos(user_id, auto_id) == 0
    assert _lotes_abertos(user_id, legado) == 1        # e o backfill legítimo segue vivo

    db.disconnect_open_finance_connection(user_id, conn_id)

    assert "Caixinha Nubank" not in _pockets(user_id)
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 0.0
    with pytest.raises(ValueError, match="INSUFFICIENT_POCKET"):
        db.pocket_withdraw_to_account(user_id, "Viagem", 1000.0)
    assert _carteira(user_id) == 0.0                   # 1800 que estão no banco


def test_desconectar_nao_apaga_a_manual_zerada(user_id):
    """O filtro `source == 'open_finance'` do delete: a meta do usuário que estava em
    espelho puro fica em zero depois do unbind e cairia no mesmo `balance <= 0` que
    apaga a do sync. Nome, emoji e meta são dele — a linha fica."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    pocket_id = _meta_manual(user_id)                  # sem depósito: não tem próprio
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)

    db.disconnect_open_finance_connection(user_id, conn_id)

    p = _pockets(user_id)["Viagem"]                    # KeyError se o delete a levar
    assert float(p["balance"]) == 0.0
    assert p["of_investment_id"] is None
    assert p["emoji"] == "✈️" and float(p["target_amount"]) == 5000.0


def test_desconectar_nao_apaga_a_caixinha_do_sync_que_tem_aporte_do_usuario(user_id):
    """O `balance <= 0` do delete é o que separa espelho de dinheiro do usuário.

    Linha com `source='open_finance'` E lote aberto existe no legado: o dedup por nome
    adotava a caixinha do usuário sem tocar em `source` (ver a auto-cura, db/open_finance.py),
    então a adotada carrega os depósitos que ela tinha antes. Sem o `balance <= 0` o
    disconnect apagaria a linha inteira — e com ela os R$300 que saíram da carteira."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    pocket_id = _meta_manual(user_id)
    db.pocket_deposit_from_account(user_id, "Viagem", 300.0)      # carteira 2000 → 1700
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    with get_conn() as conn:                                      # a adotada do legado
        with conn.cursor() as cur:
            cur.execute("update pockets set source='open_finance' where id=%s and user_id=%s",
                        (pocket_id, user_id))
        conn.commit()
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 1000.0

    db.disconnect_open_finance_connection(user_id, conn_id)

    assert float(_pockets(user_id)["Viagem"]["balance"]) == 300.0   # o aporte ficou
    db.pocket_withdraw_to_account(user_id, "Viagem", 300.0)
    assert _carteira(user_id) == 2000.0                             # e voltou inteiro


def test_desconectar_de_um_usuario_nao_toca_nas_caixinhas_do_outro(user_id):
    """Isolamento (CLAUDE.md §0): desconectar o banco de um usuário não mexe na meta
    nem na caixinha do vizinho.

    MEDIDO: as três guardas de `user_id` do caminho (o `p.user_id`/`c.user_id` do join,
    o `user_id` do update em `_unbind_pocket` e o do delete) são redundantes entre si —
    tirando UMA, a suíte segue verde, porque a de baixo ainda filtra e nenhum estado
    alcançável liga o pocket de um usuário ao investimento de outro (o `bind` valida a
    posse). Este teste fica vermelho com as três fora ao mesmo tempo, que é o que ele
    tranca: a garantia, não um `and` específico."""
    outro = user_id + 1
    db.ensure_user(outro)
    conn_b = _seed_connection(outro, item="test-cx-item-b")
    _save(conn_b, [CDB, {"id": "cx-auto", "name": "Caixinha Nubank", "type": "FIXED_INCOME",
                         "subtype": "CDB", "balance": 800.0}])
    meta_b = _meta_manual(outro)
    db.bind_pocket_to_caixinha(outro, meta_b, _of_id(conn_b, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_b, outro)

    conn_a = _seed_connection(user_id)
    _save(conn_a, [CDB])
    db.disconnect_open_finance_connection(user_id, None)     # A desconecta TUDO que é dele

    pk = _pockets(outro)
    assert float(pk["Caixinha Nubank"]["balance"]) == 800.0
    assert pk["Caixinha Nubank"]["of_investment_id"] is not None
    assert float(pk["Viagem"]["balance"]) == 1000.0
    assert pk["Viagem"]["of_investment_id"] is not None


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
