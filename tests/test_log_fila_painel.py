"""O espelho do logging sai da thread de quem loga (PR-10a, #657 fatia E): `core/log_fila.py`.

Na suíte o espelho grava na thread de quem loga (`tests/conftest.py`, P1 = B); aqui a fila de
VERDADE é instalada pela própria `_configure_root_logger` (`raiz_de_producao`) ou roda num
subprocesso com a configuração de produção. Controles por grupo (CLAUDE.md §3), registrados no
relato do PR-10a:
  1. o loop não para: negativo = `_DashboardHandler` direto no root (T1/T2 veem ~1 s); PR-10b, as
     portas que tiraram o `to_thread`: negativo A = o `to_thread` de volta (a marca "dentro do
     loop" fica vermelha) e negativo B = `_DashboardHandler` ao lado da fila (o tempo fica vermelho);
  2. as linhas saem iguais: negativo = o `prepare` da stdlib (o caso `exc_info` fica vermelho);
  3. reentrada: negativo = sem `_fora_do_ouvinte` (≥ 2 conexões); positivo = outra thread grava;
  4. fila cheia / ouvinte que não morre: negativo = `enqueue` da stdlib; `handle` sem `try`;
  5. saída: negativo = sem o `atexit`; `para` sem prazo.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient

import core.observability as observability
import db
from conftest import usuario_pagante
from core import log_fila
from db.connection import get_conn
from _system_event_log_helpers import sem_env_de_teto, tabela_travada, tabelas_admin  # noqa: F401

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def raiz_de_producao(monkeypatch):
    """O root como o processo de produção o monta: `_configure_root_logger` num root vazio."""
    root = logging.getLogger()
    antes, nivel = list(root.handlers), root.level
    for h in antes:
        root.removeHandler(h)
    monkeypatch.setattr(observability, "_root_configured", False)
    observability._configure_root_logger()
    try:
        yield
    finally:
        log_fila.esvazia(10)
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in antes:
            root.addHandler(h)
        root.setLevel(nivel)


@pytest.fixture
def fonte():
    """Nome de logger (vira o `source`) só deste teste; apaga as linhas dele no fim."""
    nome = f"teste_log_fila.{uuid.uuid4().hex[:10]}"
    yield nome
    log_fila.esvazia(10)
    with get_conn() as c:
        c.execute("delete from system_event_logs where source = %s", (nome,))
        c.commit()


def _linhas(*, source: str | None = None, user_id: int | None = None) -> list[tuple]:
    campo, valor = ("source", source) if source is not None else ("user_id", user_id)
    with get_conn() as c, c.cursor() as cur:
        cur.execute("select level, event_type, message, source, user_id, details "
                    f"from system_event_logs where {campo} = %s order by id", (valor,))
        return [tuple(r.values()) for r in cur.fetchall()]


# ── Grupo 1: o loop não para ─────────────────────────────────────────────────

def test_warning_no_loop_nao_para_com_a_tabela_travada(raiz_de_producao, fonte, user_id, monkeypatch):
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "3000")  # trava de 2 s < teto: a linha grava depois

    async def cenario() -> float:
        maior, parar = 0.0, False

        async def batida():
            nonlocal maior
            ult = time.perf_counter()
            while not parar:
                await asyncio.sleep(0.005)
                agora = time.perf_counter()
                maior, ult = max(maior, agora - ult), agora

        t = asyncio.create_task(batida())
        await asyncio.sleep(0.05)
        logging.getLogger(fonte).warning("travado %s", 1, extra={"user_id": user_id})
        await asyncio.sleep(0.1)
        parar = True
        await t
        return maior

    with tabela_travada(2.0):
        maior = asyncio.run(cenario())
    assert log_fila.esvazia(10)
    assert maior < 1.0, f"o loop parou {maior * 1000:.0f} ms: o espelho gravou na thread do loop"
    assert _linhas(source=fonte) == [
        ("warning", "logger.warning", "travado 1", fonte, user_id, {"logger": fonte})]


def test_rota_que_loga_warning_responde_com_a_tabela_travada(raiz_de_producao, monkeypatch):
    """`frontend/routes/analytics.py`: um `logger.warning` no event loop quando a comparação cai."""
    import frontend.finance_bot_websocket_custom as dashboard
    from core.services import comparacao_periodos as cp
    from frontend.routes import shared

    def quebra(*a, **k):
        raise RuntimeError("comparação fora")

    monkeypatch.setattr(shared, "authorize_dashboard_access", lambda *a, **k: None)
    monkeypatch.setattr(cp, "comparar_mes", quebra)
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "3000")
    uid = usuario_pagante("plus")
    cliente = TestClient(dashboard.app)
    assert cliente.get(f"/analytics/{uid}/kpis").status_code == 200  # aquece (imports, 1ª conexão)
    assert log_fila.esvazia(10)
    msg = "analytics_kpis: comparação indisponível (RuntimeError)"
    antes = [r for r in _linhas(source="frontend.routes.analytics") if r[2] == msg]

    with tabela_travada(2.0):  # o negativo (espelho síncrono) para ~2 s: margem de 2× sobre o limite
        t0 = time.perf_counter()
        r = cliente.get(f"/analytics/{uid}/kpis")
        gasto = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    assert gasto < 1.0, f"a rota levou {gasto * 1000:.0f} ms: o warning pagou o INSERT no loop"
    assert log_fila.esvazia(10)
    depois = [r for r in _linhas(source="frontend.routes.analytics") if r[2] == msg]
    assert len(depois) == len(antes) + 1


class _MarcaLoop(logging.Handler):
    """Registra, por linha de log, se ela foi emitida DENTRO do event loop."""

    def __init__(self):
        super().__init__()
        self.dentro_do_loop: list[tuple[str, bool]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            asyncio.get_running_loop()
            dentro = True
        except RuntimeError:
            dentro = False
        self.dentro_do_loop.append((record.getMessage(), dentro))


class _ErroStripe(Exception):
    pass


# (método, url, status, começo da linha do `_log_falha`). `{u}` é o dono da requisição.
_PORTAS_SEM_TO_THREAD = {
    "launch_tecnico": ("delete", "/launches/{u}/424242", 500, "delete_launch: falha"),
    "launch_dominio": ("delete", "/launches/{u}/400400", 400, "delete_launch: falha"),
    "credito": ("delete", "/credit-transactions/{u}/9", 500, "undo_credit_transaction: falha"),
    "cartao": ("delete", "/cards/{u}/9", 500, "delete_card: falha"),
    "parcelamento": ("delete", "/installments/{u}/PC12345678", 500, "undo_installment_group: falha"),
    "portal": ("post", "/billing/portal", 502, "billing_portal: falha"),
}


@pytest.mark.parametrize("caso", list(_PORTAS_SEM_TO_THREAD))
def test_rota_que_logava_por_to_thread_loga_no_loop_sem_travar(caso, raiz_de_producao, user_id, monkeypatch):
    """PR-10b: estas portas chamavam `_log_falha` por `asyncio.to_thread` porque o espelho gravava na
    thread de quem loga. Com a fila, logam no loop e a rota responde com a tabela travada.

    A marca "dentro do loop" é o que discrimina a retirada: com a fila, o `to_thread` também é rápido."""
    import db.cards as cards_mod
    import frontend.finance_bot_websocket_custom as dashboard
    from db.accounts import LaunchUnsafeRollback
    from test_log_falha_traceback import _SEGREDO, _client

    def explode(uid, lid):
        if lid == 400400:  # ramo de DOMÍNIO da `/launches`: WARNING + 400
            raise LaunchUnsafeRollback("efeitos desconhecidos", "chave_desconhecida")
        raise RuntimeError(_SEGREDO)

    def stripe_cai(**kw):
        raise _ErroStripe("portal fora")

    monkeypatch.setattr(dashboard, "delete_launch_and_rollback", explode)
    monkeypatch.setattr(dashboard, "undo_credit_transaction", explode)
    monkeypatch.setattr(cards_mod, "get_card_by_id", lambda uid, cid: {"id": cid, "name": "Nubank"})
    monkeypatch.setattr(cards_mod, "delete_card", explode)
    monkeypatch.setattr(cards_mod, "undo_installment_group", explode)
    monkeypatch.setitem(sys.modules, "stripe", SimpleNamespace(
        api_key=None, error=SimpleNamespace(StripeError=_ErroStripe),
        billing_portal=SimpleNamespace(Session=SimpleNamespace(create=stripe_cai))))
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_pr10b_fake")
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "3000")  # trava de 2 s < teto: a linha grava depois
    dashboard.limiter._storage.reset()  # `/billing/portal` tem 30/hora
    if caso == "portal":  # o portal exige credencial e cliente Stripe (molde: tests/test_billing_portal.py)
        user_id = int(db.register_auth_user(f"pr10b-{uuid.uuid4().hex}@example.com",
                                            "senha-teste-123")["user_id"])
        db.set_stripe_customer(user_id, f"cus_pr10b_{uuid.uuid4().hex}")

    metodo, url, esperado, prefixo = _PORTAS_SEM_TO_THREAD[caso]
    cli = _client(user_id)
    pede = getattr(cli, metodo)
    url, cab = url.format(u=user_id), {dashboard.CSRF_HEADER_NAME: "test-csrf-token"}
    assert pede(url, headers=cab).status_code == esperado  # aquece (imports, 1ª conexão)
    assert log_fila.esvazia(10)
    antes = [r for r in _linhas(user_id=user_id) if r[2].startswith(prefixo)]

    marca, lg = _MarcaLoop(), logging.getLogger("core.observability")
    lg.addHandler(marca)
    try:
        with tabela_travada(2.0):  # o negativo (espelho síncrono) para ~2 s: margem de 2× sobre o limite
            t0 = time.perf_counter()
            r = pede(url, headers=cab)
            gasto = time.perf_counter() - t0
    finally:
        lg.removeHandler(marca)
    assert r.status_code == esperado, r.text
    assert [d for m, d in marca.dentro_do_loop if m.startswith(prefixo)] == [True], marca.dentro_do_loop
    assert gasto < 1.0, f"a rota levou {gasto * 1000:.0f} ms: o log pagou o INSERT no loop"
    assert log_fila.esvazia(10)
    depois = [r for r in _linhas(user_id=user_id) if r[2].startswith(prefixo)]
    assert len(depois) == len(antes) + 1, (antes, depois)


def test_poda_que_levanta_loga_no_loop_sem_travar(raiz_de_producao, monkeypatch):
    """PR-10b, a 7ª porta: o `except` de `run_table_cleanup_loop` loga no loop, sem `to_thread`."""
    import core.services.table_cleanup as tc

    marcador = uuid.uuid4().hex
    espera_real = asyncio.sleep
    monkeypatch.setenv("TABLE_CLEANUP_INTERVAL_HOURS", "24")
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "3000")

    def _poda(**kw):
        os.environ["TABLE_CLEANUP_INTERVAL_HOURS"] = "0"  # 2ª volta encerra
        raise RuntimeError(f"poda quebrou {marcador}")

    async def _sem_esperar(_segundos):
        await espera_real(0)  # sem os 120 s do boot nem as 24 h

    monkeypatch.setattr(tc, "run_table_cleanup", _poda)
    monkeypatch.setattr(asyncio, "sleep", _sem_esperar)

    async def cenario() -> float:
        maior, parar = 0.0, False

        async def batida():
            nonlocal maior
            ult = time.perf_counter()
            while not parar:
                await espera_real(0.005)
                agora = time.perf_counter()
                maior, ult = max(maior, agora - ult), agora

        t = asyncio.create_task(batida())
        await espera_real(0.05)
        await asyncio.wait_for(tc.run_table_cleanup_loop(), timeout=10)
        await espera_real(0.05)
        parar = True
        await t
        return maior

    def nossas() -> list[tuple]:
        return [r for r in _linhas(source="core.services.table_cleanup") if marcador in r[2]]

    marca, lg = _MarcaLoop(), logging.getLogger("core.services.table_cleanup")
    lg.addHandler(marca)
    try:
        with tabela_travada(2.0):
            maior = asyncio.run(cenario())
    finally:
        lg.removeHandler(marca)
    assert [d for m, d in marca.dentro_do_loop if marcador in m] == [True], marca.dentro_do_loop
    assert maior < 1.0, f"o loop parou {maior * 1000:.0f} ms: o log pagou o INSERT no loop"
    assert log_fila.esvazia(10)
    assert len(nossas()) == 1


# ── Grupo 2: as linhas saem iguais ───────────────────────────────────────────

def _caso(nome: str, lg: logging.Logger, uid: int) -> None:
    if nome == "args":
        lg.warning("simples %s=%r", "k", {"a": 1})
    elif nome == "dono":
        lg.warning("com dono", extra={"user_id": uid})
    elif nome == "exc_info":
        try:
            1 / 0
        except ZeroDivisionError:
            lg.error("com pilha", exc_info=True, extra={"user_id": uid})
    elif nome == "failing_row":
        try:
            raise RuntimeError("Failing row contains (1, segredo-da-linha)")
        except RuntimeError as e:
            lg.error("falhou: %s", e, exc_info=True, extra={"user_id": uid})
    else:  # `_log_falha`, com e sem pilha
        try:
            raise psycopg.errors.UniqueViolation("duplicate key")
        except psycopg.errors.UniqueViolation as exc:
            observability._log_falha("teste_log_fila", uid, exc, com_traceback=nome == "falha_com_pilha")


@pytest.mark.parametrize("nome", ["args", "dono", "exc_info", "failing_row",
                                  "falha_com_pilha", "falha_sem_pilha"])
def test_fila_grava_a_mesma_linha_que_o_espelho_sincrono(raiz_de_producao, fonte, user_id, nome):
    """O "antes" é o `_DashboardHandler` como o root o tinha (mesmos filtros), no MESMO record:
    cada log vira duas linhas, uma síncrona e uma pela fila, e elas têm de ser iguais."""
    antigo = observability._DashboardHandler()
    antigo.setLevel(logging.WARNING)
    antigo.addFilter(observability._sem_ratelimit_no_banco)
    antigo.addFilter(observability._sem_failing_row)
    root = logging.getLogger()
    root.addHandler(antigo)
    try:
        _caso(nome, logging.getLogger(fonte), user_id)
    finally:
        root.removeHandler(antigo)
    assert log_fila.esvazia(10)
    linhas = _linhas(source=fonte) if nome in ("args",) else _linhas(user_id=user_id)
    assert len(linhas) == 2, linhas
    assert linhas[0] == linhas[1], f"a fila mudou a linha:\nsíncrona: {linhas[0]}\nfila:     {linhas[1]}"
    if nome in ("exc_info", "falha_com_pilha", "failing_row"):
        assert "Traceback" in linhas[1][5]["traceback"] and "Traceback" not in linhas[1][2]
    assert "segredo-da-linha" not in json.dumps(linhas, ensure_ascii=False)


def test_mensagem_sai_formatada_na_hora_e_na_thread_de_quem_loga(raiz_de_producao, fonte, user_id,
                                                                 monkeypatch):
    """O ouvinte grava depois: o `prepare` formata ANTES, na thread de quem loga. Senão um argumento
    mutável alterado depois do log, ou um `__str__` que lê `threading.local`, gravaria outra coisa."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "1500")
    local = threading.local()
    local.quem = "quem logou"

    class Contexto:
        def __str__(self):
            return getattr(local, "quem", "a thread do ouvinte")

    lg = logging.getLogger(fonte)
    with tabela_travada(0.5):  # o ouvinte fica preso no 1º INSERT até a trava sair, depois da mutação
        lg.warning("segura")
        saldo = {"saldo": 100}
        lg.warning("saldo %s de %s", saldo, Contexto(), extra={"user_id": user_id})
        saldo["saldo"] = 999
    assert log_fila.esvazia(10)
    assert [r[2] for r in _linhas(source=fonte)] == ["segura", "saldo {'saldo': 100} de quem logou"]


