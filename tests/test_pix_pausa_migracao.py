"""A migração da pausa da concessão do Pix (#1574): o bloco `INSTALAR_GATILHOS` de `db/pix_pausa.py`.

No banco da `main` (sem nada da pausa), com escrita em voo nas tabelas e com instalação parcial. As
conexões que rodam o bloco sozinho têm `lock_timeout`: um laço que entrasse na fila falha aqui em vez de
travar a suíte.
"""
from __future__ import annotations

import threading
import time

import psycopg
import pytest

import db
from db.connection import get_conn
from db.pix_pausa import GATILHOS, INSTALAR_GATILHOS, ddl
from test_pix_pausa import _sql

TODOS = {g[0]: "O" for g in GATILHOS}


def _gatilhos():
    return {r["tgname"]: r["tgenabled"] for r in _sql(
        "select tgname, tgenabled from pg_trigger where tgname like 'trg_pix_pausa%'")}


def _sem_gatilhos(*nomes):
    for nome, tabela, *_ in GATILHOS:
        if not nomes or nome in nomes:
            _sql(f"drop trigger if exists {nome} on {tabela}")


def _boot_isolado(sql, escrita_em):
    """Roda `sql` com `escrita_em` ocupada por uma escrita aberta (`row exclusive`, o lock do webhook)."""
    with get_conn() as escrita, get_conn() as boot:
        escrita.execute(f"lock table {escrita_em} in row exclusive mode")
        boot.execute("set lock_timeout = '2s'")
        try:
            boot.execute(sql)
        finally:
            boot.rollback()
            escrita.rollback()


def _com_3_tentativas():
    return INSTALAR_GATILHOS.replace("1..160 loop", "1..3 loop").replace("tentativa = 160", "tentativa = 3")


def test_migracao_no_banco_da_main():
    try:
        _sem_gatilhos()
        _sql("drop table if exists pix_pausa_concessao")  # leva junto o gatilho do `delete`
        for funcao in ("pix_pausa_recusa()", "pix_pausa_ativar(text)", "pix_pausa_retem(text)",
                       "pix_pausa_guarda(text, text, integer)", "pix_pausa_ativa()", "pix_pausa_sem_delete()"):
            _sql(f"drop function if exists {funcao}")
        db.init_db()
        assert _gatilhos() == TODOS
        assert _sql("select pix_pausa_ativa() as a")[0]["a"] is False
        assert _sql("select count(*) as n from pg_trigger where tgname like 'trg_pausa_pix_sem_%'")[0]["n"] == 2
    finally:
        db.init_db()


@pytest.mark.parametrize("faltando", [None, "trg_pix_pausa_comissao"], ids=["nenhum", "so_um_faltando"])
def test_tabela_ocupada_alem_do_teto_derruba_o_boot_sem_estado_parcial(faltando):
    """Com os 4 ausentes, ou só um (instalação parcial): o bloco entra no laço `nowait` e, sem a trava, o
    boot cai com o erro dele, sem criar gatilho nenhum."""
    assert "1..160 loop" in INSTALAR_GATILHOS and 160 * 0.05 < 12, "cabe no STARTUP_STEP_TIMEOUT do lifespan"
    try:
        _sem_gatilhos(*([faltando] if faltando else []))
        antes = _gatilhos()
        with pytest.raises(psycopg.errors.LockNotAvailable, match="o boot para aqui"):
            _boot_isolado(_com_3_tentativas(), "affiliate_commissions")
        assert _gatilhos() == antes and len(antes) == (3 if faltando else 0)
    finally:
        db.init_db()


def test_migracao_espera_a_escrita_em_voo_sem_entrar_na_fila():
    """Escrita curta em voo: o laço espera ela terminar e instala os 4."""
    try:
        _sem_gatilhos()
        for stmt in ddl():  # tudo menos os gatilhos, que o laço instala com a escrita em voo
            if stmt is not INSTALAR_GATILHOS:
                _sql(stmt)
        with get_conn() as escrita:
            escrita.execute("lock table pix_charges in row exclusive mode")
            threading.Timer(0.3, escrita.rollback).start()
            inicio = time.monotonic()
            _sql(INSTALAR_GATILHOS)
            assert time.monotonic() - inicio >= 0.25
        assert _gatilhos() == TODOS
    finally:
        db.init_db()


def test_boot_com_os_gatilhos_ja_criados_nao_pede_lock():
    """Sem `drop` + `create` a cada boot: com os 4 no banco, a tabela ocupada não atrasa nem derruba o boot."""
    assert _gatilhos() == TODOS
    _boot_isolado(_com_3_tentativas(), "plan_grants, pix_charges, affiliate_commissions")
