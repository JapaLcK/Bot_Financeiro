"""#596: nome de investimento e caixinha é único sem diferenciar maiúscula.

`unique(user_id, name)` deixava "cdb" e "CDB" coexistirem, e todo o resto do código
resolve por `lower(name)=lower(%s) ... fetchone()` — linha arbitrária. O conserto é o
índice `(user_id, lower(name))` criado no boot (`db/schema_repairs.py`).
"""
import os
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

import db
from conftest import promote_to_pro
from db.schema_repairs import ensure_lower_name_unique
from tests.test_investimento_juro_e_desfazer import _estado, _recusa


def _invs(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id, name, balance from investments where user_id=%s", (uid,))
        return [tuple(r.values()) for r in cur.fetchall()]


def _n_launches(uid):
    return _estado(uid)[3]


# ── A: criar com outra caixa resolve para o existente ─────────────────────────

def test_criar_investimento_com_outra_caixa_resolve_para_o_existente(user_id):
    _, inv_id, _ = db.create_investment_db(user_id, "cdb", rate=0.10, period="yearly")
    n = _n_launches(user_id)
    assert db.create_investment_db(user_id, "CDB", rate=0.20, period="yearly") == (None, inv_id, "cdb")
    assert [i[:2] for i in _invs(user_id)] == [(inv_id, "cdb")]
    assert _n_launches(user_id) == n


def test_criar_investimento_pela_ia_com_outra_caixa_resolve_para_o_existente(user_id):
    db.create_investment(user_id, "cdb", 0.10, "yearly")
    n = _n_launches(user_id)
    assert db.create_investment(user_id, "CDB", 0.20, "yearly") == (None, "cdb")
    assert [i[1] for i in _invs(user_id)] == ["cdb"]
    assert _n_launches(user_id) == n


def test_criar_caixinha_com_outra_caixa_resolve_para_a_existente(user_id):
    promote_to_pro(user_id)
    _, pid, _ = db.create_pocket(user_id, "Viagem")
    assert db.create_pocket(user_id, "viagem") == (None, pid, "Viagem")
    assert [p["name"] for p in db.list_pockets(user_id, accrue=False)] == ["Viagem"]


# ── B: desfazer o apagar quando o nome foi recriado em outra caixa ────────────

def test_desfazer_apagar_com_recriacao_em_outra_caixa_e_recusado(user_id):
    """Guarda por `lower()`: com igualdade exata o desfazer passava, o `on conflict
    do nothing` pulava a recriação e o launch do apagar sumia."""
    db.create_investment_db(user_id, "CDB", rate=0.10, period="yearly")
    d, _ = db.delete_investment(user_id, "CDB")
    db.create_investment_db(user_id, "cdb", rate=0.20, period="yearly")
    _recusa(user_id, d)


# ── C: a migração ────────────────────────────────────────────────────────────

@pytest.fixture()
def cur_sombra():
    """Conexão em AUTOCOMMIT — o modo do `_run_ddl` em produção — com
    `investments` e `pockets` TEMPORÁRIAS: `pg_temp` vem primeiro no search_path,
    então a função roda o SQL de verdade sem tocar nas tabelas reais do database
    da sessão (onde outros testes do worker precisam do índice)."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True)
    cur = conn.cursor()
    for t in ("investments", "pockets"):
        cur.execute(f"create temp table {t} (user_id bigint, name text, balance numeric)")
    yield cur
    conn.close()


def _indices_sombra(cur):
    cur.execute("select indexname from pg_indexes where schemaname = pg_my_temp_schema()::regnamespace::text")
    return sorted(r["indexname"] for r in cur.fetchall())


def test_migracao_com_duplicata_pula_so_a_tabela_e_nao_mexe_em_dado(cur_sombra):
    cur = cur_sombra
    cur.execute("insert into investments values (1,'cdb',10), (1,'CDB',5), (2,'cdb',7)")
    cur.execute("select name, balance from investments order by name, balance")
    antes = cur.fetchall()

    assert ensure_lower_name_unique(cur) == ["investments"]

    cur.execute("select name, balance from investments order by name, balance")
    assert cur.fetchall() == antes
    # A falha ficou no statement: a conexão segue e a caixinha ganhou o índice.
    assert _indices_sombra(cur) == ["uq_pockets_user_lower_name"]


def test_migracao_com_duplicata_avisa_em_system_event_logs(cur_sombra, caplog):
    """O aviso é WARNING (não `print`): o `_DashboardHandler` do root o grava em
    `system_event_logs`. Só o nome da tabela — nada de nome nem `user_id`."""
    import asyncio
    import logging

    import core.observability as observability
    from core.admin_dashboard import ensure_admin_tables

    asyncio.run(ensure_admin_tables())
    cur_sombra.execute("insert into investments values (7,'cdb',10), (7,'CDB',5)")
    msg = ("[schema_repairs] AVISO #596: investments tem nome duplicado por maiúscula; "
           "índice uq_investments_user_lower_name NÃO criado")
    handler = observability._DashboardHandler()
    logging.getLogger().addHandler(handler)
    try:
        with caplog.at_level(logging.WARNING):
            assert ensure_lower_name_unique(cur_sombra) == ["investments"]
    finally:
        logging.getLogger().removeHandler(handler)

    assert [(r.levelname, r.getMessage()) for r in caplog.records
            if "#596" in r.getMessage()] == [("WARNING", msg)]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from system_event_logs where message=%s "
                    "returning level, source, user_id", (msg,))
        linhas = cur.fetchall()
        conn.commit()
    assert linhas and all(tuple(r.values()) == ("warning", "db.schema_repairs", None)
                          for r in linhas), linhas


def test_migracao_sem_duplicata_cria_os_dois_e_e_idempotente(cur_sombra):
    cur = cur_sombra
    cur.execute("insert into investments values (1,'cdb',10), (2,'CDB',5)")
    assert ensure_lower_name_unique(cur) == []
    assert ensure_lower_name_unique(cur) == []
    assert _indices_sombra(cur) == ["uq_investments_user_lower_name", "uq_pockets_user_lower_name"]


def test_schema_da_sessao_tem_o_indice_novo_e_o_unique_antigo():
    """O unique antigo FICA: o container velho do deploy usa
    `on conflict (user_id, name)`, e sem ele esse insert estoura."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select indexname from pg_indexes where schemaname='public' "
                    "and indexname like 'uq_%%_user_lower_name'")
        assert sorted(r["indexname"] for r in cur.fetchall()) == [
            "uq_investments_user_lower_name", "uq_pockets_user_lower_name"]
        cur.execute("select conname from pg_constraint where contype='u' "
                    "and conrelid in ('investments'::regclass, 'pockets'::regclass)")
        assert sorted(r["conname"] for r in cur.fetchall()) == [
            "investments_user_id_name_key", "pockets_user_id_name_key"]


