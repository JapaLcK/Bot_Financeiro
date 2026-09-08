"""O pool async de frontend/routes/shared.py atravessando dois event loops.

A mina é o `_db_pool_lock`: um `asyncio.Lock()` de nível de módulo, criado no
import. `asyncio.Lock` só resolve o event loop no `acquire` CONTENDIDO
(`asyncio.mixins._LoopBoundMixin._get_loop`) e a partir daí fica preso a ele
PARA SEMPRE. Quem zerar só `shared._db_pool` e depois disparar concorrência de
outro loop leva `RuntimeError: ... is bound to a different event loop` DENTRO
do gather — e a pendura vem daí, de segunda ordem: a corrotina irmã fica órfã
em `pool.open()`, o pool aberto nunca chega a `_db_pool` e o `asyncio.run`
seguinte trava em `asyncio.runners._cancel_all_tasks`. Foi o que travou a suíte
no #211; lá o conserto DESVIOU da mina (abrir o pool antes do gather), aqui ela
é reproduzida de frente contra `shared.reset_db_pool()`.

Reproduz direto, sem depender de arranjo de arquivo: no #211, 6 arquivos em
ordem inversa e sem o fix ainda davam 109 passed — arranjo não discrimina.
"""

import asyncio
import os
import signal
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import frontend.routes.shared as shared


async def _duas_conexoes_em_gather():
    """Zera o pool, abre DUAS conexões concorrentes, fecha o pool AQUI.

    O gather é o que importa: com o pool em `None`, as duas corrotinas chamam
    `_get_db_pool` juntas e a segunda espera no lock — a contenção que prende o
    lock ao loop. O `close` tem de acontecer dentro do loop em que o pool
    nasceu; deixá-lo aberto pendura o teardown pelos workers do `psycopg_pool`,
    o que confundiria o diagnóstico com um travamento de outra causa.
    """
    shared.reset_db_pool()

    async def _um():
        async with await shared.db_connect() as conn:
            cur = await conn.execute("select 1 as um")
            return (await cur.fetchone())["um"]

    try:
        return await asyncio.gather(_um(), _um())
    finally:
        if shared._db_pool is not None:
            await shared._db_pool.close()


def _pendurou(*_):
    """Handler do SIGALRM: transforma a pendura em falha deste teste só."""
    raise TimeoutError("pendurou: o _db_pool_lock voltou a ficar preso a loop morto")


# `signal.SIGALRM` é POSIX-only e `docs/readme.md:18` lista Windows como
# suportado. A COLETA não quebra (o uso está no corpo, não no import), mas lá o
# caso daria `AttributeError` em vez de rodar. No CI (ubuntu-latest nos 4 jobs)
# o skip nunca dispara.
@pytest.mark.skipif(
    not hasattr(signal, "SIGALRM"), reason="SIGALRM é POSIX-only (não existe no Windows)"
)
def test_o_pool_async_aguenta_dois_event_loops_com_gather():
    """Dois `asyncio.run`, cada um com o pool zerado e DUAS conexões em gather.

    CONTROLE NEGATIVO: apague `_db_pool_lock = asyncio.Lock()` de
    `shared.reset_db_pool` e este caso passa no loop 1 e PENDURA no loop 2, em
    `asyncio.runners._cancel_all_tasks`. MEDIDO em 03/09/2026: com o controle
    armado, `1 failed in 30,7s` — e o arquivo SEGUINTE ainda roda e reporta.
    """
    anterior = shared.reset_db_pool()
    # SIGALRM, e não `faulthandler.dump_traceback_later(30, exit=True)`: o
    # `exit=True` MATA o processo do pytest e leva junto o relatório dos
    # demais testes da suíte. O sinal levanta TimeoutError na main thread e falha
    # SÓ este caso — MEDIDO com o controle negativo armado: `1 failed, 6 passed
    # in 31,1s`, com o arquivo seguinte rodando e reportando normalmente.
    # 30s: o caso verde leva 0,55s. Este watchdog cobre só este teste; a pendura
    # de QUALQUER outro é do `timeout-minutes` do job `pytest`
    # (.github/workflows/tests.yml), que fecha a categoria.
    # `signal.signal` já DEVOLVE o handler anterior — guardá-lo e repô-lo no
    # `finally` custa duas palavras e evita deixar `_pendurou` instalado como
    # handler global para os demais testes da suíte.
    handler_anterior = signal.signal(signal.SIGALRM, _pendurou)
    signal.alarm(30)
    try:
        for _ in range(2):
            assert asyncio.run(_duas_conexoes_em_gather()) == [1, 1]
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, handler_anterior)
        # `reset_db_pool` de novo antes de restaurar: o gather do último loop
        # prendeu o lock a um loop que acabou de morrer, e deixá-lo no módulo
        # seria plantar aqui a mina que este caso existe para provar apagada.
        shared.reset_db_pool()
        shared._db_pool = anterior
