"""Quem pode soltar o vínculo de uma caixinha OF — e quem é recusado.

Decisão do dono (O3):
  1. caixinha CRIADA pelo sync (`source='open_finance'`) não se desvincula — ela é
     espelho, e a posição volta no sync seguinte de qualquer jeito;
  2. meta MANUAL pode, e quando solta volta a valer o saldo PRÓPRIO dela.

O que acontece com o DINHEIRO quando o vínculo sai (lotes, disconnect, auto-cura,
o backfill do `init_db`) mora no irmão `tests/test_of_caixinha_dinheiro.py`, que
reaproveita os helpers daqui. A divisão é o teto de 350 linhas por arquivo
(tests/test_max_lines_python.py), e o corte é por assunto: aqui a REGRA de quem
pode, lá o EFEITO no saldo.

CONTROLE NEGATIVO deste arquivo: tirar o `raise ValueError("OF_POCKET_READONLY")`
do `bind_pocket_to_caixinha` deixa vermelhos os três de recusa e o da rota; tirar o
`user_id` do update deixa vermelho o de isolamento; tirar o `of_investment_id is
not null` deixa vermelho o de desvincular quem não está vinculado. CONTROLE
POSITIVO: a meta manual continua desvinculando (`test_meta_manual_desvincula...`) e
a rota continua devolvendo 200 pra ela.
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


def test_meta_manual_toma_a_posicao_de_um_espelho_puro(user_id):
    """POLÍTICA NOVA (a escotilha). Antes este caso era recusado com
    `OF_POCKET_READONLY`, com o argumento de que soltar deixaria o pocket do sync
    órfão. Ele não fica órfão: é apagado na mesma transação, porque espelho PURO
    (zero lote aberto) não é dinheiro de ninguém — é a cópia que o auto-import fez
    da posição, e o dinheiro está no banco.

    A recusa existia e passou a prender o usuário: a meta que perdeu o vínculo por
    AUSÊNCIA da posição via ela voltar no nome de uma caixinha automática e não
    conseguia religar de jeito nenhum. A religação automática pela lápide cobre o
    caminho comum (`tests/test_of_investimento_reconciliacao.py`, B9); esta é a
    saída manual para quando a lápide não existe — conta antiga, ou vínculo que o
    usuário desfez na mão.

    O que NÃO mudou, e tem teste próprio logo abaixo: pocket do sync como ORIGEM
    do bind (desvincular, trocar de posição) continua recusando. E espelho COM
    lote aberto também continua recusando — `test_of_investimento_reconciliacao.py`
    B15 mede as duas metades juntas."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [{"id": "cx-auto", "name": "Caixinha Viagem", "type": "FIXED_INCOME",
                     "subtype": "CDB", "balance": 500.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    auto = _pockets(user_id)["Caixinha Viagem"]
    outra = _meta_manual(user_id, "Outra meta")

    assert db.bind_pocket_to_caixinha(user_id, outra, auto["of_investment_id"]) is True

    pk = _pockets(user_id)
    assert "Caixinha Viagem" not in pk, "o espelho puro é apagado, não fica órfão"
    assert pk["Outra meta"]["of_investment_id"] == auto["of_investment_id"]
    # o espelho do saldo é do passo 3 do sync, não do bind (vale para QUALQUER
    # vínculo manual, é assim desde sempre). Depois dele, os 500 do banco aparecem
    # UMA vez: na meta, e não também na renda fixa.
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert float(_pockets(user_id)["Outra meta"]["balance"]) == 500.0
    assert _total(user_id) == 500.0


def test_mudar_a_posicao_que_a_caixinha_do_sync_espelha_e_recusado(user_id):
    """O outro lado da mesma troca: o ALVO é o pocket do sync, e o pedido (forjado ou
    velho — a tela deixa a linha read-only) manda apontar pra OUTRA posição. Sem
    conferir o `source` do alvo, A ficava sem vínculo (e voltava no sync seguinte) e o
    pocket passava a espelhar B com o NOME de A. CONTROLE POSITIVO: a meta manual
    continua trocando de posição livremente."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB, {"id": "cx-auto", "name": "Caixinha Viagem", "type": "FIXED_INCOME",
                          "subtype": "CDB", "balance": 500.0}])
    db.sync_open_finance_caixinhas(conn_id, user_id)
    auto = _pockets(user_id)["Caixinha Viagem"]
    outro_investimento = _of_id(conn_id, "cdb-vinc")

    with pytest.raises(ValueError, match="OF_POCKET_READONLY"):
        db.bind_pocket_to_caixinha(user_id, auto["id"], outro_investimento)

    pk = _pockets(user_id)
    assert pk["Caixinha Viagem"]["of_investment_id"] == auto["of_investment_id"]
    assert float(pk["Caixinha Viagem"]["balance"]) == 500.0

    manual = _meta_manual(user_id)                      # POSITIVO: a manual troca
    assert db.bind_pocket_to_caixinha(user_id, manual, outro_investimento) is True
    assert _pockets(user_id)["Viagem"]["of_investment_id"] == outro_investimento



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
