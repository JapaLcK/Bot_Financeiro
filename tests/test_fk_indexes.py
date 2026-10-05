"""Índices das FKs que apontam para launches/credit_transactions/caixinhas/investimentos (#253).

Sem índice na coluna-filha, cada linha-pai apagada varre a tabela-filha inteira.
`ensure_fk_indexes` os cria com CONCURRENTLY, numa tarefa de fundo do `lifespan`
(`ensure_fk_indexes_once`) — NÃO no `init_db`, que roda sob o `wait_for` de `STARTUP_STEP_TIMEOUT` do
startup. O banco de teste os ganha no `_init_schema` do conftest.
"""
import os
import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row

import db
from _lifespan_probe import sondar
from db.schema_repairs import _FK_INDEXES, ensure_fk_indexes, ensure_fk_indexes_once

_AUDITORIA = """
    select c.conrelid::regclass::text || '.' || a.attname as coluna, c.confrelid::regclass::text as pai
      from pg_constraint c
      join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
     where c.contype = 'f'
       and c.confrelid in ('launches'::regclass, 'credit_transactions'::regclass,
                           'pockets'::regclass, 'investments'::regclass)
       and not exists (select 1 from pg_index i
                        where i.indrelid = c.conrelid and i.indkey[0] = a.attnum
                          and i.indisvalid and i.indisready)
     order by 1
"""


@pytest.fixture()
def cur():
    """AUTOCOMMIT, o modo do `_run_ddl` (`create index concurrently` não roda em transação)."""
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as conn:
        yield conn.cursor()


def _nome(tabela, coluna):
    return f"idx_{tabela}_{coluna}"


def _estado(cur, nome):
    cur.execute("select c.relfilenode, i.indisvalid from pg_class c join pg_index i on i.indexrelid = c.oid "
                "where c.oid = to_regclass(%s)", (nome,))
    return cur.fetchone()


def test_toda_fk_para_os_pais_pesados_tem_indice_valido(cur):
    """AUDITORIA DE CLASSE: FK nova sem índice reprova aqui, esteja ou não em `_FK_INDEXES`."""
    cur.execute(_AUDITORIA)
    assert cur.fetchall() == []


def test_indice_invalido_e_reconstruido(cur):
    """Marca o índice como inválido em `pg_index`: exige SUPERUSUÁRIO (o CI usa `postgres`;
    a skill baseline-testes só pede CREATEDB, e aí o teste é pulado, não enfraquecido)."""
    cur.execute("select rolsuper from pg_roles where rolname = current_user")
    if not cur.fetchone()["rolsuper"]:
        pytest.skip("precisa de superusuário para update em pg_index")
    tabela, coluna, _ = _FK_INDEXES[0]
    nome = _nome(tabela, coluna)
    ensure_fk_indexes(cur)
    antes = _estado(cur, nome)
    cur.execute("update pg_index set indisvalid = false where indexrelid = to_regclass(%s)", (nome,))
    assert _estado(cur, nome)["indisvalid"] is False

    assert ensure_fk_indexes(cur) == []

    depois = _estado(cur, nome)
    assert depois["indisvalid"] is True and depois["relfilenode"] != antes["relfilenode"]
    cur.execute(_AUDITORIA)
    assert cur.fetchall() == []


def test_indice_valido_nao_e_reconstruido(cur):
    ensure_fk_indexes(cur)
    antes = {t: _estado(cur, _nome(t, c)) for t, c, _ in _FK_INDEXES}
    assert ensure_fk_indexes(cur) == []
    assert {t: _estado(cur, _nome(t, c)) for t, c, _ in _FK_INDEXES} == antes


