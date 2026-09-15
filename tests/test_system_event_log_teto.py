"""Teto de EXECUÇÃO das duas conexões de `core/system_event_log.py`.

O `connect_timeout=2` que já existia limita só o handshake. Com a
`system_event_logs` travada em `access exclusive` (o que um `alter table`, um
`vacuum full` ou uma migração fazem), o handshake era instantâneo e o INSERT
esperava o LOCK inteiro — e, como o `_DashboardHandler` mora no ROOT logger em
nível WARNING (`core/observability.py`), essa espera é paga por qualquer
`logger.warning()`/`error()` do processo, inclusive dentro do event loop.

MECANISMO: uma 2ª sessão com `psycopg.connect` DEDICADO (nunca `get_conn` — o
lock ficaria segurando uma vaga do pool durante o teste inteiro) trava a tabela,
e um `threading.Timer` solta. O Timer é o que impede o vermelho de pendurar a
suíte: se o teto não funcionar, o teste falha na asserção de tempo em vez de
travar até o pytest ser morto.

CONTROLE NEGATIVO DO GRUPO: tire o `options=_statement_timeout_options()` dos
DOIS `psycopg.connect` de `core/system_event_log.py`. VERMELHOS, os quatro:
`test_insert_com_tabela_travada_desiste_dentro_do_teto`,
`test_leitura_com_tabela_travada_desiste_dentro_do_teto`,
`test_options_chega_no_connect` e `test_warning_do_psycopg_nao_reentra_no_handler`
— este último porque, sem teto, o INSERT espera o lock cair e TERMINA BEM, e aí
não há `__exit__` com exceção para o psycopg avisar nem reentrada a ignorar. Os
quatro estavam verdes com o conserto, que é onde a injeção discrimina.

CONTROLE NEGATIVO DA GUARDA (injeção SEPARADA, outro caminho de código): faça
`_reentrou` devolver `False` sempre. VERMELHO: só
`test_warning_do_psycopg_nao_reentra_no_handler`. Ele aparece nos dois controles
por motivos opostos — aqui a reentrada acontece, ali ela nem chega a existir.

CONTROLES POSITIVOS: a mudança RESTRINGE (passa a cancelar query). Sem
`test_tabela_livre_continua_gravando`, `test_valor_sem_sentido_volta_ao_default`
e `test_options_chega_no_connect`, este arquivo inteiro passaria num código que
simplesmente não grava nada — que é pior que o bug.

CONFUNDIDOR, e por isso ele tem teste próprio
(`test_destacar_o_handler_e_o_que_faz_o_cronometro_medir_a_funcao`): com a
tabela travada, QUALQUER `logger.warning()` da suíte também bloqueia. Se o
`_DashboardHandler` continuasse no root durante os testes de cronômetro, o
cronômetro estaria medindo o handler e não a função sob teste.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
import time

import psycopg
import pytest

import core.observability as observability
from core.system_event_log import (
    _TETO_MAX_MS,
    _statement_timeout_options,
    log_system_event_sync,
    recent_event_exists,
)
from db.connection import get_conn

# Valor exato usado na limpeza — nunca `like`, nunca prefixo (CLAUDE.md §0:
# limpe só o que você criou).
EVENTO_INSERT = "teste_teto_insert"
EVENTO_LEITURA = "teste_teto_leitura"
EVENTO_LIVRE = "teste_teto_livre"
EVENTO_DEFAULT = "teste_teto_default"
EVENTO_OPTIONS = "teste_teto_options"

TETO_PADRAO_OPTIONS = "-c statement_timeout=2000ms"


@pytest.fixture(autouse=True)
def tabelas_admin():
    """`system_event_logs` não nasce de `db/schema.py` — é criada
    preguiçosamente por `core/admin_dashboard.py`. Sem ela o INSERT falha em
    silêncio e os controles positivos não mediriam nada (mesmo idioma de
    `tests/_billing_grants_helpers.py:29-30` e `tests/test_of_health.py:372`)."""
    from core.admin_dashboard import ensure_admin_tables
    asyncio.run(ensure_admin_tables())


@pytest.fixture(autouse=True)
def sem_env_de_teto(monkeypatch):
    """A env é o knob sob teste: quem quiser um valor, faz `setenv` explícito."""
    monkeypatch.delenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", raising=False)


@pytest.fixture
def sem_dashboard_handler():
    """Destaca os `_DashboardHandler` do root logger e repõe no fim.

    É O CONFUNDIDOR dos testes de cronômetro: com a tabela travada, um
    `logger.warning()` de qualquer módulo da suíte paga a mesma espera, e o
    cronômetro passaria a medir o handler em vez da função sob teste.
    `test_destacar_o_handler_e_o_que_faz_o_cronometro_medir_a_funcao` mede que o
    destacamento de fato funciona."""
    root = logging.getLogger()
    presos = [h for h in root.handlers
              if isinstance(h, observability._DashboardHandler)]
    for h in presos:
        root.removeHandler(h)
    try:
        yield
    finally:
        for h in presos:
            root.addHandler(h)


@contextlib.contextmanager
def tabela_travada(segundos: float):
    """`system_event_logs` em `access exclusive` por `segundos`, solta por Timer.

    Conexão DEDICADA de propósito: com `get_conn` o lock prenderia uma vaga do
    pool que o resto do teste (e o cleanup) precisa. `lock_timeout` porque o
    próprio `lock table` pendura sem limite se outra transação estiver segurando
    a tabela, e o Timer que solta só é armado DEPOIS dele: sem teto aqui, a
    suíte trava em vez de falhar."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5,
                           options="-c lock_timeout=5000ms")
    with conn.cursor() as cur:
        cur.execute("lock table system_event_logs in access exclusive mode")
    solta = threading.Timer(segundos, conn.rollback)
    solta.start()
    try:
        yield
    finally:
        solta.cancel()
        solta.join()
        try:
            conn.rollback()
        finally:
            conn.close()


