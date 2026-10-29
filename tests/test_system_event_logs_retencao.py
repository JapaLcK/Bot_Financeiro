"""Retenção de `system_event_logs` (core/services/log_retention.py), pelo
`run_table_cleanup()` real: apaga o velho, preserva o recente e os
`EVENTOS_PERMANENTES` (com o leitor de cada um ainda achando a linha), e tira o
texto cru das falhas de remoção na Pluggy, que ficam sem prazo. No fim,
o `apagar_em_lotes` numa tabela própria do teste: `lock_timeout`, `skip locked`,
`order by` e o `set_config` local à transação.

Isolamento: a poda é GLOBAL e roda no database dos outros testes da mesma
execução, então cada linha leva uma chave SÓ DE LETRAS na coluna `source` e
toda asserção filtra por ela, nunca `count(*)` da tabela.

Rodar:
  DATABASE_URL=... PYTHONPATH=. .venv/bin/python -m pytest tests/test_system_event_logs_retencao.py -q
"""
from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from _billing_grants_helpers import garantir_system_event_logs
from core.observability import recent_event_exists
from core.services import log_retention
from core.services.log_retention import (
    RETENCAO_MINIMA_DIAS,
    TEXTO_FALHA_DELETE,
    _dias,
    apagar_em_lotes,
    limpar_texto_das_falhas_de_remocao,
)
from core.services.table_cleanup import run_table_cleanup
from db.connection import get_conn
from db.open_finance_diagnostico import EVENTOS_FALHA_DELETE, itens_com_remocao_remota_falha

_SO_LETRAS = str.maketrans("0123456789", "ghijklmnop")


@pytest.fixture
def chave():
    """Gera chaves só de letras e apaga as linhas delas no fim."""
    garantir_system_event_logs()
    feitas: list[str] = []

    def nova() -> str:
        feitas.append(uuid.uuid4().hex.translate(_SO_LETRAS))
        return feitas[-1]

    yield nova
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from system_event_logs where source = any(%s)", (feitas,))
        conn.commit()


def _inserir(chave: str, rotulo: str, evento: str, dias: int, *,
             user_id: int | None = None, details: dict | None = None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into system_event_logs"
            " (level, event_type, message, source, user_id, details, created_at)"
            " values ('info', %s, %s, %s, %s, %s, now() - make_interval(days => %s))",
            (evento, rotulo, chave, user_id, Jsonb(details or {}), dias),
        )
        conn.commit()


