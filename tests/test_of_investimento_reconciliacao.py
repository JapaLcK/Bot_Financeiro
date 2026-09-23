"""OF-01, defeito 2: `save_open_finance_investments` era upsert puro, sem delete.

Posição que sumia do banco deixava a linha em `open_finance_investments` para
sempre, e o passo 3 de `sync_open_finance_caixinhas` seguia espelhando saldo
morto (a auto-cura do passo 4 só alcança pocket cujo `of_investment_id` ainda
aponta para linha existente). Apagar a linha sem política produziria a caixinha
fantasma que o disconnect já mediu (`on delete set null` → 800 + 1000 do nada).

Irmão direto de `tests/test_of_caixinha_dinheiro.py`, de onde vêm os helpers e
os cenários — aqui a mesma política, disparada pela RECONCILIAÇÃO em vez do
disconnect. Postgres real, como os irmãos.

CONTROLE NEGATIVO (medido, ver relato): tirar o bloco `if leitura_completa:` de
`save_open_finance_investments` deixa B1, B2, B5, B6 e B7 vermelhos; fixar o
gate em `True` (ignorar o argumento) deixa B4 vermelho; tirar `i.currency,
i.updated_at` do select do snapshot deixa B8 vermelho; tirar o `balance <= 0` do
delete de `_desvincula_e_limpa_caixinhas` deixa B3 vermelho.

CONTROLE POSITIVO: B3 e B4 — o conserto REMOVE coisa, então o grupo precisa
provar o que ele NÃO pode levar (caixinha do sync com aporte do usuário) e
quando ele nem roda (leitura incompleta).
"""
import pytest

import db
from db import get_conn
from core.services.pluggy_sync import normalize_pluggy_investment
from test_of_caixinha_autoimport import _pockets, _save, _seed_connection
from test_of_caixinha_dinheiro import _carteira, _lotes_abertos
from test_of_caixinha_vinculo import CDB, _meta_manual, _of_id, _total

CX_AUTO = {"id": "cx-auto", "name": "Caixinha Nubank", "type": "FIXED_INCOME",
           "subtype": "CDB", "balance": 800.0}


def _reconcilia(conn_id: int, raws: list[dict]) -> dict:
    """O que o sync faz quando a leitura de `/investments` foi PROVADA inteira."""
    return db.save_open_finance_investments(
        conn_id, [normalize_pluggy_investment(r) for r in raws], leitura_completa=True)


def _posicoes(conn_id: int) -> set[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select provider_investment_id from open_finance_investments "
                        "where connection_id=%s", (conn_id,))
            return {r["provider_investment_id"] for r in (cur.fetchall() or [])}


# B1 ─────────────────────────────────────────────────────────────────────────

def test_posicao_que_some_leva_a_caixinha_do_sync_e_o_dinheiro_nao_nasce(user_id):
    """Espelho de `test_desconectar_o_banco_apaga_a_caixinha_do_sync...`: a posição
    sai do banco (não a conexão), e o efeito no dinheiro tem de ser o mesmo."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CX_AUTO])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Caixinha Nubank"]["balance"]) == 800.0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update accounts set balance=0 where user_id=%s", (user_id,))
        conn.commit()

    res = _reconcilia(conn_id, [])

    assert res["investments_removed"] == 1
    assert res["caixinhas_removidas"] == 1
    assert _posicoes(conn_id) == set()
    assert "Caixinha Nubank" not in _pockets(user_id)
    with pytest.raises(LookupError):
        db.pocket_withdraw_to_account(user_id, "Caixinha Nubank", 800.0)
    assert _carteira(user_id) == 0.0, "os 800 do Nubank não viraram meus"


# B2 ─────────────────────────────────────────────────────────────────────────

def test_posicao_que_some_devolve_a_meta_manual_ao_saldo_proprio(user_id):
    """A meta do usuário não é apagada, mas também não pode ficar com o saldo do
    banco: volta pros lotes dela (espelho do caso do disconnect)."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    pocket_id = _meta_manual(user_id)
    db.pocket_deposit_from_account(user_id, "Viagem", 300.0)      # carteira 2000 → 1700
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 1000.0

    _reconcilia(conn_id, [])

    p = _pockets(user_id)["Viagem"]
    assert p["of_investment_id"] is None
    assert float(p["balance"]) == 300.0, "o próprio, não os 1000 do banco"
    assert p["emoji"] == "✈️" and float(p["target_amount"]) == 5000.0
    with pytest.raises(ValueError, match="INSUFFICIENT_POCKET"):
        db.pocket_withdraw_to_account(user_id, "Viagem", 1000.0)
    assert _carteira(user_id) == 1700.0


# B3 ─────────── CONTROLE POSITIVO ───────────────────────────────────────────

def test_reconciliacao_nao_apaga_caixinha_do_sync_com_aporte_do_usuario(user_id):
    """O `balance <= 0` do delete é o que separa espelho de dinheiro do usuário.
    Linha `source='open_finance'` COM lote aberto existe no legado (o dedup por
    nome adotava a caixinha do usuário sem tocar em `source`)."""
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

    res = _reconcilia(conn_id, [])

    assert res["investments_removed"] == 1
    assert res["caixinhas_removidas"] == 0, "linha com aporte não é levada pelo delete"
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 300.0
    assert _lotes_abertos(user_id, pocket_id) == 1
    assert _carteira(user_id) == 1700.0


# B4 ─────────── CONTROLE POSITIVO (o gate) ──────────────────────────────────