def _insere_durante(construtor, tabela="recurring_charges"):
    """A segura RowExclusive na tabela (o que um INSERT pega) com a transação aberta; `construtor`
    roda numa thread; B tenta o mesmo com lock_timeout curto. Devolve a exceção de B, ou None."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=False) as a, psycopg.connect(os.environ["DATABASE_URL"], autocommit=False) as b:
        a.execute(f"lock table {tabela} in row exclusive mode")
        t = threading.Thread(target=construtor)
        t.start()
        try:
            for _ in range(100):  # o construtor chegou na espera (por A) ou já terminou
                esperando = a.execute("select count(*) from pg_stat_activity where datname = current_database() "
                                      "and wait_event_type = 'Lock' and pid <> pg_backend_pid()").fetchone()[0]
                if esperando or not t.is_alive():
                    break
                time.sleep(0.05)
            b.execute("set local lock_timeout = '500ms'")
            try:
                b.execute(f"lock table {tabela} in row exclusive mode")
                return None
            except psycopg.errors.LockNotAvailable as exc:
                return exc
        finally:  # o CONCURRENTLY também espera a transação de B terminar
            a.rollback()
            b.rollback()
            t.join(20)


def test_concurrently_nao_bloqueia_insert_na_tabela_filha(cur):
    tabela, coluna, _ = next(x for x in _FK_INDEXES if x[0] == "recurring_charges")
    cur.execute(f"drop index if exists {_nome(tabela, coluna)}")
    resultado: list = []

    def constroi():
        with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=True) as c:
            resultado.append(ensure_fk_indexes(c.cursor()))
    assert _insere_durante(constroi) is None
    assert resultado == [[]] and _estado(cur, _nome(tabela, coluna))["indisvalid"] is True


def test_create_index_comum_bloquearia_o_insert(cur):
    """Controle: sem o CONCURRENTLY o mesmo cenário trava B. Sem este caso o teste acima
    passaria também numa implementação que não usa CONCURRENTLY."""
    def comum():
        with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as c:
            c.execute("create index idx_tmp_fk_indexes on recurring_charges (launch_id)")
    try:
        assert isinstance(_insere_durante(comum), psycopg.errors.LockNotAvailable)
    finally:
        cur.execute("drop index if exists idx_tmp_fk_indexes")


def test_init_db_nao_cria_os_indices(cur):
    """Se voltassem para o `init_db`, um writer em voo seguraria o boot além do `wait_for`."""
    tabela, coluna, _ = _FK_INDEXES[0]
    cur.execute(f"drop index if exists {_nome(tabela, coluna)}")
    db.init_db()
    assert _estado(cur, _nome(tabela, coluna)) is None
    assert ensure_fk_indexes(cur) == []


def test_startup_do_app_agenda_os_indices():
    """O processo web de verdade chama a função (a que existe e ninguém chama não protege nada)."""
    resultado, diagnostico = sondar({"fk": "db.schema_repairs:ensure_fk_indexes_once"})
    assert resultado == {"fk": True, "do_disco": []}, diagnostico


def test_writer_em_voo_deixa_a_tarefa_pendente_sem_prender_o_resto(cur):
    """O CONCURRENTLY espera o writer; quem chamou é a tarefa de fundo, e o pool segue
    atendendo. Um segundo container (outro processo) vê o lock e não duplica o trabalho."""
    tabela, coluna, _ = next(x for x in _FK_INDEXES if x[0] == "recurring_charges")
    cur.execute(f"drop index if exists {_nome(tabela, coluna)}")
    resultado: list = []
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=False) as writer:
        writer.execute(f"lock table {tabela} in row exclusive mode")
        t = threading.Thread(target=lambda: resultado.append(ensure_fk_indexes_once()))
        t.start()
        try:
            for _ in range(100):  # espera ela chegar na espera pelo writer
                if writer.execute("select count(*) from pg_stat_activity where datname = current_database() "
                                  "and wait_event_type = 'Lock'").fetchone()[0]:
                    break
                time.sleep(0.05)
            assert t.is_alive() and resultado == []
            with db.get_conn() as conn, conn.cursor() as c2:  # o app segue respondendo
                c2.execute("select 1 as ok")
                assert c2.fetchone()["ok"] == 1
            assert ensure_fk_indexes_once() is None  # outro processo: o lock de sessão está tomado
        finally:
            writer.rollback()
            t.join(20)
    assert resultado == [[]] and _estado(cur, _nome(tabela, coluna))["indisvalid"] is True


def test_falha_vira_warning_com_sqlstate_e_sem_texto_da_excecao(caplog):
    """Fora do autocommit o CONCURRENTLY recusa (25001): o aviso traz só índice e sqlstate."""
    import logging
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        with caplog.at_level(logging.WARNING, logger="db.schema_repairs"):
            falhou = ensure_fk_indexes(conn.cursor())
    msgs = [r.getMessage() for r in caplog.records if "#253" in r.getMessage()]
    assert len(falhou) == len(_FK_INDEXES) == len(msgs)
    assert "sqlstate=25001" in msgs[0] and f"idx_{_FK_INDEXES[0][0]}_{_FK_INDEXES[0][1]}" in msgs[0]
    assert not any("transaction block" in m for m in msgs)


def test_tarefa_do_startup_nao_derruba_o_app_e_loga_so_tipo_e_sqlstate(monkeypatch, caplog):
    import asyncio
    import logging

    import db.schema_repairs as reparos
    import frontend.finance_bot_websocket_custom as app

    def deadlock():
        raise psycopg.errors.DeadlockDetected("segredo 77,50")
    monkeypatch.setattr(reparos, "ensure_fk_indexes_once", deadlock)
    with caplog.at_level(logging.INFO, logger=app.__name__):
        asyncio.run(app._tarefa_fk_indexes())  # não levanta
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING and "[fk_indexes]" in r.getMessage()]
    assert [r.getMessage() for r in avisos] == ["[fk_indexes] erro: DeadlockDetected sqlstate=40P01"]
    assert "segredo" not in caplog.text

    # POSITIVO: outro processo construindo (None) e sucesso são INFO, não aviso.
    for retorno, trecho in ((None, "outro processo"), ([], "índices não criados: nenhum")):
        caplog.clear()
        monkeypatch.setattr(reparos, "ensure_fk_indexes_once", lambda r=retorno: r)
        with caplog.at_level(logging.INFO, logger=app.__name__):
            asyncio.run(app._tarefa_fk_indexes())
        assert [(r.levelno, trecho in r.getMessage()) for r in caplog.records
                if "[fk_indexes]" in r.getMessage()] == [(logging.INFO, True)]