def test_info_e_slowapi_nao_gravam(raiz_de_producao, fonte):
    logging.getLogger(fonte).info("info não vai")
    logging.getLogger(f"slowapi.{fonte}").warning("não é o slowapi")  # controle: este grava
    logging.getLogger("slowapi").warning("ratelimit %s exceeded", fonte)
    assert log_fila.esvazia(10)
    assert _linhas(source=fonte) == []
    with get_conn() as c, c.cursor() as cur:
        cur.execute("select count(*) as n from system_event_logs where source = 'slowapi' and message like %s",
                    (f"%{fonte}%",))
        assert cur.fetchone()["n"] == 0
    assert len(_linhas(source=f"slowapi.{fonte}")) == 1
    with get_conn() as c:
        c.execute("delete from system_event_logs where source = %s", (f"slowapi.{fonte}",))
        c.commit()


# ── Grupo 3: reentrada ───────────────────────────────────────────────────────

@pytest.mark.parametrize("outra_thread", [False, True], ids=["so_o_warning", "positivo_outra_thread"])
def test_warning_do_psycopg_no_ouvinte_nao_reabre_o_ciclo(capfd, raiz_de_producao, fonte, monkeypatch,
                                                          outra_thread):
    # `capfd` antes da raiz: o `StreamHandler` dela guarda o `sys.stderr` da hora em que nasce
    """O cenário de `tests/test_system_event_log_teto.py::test_warning_do_psycopg_nao_reentra_no_handler`
    pela fila: o `warning` do psycopg nasce na thread do ouvinte, fora do `_reentrou`."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "300")
    conexoes: list[str] = []
    real = psycopg.connect

    def boom():
        raise RuntimeError("rollback falhou (gatilho de connection.py:170)")

    with tabela_travada(3.0):
        def espia(url, **kw):
            conexoes.append(threading.current_thread().name)
            conn = real(url, **kw)
            conn.rollback = boom
            return conn

        monkeypatch.setattr(psycopg, "connect", espia)
        logging.getLogger(fonte).warning("x")
        if outra_thread:
            t = threading.Thread(target=logging.getLogger(fonte).warning, args=("y",))
            t.start()
            t.join()
        time.sleep(2.5)
        monkeypatch.setattr(psycopg, "connect", real)
    esperado = 2 if outra_thread else 1
    assert len(conexoes) == esperado, f"{len(conexoes)} conexões para {esperado} warning(s): o ciclo reabriu"
    assert "error ignored in rollback" in capfd.readouterr().err  # a perda não é muda


# ── Grupo 4: fila cheia e ouvinte que não morre ──────────────────────────────

@contextlib.contextmanager
def _fila_de_teste(tamanho: int, emit, filtro=None):
    """Fila e ouvinte avulsos (não os do processo), com um espelho falso."""
    class Espelho(logging.Handler):
        def emit(self, record):
            emit(record)

    espelho = Espelho()
    if filtro:
        espelho.addFilter(filtro)
    fila = log_fila.FilaDoPainel(queue.Queue(tamanho))
    ouvinte = log_fila._Ouvinte(fila.queue, espelho)
    ouvinte.start()
    lg = logging.getLogger(f"teste_log_fila_cheia.{uuid.uuid4().hex[:8]}")
    lg.propagate = False
    lg.addHandler(fila)
    try:
        yield fila, lg
    finally:
        ouvinte.stop()


def _espera_vazia(fila) -> None:
    for _ in range(500):
        if not fila.queue.unfinished_tasks:
            return
        time.sleep(0.01)


def test_fila_cheia_descarta_o_novo_sem_travar_e_avisa_uma_vez_por_rajada(capfd):
    entrou, solta, gravados = threading.Event(), threading.Event(), []

    def emit(record):
        entrou.set()
        solta.wait(10)
        gravados.append(record.getMessage())

    with _fila_de_teste(3, emit) as (fila, lg):
        def rajada(tag: str) -> float:
            entrou.clear()
            solta.clear()
            lg.warning("%s0", tag)
            assert entrou.wait(5)  # o ouvinte segura o 1º: a fila (3) enche com os seguintes, sem corrida
            t0 = time.perf_counter()  # só os 9 com o ouvinte preso
            for i in range(1, 10):
                lg.warning("%s%d", tag, i)
            gasto = time.perf_counter() - t0
            solta.set()
            _espera_vazia(fila)
            return gasto

        try:
            gasto = rajada("a")
            err = capfd.readouterr().err
            assert gasto < 0.05, f"9 warnings levaram {gasto * 1000:.0f} ms com a fila cheia"
            assert "--- Logging error ---" not in err
            assert err.count("fila do painel cheia") == 1, err
            assert len(gravados) == 4 and fila.descartados == 6, (gravados, fila.descartados)
            # positivo: com vaga, tudo chega
            for i in range(3):
                lg.warning("depois%d", i)
            _espera_vazia(fila)
            assert gravados[-3:] == ["depois0", "depois1", "depois2"]
            # a fila esvaziou: a rajada seguinte avisa de novo
            rajada("b")
            assert capfd.readouterr().err.count("fila do painel cheia") == 1
            assert fila.descartados == 12
        finally:
            solta.set()


def test_fluxo_continuo_com_a_fila_cheia_avisa_uma_vez(capfd):
    """Ouvinte de 20 ms por evento e um warning a cada 0,5 ms: a fila nunca esvazia, é uma rajada só
    (o pulmão, 10 × 20 ms, aguenta uma pausa de 200 ms de quem loga). Rearmar o aviso a cada vaga
    dava um aviso por evento gravado."""
    with _fila_de_teste(10, lambda record: time.sleep(0.02)) as (fila, lg):
        for i in range(600):
            lg.warning("c%d", i)
            time.sleep(0.0005)
        _espera_vazia(fila)
    err = capfd.readouterr().err
    assert fila.descartados > 100, fila.descartados
    assert err.count("fila do painel cheia") == 1, f"{err.count('fila do painel cheia')} avisos numa rajada"


def test_filtro_que_levanta_e_stderr_quebrado_nao_matam_o_ouvinte(monkeypatch):
    gravados: list[str] = []

    def emit(record):
        if record.getMessage() == "emit":
            raise RuntimeError("emit quebrado")
        gravados.append(record.getMessage())

    def filtro(record):
        if record.getMessage() == "filtro":
            raise RuntimeError("filtro quebrado")
        return True

    class StderrFechado:
        def write(self, *_):
            raise OSError("stderr fechado")

        def flush(self):
            pass

    with _fila_de_teste(10, emit, filtro) as (fila, lg):
        monkeypatch.setattr(sys, "stderr", StderrFechado())  # o `print` do `except` também levanta
        for msg in ("filtro", "emit", "depois"):
            lg.warning(msg)
        _espera_vazia(fila)
        monkeypatch.undo()
    assert gravados == ["depois"], "o ouvinte morreu antes do 3º evento"


def test_excecao_no_espelho_nao_mata_o_ouvinte(capfd, raiz_de_producao, fonte, monkeypatch):
    gravados: list[str] = []

    def falha_uma_vez(level, **kw):
        if not gravados:
            gravados.append("levantou")
            raise RuntimeError("texto da exceção com segredo-123")
        gravados.append(kw["message"])

    monkeypatch.setattr(observability, "log_system_event_sync", falha_uma_vez)
    lg = logging.getLogger(fonte)
    lg.warning("primeiro")
    lg.warning("segundo")
    assert log_fila.esvazia(5), "o ouvinte morreu: a fila não esvazia"
    assert gravados == ["levantou", "segundo"]
    err = capfd.readouterr().err
    assert "[observability] espelho falhou: motivo=RuntimeError" in err
    assert "segredo-123" not in err


# ── Grupo 5: saída do processo (subprocesso, configuração de produção) ───────

_SAIDA = r'''
import logging, sys, time
import core.observability as obs
from core import log_fila
if len(sys.argv) > 2:
    log_fila._PRAZO_SAIDA_S = float(sys.argv[2])
obs._configure_root_logger()
for i in range(5):
    logging.getLogger(sys.argv[1]).warning("saida %d", i)
print("FIM:%r" % time.time())
'''


def _roda(script: str, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", script, *args], cwd=RAIZ,
                          env={**os.environ, "PYTHONPATH": ".", **env},
                          capture_output=True, text=True, timeout=180)


def _saida_s(proc: subprocess.CompletedProcess, fim: float) -> float:
    """Do fim do script ao fim do processo: o tempo do `atexit`, sem o import."""
    linha = next(l for l in proc.stdout.splitlines() if l.startswith("FIM:"))
    return fim - float(linha[4:])


def test_processo_curto_grava_tudo_antes_de_sair(fonte):
    proc = _roda(_SAIDA, fonte)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert [r[2] for r in _linhas(source=fonte)] == [f"saida {i}" for i in range(5)]


def test_saida_com_a_tabela_travada_respeita_o_prazo_e_avisa(fonte):
    with tabela_travada(15.0):  # teto de 5 s: o INSERT em voo dura mais que o prazo, e a diferença aparece
        proc = _roda(_SAIDA, fonte, "1.0", SYSTEM_EVENT_LOG_TIMEOUT_MS="5000")
        saida = _saida_s(proc, time.time())
    assert proc.returncode == 0, proc.stderr[-2000:]
    # com o lock do handler no `_Ouvinte.handle`, o `logging.shutdown` esperava o INSERT em voo
    assert saida < 2.0, f"a saída levou {saida:.1f} s com prazo de 1 s"
    assert "fila do painel: 5 evento(s) não gravado(s) na saída" in proc.stderr, proc.stderr[-2000:]


_DESCARTA = r'''
import logging, sys
from core import log_fila
log_fila._MAX = int(sys.argv[2])
import core.observability as obs
obs._configure_root_logger()
for i in range(int(sys.argv[3])):
    logging.getLogger(sys.argv[1]).warning("d %d", i)
'''


@pytest.mark.parametrize("tamanho,eventos", [(2, 50), (1000, 5)], ids=["com_descartes", "positivo_sem_descartes"])
def test_saida_diz_o_total_descartado_por_fila_cheia(fonte, tamanho, eventos):
    """Durante o funcionamento sai um aviso por rajada; o TOTAL descartado sai na saída do processo,
    mesmo quando ela esvaziou a fila no prazo. Sem descarte e sem perda, nenhuma linha."""
    import re

    proc = _roda(_DESCARTA, fonte, str(tamanho), str(eventos))
    assert proc.returncode == 0, proc.stderr[-2000:]
    gravados = len(_linhas(source=fonte))
    saida = re.findall(r"fila do painel: (\d+) evento\(s\) não gravado\(s\) na saída; "
                       r"(\d+) descartado\(s\) por fila cheia desde o início", proc.stderr)
    if tamanho == 1000:
        assert gravados == eventos and saida == [], proc.stderr[-2000:]
    else:
        assert len(saida) == 1, proc.stderr[-2000:]
        nao_gravados, descartados = map(int, saida[0])
        assert nao_gravados == 0 and descartados > 0 and gravados + descartados == eventos


_SEM_THREAD = r'''
import json, logging, os, threading
import core.observability as obs
obs._configure_root_logger()
contas = [threading.active_count()]
url = os.environ.pop("DATABASE_URL")
logging.getLogger("teste_log_fila.sem_banco").warning("sem banco")
contas.append(threading.active_count())
os.environ["DATABASE_URL"] = url
logging.getLogger("teste_log_fila.sem_banco").warning("com banco")
contas.append(threading.active_count())
print("RESULTADO:" + json.dumps(contas))
'''


def test_import_e_log_sem_banco_nao_criam_thread():
    """O harness hermético (`harness_support/safety_guards.py`) proíbe criar thread e roda sem
    `DATABASE_URL`: o ouvinte nasce no 1º evento que seria gravado, não no import."""
    proc = _roda(_SEM_THREAD)
    linha = next((l for l in proc.stdout.splitlines() if l.startswith("RESULTADO:")), None)
    assert linha, proc.stderr[-2000:]
    assert json.loads(linha[len("RESULTADO:"):]) == [1, 1, 2]  # positivo: com banco, a thread nasce


_APP_SIGTERM = r'''
import contextlib, logging, os
import frontend.finance_bot_websocket_custom as m
from core import log_fila
log_fila._PRAZO_SAIDA_S = 1.0
_lifespan = m.app.router.lifespan_context


@contextlib.asynccontextmanager
async def _avisa_no_shutdown(app):  # ex.: "tarefa X não terminou" no shutdown do deploy
    async with _lifespan(app):
        yield
        for i in range(5):
            logging.getLogger(os.environ["FONTE_TESTE"]).warning("shutdown %d", i)

m.app.router.lifespan_context = _avisa_no_shutdown
app = m.app
'''


@pytest.mark.parametrize("travada", [False, True], ids=["banco_sadio", "tabela_travada"])
def test_uvicorn_com_sigterm_grava_ou_avisa_o_que_logou_no_shutdown(fonte, tmp_path, travada):
    """O uvicorn re-levanta o SIGTERM depois do shutdown e o processo morre pelo sinal, sem `atexit`:
    quem esvazia a fila é o fim do lifespan do monólito (`log_fila.esvazia_na_saida`)."""
    import signal
    import socket

    (tmp_path / "app_sigterm.py").write_text(_APP_SIGTERM, encoding="utf-8")
    arquivo_err = tmp_path / "stderr.txt"  # arquivo, não PIPE: ninguém lê o pipe durante o startup
    stderr_do_filho = arquivo_err.open("w", encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app_sigterm:app", "--host", "127.0.0.1", "--port", str(porta),
         "--log-level", "warning"], cwd=RAIZ, stdout=subprocess.DEVNULL, stderr=stderr_do_filho,
        env={**os.environ, "PYTHONPATH": f"{RAIZ}{os.pathsep}{tmp_path}", "RUN_BACKGROUND_TASKS": "0",
             "FONTE_TESTE": fonte})
    try:
        for _ in range(1800):
            assert proc.poll() is None, arquivo_err.read_text(encoding="utf-8")[-2000:]
            try:
                socket.create_connection(("127.0.0.1", porta), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.1)
        with tabela_travada(30.0) if travada else contextlib.nullcontext():
            t0 = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=60)
            gasto = time.monotonic() - t0
    finally:
        if proc.poll() is None:
            proc.kill()
        stderr_do_filho.close()
    err = arquivo_err.read_text(encoding="utf-8")
    assert proc.returncode == -signal.SIGTERM, (proc.returncode, err[-2000:])  # morreu pelo sinal
    if travada:
        assert "fila do painel: 5 evento(s) não gravado(s) na saída" in err, err[-2000:]
        assert gasto < 5.0, f"a saída levou {gasto:.1f} s com prazo de 1 s"
    else:
        assert [r[2] for r in _linhas(source=fonte)] == [f"shutdown {i}" for i in range(5)], err[-2000:]


# ── Guarda na raiz (P5): nenhum handler grava síncrono no root do processo do app ─

_RAIZ_DO_APP = r'''
import json, logging, logging.handlers
import frontend.finance_bot_websocket_custom  # noqa: F401
import core.observability as obs
from core import log_fila
root = logging.getLogger()
filas = [h for h in root.handlers if isinstance(h, log_fila.FilaDoPainel)]
print("RESULTADO:" + json.dumps({
    "filas": len(filas),
    "sincronos": sum(isinstance(h, obs._DashboardHandler) for h in root.handlers),
    "outros": sorted({type(h).__name__ for h in root.handlers if h not in filas}),
    "prepare_proprio": all(type(f).prepare is not logging.handlers.QueueHandler.prepare for f in filas),
}))
'''


def test_raiz_do_processo_do_app_so_tem_a_fila_e_o_stderr():
    """A única forma de um `logging.*` somar tempo ao prazo de quem loga (o `_log_com_teto` e os
    futuros) é um handler que grava síncrono no root. Interpretador limpo, como o do uvicorn."""
    proc = _roda(_RAIZ_DO_APP, RUN_BACKGROUND_TASKS="0")
    linha = next((l for l in proc.stdout.splitlines() if l.startswith("RESULTADO:")), None)
    assert linha, f"subprocesso não chegou ao fim:\n{proc.stderr[-2000:]}"
    assert json.loads(linha[len("RESULTADO:"):]) == {
        "filas": 1, "sincronos": 0, "outros": ["StreamHandler"], "prepare_proprio": True}


# ── T10: `core/log_fila.py` não chama `logging` (a fila se realimentaria) ────

_PROIBIDOS = {"debug", "info", "warning", "error", "exception", "critical", "log", "getLogger", "get_logger"}


def _chamadas_de_log(codigo: str) -> list[str]:
    achados = []
    for no in ast.walk(ast.parse(codigo)):
        if isinstance(no, ast.Call):
            nome = getattr(no.func, "attr", None) or getattr(no.func, "id", None)
            if nome in _PROIBIDOS:
                achados.append(f"linha {no.lineno}: {nome}(…)")
    return achados


@pytest.mark.parametrize("codigo,pega", [
    ("logging.warning('x')", True), ("logger.error('x')", True), ("logging.getLogger(__name__)", True),
    ("get_logger(__name__)", True), ("self.log(30, 'x')", True), ("print('x', file=sys.stderr)", False),
    ("logging.handlers.QueueHandler(q)", False),
])
def test_detector_de_chamada_de_log(codigo, pega):
    assert bool(_chamadas_de_log(codigo)) is pega


def test_log_fila_nao_chama_logging():
    assert _chamadas_de_log((RAIZ / "core" / "log_fila.py").read_text(encoding="utf-8")) == []
