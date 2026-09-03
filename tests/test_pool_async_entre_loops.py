"""O pool async de frontend/routes/shared.py atravessando dois event loops.

A mina é o `_db_pool_lock`: um `asyncio.Lock()` de nível de módulo, criado no
import. `asyncio.Lock` só resolve o event loop no `acquire` CONTENDIDO
(`asyncio.mixins._LoopBoundMixin._get_loop`) e a partir daí fica preso a ele
PARA SEMPRE — quem zerar só `shared._db_pool` e depois disparar concorrência
de outro loop pendura o processo, não falha. Foi o que travou a suíte no #211;
lá o conserto DESVIOU da mina (abrir o pool antes do gather), aqui ela é
reproduzida de frente contra `shared.reset_db_pool()`.

Reproduz direto, sem depender de arranjo de arquivo: no #211, 6 arquivos em
ordem inversa e sem o fix ainda davam 109 passed — arranjo não discrimina.
"""

import asyncio
import faulthandler
import os
import sys

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


def test_o_pool_async_aguenta_dois_event_loops_com_gather():
    """Dois `asyncio.run`, cada um com o pool zerado e DUAS conexões em gather.

    CONTROLE NEGATIVO: apague `_db_pool_lock = asyncio.Lock()` de
    `shared.reset_db_pool` e este caso passa no loop 1 e PENDURA no loop 2 —
    dump do faulthandler em `asyncio.runners._cancel_all_tasks`.
    """
    anterior = shared.reset_db_pool()
    faulthandler.dump_traceback_later(30, exit=True)   # ponytail: teto — se o
    # conserto regredir isto MATA o processo do pytest em 30s; sem isto o modo
    # de falha é travar o runner do GitHub por 6h (foi o do #211). O dump em si
    # some sob a captura de fd do pytest (ele morre com o processo); para lê-lo,
    # rode com `-s` — MEDIDO nas duas formas com o controle negativo armado.
    try:
        for _ in range(2):
            assert asyncio.run(_duas_conexoes_em_gather()) == [1, 1]
    finally:
        faulthandler.cancel_dump_traceback_later()
        # `reset_db_pool` de novo antes de restaurar: o gather do último loop
        # prendeu o lock a um loop que acabou de morrer, e deixá-lo no módulo
        # seria plantar aqui a mina que este caso existe para provar apagada.
        shared.reset_db_pool()
        shared._db_pool = anterior
