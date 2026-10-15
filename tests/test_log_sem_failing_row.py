"""O DETAIL `Failing row contains (...)` do Postgres (linha inteira do usuário) não chega a log nenhum.

O filtro mora nos dois handlers do root (core/observability.py), então vale para qualquer
`logger.*(..., exc)` presente ou futuro, sem cada site lembrar de usar `texto_seguro`.
"""
import logging

import psycopg
import pytest

from conftest import usuario_pagante
from core import observability as O
from db import get_conn


def _violacao():
    """CheckViolation real do banco (accounts.balance acima do limite)."""
    uid = usuario_pagante()
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("update accounts set balance = 1e20 where user_id = %s", (uid,))
            cur.execute("insert into accounts (user_id, balance) values (%s, 1e20) on conflict (user_id) do update set balance = 1e20", (uid,))
    except psycopg.errors.CheckViolation as exc:
        return exc
    raise AssertionError("o banco aceitou 1e20: o CHECK finito não está instalado")


def _cru(exc):
    """Texto CRU do Postgres (com a linha do usuário): `str(exc)` já sai redigido (db/connection.py),
    mas o filtro e os gravadores protegem também o que não passou por ele (outro tipo de exceção,
    texto montado à mão, conexão fora do módulo)."""
    return exc.args[0]


def _registro(exc, com_traceback):
    import sys
    cru = _cru(exc)
    try:
        raise RuntimeError(cru)
    except RuntimeError:
        return logging.LogRecord("t", logging.ERROR, __file__, 1, "falhou: %s", (cru,),
                                 sys.exc_info() if com_traceback else None)


def test_str_do_psycopg_ja_sai_sem_a_linha_do_usuario():
    """db/connection.py redige o `__str__` do psycopg.Error: print/f-string/log/traceback de TODO site."""
    import traceback
    exc = _violacao()
    assert "Failing row contains (" in _cru(exc) and "100000000000000000000" in _cru(exc), "premissa: o args[0] guarda o texto cru"
    for texto in (str(exc), f"{exc}", "".join(traceback.format_exception(exc))):
        assert "Failing row contains (omitido)" in texto and "100000000000000000000" not in texto, texto


@pytest.mark.parametrize("com_traceback", [False, True])
def test_filtro_tira_failing_row_da_mensagem_e_do_traceback(com_traceback):
    exc = _violacao()
    assert "Failing row contains (" in _cru(exc) and "100000000000000000000" in _cru(exc), "premissa: o psycopg traz a linha no texto cru"
    rec = _registro(exc, com_traceback)
    assert O._sem_failing_row(rec) is True
    saida = logging.Formatter("%(message)s").format(rec)
    assert "Failing row contains (omitido)" in saida
    assert "1e+20" not in saida and "100000000000000000000" not in saida, saida


def test_registro_sem_failing_row_passa_intacto():
    rec = logging.LogRecord("t", logging.WARNING, __file__, 1, "ola %s", ("mundo",), None)
    O._sem_failing_row(rec)
    assert rec.getMessage() == "ola mundo"


def test_filtro_esta_em_todo_handler_do_root(monkeypatch):
    monkeypatch.setattr(O, "_root_configured", False)
    root = logging.getLogger()
    antes = list(root.handlers)
    try:
        for h in list(root.handlers):
            root.removeHandler(h)
        root.addHandler(logging.NullHandler())  # handler de root instalado ANTES do módulo
        O._configure_root_logger()
        assert len(root.handlers) == 2 and all(O._sem_failing_row in h.filters for h in root.handlers)
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in antes:
            root.addHandler(h)


# ── filtro nunca levanta ─────────────────────────────────────────────────────

class _StrQuebrada:
    def __str__(self):
        raise RuntimeError("boom")


@pytest.mark.parametrize("msg,args", [("x %s %s", ("so-um",)), ("x %s", (_StrQuebrada(),)), ("x", (1, 2))],
                         ids=["args-a-menos", "str-levanta", "args-a-mais"])
def test_filtro_nunca_levanta_no_chamador(msg, args):
    import io
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)
    assert O._sem_failing_row(rec) is True
    h = logging.StreamHandler(io.StringIO())
    h.addFilter(O._sem_failing_row)
    lg = logging.Logger("t_filtro_nunca_levanta", logging.INFO)  # fora do manager: só o handler acima
    lg.addHandler(h)
    lg.info(msg, *args)  # o erro de formatação é do handleError (stderr), jamais do chamador


# ── a pilha fica; o DETAIL sai ───────────────────────────────────────────────

def test_pilha_continua_no_formatado_e_no_details_do_dashboard(monkeypatch):
    exc = _violacao()
    rec = _registro(exc, True)
    O._sem_failing_row(rec)
    saida = logging.Formatter("%(message)s").format(rec)
    assert "_registro" in saida and "Traceback" in saida, saida  # a pilha continua lá
    assert "Failing row contains (omitido)" in saida and "100000000000000000000" not in saida, saida
    gravado = {}
    monkeypatch.setattr(O, "log_system_event_sync", lambda *a, **k: gravado.update(k, msg=a))
    O._DashboardHandler().emit(rec)
    tb = gravado["details"]["traceback"]
    assert "_registro" in tb and "100000000000000000000" not in tb and "Failing row contains (omitido)" in tb, tb
    assert "100000000000000000000" not in gravado["message"]


# ── gravações diretas em system_event_logs (sem passar pelo logging) ─────────

def test_gravadores_diretos_nao_persistem_a_linha_do_usuario():
    import asyncio
    import re
    import traceback
    from core.admin_dashboard import ensure_admin_tables, log_system_event
    asyncio.run(ensure_admin_tables())
    exc = _violacao()
    cru = _cru(exc)
    try:
        raise RuntimeError(cru)
    except RuntimeError as e:
        tb = "".join(traceback.format_exception(e))
    assert "Failing row contains (" in tb and "100000000000000000000" in cru
    sync, assinc = "teste_failing_row_sync", "teste_failing_row_async"
    try:
        O.log_system_event_sync("error", sync, cru, details={"traceback": tb, "x": [cru]})
        asyncio.run(log_system_event("error", assinc, cru, details={"traceback": tb}))
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("select event_type, message || details::text as t from system_event_logs "
                        "where event_type in (%s, %s)", (sync, assinc))
            linhas = cur.fetchall()
    finally:
        with get_conn() as conn:
            conn.execute("delete from system_event_logs where event_type in (%s, %s)", (sync, assinc))
    assert sorted(r["event_type"] for r in linhas) == [assinc, sync], linhas
    for r in linhas:
        assert "Failing row contains (omitido)" in r["t"], r
        assert not re.search(r"Failing row contains \((?!omitido)", r["t"]), r
        assert "100000000000000000000" not in r["t"], r