# ── Renomear caixinha para nome ocupado ──────────────────────────────────────

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


def test_renomear_caixinha_para_nome_ocupado_em_outra_caixa_da_400(user_id):
    promote_to_pro(user_id)
    db.create_pocket(user_id, "Viagem")
    _, pid, _ = db.create_pocket(user_id, "Reserva")
    resp = _renomeia(user_id, pid, "viagem")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Já existe uma caixinha com esse nome."
    assert sorted(p["name"] for p in db.list_pockets(user_id, accrue=False)) == ["Reserva", "Viagem"]


def test_renomear_caixinha_para_nome_livre_ou_so_trocando_a_caixa_da_200(user_id):
    """Positivo: a recusa é só para nome de OUTRA caixinha."""
    promote_to_pro(user_id)
    db.create_pocket(user_id, "Viagem")
    _, pid, _ = db.create_pocket(user_id, "Reserva")
    assert _renomeia(user_id, pid, "Carro").status_code == 200
    assert _renomeia(user_id, pid, "CARRO").status_code == 200
    assert sorted(p["name"] for p in db.list_pockets(user_id, accrue=False)) == ["CARRO", "Viagem"]


# ── D: a conversa, pelo `handle_incoming` ─────────────────────────────────────

def test_conversa_aporte_em_CDB_cai_no_cdb_depois_de_criar_com_outra_caixa():
    """O "CDB" criado pelo painel depois do "cdb" não vira segunda linha, então o
    aporte digitado "CDB" no WhatsApp não tem linha para errar.

    Quem discrimina aqui é o seed `create_investment_db(uid, "CDB")`: sem o índice
    ele cria a segunda linha e o `_invs` fica vermelho. A conversa não prova nada
    sobre a caixa — o WhatsApp baixa o texto para minúsculas antes de extrair o
    nome (`parse_investment_deposit_natural` faz `text.lower()`; criar caixinha e
    investimento extraem de `_normalize`). Ela prova que o aporte cai na linha
    única e a Carteira bate depois de outro assunto no meio. A porta que preserva
    a caixa é o painel: bloco F."""
    from conftest import usuario_pagante
    from tests.test_pending_rollback import _diga

    uid = usuario_pagante()
    db.add_launch_and_update_balance(uid, "receita", 1000, None, "seed")
    db.create_investment_db(uid, "cdb", rate=0.10, period="yearly")
    assert "mercado" in _diga(uid, "gastei 50 no mercado").lower()
    db.create_investment_db(uid, "CDB", rate=0.10, period="yearly")
    resp = _diga(uid, "investi 100 em CDB")

    assert [i[1:] for i in _invs(uid)] == [("cdb", Decimal("100"))], resp
    assert db.get_balance(uid) == Decimal("850"), resp


