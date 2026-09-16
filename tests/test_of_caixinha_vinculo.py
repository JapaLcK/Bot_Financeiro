"""Quem pode soltar o vínculo de uma caixinha OF, e o que acontece com o saldo.

Decisão do dono (O3):
  1. caixinha CRIADA pelo sync (`source='open_finance'`) não se desvincula — ela é
     espelho, e a posição volta no sync seguinte de qualquer jeito;
  2. meta MANUAL que perde o vínculo volta a valer o SALDO PRÓPRIO dela, recomposto
     dos lotes abertos (`_unbind_pocket`). Nem o espelho (o mesmo dinheiro contaria
     duas vezes: no pocket e na renda fixa do banco, que volta a listar a posição
     assim que o `of_investment_id` some) nem zero (destruía o que o usuário
     depositou antes do vínculo, que saiu da carteira dele);
  3. desconectar o banco não deixa fantasma: quem veio do sync é apagada (mesma
     regra da auto-cura) e a manual volta ao próprio — senão o último saldo
     espelhado vira lote no accrual seguinte e sai pelo "Sacar".

CONTROLE NEGATIVO: `balance=0` no `_unbind_pocket` (ou pular o
`_sync_pocket_from_lots`) deixa vermelho o teste dos R$300; tirar o bloco de
espelhos do `disconnect_open_finance_connection` deixa vermelhos os dois de
desconexão; tirar a guarda do `_ensure_pocket_lots` (db/pockets.py) deixa vermelho
o da edição de meta; tirar o `balance <= 0` do delete deixa vermelho o da caixinha
do sync com aporte; tirar o filtro `source == 'open_finance'` deixa vermelho o da
manual zerada. O do isolamento só fica vermelho com as TRÊS guardas de `user_id` do
disconnect fora juntas — o porquê está no docstring dele.
"""
import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
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
    prova de que o espelho indo embora fecha a dupla contagem. Aqui o pocket não tem
    lote nenhum (nunca recebeu depósito), então o próprio dele é 0 mesmo — o caso em
    que há aporte do usuário é o teste dos R$300, logo abaixo."""
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


def test_desvincular_nao_alcanca_a_meta_de_outro_usuario(user_id):
    """Isolamento (CLAUDE.md §0): o pocket_id vem do corpo do POST. Sem o `user_id`
    no update, um id vizinho soltaria o vínculo — e o saldo — da meta alheia."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    vitima = _meta_manual(user_id)
    db.bind_pocket_to_caixinha(user_id, vitima, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)

    outro = user_id + 1
    db.ensure_user(outro)
    assert db.bind_pocket_to_caixinha(outro, vitima, None) is False

    p = _pockets(user_id)["Viagem"]
    assert p["of_investment_id"] is not None          # vínculo intacto
    assert float(p["balance"]) == 1000.0              # e o saldo também


def test_desvincular_quem_nao_esta_vinculado_e_recusado(user_id):
    """Meta sem vínculo nenhum: o update não acha linha e a rota devolve 400. É o
    guard `of_investment_id is not null` que impede um pocket_id solto de chegar ao
    recálculo de saldo — não alcançável pela tela (o select só oferece troca)."""
    pocket_id = _meta_manual(user_id)
    db.pocket_deposit_from_account(user_id, "Viagem", 300.0)

    assert db.bind_pocket_to_caixinha(user_id, pocket_id, None) is False
    assert float(_pockets(user_id)["Viagem"]["balance"]) == 300.0


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


# ── a rota: o 400 da recusa e o `pocket_auto` que deixa a linha read-only ─────

def _auth(client, user_id: int) -> dict:
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "cx@t.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME,
                       dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "t-csrf")
    return {dashboard.CSRF_HEADER_NAME: "t-csrf", "Content-Type": "application/json"}


def test_rota_recusa_com_400_e_a_frase_da_tela(user_id):
    """O `ValueError` do db vira 400 com frase de gente — sem o `except` ele sobe
    como 500 (a tela mostra "Erro inesperado") e o controle POSITIVO é a manual,
    que continua desvinculando com 200."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB, {"id": "cx-auto", "name": "Caixinha Nubank", "type": "FIXED_INCOME",
                          "subtype": "CDB", "balance": 800.0}])
    manual = _meta_manual(user_id)
    db.bind_pocket_to_caixinha(user_id, manual, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    auto = _pockets(user_id)["Caixinha Nubank"]

    client = TestClient(dashboard.app)
    headers = _auth(client, user_id)
    r = client.post(f"/open-finance/{user_id}/caixinhas/bind",
                    json={"pocket_id": auto["id"], "of_investment_id": None}, headers=headers)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        "Essa caixinha vem do seu banco: o vínculo é automático e não pode ser desfeito aqui.")
    assert _pockets(user_id)["Caixinha Nubank"]["of_investment_id"] is not None

    r = client.post(f"/open-finance/{user_id}/caixinhas/bind",
                    json={"pocket_id": manual, "of_investment_id": None}, headers=headers)
    assert r.status_code == 200, r.text


def test_rota_marca_pocket_auto_so_na_criada_pelo_sync(user_id):
    """`pocket_auto` é o que deixa a linha read-only na tela (settings.html). Fixo em
    False, a tela ofereceria trocar o vínculo da caixinha do banco — e só voltaria 400."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB, {"id": "cx-auto", "name": "Caixinha Nubank", "type": "FIXED_INCOME",
                          "subtype": "CDB", "balance": 800.0}])
    manual = _meta_manual(user_id)
    db.bind_pocket_to_caixinha(user_id, manual, _of_id(conn_id, "cdb-vinc"))
    db.sync_open_finance_caixinhas(conn_id, user_id)

    client = TestClient(dashboard.app)
    r = client.get(f"/open-finance/{user_id}/caixinhas", headers=_auth(client, user_id))
    assert r.status_code == 200, r.text
    por_nome = {c["name"]: c for c in r.json()["caixinhas"]}
    assert por_nome["Caixinha Nubank"]["pocket_auto"] is True
    assert por_nome["CDB Banco Inter"]["pocket_auto"] is False   # vinculada na mão
