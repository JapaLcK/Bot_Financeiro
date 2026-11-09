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
e vale para qualquer thread ou processo que grave (WhatsApp, rota, job, script);
`escutar_banco`, uma tarefa do lifespan com conexão própria, repassa cada uid
ao `avisar` como `"tudo"`, ANTES marcando o cache de insights do dono como velho
(`_Marcador`: o refetch do aviso já recalcula). O marcador usa uma 2ª conexão async
própria, fora do pool e do threadpool, com prazos no servidor (`OPCOES_MARCADOR`) e
no cliente: o upsert espera até `TETO_MARCADOR_S`, abrir a conexão até
`CONNECT_MARCADOR_S`, e reabrir uma conexão caída soma os dois. Toda falha pausa o
marcador por `PAUSA_MARCADOR_S`, então uma rajada paga isso uma vez, não por aviso; a
exceção é a conexão antiga caída, que reabre sem pausar. A reconexão do LISTEN zera a
pausa. Falha vai ao stderr e o aviso sai igual: quem não marcou fica pendente e vai nas
próximas marcações (aviso ou batimento), até `LOTE_MARCADOR` por vez, que depois de
gravar avisam de novo esses usuários, porque o refetch do 1º aviso achou o cache velho.
Usuário preso por outra transação é pulado e continua pendente, sem segurar os demais.
Depois de uma queda ela avisa todo inscrito, porque o que foi gravado no meio não
chegou.
"""
import asyncio
import os
import sys
import time
from collections.abc import AsyncIterable
from itertools import islice
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.v2.sessao import usuario_atual
from core.pg_text import causa_segura

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
# O marcador de insights segura o aviso SSE de todos: prazos no servidor (no connect, como o
# `statement_timeout_options` do log), teto no cliente (conexão muda, sem RST: o servidor não
# corta nada) e pausa depois de qualquer falha. 2 s é o mínimo do `connect_timeout` do libpq.
OPCOES_MARCADOR = "-c lock_timeout=300ms -c statement_timeout=500ms"
TETO_MARCADOR_S = 1.0
CONNECT_MARCADOR_S = 2
# Uma parte dos pendentes por marcação: o upsert segura o aviso de todos e tem de caber no `statement_timeout` de
# `OPCOES_MARCADOR` com o banco carregado (medição no PR). O que passa do lote fica na fila para as próximas.
LOTE_MARCADOR = 500
PAUSA_MARCADOR_S = 30.0

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


class _Marcador:
    """A conexão do marcador de insights (D13), aberta sob demanda; o `async with` a fecha com a do LISTEN, mas os
    pendentes sobrevivem à reconexão (a instância é a mesma). Nunca levanta.

    `pendentes` é uma fila por ordem de chegada (dict) e guarda só quem JÁ foi avisado sem marca; o uid do aviso corrente
    entra SEMPRE no lote e o `avisar` normal dele vem depois de `marcar`. Cada marcação grava até `LOTE_MARCADOR`, do
    começo da fila. Estados: F (sem conexão), A (aberta), P (pausado). Eventos: aviso (`marcar(uid)`), batimento
    (`marcar()`).
    - F/A × aviso ou batimento, upsert ok → A. Gravados saem de `pendentes` e, DEPOIS de gravar, os atrasados do lote
      recebem `avisar`, 1 por uid (o uid corrente não: o normal cobre). Quem não tem mais conta sai, sem aviso. O resto
      da fila espera a próxima marcação.
    - Usuário preso por outra transação (`users` ou o próprio marcador): pulado sem esperar, os demais gravam, ele vai
      para o FIM da fila (travados não tomam o lote dos outros). Ganha o aviso dele quando gravar.
    - Toda falha → P por `PAUSA_MARCADOR_S`, `pendentes` preservados com o lote inteiro, nenhum aviso extra: connect falho (recusa ou
      `CONNECT_MARCADOR_S`), upsert sem resposta em `TETO_MARCADOR_S` (conexão fica fechada), erro do servidor com a
      conexão de pé (`lock_timeout` no que `skip locked` não pula, `statement_timeout`, outro), conexão recém-aberta
      que cai no upsert.
    - Exceção: conexão ANTIGA caída no upsert (ociosa derrubada) reabre e tenta 1x no mesmo aviso.
    - P × aviso: o uid entra em `pendentes`, sem ir ao banco. P × batimento: nada. Fim da pausa: sem evento próprio, o
      próximo aviso ou batimento marca o lote seguinte, com o aviso extra.
    - Reconexão do LISTEN: fecha a conexão; `pausa_ate` zera (o LISTEN de pé prova que o banco abre conexão);
      `pendentes` ficam para a próxima marcação. Shutdown: `pendentes` se perdem.
    Pendência retida não é marca perdida: enquanto uma falha se repetir, a fila fica parada, sem descartar ninguém."""

    def __init__(self):
        self.conn, self.pausa_ate, self.pendentes = None, 0.0, {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if self.conn is not None:
            await self.conn.close()

    async def marcar(self, uid: int | None = None) -> None:
        from core.ai_patterns import invalidar_insights
        if uid is not None and -2**63 <= uid < 2**63:  # fora de bigint um payload estranho envenenaria o lote
            self.pendentes[uid] = None
        for _ in range(2):
            if not self.pendentes or time.monotonic() < self.pausa_ate:
                return
            reaberta = self.conn is None or self.conn.closed
            if reaberta:
                try:
                    self.conn = await psycopg.AsyncConnection.connect(
                        os.environ["DATABASE_URL"], autocommit=True, connect_timeout=CONNECT_MARCADOR_S,
                        options=OPCOES_MARCADOR, application_name="pb_insights_marca")
                except Exception as exc:
                    return self._falhou(exc)
            lote = list(islice(self.pendentes, LOTE_MARCADOR))
            if uid in self.pendentes and uid not in lote:
                lote[-1] = uid  # marca antes do `avisar` dele, mesmo com a fila cheia
            tarefa = asyncio.ensure_future(invalidar_insights(self.conn, sorted(lote)))
            if not (await asyncio.wait({tarefa}, timeout=TETO_MARCADOR_S))[0]:
                # Conexão muda: fechar ANTES de cancelar. Cancelada com a conexão aberta, a tarefa largada tenta o
                # cancel do psycopg pela mesma rede (5 s) e loga pelo `logging`, que grava no banco dentro do loop.
                await self.conn.close()
                tarefa.cancel()
                tarefa.add_done_callback(lambda t: t.cancelled() or t.exception())
                return self._falhou(TimeoutError())
            try:
                gravados, travados = tarefa.result()
            except Exception as exc:
                if reaberta or not self.conn.closed:
                    return self._falhou(exc)
                continue  # conexão ociosa derrubada: reabre 1x no mesmo aviso
            for saiu in lote:
                del self.pendentes[saiu]
            self.pendentes.update(dict.fromkeys(travados))  # fim da fila
            for atrasado in gravados - {uid}:  # o refetch do aviso deles achou o cache velho
                avisar(atrasado, "tudo")
            return

    def _falhou(self, exc: Exception) -> None:
        self.pausa_ate = time.monotonic() + PAUSA_MARCADOR_S
        # `print`, como o resto deste ouvinte: um `logger.warning` no loop grava `system_event_logs`
        # de forma síncrona (`_DashboardHandler`) e seguraria o aviso que este teto protege. Sem uid: a contagem basta.
        print(f"[eventos] marcador de insights falhou: {type(exc).__name__}, pausa {PAUSA_MARCADOR_S:g} s, "
              f"{len(self.pendentes)} pendente(s)", file=sys.stderr, flush=True)


async def escutar_banco() -> None:
    """LISTEN `pb_escrita` → `avisar(uid, "tudo")`, para sempre. Conexão própria
    (não o pool: ela fica presa no LISTEN) e `DATABASE_URL` lido na hora, como o
    `_get_pool`. Caiu: reconecta com espera crescente, loga a 1ª falha e, enquanto seguir
    caído, de novo a cada `RELOG_CAIDO_S`. A queda só acaba depois de um batimento
    (`select 1`) de pé: cair logo depois do LISTEN é a mesma queda."""
    espera = caiu_em = logado_em = 0.0
    marcador = _Marcador()
    while True:
        try:
            async with await psycopg.AsyncConnection.connect(
                os.environ["DATABASE_URL"], autocommit=True, connect_timeout=10,
                application_name="pb_escrita_listen",
            ) as conn, marcador:
                await conn.execute("LISTEN pb_escrita")
                marcador.pausa_ate = 0.0  # o LISTEN de pé prova que o banco abre conexão
                for uid in _inscritos:
                    avisar(uid, "tudo")
                while True:
                    async for n in conn.notifies(timeout=BATIMENTO_S):
                        try:
                            uid = int(n.payload)
                        except ValueError:
                            continue
                        await marcador.marcar(uid)  # antes do aviso: o refetch já acha o cache velho
                        avisar(uid, "tudo")
                    await conn.execute("select 1")
                    await marcador.marcar()  # os pendentes de uma pausa não esperam o próximo aviso
                    espera = 0.0  # um batimento inteiro de pé: a queda acabou
        except Exception as exc:
            agora = time.monotonic()
            if not espera:
                caiu_em = logado_em = agora
                print(f"[eventos] LISTEN pb_escrita caiu: {causa_segura(exc)}", file=sys.stderr, flush=True)
            elif agora - logado_em >= RELOG_CAIDO_S:
                logado_em = agora
                print(f"[eventos] LISTEN pb_escrita segue caído há {agora - caiu_em:.0f} s: {causa_segura(exc)}",
                      file=sys.stderr, flush=True)
            espera = min(espera * 2 or 1.0, 60.0)
            await asyncio.sleep(espera)