# ── E: a ferramenta da IA diz "já existe", com o nome canônico ────────────────

def test_ia_criar_investimento_com_outra_caixa_diz_que_ja_existe(user_id):
    from core.services.ai_chat.tools.investments import _create_investment_execute

    args = {"rate": 10, "period": "yearly"}
    assert _create_investment_execute(user_id, {"name": "cdb", **args}) == '✅ Investimento "cdb" criado.'
    n = _n_launches(user_id)
    assert _create_investment_execute(user_id, {"name": "CDB", **args}) == 'ℹ️ O investimento "cdb" já existe.'
    assert [i[1] for i in _invs(user_id)] == ["cdb"]
    assert _n_launches(user_id) == n


def test_ia_criar_caixinha_com_outra_caixa_diz_que_ja_existe(user_id):
    from core.services.ai_chat.tools.pockets import _create_pocket_execute

    promote_to_pro(user_id)
    assert _create_pocket_execute(user_id, {"name": "Viagem"}) == '✅ Caixinha "Viagem" criada.'
    n = _n_launches(user_id)
    assert _create_pocket_execute(user_id, {"name": "viagem"}) == 'ℹ️ A caixinha "Viagem" já existe.'
    assert [p["name"] for p in db.list_pockets(user_id, accrue=False)] == ["Viagem"]
    assert _n_launches(user_id) == n


# ── F: o painel manda o nome como digitado — é a porta que discrimina a caixa ──

def _post(uid, path, body):
    from tests.test_pockets_endpoints import _auth, _csrf_headers
    import frontend.finance_bot_websocket_custom as dashboard

    client = TestClient(dashboard.app)
    _auth(client, uid)
    return client.post(path, json=body, headers=_csrf_headers(client))