def _linhas(event_type: str) -> int:
    with get_conn() as c:
        with c.cursor() as cur:
            cur.execute("select count(*) as n from system_event_logs where event_type = %s",
                        (event_type,))
            return int((cur.fetchone() or {}).get("n") or 0)


def _limpa(event_type: str) -> None:
    with get_conn() as c:
        c.execute("delete from system_event_logs where event_type = %s", (event_type,))
        c.commit()


# ── Cronômetro: a tabela travada não pode mais pendurar o caller ─────────────

def test_insert_com_tabela_travada_desiste_dentro_do_teto(
        monkeypatch, capsys, sem_dashboard_handler):
    """HOJE (sem `options`): ~3,0s e a linha ACABA GRAVADA quando o lock cai.
    Com o teto de 300ms: desiste em ~0,3s e não grava.

    Não se afirma por exceção: o `except Exception` de `log_system_event_sync`
    engole o `QueryCanceled` de propósito (perder o log não pode virar um
    segundo modo de falha em cima do incidente). O que se mede é tempo de
    parede, ausência da linha e o rastro no stderr."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "300")
    try:
        with tabela_travada(3.0):
            t0 = time.perf_counter()
            log_system_event_sync("warning", EVENTO_INSERT, "mensagem", source="teste")
            gasto = time.perf_counter() - t0

        assert gasto < 1.5, f"pendurou {gasto:.2f}s no lock — teto de 300ms não pegou"
        assert _linhas(EVENTO_INSERT) == 0, \
            "gravou depois do lock cair: a chamada esperou o lock inteiro"
        assert "failed to record" in capsys.readouterr().err
    finally:
        _limpa(EVENTO_INSERT)


def test_leitura_com_tabela_travada_desiste_dentro_do_teto(
        monkeypatch, capsys, sem_dashboard_handler, user_id):
    """A gêmea de leitura: hoje espera o lock inteiro. Depois devolve `False`
    dentro do teto — que é exatamente o que o `except` dela já devolvia."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "300")
    with tabela_travada(3.0):
        t0 = time.perf_counter()
        achou = recent_event_exists(EVENTO_LEITURA, user_id)
        gasto = time.perf_counter() - t0

    assert gasto < 1.5, f"pendurou {gasto:.2f}s no lock — teto de 300ms não pegou"
    assert achou is False
    assert "failed to check" in capsys.readouterr().err