def test_leitura_incompleta_preserva_a_posicao_e_a_caixinha(user_id):
    """Default `leitura_completa=False`: quem não provou que leu a carteira
    inteira não remove NADA. É este caminho que um 429 em `/investments` ou
    `statusDetail.investments.isUpdated=false` toma."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CX_AUTO])
    db.sync_open_finance_caixinhas(conn_id, user_id)

    res = db.save_open_finance_investments(conn_id, [])            # sem leitura_completa

    assert res["investments_removed"] == 0
    assert _posicoes(conn_id) == {"cx-auto"}
    assert float(_pockets(user_id)["Caixinha Nubank"]["balance"]) == 800.0


# B5 ─────────────────────────────────────────────────────────────────────────

def test_posicao_que_some_e_volta_e_reimportada_sem_dobrar_dinheiro(user_id):
    """Ida e volta (o banco some com a posição num sync e a devolve no seguinte):
    uma caixinha só, com o saldo do banco, e a carteira não ganha nada."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CX_AUTO])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    carteira_antes = _carteira(user_id)

    _reconcilia(conn_id, [])
    assert "Caixinha Nubank" not in _pockets(user_id)

    _reconcilia(conn_id, [CX_AUTO])
    db.sync_open_finance_caixinhas(conn_id, user_id)

    pk = _pockets(user_id)
    assert [n for n in pk if n.startswith("Caixinha Nubank")] == ["Caixinha Nubank"]
    assert float(pk["Caixinha Nubank"]["balance"]) == 800.0
    assert _lotes_abertos(user_id, pk["Caixinha Nubank"]["id"]) == 0, "espelho não vira lote"
    assert _carteira(user_id) == carteira_antes
    # Os 800 aparecem UMA vez: `list_of_fixed_income` esconde a posição já
    # vinculada, justamente para o patrimônio não contar o mesmo dinheiro duas
    # vezes. A ida e volta não pode desfazer esse pareamento.
    assert _total(user_id) == 800.0


# B6 ─────────────────────────────────────────────────────────────────────────

def test_reconciliacao_de_um_usuario_nao_toca_no_outro(user_id):
    """Isolamento (CLAUDE.md §0): o MESMO `provider_investment_id` em conexões de
    donos diferentes. Reconciliar a de A não pode alcançar a posição nem a
    caixinha de B."""
    outro = user_id + 1
    db.ensure_user(outro)
    conn_b = _seed_connection(outro, item="test-cx-item-b")
    _save(conn_b, [CX_AUTO])
    db.sync_open_finance_caixinhas(conn_b, outro)
    assert float(_pockets(outro)["Caixinha Nubank"]["balance"]) == 800.0

    conn_a = _seed_connection(user_id)
    _save(conn_a, [CX_AUTO])
    db.sync_open_finance_caixinhas(conn_a, user_id)

    _reconcilia(conn_a, [])                       # A esvazia a carteira dele

    assert _posicoes(conn_a) == set()
    assert "Caixinha Nubank" not in _pockets(user_id)
    assert _posicoes(conn_b) == {"cx-auto"}, "a posição de B continua"
    pb = _pockets(outro)["Caixinha Nubank"]
    assert float(pb["balance"]) == 800.0
    assert pb["of_investment_id"] is not None, "o vínculo de B nem foi tocado"


# B7 ─────────── D0: leitura válida com zero posição APAGA ───────────────────

def test_carteira_vazia_valida_reconcilia_a_conexao_inteira(user_id):
    """Decisão do dono: leitura VÁLIDA de `/investments` com zero posição é
    carteira vazia, não "não li" — e reconcilia a conexão inteira. Quem separa os
    dois é `list_pluggy_investments`, que levanta em leitura parcial."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB, CX_AUTO])
    pocket_id = _meta_manual(user_id)
    db.pocket_deposit_from_account(user_id, "Viagem", 300.0)       # carteira 2000 → 1700
    db.bind_pocket_to_caixinha(user_id, pocket_id, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Caixinha Nubank"]["balance"]) == 800.0

    res = _reconcilia(conn_id, [])

    assert res["investments_synced"] == 0
    assert res["investments_removed"] == 2, "as DUAS posições da conexão saem"
    assert _posicoes(conn_id) == set()
    pk = _pockets(user_id)
    assert "Caixinha Nubank" not in pk, "a do sync, zerada, sai"
    assert float(pk["Viagem"]["balance"]) == 300.0, "a manual volta ao próprio"
    assert pk["Viagem"]["of_investment_id"] is None
    assert _carteira(user_id) == 1700.0


# B8 ─────────── defeito 3: o snapshot não trazia moeda nem data ─────────────

def test_snapshot_de_investimento_traz_moeda_e_data(user_id):
    """O select de investimentos do snapshot não trazia `currency` nem
    `updated_at` — o de contas traz os dois, a coluna existe e `db/rv.py` já a
    usa. Sem moeda, posição em USD aparece como se fosse real."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB, {"id": "usd-1", "name": "Treasury", "type": "FIXED_INCOME",
                          "subtype": "CDB", "balance": 50.0, "currencyCode": "USD"}])
    outro = user_id + 1
    db.ensure_user(outro)
    _save(_seed_connection(outro, item="test-cx-item-b"), [CX_AUTO])

    invs = {i["name"]: i for i in db.get_open_finance_snapshot(user_id)["investments"]}

    assert set(invs) == {"CDB Banco Inter", "Treasury"}, "só as do dono"
    assert invs["CDB Banco Inter"]["currency"] == "BRL"
    assert invs["Treasury"]["currency"] == "USD"
    assert invs["Treasury"]["updated_at"] is not None