def _sobra(chave: str) -> dict[str, dict]:
    """rótulo (coluna `message`) -> `details` das linhas que sobraram."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select message, details from system_event_logs where source = %s", (chave,))
        return {r["message"]: r["details"] for r in cur.fetchall()}


def test_evento_comum_cai_aos_91_dias_e_fica_aos_89(chave):
    k = chave()
    _inserir(k, "velho", "whatsapp_status_error", 91)
    _inserir(k, "recente", "whatsapp_status_error", 89)

    resumo = run_table_cleanup()

    assert resumo["errors"] == []
    assert set(_sobra(k)) == {"recente"}


def test_founder_email_sent_fica_e_o_dedup_continua_achando(chave, user_id):
    k = chave()
    _inserir(k, "d91", "founder_email_sent", 91, user_id=user_id)
    _inserir(k, "d400", "founder_email_sent", 400, user_id=user_id)

    run_table_cleanup()

    assert set(_sobra(k)) == {"d91", "d400"}
    assert recent_event_exists("founder_email_sent", user_id, 3650) is True


@pytest.mark.parametrize("evento", [
    # Decisão A (2026-10-27): rastro de dinheiro de cliente, consultado sem janela
    # pelo runbook do Pix em `docs/CLAUDE.md` (fora do alcance da guarda AST).
    "pix_pagamento_parcial_retido", "pix_excesso_recebido", "pix_retido_completado",
])
def test_rastro_do_pix_fica(chave, user_id, evento):
    k = chave()
    _inserir(k, "pix", evento, 400, user_id=user_id)

    run_table_cleanup()

    assert set(_sobra(k)) == {"pix"}


@pytest.mark.parametrize("evento", EVENTOS_FALHA_DELETE)
def test_falha_de_remocao_na_pluggy_fica_e_o_diagnostico_ainda_ve(chave, evento):
    k = chave()
    item = chave()  # também só letras: é o `item_id` lido pelo diagnóstico
    _inserir(k, "falha", evento, 91, details={"item_id": item})

    run_table_cleanup()

    assert len(_sobra(k)) == 1  # o texto (`message`) é reescrito pela limpeza; a linha fica
    assert item in itens_com_remocao_remota_falha()


_CORPO = 'HTTP 400: {"name": "Fulano de Tal", "cpf": "12345678900"}'


def _cru(chave: str, evento: str, message: str, details: dict, dias: int = 1) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into system_event_logs (level, event_type, message, source, details, created_at)"
            " values ('warning', %s, %s, %s, %s, now() - make_interval(days => %s)) returning id",
            (evento, message, chave, Jsonb(details), dias))
        conn.commit()
        return cur.fetchone()["id"]


def _linha(i: int) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        return dict(cur.execute("select message, details from system_event_logs where id = %s",
                                (i,)).fetchone())


def test_falha_de_remocao_perde_o_texto_cru_e_o_diagnostico_ainda_ve(chave, user_id):
    k, item, item2 = chave(), chave(), chave()
    # Formato de 4de19859..bad3a22b: corpo cru da Pluggy no texto (R2-1).
    antigo = _cru(k, "pluggy_item_delete_failed",
                  f"Falha ao deletar item {item} na Pluggy no disconnect: {_CORPO}",
                  {"item_id": item, "error": _CORPO})
    # Formato de 35b5c733..d027ec3d: uid no texto e em `details`, coluna nula (R2-2).
    com_uid = _cru(k, "pluggy_disconnect_auth_failed",
                   f"Sem apiKey pra deletar items no disconnect do user {user_id}: boom",
                   {"user_id": user_id, "error": "boom", "items": [item2],
                    "motivo": "PluggyApiError", "sqlstate": None, "status_code": 401})
    comum = _cru(k, "whatsapp_status_error", f"erro {_CORPO}", {"item_id": item, "error": _CORPO})
    recente = _cru(k, "pluggy_item_delete_failed", f"x {_CORPO}", {"item_id": item2, "error": "y"}, 0)
    intocada = _linha(comum)

    assert run_table_cleanup()["errors"] == []

    a, b = _linha(antigo), _linha(com_uid)
    for linha in (a, b):
        for proibido in ("Fulano", "12345678900", str(user_id), "boom"):
            assert proibido not in json.dumps(linha), (proibido, linha)
    assert a == {"message": TEXTO_FALHA_DELETE["pluggy_item_delete_failed"],
                 "details": {"item_id": item}}
    assert b["details"] == {"items": [item2], "motivo": "PluggyApiError",
                            "sqlstate": None, "status_code": 401}
    assert {item, item2} <= set(itens_com_remocao_remota_falha())
    assert _linha(comum) == intocada  # evento comum: intocado
    assert _linha(recente) == {"message": TEXTO_FALHA_DELETE["pluggy_item_delete_failed"],
                               "details": {"item_id": item2}}  # nova também é limpa

    def xmin():  # muda a cada UPDATE, mesmo com o mesmo valor
        with get_conn() as conn:
            return conn.execute("select array_agg(xmin::text order by id) as x from"
                                " system_event_logs where source = %s", (k,)).fetchone()["x"]
    antes = xmin()
    limpar_texto_das_falhas_de_remocao()  # 2ª volta: não reescreve nada
    assert xmin() == antes and (_linha(antigo), _linha(com_uid)) == (a, b)


def test_dry_run_nao_limpa_o_texto(chave):
    i = _cru(chave(), "account_deletion_pluggy_cleanup_failed", f"x {_CORPO}", {"error": _CORPO})
    antes = _linha(i)
    run_table_cleanup(dry_run=True)
    assert _linha(i) == antes


@pytest.mark.parametrize("valor, sobra", [
    ("0", {"d31", "d89", "d91"}),     # <= 0 desliga
    ("-5", {"d31", "d89", "d91"}),
    ("abc", {"d31", "d89"}),          # inválido cai no 90
    ("30", {"d31", "d89"}),           # 1..89 sobe para o piso de 90
    ("120", {"d31", "d89", "d91"}),   # acima do piso vale o valor
])
def test_env_da_retencao(chave, monkeypatch, valor, sobra):
    monkeypatch.setenv("SYSTEM_EVENT_LOGS_RETENTION_DAYS", valor)
    k = chave()
    for dias in (31, 89, 91):
        _inserir(k, f"d{dias}", "whatsapp_status_error", dias)

    run_table_cleanup()

    assert set(_sobra(k)) == sobra


def test_dry_run_nao_apaga(chave):
    k = chave()
    _inserir(k, "velho", "whatsapp_status_error", 91)

    run_table_cleanup(dry_run=True)

    assert set(_sobra(k)) == {"velho"}


def test_teto_de_lotes_para_e_a_proxima_volta_continua(chave):
    """Direto no helper: pelo `run_table_cleanup` o lote pegaria linhas velhas
    de outros testes (a poda é global) e a conta deixaria de ser exata."""
    k = chave()
    for i in range(5):
        _inserir(k, f"l{i}", "whatsapp_status_error", 200)

    assert apagar_em_lotes("system_event_logs", "source = %s", (k,), lote=2, teto_lotes=2) == 4
    assert len(_sobra(k)) == 1
    assert apagar_em_lotes("system_event_logs", "source = %s", (k,), lote=2, teto_lotes=2) == 1
    assert _sobra(k) == {}


@pytest.mark.parametrize("valor, esperado, avisa", [
    ("365.0", 90, True), ("1e3", 90, True), ("", 90, True), ("30", 90, True),
    (" 120 ", 120, False), ("0", 0, False),
])
def test_dias_cai_no_90_com_aviso(monkeypatch, capsys, valor, esperado, avisa):
    monkeypatch.setenv("SYSTEM_EVENT_LOGS_RETENTION_DAYS", valor)
    assert _dias("SYSTEM_EVENT_LOGS_RETENTION_DAYS", RETENCAO_MINIMA_DIAS) == esperado
    assert ("SYSTEM_EVENT_LOGS_RETENTION_DAYS" in capsys.readouterr().err) is avisa


# ── apagar_em_lotes numa tabela do teste: os testes abaixo apagam com `onde="true"`
# e criam trigger, o que em `system_event_logs` levaria as linhas dos outros testes.

@pytest.fixture
def tabela():
    nome = "retencao_teste_" + uuid.uuid4().hex.translate(_SO_LETRAS)
    with get_conn() as conn:
        conn.execute(f"create table {nome} (id bigserial primary key,"
                     " created_at timestamptz not null, rotulo text)")
        conn.commit()
    yield nome
    with get_conn() as conn:
        conn.execute(f"drop table {nome}")
        conn.commit()


@pytest.fixture
def conexao_unica(monkeypatch):
    """`get_conn` do helper devolve sempre ESTA conexão (fora do pool), com
    `statement_timeout` de 5 s: sem `lock_timeout` o teste falha, não trava."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row,
                           options="-c statement_timeout=5000")

    @contextmanager
    def mesma():
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise

    monkeypatch.setattr(log_retention, "get_conn", mesma)
    yield conn
    conn.close()