def test_destacar_o_handler_e_o_que_faz_o_cronometro_medir_a_funcao(
        monkeypatch, capsys):
    """PROVA DE QUE O CONTROLE DISCRIMINA — não é teste da feature, é teste do
    aparelho de medição dos dois acima.

    Com a tabela travada, cronometra o MESMO `logger.warning()` com e sem o
    `_DashboardHandler` no root. COM o handler tem de custar ~o teto; SEM, ~0.
    Se os dois números derem iguais, o destacamento não funcionou e os dois
    cronômetros acima estão medindo o handler em vez da função sob teste."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "300")
    root = logging.getLogger()
    observability.get_logger(__name__)  # garante o handler configurado no root
    presos = [h for h in root.handlers
              if isinstance(h, observability._DashboardHandler)]
    assert presos, "sem handler no root não há confundidor a medir"

    logger = logging.getLogger("teste_teto_confundidor")
    with tabela_travada(3.0):
        t0 = time.perf_counter()
        logger.warning("y")
        com = time.perf_counter() - t0

        for h in presos:
            root.removeHandler(h)
        try:
            t0 = time.perf_counter()
            logger.warning("y")
            sem = time.perf_counter() - t0
        finally:
            for h in presos:
                root.addHandler(h)

    capsys.readouterr()
    assert com > 0.1, (
        f"com o handler o warning custou {com * 1000:.0f}ms — abaixo do teto de "
        "300ms, então este warning nem chegou ao banco e a comparação não vale"
    )
    assert sem < 0.05, (
        f"sem o handler o warning custou {sem * 1000:.0f}ms (com: {com * 1000:.0f}ms) "
        "— o destacamento não funcionou e os cronômetros medem o handler"
    )


def test_warning_do_psycopg_nao_reentra_no_handler(monkeypatch, capsys):
    """O ciclo PRÉ-EXISTENTE que a guarda de `threading.local` fecha — e que
    este teto NÃO torna mais frequente: medido no cenário sem forçar o rollback
    (tabela travada, teto de 300ms), dá 1 conexão por `warning` com e sem a
    guarda, porque o `statement_timeout` deixa a conexão em `INERROR` mas sadia
    e o `rollback()` funciona. O que alcança o ciclo é o rollback também falhar
    (socket quebrado, servidor morto), que já era alcançável antes deste PR — e
    é por isso que este teste o força explicitamente abaixo.

    `psycopg` tem `logging.getLogger("psycopg")` e emite
    `logger.warning("error ignored in rollback on %s: %s", …)` em
    `Connection.__exit__` (psycopg 3.3.5, `connection.py:170`) quando o `with`
    sai com exceção E o `rollback()` também falha. Esse record sobe ao root, o
    `_DashboardHandler` o pega e chama `log_system_event_sync` DE NOVO — connect
    novo na mesma tabela travada, cada volta custando um TCP connect mais o teto.

    Aqui o handler fica ATTACHED (ao contrário dos testes de cronômetro): é
    justamente ele que se quer ver não reentrando. O gatilho literal de
    `connection.py:170` é reproduzido forçando `rollback()` a levantar na
    conexão REAL devolvida pelo connect."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "300")
    observability.get_logger(__name__)
    root = logging.getLogger()
    assert any(isinstance(h, observability._DashboardHandler) for h in root.handlers), \
        "sem o handler no root não há reentrância a provar"

    conexoes: list[int] = []
    real = psycopg.connect

    def boom():
        raise RuntimeError("rollback falhou (gatilho de connection.py:170)")

    # A trava é aberta ANTES da espiã: a conexão do lock não pode ser contada
    # nem ganhar o `rollback` quebrado.
    with tabela_travada(3.0):
        def espia(url, **kw):
            conexoes.append(1)
            conn = real(url, **kw)
            conn.rollback = boom
            return conn

        monkeypatch.setattr(psycopg, "connect", espia)
        try:
            logging.getLogger("teste_teto_reentrancia").warning("x")
        except RecursionError:  # pragma: no cover - é o bug que a guarda fecha
            pytest.fail("RecursionError: o handler reentrou em log_system_event_sync")

    assert len(conexoes) == 1, (
        f"{len(conexoes)} conexões para UM warning — o WARNING do psycopg "
        "reentrou no _DashboardHandler"
    )
    assert "reentrada ignorada" in capsys.readouterr().err