def _pockets(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from pockets where user_id=%s order by id", (uid,))
        return cur.fetchall()


_JA_EXISTE_CAIXINHA = (400, "Já existe uma caixinha com esse nome.")


@pytest.mark.parametrize("repetido", ["viagem", "Viagem"])
def test_painel_criar_caixinha_que_ja_existe_recusa_sem_mexer_em_nada(user_id, repetido):
    """Já existe (exato ou com outra caixa) = 400 e nada muda. O 200 `created:false`
    levava a tela de Metas a reescrever a caixinha que já existia."""
    promote_to_pro(user_id)
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")
    r1 = _post(user_id, f"/pockets/{user_id}",
               {"name": "viagem", "description": "praia", "interest_rate": 1.1})
    assert r1.status_code == 200 and r1.json()["created"] is True, r1.text
    db.pocket_deposit_from_account(user_id, "viagem", 50, None)
    antes, n = _pockets(user_id), _n_launches(user_id)

    r2 = _post(user_id, f"/pockets/{user_id}", {"name": repetido, "description": "outra",
                                                 "interest_enabled": False})
    assert (r2.status_code, r2.json().get("detail")) == _JA_EXISTE_CAIXINHA, r2.text
    assert _pockets(user_id) == antes and _n_launches(user_id) == n


def test_tela_de_metas_com_nome_que_ja_existe_nao_reescreve_a_caixinha(user_id):
    """O fluxo do `dashboard.js` (saveGoal): POST cria, e só se `ok` faz o PATCH da
    meta no `pocket.id` devolvido. Com o 200 `created:false`, o PATCH caía na
    caixinha que já existia."""
    promote_to_pro(user_id)

    def criar_meta(nome, meta, taxa):
        r = _post(user_id, f"/pockets/{user_id}", {"name": nome, "interest_rate": taxa})
        if r.status_code == 200:
            from tests.test_pockets_endpoints import _auth, _csrf_headers
            import frontend.finance_bot_websocket_custom as dashboard
            client = TestClient(dashboard.app)
            _auth(client, user_id)
            client.patch(f"/pockets/{user_id}/{r.json()['pocket']['id']}/meta",
                         json={"target_amount": meta, "interest_rate": taxa},
                         headers=_csrf_headers(client))
        return r

    assert criar_meta("viagem", 1000, 1.1).status_code == 200
    antes = _pockets(user_id)
    assert antes[0]["target_amount"] == Decimal("1000")

    r = criar_meta("Viagem", 5000, 1.3)
    assert (r.status_code, r.json().get("detail")) == _JA_EXISTE_CAIXINHA, r.text
    assert _pockets(user_id) == antes


def test_painel_criar_caixinha_com_nome_novo_da_200(user_id):
    promote_to_pro(user_id)
    assert _post(user_id, f"/pockets/{user_id}", {"name": "viagem"}).status_code == 200
    r = _post(user_id, f"/pockets/{user_id}", {"name": "carro"})
    assert r.status_code == 200 and r.json()["created"] is True, r.text
    assert [p["name"] for p in _pockets(user_id)] == ["viagem", "carro"]


@pytest.mark.parametrize("repetido", ["CDB", "cdb"])
def test_painel_criar_investimento_que_ja_existe_recusa_sem_mexer_em_nada(user_id, repetido):
    """Já existe (exato ou com outra caixa) = 400 e nada muda. O 200 `created:false`
    descartava o aporte inicial em silêncio e a tela dizia "✓ Investimento criado"."""
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    body = {"name": "CDB", "rate": 0.10, "period": "yearly", "initial_amount": 100}
    r1 = _post(user_id, f"/investments/{user_id}", body)
    assert r1.status_code == 200 and r1.json()["created"] is True, r1.text
    antes = _estado(user_id)
    assert antes[0] == Decimal("900") and antes[2] == [("CDB", Decimal("100"))], antes

    r2 = _post(user_id, f"/investments/{user_id}", body | {"name": repetido, "initial_amount": 200})
    assert (r2.status_code, r2.json().get("detail")) == (
        400, "Já existe um investimento com esse nome. Para colocar dinheiro nele, use Aportar."), r2.text
    assert _estado(user_id) == antes
