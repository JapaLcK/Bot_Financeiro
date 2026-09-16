"""Teto de EXECUÇÃO das duas conexões de `core/system_event_log.py`, medido com
a tabela TRAVADA. A outra metade do assunto — o helper
`_statement_timeout_options`, que decide o valor — mora em
`tests/test_system_event_log_config.py`; as fixtures e o `tabela_travada` dos
dois moram em `tests/_system_event_log_helpers.py`.

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
DOIS `psycopg.connect` de `core/system_event_log.py`. VERMELHOS AQUI, os três:
`test_insert_com_tabela_travada_desiste_dentro_do_teto`,
`test_leitura_com_tabela_travada_desiste_dentro_do_teto` e
`test_warning_do_psycopg_nao_reentra_no_handler` — este último porque, sem teto,
o INSERT espera o lock cair e TERMINA BEM, e aí não há `__exit__` com exceção
para o psycopg avisar nem reentrada a ignorar. A MESMA injeção derruba
`test_options_chega_no_connect` no arquivo irmão
(`tests/test_system_event_log_config.py`) e o portão estrutural
`tests/test_log_falha_traceback.py::test_todo_connect_do_system_event_log_tem_timeout_e_teto`
— são 5 vermelhos somando os três arquivos, e só três deles são daqui. Os três
estavam verdes com o conserto, que é onde a injeção discrimina.

CONTROLE NEGATIVO DA GUARDA (injeção SEPARADA, outro caminho de código): faça
`_reentrou` devolver `False` sempre. VERMELHO: só
`test_warning_do_psycopg_nao_reentra_no_handler`. Ele aparece nos dois controles
por motivos opostos — aqui a reentrada acontece, ali ela nem chega a existir.

CONTROLE POSITIVO: a mudança RESTRINGE (passa a cancelar query). Sem
`test_tabela_livre_continua_gravando`, este arquivo inteiro passaria num código
que simplesmente não grava nada — que é pior que o bug. O irmão tem o seu
próprio, pelo mesmo motivo.

CONFUNDIDOR, e por isso ele tem teste próprio
(`test_destacar_o_handler_e_o_que_faz_o_cronometro_medir_a_funcao`): com a
tabela travada, QUALQUER `logger.warning()` da suíte também bloqueia. Se o
`_DashboardHandler` continuasse no root durante os testes de cronômetro, o
cronômetro estaria medindo o handler e não a função sob teste.
"""
from __future__ import annotations

import logging
import time

import psycopg
import pytest

import core.observability as observability
# Pela FACHADA, não pelo módulo de origem, e por dois motivos medidos: (1) é o
# caminho que a produção usa — nenhum dos 10 call sites de `recent_event_exists`
# fora de `tests/` importa de `core.system_event_log` (CLAUDE.md §3, "rode a
# conversa, não a função"); (2) com ele o arquivo COLETA na coluna antiga do
# `scripts/coluna_dupla.py`, onde `core.system_event_log` ainda não existe — e é
# o que tira o gate de prova FRACA (3 `<error>` de import, corpo nenhum rodou)
# para FORTE (os três cronômetros rodam e falham lá, sem `options`).
from core.observability import log_system_event_sync, recent_event_exists

from _system_event_log_helpers import (  # noqa: F401  (fixtures autouse)
    EVENTO_INSERT,
    EVENTO_LEITURA,
    EVENTO_LIVRE,
    _limpa,
    _linhas,
    sem_dashboard_handler,
    sem_env_de_teto,  # inerte hoje (4 testes fazem `setenv`, o 5º grava em ~4ms
                      # e cabe em qualquer valor aceito); fica porque é ela que
                      # torna verdadeira a premissa "default de 2000ms" do
                      # controle positivo com qualquer env no ambiente
    tabela_travada,
    tabelas_admin,
)


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


# ── Controle POSITIVO: o caminho legítimo continua funcionando ───────────────

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
