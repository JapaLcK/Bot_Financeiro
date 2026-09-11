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


def _seed_connection(user_id: int, institution: str = "Pluggy Bank",
                    item: str = "test-cx-item") -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """insert into open_finance_connections
                   (user_id, provider, provider_item_id, status, institution_id, institution_name)
                   values (%s,'pluggy',%s,'ACTIVE','2',%s)
                   on conflict (user_id, provider, provider_item_id)
                   do update set status='ACTIVE', institution_name=excluded.institution_name
                   returning id""",
                (user_id, item, institution),
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
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, [
        {"id": "cx-live", "name": "CDB - NU FINANCEIRA S.A.", "issuer": "NU FINANCEIRA S.A.",
         "type": "FIXED_INCOME", "subtype": "CDB", "balance": 500.0},
        {"id": "cx-zero", "name": "CDB - NU FINANCEIRA S.A.", "issuer": "NU FINANCEIRA S.A.",
         "type": "FIXED_INCOME", "subtype": "CDB", "balance": 0.0},
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


# ── Caixinha do Nubank: o banco manda o nome JURÍDICO do papel ────────────────
# Dado real do dono (10 posições com saldo > 0): `name` e `issuer` IDÊNTICOS nas
# dez, sem nada de "caixinha" no nome. Nenhum padrão de nome casava — as dez
# eram descartadas em silêncio. A regra passou a aceitar CDB emitido pelo
# PRÓPRIO banco conectado.
NU_NAME = "CDB - NU FINANCEIRA S.A. - SOCIEDADE DE CREDITO, FINANCIAMENTO E INVESTIMENTO"
NU_ISSUER = "NU FINANCEIRA S.A. - SOCIEDADE DE CREDITO, FINANCIAMENTO E INVESTIMENTO"
NU_SALDOS = [11618.82, 10002.02, 8103.65, 1108.99, 464.76, 459.20, 455.10, 482.64, 381.81, 115.83]


def _nubank_raws(saldos=None) -> list[dict]:
    """As posições do dono, como a Pluggy manda (mesmo name/issuer, number nulo)."""
    return [{"id": f"nu{i}", "name": NU_NAME, "issuer": NU_ISSUER, "number": None,
             "type": "FIXED_INCOME", "subtype": "CDB", "balance": b}
            for i, b in enumerate(saldos if saldos is not None else NU_SALDOS)]


# CDB de TERCEIRO no mesmo banco e Tesouro (issuer nulo, como o do dono): ficam fora.
CDB_TERCEIRO = {"id": "inter1", "name": "CDB Banco Inter", "issuer": "BANCO INTER S.A.",
                "type": "FIXED_INCOME", "subtype": "CDB", "balance": 9000.0}
TESOURO = {"id": "tes1", "name": "Tesouro IPCA+ 2032", "issuer": None,
           "type": "FIXED_INCOME", "subtype": "TREASURY", "balance": 3000.0}


def _rf_names(user_id: int) -> set:
    from db.rv import list_of_fixed_income
    return {r["name"] for r in list_of_fixed_income(user_id)}


def test_cdb_do_proprio_banco_vira_caixinha(user_id):
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws() + [CDB_TERCEIRO, TESOURO])
    res = db.sync_open_finance_caixinhas(conn_id, user_id)

    assert res["caixinhas_created"] == 10          # as dez do dono entraram
    pk = _pockets(user_id)
    assert len(pk) == 10 and len(set(pk)) == 10    # dez nomes distintos
    assert sorted(float(p["balance"]) for p in pk.values()) == sorted(NU_SALDOS)
    # CDB de terceiro e Tesouro continuam investimento, não caixinha
    assert "CDB Banco Inter" not in pk
    assert _rf_names(user_id) == {"CDB", "Tesouro IPCA+ 2032"}  # rótulos de db.rv


def test_cdb_de_outro_emissor_nao_vira_caixinha(user_id):
    """Controle negativo do casamento: mesmo papel, banco conectado DIFERENTE."""
    conn_id = _seed_connection(user_id, institution="Banco Inter")
    _save(conn_id, _nubank_raws())
    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 0           # emissor ≠ banco conectado
    assert _pockets(user_id) == {}
    assert _rf_names(user_id) == {"CDB · Nu Financeira"}   # segue contando como renda fixa