def _linhas(tabela: str, *idades: int) -> list[int]:
    with get_conn() as conn, conn.cursor() as cur:
        ids = [cur.execute(f"insert into {tabela} (created_at, rotulo) values"
                           " (now() - make_interval(days => %s), %s) returning id",
                           (d, f"d{d}")).fetchone()["id"] for d in idades]
        conn.commit()
    return ids


def _rotulos(tabela: str) -> set[str]:
    with get_conn() as conn, conn.cursor() as cur:
        return {r["rotulo"] for r in cur.execute(f"select rotulo from {tabela}").fetchall()}


def test_lock_timeout_desiste_e_o_parcial_sai_em_stderr(tabela, conexao_unica, capsys):
    _linhas(tabela, 200)
    trava = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        trava.execute(f"lock table {tabela} in access exclusive mode")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            apagar_em_lotes(tabela, "true", ())
    finally:
        trava.close()
    assert "0 linha(s) apagada(s) antes da falha" in capsys.readouterr().err


def test_parcial_apagado_antes_da_falha_sai_em_stderr(tabela, capsys):
    _linhas(tabela, 300, 200, 100)
    with get_conn() as conn:
        conn.execute(f"""
            create function {tabela}_bomba() returns trigger language plpgsql as $$
            begin if old.rotulo = 'd200' then raise exception 'bomba'; end if; return old; end $$;
            create trigger bomba before delete on {tabela}
            for each row execute function {tabela}_bomba()""")
        conn.commit()
    try:
        with pytest.raises(psycopg.errors.RaiseException):
            apagar_em_lotes(tabela, "true", (), lote=1)
        assert "1 linha(s) apagada(s) antes da falha" in capsys.readouterr().err
        assert _rotulos(tabela) == {"d200", "d100"}
    finally:
        with get_conn() as conn:
            conn.execute(f"drop function {tabela}_bomba() cascade")
            conn.commit()


def test_lock_timeout_nao_vaza_para_a_conexao(tabela, conexao_unica):
    antes = conexao_unica.execute("show lock_timeout").fetchone()["lock_timeout"]
    _linhas(tabela, 200)
    assert apagar_em_lotes(tabela, "true", ()) == 1
    assert conexao_unica.execute("show lock_timeout").fetchone()["lock_timeout"] == antes


def test_skip_locked_pula_a_linha_travada(tabela, conexao_unica):
    travada, _ = _linhas(tabela, 300, 200)
    outra = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        outra.execute(f"select 1 from {tabela} where id = %s for update", (travada,))
        assert apagar_em_lotes(tabela, "true", ()) == 1
    finally:
        outra.close()
    assert _rotulos(tabela) == {"d300"}


def test_lote_leva_as_mais_velhas_primeiro(tabela):
    _linhas(tabela, 100, 200, 300)  # inseridas da mais nova para a mais velha
    assert apagar_em_lotes(tabela, "true", (), lote=2, teto_lotes=1) == 2
    assert _rotulos(tabela) == {"d100"}
