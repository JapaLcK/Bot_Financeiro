"""`GET /api/v2/eventos`: aviso ao vivo (SSE) de que algo do usuário mudou.

O aviso só diz O QUÊ mudou (`{"recurso": ...}`), sem dado financeiro nem id: o
cliente refaz as consultas pela API, que passa pelo `usuario_atual` de novo. Sem
`id:` de propósito — não há replay; quem reconecta refaz tudo, e o que caiu no
meio não se perde.

A sessão é rechecada (`usuario_atual`) antes de cada envio e a cada
`RECHECAGEM_S`: sessão revogada, token expirado, plano ou chave v2 caídos nunca
recebem aviso, e o stream fecha em até `RECHECAGEM_S`. O `EventSource` reconecta
e leva a recusa no envelope.

`avisar` fala com os streams DESTE loop. As escritas financeiras chegam por
LISTEN/NOTIFY: o trigger `trg_pb_aviso_escrita` (`db/schema.py`,
`TABELAS_QUE_AVISAM`) faz `pg_notify('pb_escrita', <uid>)`, que sai só no commit
e vale para qualquer thread ou processo que grave (WhatsApp, `bot.py`, rota);
`escutar_banco`, uma tarefa do lifespan com conexão própria, repassa cada uid
ao `avisar` como `"tudo"`. Depois de uma queda ela avisa todo inscrito, porque o
que foi gravado no meio não chegou.
"""
import asyncio
import os
import sys
import time
from collections.abc import AsyncIterable
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.v2.sessao import usuario_atual

router = APIRouter()

# ponytail: cada stream faz ~5 queries a cada 30 s no pool síncrono (8 conexões);
# se o número de streams abertos pesar no pool, cachear a rechecagem por sessão.
RECHECAGEM_S = 30.0
# O mesmo teto do `/ws` (`ConnectionManager.MAX_CONNECTIONS_PER_USER`).
MAX_STREAMS = 5
# A cada BATIMENTO_S sem aviso, um `select 1` descobre conexão morta em silêncio.
BATIMENTO_S = 30.0
# Caído sem voltar (PgBouncer em modo transação, `DATABASE_URL` errado): loga de
# novo a cada RELOG_CAIDO_S, para a queda nunca ficar em silêncio.
RELOG_CAIDO_S = 600.0

Recurso = Literal["open_finance", "tudo"]


class Aviso(BaseModel):
    recurso: Recurso


class _Inscrito:
    def __init__(self):
        self.pendentes: set[str] = set()
        self.acordar = asyncio.Event()


# Só do loop do processo web: nada asyncio nasce no nível do módulo.
_inscritos: dict[int, set[_Inscrito]] = {}


def avisar(user_id: int, recurso: Recurso) -> None:
    """Avisa os streams abertos de `user_id`, e só os dele. Síncrona e SÓ na thread
    do event loop (rota `async`, tarefa do loop): de outra thread, o `Event` não é
    seguro. Chame depois do commit — o cliente refaz a consulta na hora."""
    for ins in _inscritos.get(user_id, ()):
        ins.pendentes.add(recurso)
        ins.acordar.set()


async def _vaga(uid: int = Depends(usuario_atual)):
    """Confere e reserva a vaga sem `await` no meio: aberturas simultâneas não
    passam juntas. Aqui e não no gerador, que só roda com os headers já fora (o
    429 não chegaria). A saída do `yield` roda depois de o stream terminar."""
    vizinhos = _inscritos.setdefault(uid, set())
    if len(vizinhos) >= MAX_STREAMS:
        raise HTTPException(status_code=429, detail="Limite de conexões simultâneas atingido.")
    ins = _Inscrito()
    vizinhos.add(ins)
    try:
        yield ins
    finally:
        vizinhos.discard(ins)
        if not vizinhos:
            del _inscritos[uid]


@router.get("/eventos", response_class=EventSourceResponse)
async def eventos(request: Request, ins: _Inscrito = Depends(_vaga)) -> AsyncIterable[Aviso]:
    while True:
        try:
            await asyncio.wait_for(ins.acordar.wait(), RECHECAGEM_S)
        except TimeoutError:
            pass
        ins.acordar.clear()
        lote, ins.pendentes = ins.pendentes, set()
        try:
            await run_in_threadpool(usuario_atual, request)
        except StarletteHTTPException:
            return  # sessão, plano ou chave caiu: fecha sem mandar o lote
        for recurso in sorted(lote):
            yield Aviso(recurso=recurso)


async def escutar_banco() -> None:
    """LISTEN `pb_escrita` → `avisar(uid, "tudo")`, para sempre. Conexão própria
    (não o pool: ela fica presa no LISTEN) e `DATABASE_URL` lido na hora, como o
    `_get_pool`. Caiu: reconecta com espera crescente, loga a 1ª falha e, enquanto seguir
    caído, de novo a cada `RELOG_CAIDO_S`."""
    espera = caiu_em = logado_em = 0.0
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(
                os.environ["DATABASE_URL"], autocommit=True, connect_timeout=10,
                application_name="pb_escrita_listen",
            ) as conn:
                await conn.execute("LISTEN pb_escrita")
                espera = 0.0
                for uid in _inscritos:
                    avisar(uid, "tudo")
                while True:
                    async for n in conn.notifies(timeout=BATIMENTO_S):
                        try:
                            avisar(int(n.payload), "tudo")
                        except ValueError:
                            pass
                    await conn.execute("select 1")
        except Exception as exc:
            agora = time.monotonic()
            if not espera:
                caiu_em = logado_em = agora
                print(f"[eventos] LISTEN pb_escrita caiu: {exc!r}", file=sys.stderr, flush=True)
            elif agora - logado_em >= RELOG_CAIDO_S:
                logado_em = agora
                print(f"[eventos] LISTEN pb_escrita segue caído há {agora - caiu_em:.0f} s: {exc!r}",
                      file=sys.stderr, flush=True)
            espera = min(espera * 2 or 1.0, 60.0)
            await asyncio.sleep(espera)