def test_import_nao_muda_a_soma(user_id):
    from db.rv import list_of_fixed_income
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws() + [CDB_TERCEIRO, TESOURO])

    def total():
        rf = sum(float(r["balance"] or 0) for r in list_of_fixed_income(user_id))
        pk = sum(float(p["balance"] or 0) for p in _pockets(user_id).values())
        return round(rf + pk, 2)

    antes = total()
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert total() == antes                        # nada some, nada conta duas vezes
    assert antes == round(sum(NU_SALDOS) + 9000.0 + 3000.0, 2)


def test_idempotente_e_nome_nao_segue_o_saldo(user_id):
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws())
    db.sync_open_finance_caixinhas(conn_id, user_id)
    nomes = {p["of_investment_id"]: n for n, p in _pockets(user_id).items()}
    # o número vem da ORDEM DE CHEGADA do banco (i.id), nunca do saldo: NU_SALDOS
    # não está ordenado, então ordenar por saldo daria outro mapa aqui.
    assert nomes == {_of_id(conn_id, f"nu{i}"):
                     "Caixinha Nubank" if i == 0 else f"Caixinha Nubank {i + 1}"
                     for i in range(len(NU_SALDOS))}

    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 0           # 2ª rodada não cria nada
    assert {p["of_investment_id"]: n for n, p in _pockets(user_id).items()} == nomes

    # saldos EMBARALHADOS (o maior vira o menor): o nome é do papel, não do saldo
    _save(conn_id, _nubank_raws(list(reversed(NU_SALDOS))))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert {p["of_investment_id"]: n for n, p in _pockets(user_id).items()} == nomes


def test_renomear_sobrevive_ao_sync(user_id):
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws(NU_SALDOS[:3]))
    db.sync_open_finance_caixinhas(conn_id, user_id)
    alvo = sorted(_pockets(user_id).items())[0][1]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update pockets set name='Viagem' where id=%s", (alvo["id"],))
        conn.commit()

    res = db.sync_open_finance_caixinhas(conn_id, user_id)
    assert res["caixinhas_created"] == 0           # não recria nem renomeia
    pk = _pockets(user_id)
    assert len(pk) == 3 and "Viagem" in pk
    assert pk["Viagem"]["of_investment_id"] == alvo["of_investment_id"]


def test_baseline_do_banqueiro_nasce_zerada(user_id):
    """Import não pode virar 'você guardou R$ 11.618' no Banqueiro: baseline = saldo."""
    conn_id = _seed_connection(user_id, institution="Nubank")
    _save(conn_id, _nubank_raws())
    db.sync_open_finance_caixinhas(conn_id, user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select balance, of_last_seen_balance from pockets "
                "where user_id=%s and source='open_finance'", (user_id,))
            linhas = cur.fetchall()
    assert len(linhas) == 10
    assert all(float(r["balance"]) - float(r["of_last_seen_balance"]) == 0 for r in linhas)


def test_caixinha_de_um_nao_aparece_pro_outro(user_id):
    from db.users import ensure_user
    outro = user_id + 1
    ensure_user(outro)
    conn_a = _seed_connection(user_id, institution="Nubank")
    conn_b = _seed_connection(outro, institution="Nubank", item="test-cx-item-b")
    _save(conn_a, _nubank_raws(NU_SALDOS[:3]))
    _save(conn_b, _nubank_raws(NU_SALDOS[3:6]))

    db.sync_open_finance_caixinhas(conn_a, user_id)
    db.sync_open_finance_caixinhas(conn_b, outro)

    pk_a = _pockets(user_id)
    assert len(pk_a) == 3
    assert sorted(float(p["balance"]) for p in pk_a.values()) == sorted(NU_SALDOS[:3])
    ids_a = {c["of_investment_id"] for c in db.list_caixinha_candidates(user_id)}
    ids_b = {c["of_investment_id"] for c in db.list_caixinha_candidates(outro)}
    assert ids_a and ids_b and not (ids_a & ids_b)
