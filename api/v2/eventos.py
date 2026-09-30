"""`GET /api/v2/eventos`: aviso ao vivo (SSE) de que algo do usuário mudou.

O aviso só diz O QUÊ mudou (`{"recurso": ...}`), sem dado financeiro nem id: o
cliente refaz as consultas pela API, que passa pelo `usuario_atual` de novo. Sem
`id:` de propósito — não há replay; quem reconecta refaz tudo, e o que caiu no
meio não se perde.

A sessão é rechecada (`usuario_atual`) antes de cada envio e a cada
`RECHECAGEM_S`: sessão revogada, token expirado, plano ou chave v2 caídos nunca
recebem aviso, e o stream fecha em até `RECHECAGEM_S`. O `EventSource` reconecta
e leva a recusa no envelope.

Um processo só: `avisar` fala com os streams DESTE loop. Com vários processos
(ou avisos de fora do web, como o `bot.py`), o desenho é LISTEN/NOTIFY — quem
grava faz `pg_notify` dentro da própria transação (sai só no commit, e serve
para thread e para outro processo), e cada processo web mantém uma conexão
`LISTEN` que repassa ao `avisar` local. Não construído.
"""
import asyncio
from collections.abc import AsyncIterable
from typing import Literal

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


async def _vaga(uid: int = Depends(usuario_atual)) -> int:
    # Aqui e não no gerador: lá os headers já saíram e o 429 não chegaria.
    # Teto mole: aberturas simultâneas passam juntas antes de se inscrever (6 no pior caso).
    if len(_inscritos.get(uid, ())) >= MAX_STREAMS:
        raise HTTPException(status_code=429, detail="Limite de conexões simultâneas atingido.")
    return uid


@router.get("/eventos", response_class=EventSourceResponse)
async def eventos(request: Request, uid: int = Depends(_vaga)) -> AsyncIterable[Aviso]:
    ins = _Inscrito()
    _inscritos.setdefault(uid, set()).add(ins)  # antes de qualquer await
    try:
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
    finally:
        vizinhos = _inscritos.get(uid)
        if vizinhos is not None:
            vizinhos.discard(ins)
            if not vizinhos:
                del _inscritos[uid]