# ── Controles POSITIVOS: o caminho legítimo continua funcionando ─────────────

def test_tabela_livre_continua_gravando(user_id, capsys):
    """Sem `setenv` (default de 2000ms) e sem lock: grava e acha. Sem este
    controle, tudo acima passaria num código que simplesmente não grava nada."""
    try:
        log_system_event_sync("warning", EVENTO_LIVRE, "mensagem",
                              source="teste", user_id=user_id)
        assert _linhas(EVENTO_LIVRE) == 1, capsys.readouterr().err
        assert recent_event_exists(EVENTO_LIVRE, user_id) is True
    finally:
        _limpa(EVENTO_LIVRE)


@pytest.mark.parametrize("valor", ["0", "-1", "abacaxi", "", "50",
                                   "2147483648", "99999999999999999999"])
def test_valor_sem_sentido_volta_ao_default(valor, monkeypatch, user_id):
    """`"0"` é o que mais importa: no Postgres `statement_timeout=0` significa
    SEM LIMITE, então obedecer a env INVERTERIA o sentido do parâmetro —
    "desligado" na cabeça de quem configura viraria "sem teto nenhum". `-1` o
    servidor recusa (o connect inteiro falharia) e `"50"` está abaixo do piso de
    100ms, onde nem o INSERT em tabela livre cabe.

    O intervalo tem DOIS lados: `"2147483648"` é o primeiro valor que o Postgres
    recusa no connect ("value exceeds integer range"), e com ele obedecido o
    módulo perdia 100% dos registros com a suíte inteira verde — é a metade (b)
    abaixo que pega isso. `"99999999999999999999"` é o mesmo defeito com um
    inteiro que nem cabe em 64 bits.

    Duas metades: (a) o helper devolve o default; (b) com a tabela livre a linha
    AINDA é gravada — um valor que o servidor recusasse derrubaria o connect."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", valor)
    assert _statement_timeout_options() == TETO_PADRAO_OPTIONS

    try:
        log_system_event_sync("warning", EVENTO_DEFAULT, "mensagem",
                              source="teste", user_id=user_id)
        assert _linhas(EVENTO_DEFAULT) == 1
    finally:
        _limpa(EVENTO_DEFAULT)


def test_valor_no_limite_superior_e_obedecido(monkeypatch):
    """Controle POSITIVO do lado de cima: NO limite a env ainda manda. Sem ele,
    o caso acima passaria num helper que joga fora tudo que é grande — e aí a
    env deixaria de configurar o que ela existe para configurar."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", str(_TETO_MAX_MS))
    assert _statement_timeout_options() == f"-c statement_timeout={_TETO_MAX_MS}ms"


def test_options_chega_no_connect(monkeypatch, user_id):
    """O `options` não basta existir no helper: tem de chegar no `connect`.
    Espiã no molde de `git show b113e9c:tests/test_log_system_event_timeout_ms.py`."""
    vistos: list[dict] = []
    real = psycopg.connect

    def espia(url, **kw):
        vistos.append(kw)
        return real(url, **kw)

    monkeypatch.setattr(psycopg, "connect", espia)
    try:
        log_system_event_sync("warning", EVENTO_OPTIONS, "mensagem",
                              source="teste", user_id=user_id)
        assert vistos and vistos[0].get("options") == TETO_PADRAO_OPTIONS, vistos
        assert vistos[0].get("connect_timeout") == 2, vistos

        vistos.clear()
        assert recent_event_exists(EVENTO_OPTIONS, user_id) is True, \
            "o caminho de leitura parou de encontrar a linha"
        assert vistos and vistos[0].get("options") == TETO_PADRAO_OPTIONS, vistos
    finally:
        _limpa(EVENTO_OPTIONS)
