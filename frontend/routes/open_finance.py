"""Rotas de Open Finance (Pluggy + mock) — conexão, snapshot e webhook.

Etapa 4 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento.

O webhook /open-finance/pluggy/webhook está em CSRF_EXEMPT_PATHS no app —
o middleware CSRF compara o path da request, então a isenção segue valendo
com a rota registrada via router.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg_pool import PoolClosed, PoolTimeout

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.admin_dashboard import log_system_event
from core.audit import AuditEvent, list_audit_events, record_audit_event
from core.secure_compare import constant_time_eq
from core.pg_text import detalhe_seguro, limpa_para_pg
from core.services.pluggy import (
    PluggyApiError,
    PluggyConfigError,
    create_pluggy_api_key,
    create_pluggy_connect_token,
    delete_pluggy_item,
    get_pluggy_item,
    list_pluggy_connectors,
)
from core.services.plan_service import is_pro
from core.services.pluggy_sync import (
    ITEM_UPDATING,
    _env_int,
    refresh_and_sync_pluggy_user,
    sync_pluggy_item,
    sync_pluggy_user,
)
from db import (
    count_open_finance_connections,
    create_mock_open_finance_connection,
    delete_open_finance_transactions,
    disconnect_open_finance_connection,
    get_connections_by_item_id,
    get_open_finance_connection_by_item_id,
    get_open_finance_snapshot,
    item_registry_origins,
    list_pluggy_item_ids,
    pluggy_item_lock,
    register_item,
    save_pluggy_open_finance_item,
    token_hash,
    unregister_item,
    update_pluggy_open_finance_item_status,
    user_exists,
)
from frontend.routes import shared

router = APIRouter()

# Default DESLIGADO: o "Pluggy Bank" (dados sintéticos) não deve aparecer no
# catálogo em produção. Só ligar (=1) em ambiente de teste/sandbox.
PLUGGY_INCLUDE_SANDBOX = os.getenv("PLUGGY_INCLUDE_SANDBOX", "0") == "1"

# Eventos da Pluggy que disparam um sync (puxar contas/transações).
# transactions/deleted é tratado à parte (remove ids), não re-sincroniza.
PLUGGY_SYNC_EVENTS = {
    "item/created",
    "item/updated",
    "transactions/created",
    "transactions/updated",
}


# Um sync em voo por item + UM bit de "chegou evento enquanto rodava". A Pluggy
# manda `item/updated` e `transactions/created` com 0–17s de intervalo, e cada
# evento virava uma task: 10 `deadlock detected` medidos em produção. O dirty é
# BOOLEANO por item de propósito — fila de eventos aqui só adiaria o mesmo sync
# N vezes, e o sync é idempotente: uma re-execução cobre qualquer número de
# eventos que chegaram durante a anterior.
_INFLIGHT: dict[str, asyncio.Task] = {}
_DIRTY: set[str] = set()

# Só erro TRANSITÓRIO é retentado. Erro de programação ou de validação repetido
# 3x é o mesmo erro 3x — e ainda esconde o defeito no log.
_SYNC_MAX_ATTEMPTS = 3

# Tentativas de pegar o lock ao gravar uma reconexão. A janela do lock é só a
# fase de escrita e passa em segundos, então a segunda quase sempre entra.
_RECONNECT_LOCK_ATTEMPTS = 2

# PRAZO ÚNICO da GRAVAÇÃO da reconexão — lock, backoff, segunda tentativa e a
# escrita cabem todos aqui dentro. Antes, cada `pluggy_item_lock` reiniciava o
# relógio: até 15s de vaga MAIS 15s de advisory lock, vezes 2 tentativas, mais
# backoff — e depois disso a escrita ainda esperava o pool fora de qualquer teto
# (Codex #166, dois P2 seguidos). O pior caso não era só lento: a gravação podia
# acontecer DEPOIS do timeout do cliente, então o usuário via erro num fluxo que
# tinha dado certo, e reconectava de novo.
#
# 20s é o orçamento, não o alvo: no caminho comum o lock está livre e isto não
# custa nada. Quem estoura leva 503, que é recuperável — o item continua na
# Pluggy e o mesmo POST reaproveita.
#
# ESCOPO, porque a palavra "operação" enganou uma vez: isto NÃO é o teto do
# request. A rota (`pluggy_item_route`) tem SETE esperas, e o prazo cobre a
# QUARTA — as três de depois ficam fora dele:
#
#   | # | etapa                                   | teto                       |
#   |---|-----------------------------------------|----------------------------|
#   | 1 | `get_pluggy_item` (HTTP, + o token)     | `PLUGGY_TIMEOUT` (20s), 2× |
#   | 2 | `get_connections_by_item_id` (leitura)  | pool sync (ver abaixo)     |
#   | 3 | `_enforce_bank_limit` (leituras)        | pool sync (ver abaixo)     |
#   | 4 | `_grava_reconexao` (lock + escrita)     | **este prazo**             |
#   | 5 | `register_item` (escrita)               | pool sync (ver abaixo)     |
#   | 6 | `record_audit_event` (escrita)          | pool sync (ver abaixo)     |
#   | 7 | `get_open_finance_snapshot` (leitura)   | pool sync (ver abaixo)     |
#
# SEM número de linha, de propósito: esta tabela apodreceu TRÊS vezes neste
# mesmo PR (uma vez por rodada que inseriu linhas acima dela), e na última o
# erro uniforme de −5 denunciou que foi "corrigida" por aritmética, não por
# busca. Os sete nomes são únicos neste arquivo — `grep -n "<nome>"
# frontend/routes/open_finance.py` acha cada um sem ambiguidade, e não
# envelhece. Número de linha em comentário só se paga quando o alvo é NOUTRO
# arquivo, onde a busca custa mais.
#
# "pool sync" = espera de vaga no `ConnectionPool` de `db/connection.py:78`,
# `timeout=DB_CONNECT_TIMEOUT` com default **30**. Depois da vaga não há
# `statement_timeout`: 2, 3, 5, 6 e 7 não têm teto de execução, e 5 e 6 COMMITAM.
#
# Somadas, elas passam do que um proxy aguenta. Um teto de REQUEST é outra
# decisão — e o lugar dela provavelmente não é aqui, é o servidor. O que esta
# onda fecha é a etapa 4, que estava SEM teto nenhum e escrevia no banco.
#
# CUIDADO com `DB_CONNECT_TIMEOUT`: são QUATRO definições da mesma env var com
# DOIS defaults. `db/connection.py:78` = "30" (o pool sync, o da tabela acima);
# `core/admin_dashboard.py:48`, `frontend/routes/shared.py:51` e
# `frontend/finance_bot_websocket_custom.py:275` = "5". Ler o número do vizinho
# errado já produziu uma conta 3× maior neste mesmo comentário.
#
# Os dois `log_system_event` da etapa 4 (`of_reconnect_lock_retry` e
# `of_reconnect_lock_timeout`) ficavam FORA do prazo, e era o buraco maior: cada
# um abre conexão async NOVA (`core.admin_dashboard.db_connect`, com o
# `DB_CONNECT_TIMEOUT` de `core/admin_dashboard.py:48` — default **5**) e faz um
# INSERT SEM `statement_timeout`. O `connect_timeout` limita o handshake e nada
# limita o INSERT nem o commit, então o pior caso de cada log era ILIMITADO e
# qualquer número fechado aqui era PISO. Os dois passaram a ir pelo
# `_log_com_teto` (`asyncio.wait_for`), e aí o número vira TETO (Codex #166, P2):
#
#     ≤ 20,0s   o prazo INTEIRO      = as duas tentativas + o log do RETRY
#                                      (`min(_LOG_DIAG_TIMEOUT_S, folga)`) + o
#                                      backoff (`min(_backoff_sec, folga)`),
#                                      todos saindo do mesmo bolso
#   +  ≤ 2,0s   log FINAL            = _LOG_DIAG_TIMEOUT_S; roda com o prazo já
#                                      vencido, então é o único que soma por cima
#   =  ≈ 22,0s + o que está FORA (a lista no fim deste bloco)
#                  22,0s = _RECONNECT_DEADLINE_MS + _LOG_DIAG_TIMEOUT_S, e é o
#                  teto SÓ das duas escritas de diagnóstico. Não é o teto do
#                  `_grava_reconexao`: o commit/rollback do
#                  `save_pluggy_open_finance_item` e a fila do executor do
#                  `to_thread` somam por cima, sem número.
#
# Repartição típica DENTRO dos 20s, deduzida das constantes (nada aqui foi
# cronometrado em produção), com as duas tentativas estourando: 10,0s a 1ª
# (`folga // 2`), ≤ 2,0s o log do retry, ~0,4s de backoff (`_backoff_sec(1)`,
# 0,375–0,625s) e o resto na 2ª.
#
# O `DB_CONNECT_TIMEOUT` do `core/admin_dashboard.py:48` SAIU da conta: o
# `wait_for` corta em 2,0s independentemente dele. Era dele que vinham o piso de
# 25,0s desta conta (5 + 5 nos dois logs) e o de 70s da versão anterior dela — o
# cenário "e se o Railway definir 30?", que `.env.example` não define (grep vazio)
# e ninguém verificou. A pergunta deixou de importar aqui.
#
# A recontagem do backoff que este PR fez continua valendo: ela existe para o caso
# em que o log come quase toda a folga, e aí o sono antigo dormia por cima de
# tempo já gasto (`test_backoff_nao_dorme_por_cima_do_que_o_log_gastou`).
#
# O QUE CONTINUA FORA do teto, e é furo NOMEADO: o commit e o rollback do
# `save_pluggy_open_finance_item` (não há timeout por query no libpq — ver o
# `_CursorComTeto` em `db/open_finance.py`) e o tempo na fila do executor do
# `to_thread` (o FURO CONHECIDO no laço, abaixo). `wait_for` também não
# interrompe thread: quem limita a TENTATIVA é o orçamento que ela leva, não este
# teto — ele cobre só as duas escritas de diagnóstico.
def _prazo_reconexao_ms() -> int:
    """Prazo da reconexão, com config sem sentido voltando para o default.

    `0` é como se escreve "desligado" numa env. Com um piso de 1ms — que foi a
    primeira versão disto — `OF_RECONNECT_DEADLINE_MS=0` virava "TODA reconexão
    do Open Finance falha com 503", de lock LIVRE, e o log ainda dizia "lock do
    item ocupado", que é diagnóstico falso. Valor negativo idem. Abaixo de 1s
    não há reconexão possível (só o `connect` já leva mais que isso), então
    tratar como engano é mais honesto que obedecer.
    """
    ms = _env_int("OF_RECONNECT_DEADLINE_MS", 20000)
    return ms if ms >= 1000 else 20000


_RECONNECT_DEADLINE_MS = _prazo_reconexao_ms()

# Teto de CADA log de diagnóstico do `_grava_reconexao`. A conta completa está no
# bloco acima; o resumo é que sem ele o prazo prometido não era teto de nada.
_LOG_DIAG_TIMEOUT_S = 2.0


async def _log_com_teto(segundos: float, *args, **kwargs) -> None:
    """`log_system_event` que não pode furar o prazo da reconexão.

    `log_system_event` (`core/admin_dashboard.py:180`) abre conexão async NOVA e
    faz um INSERT sem `statement_timeout`: o `connect_timeout` limita o
    handshake e NADA limita o INSERT nem o commit. Era o que deixava o teto do
    `_grava_reconexao` ilimitado exatamente sob sobrecarga do banco, que é
    quando ele importa (Codex #166, P2).

    Engolir o `TimeoutError` é deliberado: o log é DIAGNÓSTICO, e perder o
    diagnóstico não pode virar um segundo modo de falha em cima do 503. A causa
    NÃO se perde — os dois chamadores emitem o `logging.getLogger(...).warning`
    local ANTES desta chamada, e esse canal não depende do banco (é o mesmo
    motivo pelo qual ele existe: ver o `except` do `_grava_reconexao`).

    Só `asyncio.TimeoutError` é engolido. `CancelledError` de fora (cliente
    desistiu, shutdown) continua subindo — o `wait_for` só converte em
    `TimeoutError` o cancelamento que ELE mesmo causou.

    Destacar com `asyncio.create_task` em vez de limitar seria pior nas duas
    pontas: sem ninguém aguardando, a exceção vira "task exception was never
    retrieved" e o log some em silêncio, e a escrita pendurada continua sem teto
    — só que agora fora da vista.

    Piso de 1ms pelo mesmo motivo do `_CursorComTeto`: com o prazo já vencido, o
    que se garante é que não fica parado para sempre, não que caiba.
    """
    try:
        await asyncio.wait_for(log_system_event(*args, **kwargs),
                               max(0.001, segundos))
    except asyncio.TimeoutError:
        pass


# HTTP da Pluggy que some sozinho: cota estourada e erro do lado dela. 404 fica
# de fora de propósito (é `item_missing`, tratado no sync), 4xx de credencial
# também — repetir não conserta chave errada.
_HTTP_TRANSITORIO = {429, 500, 502, 503, 504}


def _backoff_sec(tentativa: int) -> float:
    """Espera exponencial com jitter. Dois chamadores: a exceção `_retryable` e o
    `sync_in_progress`, que NÃO é exceção — duas cópias da fórmula seriam a
    mesma regra em dois lugares (§0.7)."""
    return 0.5 * (2 ** (tentativa - 1)) * (0.75 + random.random() / 2)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, PluggyApiError) and exc.status_code in _HTTP_TRANSITORIO:
        return True
    try:
        from psycopg import errors as pg_errors
        return isinstance(exc, (pg_errors.DeadlockDetected, pg_errors.SerializationFailure))
    except Exception:  # psycopg ausente/alterado: não retenta
        return False


def _salva_item_sob_lock(user_id: int, remote: dict, item_id: str,
                         budget_ms: int | None = None,
                         tinha_conexao_propria: bool = False,
                         criar_usuario: bool = True,
                         adocao_registro_id: int | None = None,
                         *, escrita_tentada: list | None = None) -> tuple[dict, bool]:
    """Grava a reconexão DENTRO do `pluggy_item_lock` do item.

    A relectura da geração em `_sync_pluggy_item_confirmado` não é atômica com as
    escritas que vêm depois dela. Uma reconexão que caísse nessa fresta deixava o
    run de geração velha gravar o espelho E rodar `import_open_finance_launches` /
    `import_open_finance_credit` — que criam LANÇAMENTO e COMPRA DE CARTÃO do
    usuário. O carimbo era recusado, mas nenhum sync posterior remove lançamento:
    o upsert só acrescenta. Resultado medido pelo Codex (#162, P1): transação
    fantasma de uma autorização que não vale mais, sobrevivendo à recuperação —
    tipicamente a conta que o usuário DESMARCOU ao reconectar.

    Pegar o mesmo lock aqui fecha a fresta na origem: enquanto um sync escreve, a
    reconexão espera; enquanto a reconexão grava, nenhum sync entra na fase de
    escrita — e o próximo a entrar relê a geração nova e aborta.

    `escrita_tentada` é o canal de SAÍDA para "a execução chegou à escrita?" — uma
    lista do chamador, marcada na linha anterior ao
    `save_pluggy_open_finance_item` e DESMARCADA no `PoolTimeout`/`PoolClosed`
    que prova que nem o pool foi adquirido. Existe porque `return` não sobrevive
    a exceção e o TIPO dela não distingue: `psycopg.OperationalError` sai igual
    do commit ambíguo e de tudo que roda antes (o `psycopg.connect` do
    `pluggy_item_lock`, o `set_config`, o `pg_advisory_lock`, as leituras das
    revalidações). Quem lê é o desfazimento da reivindicação de adoção no 503 de
    `_grava_reconexao`.

    Sem o lock NÃO grava. A primeira versão disto gravava assim mesmo e só logava
    o aviso — e essa é exatamente a escrita que o lock existe para serializar
    (Codex #162, P1): se o teto estourou é porque um sync ESTÁ na fase de
    escrita, já passou pela checagem de geração, e vai importar lançamento e
    compra de cartão da autorização velha. O fallback anulava o conserto no único
    caso em que ele importa.
    """
    t0 = time.monotonic()
    with pluggy_item_lock(item_id, budget_ms=budget_ms) as locked:
        if not locked:
            return None, False
        # Revalidação SOB o lock (Codex PR #217, 4º): se a conexão própria que
        # existia na validação da rota sumiu enquanto esperávamos o lock, quem
        # a apagou foi um reset de conta ou um disconnect — os dois únicos
        # fluxos que deletam conexão. Gravar agora ressuscitaria a linha que o
        # usuário acabou de mandar apagar, e o sync inicial repopularia os
        # dados. Aborta terminal (sem retry: o estado não volta sozinho; a
        # HTTPException passa por cima do except de infra do _grava_reconexao).
        # Item NOVO (sem conexão própria antes) não passa por aqui: conectar
        # banco DEPOIS de um reset é fluxo legítimo e não pode ser bloqueado.
        # Roda ANTES do cálculo do `resto`: o tempo gasto aqui sai do orçamento
        # da escrita sozinho (o monotonic é relido lá embaixo).
        if tinha_conexao_propria:
            propria_ainda_existe = any(
                int(c["user_id"]) == int(user_id)
                for c in get_connections_by_item_id(item_id)
            )
            if not propria_ainda_existe:
                from core.observability import log_system_event_sync

                log_system_event_sync(
                    "warning", "of_reconnect_aborted_state_gone",
                    f"Reconexão abortada: conexão do item {item_id} sumiu na espera do lock "
                    "(reset/disconnect concorrente)",
                    source="open_finance", user_id=user_id, details={"item_id": item_id},
                )
                raise HTTPException(
                    status_code=409,
                    detail="Sua conta foi reiniciada ou o banco foi desconectado enquanto "
                           "a conexão era concluída. Conecte o banco de novo.",
                )
        # A MESMA revalidação, pelo lado da ADOÇÃO (Codex #313, P1). O fato que a
        # adoção leu fora do lock não é "a conexão existe" — é "NENHUMA linha do
        # rastro deste item tem dono" (1ª guarda de `_adota_item_orfao`), e ele
        # muda na espera: duas entregas concorrentes do MESMO `item/created` leem
        # o rastro vazio, a 1ª adota, o usuário DESCONECTA, e a 2ª chegava aqui
        # com `tinha_conexao_propria=False` — item órfão nunca teve conexão, o
        # valor era honesto — pulando a revalidação acima e recriando a conexão
        # COM sync agendado, na carteira que o usuário acabou de limpar.
        # Uma releitura fecha os DOIS casos porque o disconnect preserva o
        # registry (`db/privacy.py`): duplicata pura (a 1ª já gravou o rastro) e
        # ressurreição pós-disconnect (o rastro sobreviveu à conexão). `exceto` é
        # a linha que ESTA entrega gravou segundos atrás — sem ela a adoção
        # legítima se recusaria a si mesma.
        #
        # E o aborto DESFAZ a própria reivindicação, dentro do lock, antes de
        # subir. Sem isso a revalidação sozinha trocava um estrago por outro
        # PIOR (medido pelo Tester, 30/30): duas entregas concorrentes gravam o
        # rastro antes de qualquer uma pegar o lock, cada uma enxerga o rastro
        # da OUTRA, as duas abortam, e sobra rastro com dono e ZERO conexão —
        # estado terminal, do qual nem a retentativa (1ª guarda) nem o script
        # one-shot (o filtro dele exclui rastro com dono) tiram o usuário.
        # Apagando a linha AQUI, com o lock na mão, quem entrar depois não vê
        # mais reivindicação nenhuma e adota: com N entregas simultâneas, o
        # último a pegar o lock ganha e os outros saem sem deixar rastro. São
        # DOIS os pontos que apagam (o outro é a desistência do lock, no 503 de
        # `_grava_reconexao`), e nos dois a linha é a que a própria adoção acabou
        # de escrever (`db.unregister_item`, que filtra por `user_id`).
        #
        # REGISTRADO, não consertado: quando quem "ganhou" foi o `POST
        # /pluggy-item` do MESMO usuário (o navegador voltou enquanto o webhook
        # esperava o lock), este delete tira a linha `webhook_adopt` e o log de
        # append perde o registro de que o item também entrou por ali. O desfecho
        # funcional está certo (1 conexão, 1 auditoria, a linha `pluggy_item`
        # fica) e NENHUMA decisão lê `origin` para isso — só o diagnóstico "por
        # onde ele entrou" fica mais pobre. Fechar exigiria distinguir origem
        # rival no aborto, e a distinção não muda decisão nenhuma hoje.
        if adocao_registro_id is not None:
            outras = item_registry_origins(item_id, exceto_registro_id=adocao_registro_id,
                                           exceto_user_id=user_id)
            if outras:
                unregister_item(adocao_registro_id, user_id)
                raise HTTPException(
                    status_code=409,
                    detail=f"Item {item_id} já foi atribuído por outra porta "
                           f"({sorted(outras)}): adoção abortada sob o lock.",
                )
        # O que sobrou DEPOIS de pegar o lock vai para a escrita. Sem isto o
        # orçamento parava aqui: `save_pluggy_open_finance_item` esperava o pool
        # (até `DB_CONNECT_TIMEOUT`) fora do prazo, e podia COMMITAR depois de o
        # cliente ter desistido — o mesmo defeito que o prazo veio consertar, um
        # degrau adiante (Codex #166, P2).
        #
        # Piso de 1ms em vez de desistir: já temos o lock, e abrir mão dele aqui
        # deixaria a reconexão sem gravar tendo pago o preço todo. O que se
        # garante é que a escrita não espera INDEFINIDAMENTE, não que ela caiba
        # num prazo que já venceu.
        #
        # `psycopg.OperationalError` sobe daqui de propósito — quem trata é o
        # `_grava_reconexao`. Tratar AQUI só sabia devolver `(None, False)`, que
        # o chamador lê como "lock ocupado": a CATEGORIA inteira (toda subclasse
        # de `psycopg.OperationalError` em `psycopg.errors`, mais `PoolTimeout`,
        # `PoolClosed` e `TooManyRequests` do `psycopg_pool`) virava o mesmo log
        # falso, e o tipo do erro não aparecia em lugar nenhum. Sem contagem de
        # propósito: o número muda com a versão do psycopg e envelhece errado.
        resto = None if budget_ms is None else max(
            1, budget_ms - int((time.monotonic() - t0) * 1000))
        # A ÚNICA linha que responde "a escrita chegou a ser tentada?" (Codex
        # #313, P1). Ela atravessa o `except` do chamador porque é a lista DELE
        # que está sendo mutada — `return` não sobrevive a exceção, e o tipo do
        # erro não sabe responder isso: `psycopg.OperationalError` sai tanto
        # daqui de dentro (commit ambíguo) quanto de tudo que veio ANTES (o
        # `psycopg.connect` dedicado do `pluggy_item_lock`, o `set_config`, o
        # `pg_advisory_lock`, as leituras das duas revalidações). Marcado ANTES
        # da chamada, e não no `except`, porque a pergunta é do CHAMADOR ("pode
        # ter escrito?"): se o erro vier da saída do `with` depois de um commit
        # que deu certo, ele ainda tem de contar como escrita tentada.
        if escrita_tentada is not None:
            escrita_tentada.append(True)
        try:
            return save_pluggy_open_finance_item(
                user_id, remote, budget_ms=resto, criar_usuario=criar_usuario), True
        except (PoolTimeout, PoolClosed):
            # ... e DESMARCA no único erro que prova o contrário. Os dois só
            # saem do `getconn`, e o `get_conn()` do `save_...` é a ÚNICA
            # aquisição de pool da função e a primeira linha com I/O dela: aqui
            # nenhum statement rodou. (Medido no psycopg_pool 3.3.1: `PoolTimeout`
            # só nasce da espera do `getconn`; `PoolClosed`, do
            # `_check_open_getconn`; a devolução no fim do `with` levanta
            # `ValueError`, não estes. A invariante do "única aquisição" tem
            # guarda em `tests/test_of_webhook_adopt_503.py`.)
            #
            # É o membro MAIS PROVÁVEL da classe, e não precisa de infra doente:
            # o `resto` acima tem piso de 1 ms, então uma espera longa pelo lock
            # — o cenário para o qual esta feature existe — manda
            # `get_conn(timeout=0.001)`. Medido em 2026-09-08 no Postgres local
            # SAUDÁVEL (`ConnectionPool(open=True)` + `connection(timeout=0.001)`
            # imediato): pool frio → `PoolTimeout`; quente → ok. O tempo varia com
            # a carga (1,4 ms e 4,2 ms em duas medições), remeça antes de reusar —
            # o que não varia é o desfecho. Sem isto, o caminho
            # mais provável de todos preservava a reivindicação sem uma única
            # escrita e reconstruía o estado terminal que o P0 fechou.
            #
            # `pop()` e não `clear()`: a lista é do PRAZO INTEIRO e cada chamada
            # marca no máximo uma vez, então cada uma só desfaz a SUA marca — uma
            # tentativa anterior que chegou a escrever continua contando.
            if escrita_tentada:
                escrita_tentada.pop()
            raise


async def _grava_reconexao(
    user_id: int, remote: dict, item_id: str, tinha_conexao_propria: bool = False,
    criar_usuario: bool = True, adocao_registro_id: int | None = None,
) -> dict:
    """Grava a reconexão sob o lock, RETENTANDO antes de desistir.

    Não é escolha entre dois males. Gravar sem lock cria lançamento fantasma;
    recusar de primeira deixa o banco conectado na Pluggy e invisível aqui. O
    passo que faltava é esperar de novo: a janela do lock é só a fase de escrita
    de um sync (nunca a leitura remota), então ela passa em segundos e a segunda
    tentativa quase sempre entra.

    Esgotou: 503 com mensagem de "tente de novo". O item continua existindo na
    Pluggy e o mesmo POST reaproveita, então a reconexão é recuperável — o
    lançamento fantasma não seria.

    PRAZO ÚNICO (Codex #166, P2). O relógio começa aqui e vale para a operação
    inteira: cada tentativa recebe só o que SOBROU, e o backoff sai do mesmo
    bolso. Antes, cada `pluggy_item_lock` reiniciava o teto — 15s de vaga + 15s
    de advisory, vezes 2, mais backoff — e o POST passava de um minuto sob
    contenção. O `_RECONNECT_LOCK_ATTEMPTS` continua como segunda trava, para o
    laço não girar quando o lock falha instantaneamente; quem manda no tempo é
    o prazo.
    """
    fim = time.monotonic() + _RECONNECT_DEADLINE_MS / 1000.0
    causa = None   # None = lock ocupado; senão, o erro de infra da última tentativa
    # A pergunta do desfazimento lá embaixo é "alguma tentativa pode ter
    # ESCRITO?", e ela tem duas metades.
    #
    # PRAZO INTEIRO: a lista é LATCHED (só cresce), e é por isso que não é
    # `causa is None` — `causa` é só a da ÚLTIMA tentativa, de propósito
    # (`test_causa_e_a_da_ultima_tentativa`), então infra na 1ª + lock ocupado na
    # 2ª chega ao fim com `causa is None` tendo passado por escrita de desfecho
    # DESCONHECIDO.
    #
    # PRECISÃO: quem marca é o `_salva_item_sob_lock`, na linha ANTES da escrita
    # — não o tipo da exceção (Codex #313, P1). `OperationalError` também sai de
    # tudo que roda antes dela (o `psycopg.connect` dedicado do
    # `pluggy_item_lock`, o `set_config` inicial, o `pg_advisory_lock` — que só
    # trata `LockNotAvailable`/`QueryCanceled` e deixa passar um `AdminShutdown`
    # —, as leituras das revalidações) e do próprio `get_conn` da escrita,
    # e nessas a escrita PROVADAMENTE não aconteceu. Preservar a reivindicação
    # ali reconstruía o estado terminal que o P0 fechou: zero conexões + rastro
    # com dono, a 1ª guarda de `_adota_item_orfao` recusando toda retentativa e
    # o `scripts/adotar_items_of_orfaos.py` sem enxergar a linha — o usuário sem
    # banco e sem saída pelo produto.
    escrita_tentada: list = []
    for tentativa in range(1, _RECONNECT_LOCK_ATTEMPTS + 1):
        folga_ms = int((fim - time.monotonic()) * 1000)
        if folga_ms < 1:
            break
        # O que sobra do prazo, DIVIDIDO pelas tentativas que ainda cabem. Dar o
        # prazo inteiro à primeira parecia certo e matava o retry: sob contenção
        # real ela esperava os 20s no `pg_advisory_lock`, voltava sem folga, e a
        # segunda nunca acontecia — medido, 1 tentativa e ZERO
        # `of_reconnect_lock_retry` no log, que é o sinal que separa "ocupado
        # mas recuperou" de "desistiu". A trava de tentativas virava código
        # morto e ninguém via, porque os testes stubam o lock e voltam na hora.
        restante_ms = max(1, folga_ms // (_RECONNECT_LOCK_ATTEMPTS - tentativa + 1))
        # FURO CONHECIDO, não fechado nesta onda: o `restante_ms` é fixado AQUI,
        # antes do dispatch, e o `t0` de `_salva_item_sob_lock` só começa a
        # contar quando a thread REALMENTE roda. O tempo na fila do executor
        # (default do asyncio, `min(32, cpus+4)` workers, e este request sozinho
        # já usa 6 `to_thread`) fica fora do orçamento. Fechar isso é medir o
        # `monotonic` dos dois lados e descontar — mudança no contrato de todos
        # os `to_thread` da rota, outro PR.
        try:
            connection, sob_lock = await asyncio.to_thread(
                _salva_item_sob_lock, user_id, remote, item_id, restante_ms,
                tinha_conexao_propria, criar_usuario, adocao_registro_id,
                escrita_tentada=escrita_tentada)
            causa = None
        except psycopg.OperationalError as exc:
            # UM `except` para a CATEGORIA inteira, cobrindo o lock E a escrita.
            # O Codex apontou oito vezes o mesmo fenômeno por portas diferentes,
            # e todas subiam como 500: `ConnectionTimeout` do `psycopg.connect`
            # dedicado do `pluggy_item_lock`, o `set_config('lock_timeout')` sem
            # teto, `PoolTimeout`, `PoolClosed`, `DeadlockDetected`, e o commit
            # da escrita. Todas são "a infra não respondeu" — o MESMO fenômeno
            # que, pela porta do lock, já dava o 503 recuperável documentado
            # (Codex #166, P2).
            #
            # Por que `OperationalError` e NÃO `psycopg.Error`: a fronteira é
            # medida. São subclasse de `OperationalError` — `PoolTimeout`,
            # `PoolClosed`, `QueryCanceled`, `LockNotAvailable`,
            # `ConnectionTimeout`, `DeadlockDetected`, `SerializationFailure`.
            # NÃO são — `UniqueViolation`, `ProgrammingError`, `ValueError`. A
            # hierarquia do psycopg já separa "não deu tempo" de "o código/a
            # entrada está errado", e o segundo grupo continua subindo:
            # `ValueError` vira o 400 da rota e o resto vira 500. `psycopg.Error`
            # engoliria bug de verdade — o usuário retentando para sempre um erro
            # que nunca vai passar.
            #
            # Retentar é seguro porque o upsert é `on conflict do update`,
            # idempotente. O que NÃO se pode prometer é "nada ficou pela metade":
            # vale para `QueryCanceled` (o cancelamento desfaz a transação), não
            # para o commit — `TransactionResolutionUnknown` e
            # `StatementCompletionUnknown` são desfecho DESCONHECIDO, e aí o
            # usuário pode levar 503 num fluxo que gravou. Não é regressão (na
            # `main` era 500 com o mesmo desfecho ambíguo), e a retentativa
            # idempotente converge; o que muda é o código de status.
            #
            # `causa` existe porque `(None, False)` sozinho é indistinguível de
            # "lock ocupado", e o log dizia isso para a categoria inteira — o
            # diagnóstico falso que o `_prazo_reconexao_ms` já tinha registrado
            # uma vez. Ela vai para os dois `log_system_event` abaixo E para o
            # `logging` local, e a segunda parte NÃO é redundância:
            # `log_system_event` (core/admin_dashboard.py:190-201) abre conexão
            # NOVA para gravar e engole TODA exceção com um `print` que não
            # carrega nem `message` nem `details`. Na família "o banco recusa
            # conexão" — `TooManyConnections`, `DiskFull`, `AdminShutdown`,
            # `InvalidPassword` — o canal do log é EXATAMENTE o que está
            # quebrado: `system_event_logs` fica vazio e a `causa` sumiria. Ela
            # só chega ao banco nas que não dependem de conexão nova
            # (`PoolTimeout`, `QueryCanceled`, `LockNotAvailable`).
            #
            # `causa` é a da ÚLTIMA tentativa, de propósito (é ela que decide o
            # desfecho): infra na 1ª + lock ocupado na 2ª faz o log FINAL dizer
            # "lock do item ocupado" com `erro: None`, e a infra da 1ª aparece só
            # no `of_reconnect_lock_retry`. Testado em
            # `test_causa_e_a_da_ultima_tentativa`.
            #
            # ponytail: o teto é a política de retry sob infra — sob
            # `TooManyConnections` este POST ainda tenta até 6 conexões (2
            # tentativas × dedicada do lock + pool da escrita + o log) num
            # servidor que acabou de recusar uma. Mudar isso é decidir não
            # retentar quando `causa` é da família de conexão; o gancho já existe
            # (é a própria `causa`), a decisão é de outro PR.
            connection, sob_lock = None, False
            causa = f"{type(exc).__name__}: {exc}"
        if sob_lock:
            return connection
        # O backoff também cabe no prazo: dormir "só mais um pouco" depois de
        # estourar é exatamente o que o deadline existe para impedir.
        folga = fim - time.monotonic()
        if tentativa < _RECONNECT_LOCK_ATTEMPTS and folga > 0:
            # MESMO canal local do log final (abaixo), e pela mesma razão — mas
            # aqui ele importa MAIS: este é o único log que carrega a causa da
            # 1ª tentativa, e se a 2ª pegar o lock e gravar, o `of_reconnect_
            # lock_timeout` nem acontece. Sem esta linha, uma infra na 1ª que
            # some na 2ª desaparece por completo justamente na família
            # (`TooManyConnections`/`DiskFull`/`AdminShutdown`) em que o
            # `log_system_event` não consegue gravar.
            logging.getLogger(__name__).warning(
                "of_reconnect_lock_retry item_id=%s tentativa=%s causa=%s",
                item_id, tentativa, causa or "lock do item ocupado")
            await _log_com_teto(
                min(_LOG_DIAG_TIMEOUT_S, fim - time.monotonic()),
                "warning", "of_reconnect_lock_retry",
                f"Reconexão não gravada ({tentativa}/{_RECONNECT_LOCK_ATTEMPTS}), "
                f"vai retentar — {causa or 'lock do item ocupado'}: {item_id}",
                source="open_finance",
                # `_antes_do_log` no nome porque é o que ele é: a folga medida
                # ANTES desta chamada. Este `log_system_event` gasta prazo (ver a
                # recontagem abaixo), então no instante em que a linha chega ao
                # banco o número já venceu — gravava `10000` valendo `-20000`.
                details={"item_id": item_id, "attempt": tentativa,
                         "restante_ms_antes_do_log": int(folga * 1000),
                         "erro": causa},
            )
            # RECONTA depois do log. Ele abre conexão async NOVA (o
            # `DB_CONNECT_TIMEOUT` de `core/admin_dashboard.py:48`, default 5) e
            # faz INSERT SEM `statement_timeout`, dentro da janela do prazo — é o
            # maior componente do que sobra dentro do prazo (a conta está em
            # `_prazo_reconexao_ms`). Medir a folga antes fazia o backoff dormir
            # POR CIMA de tempo já gasto. A recontagem NÃO é o que limita o log —
            # quem limita é o `_log_com_teto` acima, com `min(_LOG_DIAG_TIMEOUT_S,
            # folga)`; esta linha só divide o que sobrou depois dele.
            # Sem guarda de sinal: `asyncio.sleep` de valor negativo é no-op.
            await asyncio.sleep(min(_backoff_sec(tentativa), fim - time.monotonic()))

    # ANTES do `log_system_event`, e não em vez dele: este é o único canal que
    # sobrevive à família de erro que o `causa` existe para diagnosticar. O
    # `log_system_event` precisa de conexão NOVA para gravar (§ o comentário no
    # `except` acima), então sob `TooManyConnections`/`AdminShutdown` ele não
    # grava nada e engole a exceção. Mesmo padrão de `frontend/routes/shared.py:695`.
    logging.getLogger(__name__).warning(
        "of_reconnect_lock_timeout item_id=%s causa=%s", item_id,
        causa or "lock do item ocupado")
    await _log_com_teto(
        _LOG_DIAG_TIMEOUT_S,
        "error", "of_reconnect_lock_timeout",
        f"Reconexão NÃO gravada — {causa or 'lock do item ocupado'}: {item_id}",
        source="open_finance",
        details={"item_id": item_id, "deadline_ms": _RECONNECT_DEADLINE_MS,
                 "erro": causa},
    )
    # DESISTIR DO LOCK também desfaz a reivindicação da adoção — é o SEGUNDO
    # desfecho em que a escrita provadamente não aconteceu, e sem ele o conserto
    # do aborto sob o lock era REGRESSÃO contra a `main` (medido, duas colunas):
    # duas entregas concorrentes, a que pega o lock aborta e apaga a linha dela,
    # a que PERDE o lock deixava a dela para trás — 1 conexão saudável na `main`
    # virava 0 conexões + reivindicação abandonada, que é o estado terminal (a 1ª
    # guarda de `_adota_item_orfao` recusa a retentativa e o script one-shot não
    # lista rastro com dono). Com o desfazimento, ninguém escreveu e ninguém
    # reivindicou: a próxima entrega do `item/created` adota.
    #
    # "Provadamente" é medido, não deduzido do tipo do erro: `escrita_tentada` só
    # tem item se a execução CHEGOU ao `save_pluggy_open_finance_item`, e a marca
    # é feita lá, na linha anterior à chamada. Cobre o prazo inteiro e as duas
    # portas de saída sem escrita — o `if not locked` (lock ocupado) e a infra
    # PRÉ-escrita, que é `psycopg.OperationalError` igualzinho à do commit
    # (`psycopg.connect` do lock, `set_config`, `pg_advisory_lock`, as leituras
    # das revalidações, e o `get_conn` do próprio `save_...`) e
    # antes desta linha ficava preservada à toa (Codex #313, P1). Vazia: ninguém
    # escreveu, a reivindicação vai embora. Com item: desfecho DESCONHECIDO e a
    # reivindicação FICA — apagá-la poderia soltar uma segunda adoção por cima de
    # uma conexão que existe.
    #
    # AQUI e não no `if not locked`: apagar entre as tentativas deixaria a
    # tentativa que enfim pega o lock gravar a conexão sem rastro com dono — item
    # com banco conectado que o one-shot lista como órfão e a entrega seguinte
    # readota. O desfazimento é do desfecho, não da tentativa.
    #
    # DEPOIS dos logs: o diagnóstico do 503 já está gravado se este delete
    # estourar (ele sobe para o `except Exception` de `_adota_item_orfao`, que
    # loga e responde 200 ao webhook).
    if adocao_registro_id is not None and not escrita_tentada:
        await asyncio.to_thread(unregister_item, adocao_registro_id, user_id)
    raise HTTPException(
        status_code=503,
        detail="Não foi possível concluir a conexão agora. Tente de novo em alguns segundos.",
    )


async def _run_pluggy_sync_bg(item_id: str) -> None:
    """Roda o sync fora do request (fire-and-forget), logando o resultado REAL.

    Antes isto logava `pluggy_sync_done` em nível info mesmo com `ok:false` —
    397 sucessos e 41 falhas na mesma prateleira, e ninguém procurando por elas.
    """
    result: dict | None = None
    try:
        for tentativa in range(1, _SYNC_MAX_ATTEMPTS + 1):
            try:
                result = await asyncio.to_thread(sync_pluggy_item, item_id)
                # `sync_in_progress` não é exceção: volta como dict, então o
                # `break` abaixo encerrava a tarefa em silêncio e NINGUÉM mais
                # sincronizava. Cenário medido (Codex #162): o run de geração
                # velha segura o `pluggy_item_lock` enquanto escreve, o sync que
                # a reconexão agendou bate no lock e desiste — e com
                # `OF_REFRESH_ENABLED` off (o default) nada mais roda sozinho, o
                # espelho velho fica indefinidamente e a tela fica em âmbar até
                # o usuário tocar "Atualizar".
                #
                # SÓ `sync_in_progress`. `stale_authorization` NÃO se retenta: ele
                # significa "alguém mais novo assumiu", e quem assumiu já agendou
                # o próprio sync — retentar seria correr atrás de um trabalho que
                # já tem dono, e num par de runs que se atropelam viraria laço.
                if (result or {}).get("reason") != "sync_in_progress":
                    break
                if tentativa == _SYNC_MAX_ATTEMPTS:
                    break
                espera = _backoff_sec(tentativa)
                await log_system_event(
                    "warning", "pluggy_sync_retry",
                    f"Sync Pluggy vai repetir ({tentativa}/{_SYNC_MAX_ATTEMPTS}): {item_id}",
                    source="open_finance",
                    details={"item_id": item_id, "attempt": tentativa,
                             "error": "sync_in_progress", "sleep_sec": round(espera, 3)},
                )
                await asyncio.sleep(espera)
            except Exception as exc:
                if not _retryable(exc) or tentativa == _SYNC_MAX_ATTEMPTS:
                    raise
                espera = _backoff_sec(tentativa)
                await log_system_event(
                    "warning", "pluggy_sync_retry",
                    f"Sync Pluggy vai repetir ({tentativa}/{_SYNC_MAX_ATTEMPTS}): {item_id}",
                    source="open_finance",
                    details={"item_id": item_id, "attempt": tentativa,
                             "error": type(exc).__name__, "sleep_sec": round(espera, 3)},
                )
                await asyncio.sleep(espera)

        result = result if isinstance(result, dict) else {}
        # Atualização ao vivo (PWA): avisa o cliente conectado pra recarregar saldo/timeline.
        uid = result.get("user_id")
        if uid:
            try:
                from frontend.finance_bot_websocket_custom import manager
                await manager.broadcast_to_user(
                    int(uid), json.dumps({"type": "open_finance_synced", "item_id": item_id})
                )
            except Exception:
                pass

        ok = bool(result.get("ok"))
        reason = str(result.get("reason") or "")
        # Sync que deu certo mas deixou produto pra trás não pode ficar mudo: é
        # daqui que sai "atualizei o que deu" na tela e a contagem do painel.
        if ok and result.get("stale_products"):
            await log_system_event(
                "warning", "of_product_stale",
                f"Sync concluído com produto atrasado: {item_id}",
                source="open_finance",
                details={"item_id": item_id, "stale_products": result["stale_products"]},
            )
        if ok:
            nivel, evento = "info", "pluggy_sync_done"
        elif reason in ("item_missing", "owner_conflict"):
            nivel, evento = "error", "of_item_missing"
        else:
            nivel, evento = "warning", "pluggy_sync_done"
        await log_system_event(
            nivel,
            evento,
            f"Sync Pluggy {'concluído' if ok else 'sem sucesso'}: {item_id}"
            + (f" ({reason})" if reason else ""),
            source="open_finance",
            details={**result, "reason": reason or None},
        )
    except Exception as exc:  # noqa: BLE001 — background, não pode derrubar nada
        await log_system_event(
            "error",
            "pluggy_sync_failed",
            f"Sync Pluggy falhou: {item_id}: {exc}",
            source="open_finance",
            details={"item_id": item_id, "error": str(exc)[:200]},
        )


def _on_sync_done(item_id: str) -> None:
    """Fim de um sync: solta o slot e, se chegou evento no meio, roda UMA vez mais."""
    _INFLIGHT.pop(item_id, None)
    if item_id in _DIRTY:
        _DIRTY.discard(item_id)
        _schedule_pluggy_sync(item_id)


def _schedule_pluggy_sync(item_id: str) -> None:
    if not item_id:
        return
    if item_id in _INFLIGHT:
        _DIRTY.add(item_id)   # coalesce: uma re-execução no fim, não uma task por evento
        return
    task = asyncio.create_task(_run_pluggy_sync_bg(item_id), name=f"pluggy_sync_{item_id}")
    _INFLIGHT[item_id] = task
    task.add_done_callback(lambda _t: _on_sync_done(item_id))


async def _adota_item_orfao(item_id: str, last_event: str | None = None) -> int | None:
    """Adota um item da Pluggy que existe lá e não tem conexão local. Devolve o dono.

    Por que existe: a linha em `open_finance_connections` só nascia no
    `POST /pluggy-item`, que é chamado pelo NAVEGADOR. Quem fechava a aba no meio
    do widget ficava com o item vivo na Pluggy e nenhuma linha aqui — a tela mostra
    0 bancos, o `DELETE /open-finance/{uid}` enumera a partir das conexões e não
    apaga nada lá, e o `avoidDuplicates` do connect token recusa item novo. O
    usuário fica travado sem saída pelo produto.

    SÓ o chamador decide QUANDO adotar, e ele adota só em `item/created` — o
    porquê está no webhook.

    O dono vem SEMPRE da resposta REMOTA (`clientUserId`, que nós mesmos setamos ao
    emitir o connect token) — NUNCA do corpo do webhook, que vem de fora e não
    decide posse. E ele é aceito com a MESMA régua do `POST /pluggy-item`, que
    compara STRING (`str(dono_remoto or "") != str(session_uid)`): só passa o que
    for a forma canônica de um inteiro. `int()` sozinho aceitava mais do que a
    rota — `' 5 '`, `'+5'`, `'1_2'` (PEP 515) e `'١٢'` (dígitos árabe-índicos) —
    e as duas portas divergiam no mesmo valor.

    Três guardas de escrita, nesta ordem:
      • o item não pode ter tido dono NUNCA. "Item/created dispara uma vez" é
        premissa errada: webhook é entrega AT-LEAST-ONCE e este endpoint devolve
        401/400/503 (e pode dar 500 antes daqui), o que faz a Pluggy retentar o
        MESMO evento. Uma 2ª entrega depois de o usuário remover o banco
        ressuscitava a conexão, com sync agendado, re-importando na carteira que
        ele limpou — o delete remoto é best-effort e o item sobrevive lá. POR QUE
        o rastro com dono é o sinal certo (e por que banco removido fica "sem
        conexão local" para sempre): docstring de `db.item_registry_origins`,
        fonte única dos leitores (CLAUDE.md §0.7). A 1ª adoção grava rastro
        com dono ANTES da conexão, então ela mesma fecha a duplicata — e a
        guarda é REFEITA dentro do `pluggy_item_lock`, imediatamente antes da
        escrita da conexão (`_salva_item_sob_lock`, `adocao_registro_id`),
        porque entre as duas leituras cabe uma entrega concorrente inteira.
        Quem perde essa segunda leitura APAGA o próprio rastro antes de
        abortar, ainda com o lock na mão — e quem sai no 503 sem NUNCA ter
        chegado à escrita apaga também, no `_grava_reconexao`: reivindicação
        abandonada que fica para trás é o que torna o aborto terminal (ver o
        LIMITE CONHECIDO);
      • o usuário TEM de existir, e são DUAS defesas para o mesmo estrago.
        `user_exists` recusa por IDENTIDADE (o log diz `usuario_inexistente`), e
        é ele que responde quando a conta já não existia — mas é leitura em
        transação própria, então sozinho ele só cobre a foto do instante em que
        leu. Quem cobre a JANELA (exclusão da conta commitando entre a leitura e
        a escrita) é a FK, e só porque as duas escritas desta função passaram a
        ser incapazes de criar usuário: `register_item` nunca criou, e a conexão
        vai com `criar_usuario=False`. Antes, `ensure_user_tx` RESSUSCITAVA a
        conta apagada por LGPD (`db/privacy.py`) cujo item sobreviveu ao delete
        best-effort — pelo evento da Pluggy, e depois pela corrida (Codex #313);
      • o rastro (`register_item`) vai ANTES da conexão, e a ordem inversa é pior:
        ela deixava conexão commitada com registry VAZIO — item adotado sem
        nenhum rastro, e o rastro é a única enumeração que existe (`GET /items`
        da Pluggy devolve 401).

    O PREÇO dessa ordem, medido: se a escrita da conexão falhar no meio, sobra
    rastro COM dono e nenhuma conexão — estado indistinguível de banco removido
    pelo usuário. Aí NÃO há recuperação automática: o script one-shot deixa de
    listar o item (o filtro dele exclui rastro com dono, de propósito — a mesma
    regra, `db/open_finance_state.item_registry_origins`) e a retentativa do
    `item/created` não readota (a 1ª guarda acima). É por isso que os DOIS
    desfechos em que a escrita provadamente não aconteceu apagam o rastro que a
    adoção acabou de gravar, em vez de deixá-lo: o aborto sob o lock
    (`_salva_item_sob_lock`) e o 503 em que NENHUMA tentativa do prazo chegou ao
    `save_pluggy_open_finance_item` — lock ocupado (`if not locked`) ou infra
    PRÉ-escrita, que é `psycopg.OperationalError` idêntica à do commit e vem do
    `psycopg.connect` dedicado do lock, do `set_config`, do `pg_advisory_lock`,
    das leituras das revalidações ou do `get_conn` do próprio
    `save_pluggy_open_finance_item` (Codex #313, P1). Quem responde isso é a
    marca feita na linha anterior à escrita — desfeita quando o erro é
    `PoolTimeout`/`PoolClosed`, que só sai do `getconn` —, não o tipo do erro.
    Sem os dois desfazimentos, duas
    entregas concorrentes caíam neste estado sozinhas, sem falha nenhuma de
    escrita: consertar só o primeiro TROCAVA o perdedor (quem aborta limpa, quem
    perde o lock fica), e era regressão contra a `main`, onde a mesma
    intercalação dava 1 conexão. O que continua pagando o preço é a falha de
    INFRA DENTRO da escrita (erro no meio do upsert, commit ambíguo), em que o
    desfecho é DESCONHECIDO e apagar a reivindicação poderia soltar uma segunda
    adoção por cima de uma conexão que existe. Pool esgotado NÃO está nesta
    lista: ele estoura no `get_conn`, ANTES de qualquer statement, e
    `PoolTimeout`/`PoolClosed` saindo do `save_...` desfazem a reivindicação como
    qualquer outra falha pré-escrita. A FK da conta apagada também não está, e
    não é escolha: `open_finance_item_registry.user_id` é `on delete cascade`
    (`db/schema.py`), então o mesmo `delete from users` que faz a FK estourar já
    levou o rastro `webhook_adopt` junto — não sobra reivindicação nenhuma
    (`test_conta_apagada_no_meio_da_adocao_nao_ressuscita`). Aí a saída é
    OPERACIONAL:
    `python -m scripts.adotar_items_of_orfaos --item ID --apply --delete` apaga o
    item na Pluggy, o `avoidDuplicates` libera, e o usuário reconecta pelo
    widget. Fechar isso sozinho exigiria o disconnect deixar rastro próprio
    (`origin='disconnect'`) para separar "removido" de "adoção que falhou" —
    escrita em outro fluxo, outro PR.

    Nada escapa daqui: o chamador (webhook) tem de responder 200 mesmo em falha,
    senão a Pluggy retenta em laço. Sem dono resolvível, sem usuário, com o teto
    de bancos do plano estourado (`_enforce_bank_limit`, 402) ou com qualquer
    falha de escrita, devolve None e o chamador mantém o rastro sem dono que já
    existia.

    LIMITE CONHECIDO (medido). Duas entregas CONCORRENTES do mesmo `item/created`
    ainda leem o rastro vazio antes de qualquer uma escrever, e as duas gravam
    rastro (`register_item` fica FORA do lock, de propósito: é ele que a
    retentativa do `_grava_reconexao` não pode repetir). A revalidação sob o lock
    mais o desfazimento fecham isso NAS CORRIDAS de entrega concorrente, e só
    nelas: nenhuma intercalação delas deixa reivindicação sem conexão — no caso
    comum uma entrega grava e fica com o rastro, e as outras abortam apagando o
    que gravaram; na intercalação em que a entrega que pega o
    lock é justamente a que aborta, ninguém grava e ninguém reivindica — o item
    volta a ser órfão e a PRÓXIMA entrega (ou o script one-shot) adota. Sobra uma
    janela de LEITURA, não de estado: enquanto a perdedora não chega ao lock, o
    rastro dela existe e um leitor concorrente (o painel de saúde, o script
    one-shot) vê uma linha a mais desse item. E sobra o desfecho DESCONHECIDO —
    infra no meio da escrita mantém a reivindicação de propósito, e aí vale o
    parágrafo de cima. Três rodadas erraram este parágrafo — a 1ª chamou a janela
    de "só auditoria duplicada" (era ressurreição de banco desconectado, o P1 do
    Codex #313), a 2ª deu o rastro extra como permanente e não viu que ele
    RECUSAVA toda retentativa e sumia do script, deixando o usuário com 0 bancos
    e sem saída (o P0 do Tester, 30/30 rodadas), a 3ª consertou só o aborto sob o
    lock e criou esse MESMO estado terminal pela porta do 503. Também fica aberto:
    SÓ `item/created` adota (o gate é `event_name == "item/created"` no webhook, e
    `test_so_item_created_adota` prende), então item cujo `item/created` se perdeu
    ou nunca foi entregue não é adotado por evento NENHUM depois — nem pelo
    `item/updated` —, e a única recuperação é o one-shot
    (`scripts/adotar_items_of_orfaos.py`). Nenhum dos dois é regressão contra a
    `main`.

    ponytail: custa uma chamada HTTP a mais dentro do webhook no ramo de item
    desconhecido — inclusive quando ele acaba recusado por já ter dono, que é o
    preço da ordem escolhida acima. Se virar latência, jogar num
    `asyncio.create_task` como o `_schedule_pluggy_sync` já faz.
    """
    try:
        remote = await asyncio.to_thread(get_pluggy_item, item_id)
        bruto = str(remote.get("clientUserId") or "")
        dono = int(bruto)
        if str(dono) != bruto:   # ' 5 ', '+5', '007', '1_2', '١٢' → não é o que a rota aceita
            raise ValueError(f"clientUserId fora da forma canônica: {bruto!r}")
    except Exception as exc:
        # Sem dono resolvível: o chamador registra o rastro como antes. O MOTIVO
        # importa — `PluggyConfigError`, 404, timeout e item sem `clientUserId`
        # viravam todos o mesmo `of_webhook_item_unknown` genérico.
        await log_system_event(
            "warning", "of_webhook_adopt_skipped",
            "Item órfão sem dono resolvível",
            source="open_finance",
            details={"item_id": item_id, "motivo": type(exc).__name__,
                     "error": str(exc)[:200]},
        )
        return None

    # A leitura do rastro vem DEPOIS do HTTP de propósito, e custa um POST /auth
    # + GET /items desperdiçado na duplicata. Antes dele, ela ficava separada da
    # escrita (`register_item`) por um round-trip inteiro da Pluggy — e MEDIDO
    # (duas entregas concorrentes de `item/created`, a 2ª começando com a 1ª
    # ainda dentro do GET): 2 auditorias e 2 rastros para 1 conexão, contra 1 e 1
    # com a leitura aqui. A posição NÃO é neutra: a troca é uma chamada HTTP num
    # caminho raro (retentativa) por uma janela de corrida N vezes menor. O que
    # SOBRA de janela é o "LIMITE CONHECIDO" da docstring acima, que é onde ele
    # está descrito por extenso (CLAUDE.md §0.7).
    # Falha de leitura NÃO adota (não dá para verificar).
    try:
        origens = await asyncio.to_thread(item_registry_origins, item_id)
    except Exception as exc:  # noqa: BLE001 — nada escapa daqui (o webhook responde 200)
        await log_system_event(
            "warning", "of_webhook_adopt_skipped",
            "Rastro do item ilegível: adoção não verificável",
            source="open_finance",
            details={"item_id": item_id, "motivo": type(exc).__name__,
                     "error": str(exc)[:200]},
        )
        return None
    if origens:
        await log_system_event(
            "warning", "of_webhook_adopt_skipped",
            "Item já teve dono: adoção só vale para item nunca atribuído",
            source="open_finance",
            details={"item_id": item_id, "motivo": "rastro_com_dono",
                     "origens": sorted(origens)},
        )
        return None

    try:
        if not await asyncio.to_thread(user_exists, dono):
            await log_system_event(
                "warning", "of_webhook_adopt_skipped",
                "Item órfão de usuário que não existe mais",
                source="open_finance",
                details={"item_id": item_id, "user_id": dono, "motivo": "usuario_inexistente"},
            )
            return None
        await _enforce_bank_limit(dono, item_id)
        # O rastro DUPLICA de propósito quando o navegador volta depois (o POST
        # grava outra linha, `origin='pluggy_item'`): o registry é um log de
        # append, "este item existiu, visto por esta porta", e as duas portas
        # viram o item de verdade. Deduplicar apagaria justamente o que responde
        # "por onde ele entrou". O que NÃO pode duplicar é a conexão (uma só, o
        # upsert garante) e a auditoria (ver o `POST /pluggy-item`).
        registro_id = await asyncio.to_thread(
            register_item, dono, provider_item_id=item_id, origin="webhook_adopt",
            status=str(remote.get("status") or "") or None, last_event=last_event,
        )
        # `criar_usuario=False`: a FK da conexão é a ÚNICA coisa atômica com o
        # insert. O `user_exists` acima é leitura em transação PRÓPRIA, e uma
        # exclusão de conta (db/privacy.py) que commite entre ele e esta escrita
        # deixava o `ensure_user_tx` recriar a linha de `users` que a LGPD acabou
        # de apagar — o `register_item` acima já cai na FK dele quando a exclusão
        # chega antes, e esta fecha o resto da janela (Codex #313, P1). Sem a
        # linha de `users`, o insert estoura `ForeignKeyViolation`, o `except`
        # abaixo registra e o webhook responde 200 sem adotar.
        # Registrado, não consertado: sem o `ensure_user_tx` a adoção também
        # deixa de REPOR a linha de `accounts`, e existe estado de produção com
        # `users` sem `accounts` (`merge_users` apaga a do `from_user_id` e nunca
        # apaga o `users` dele, db/users.py:109). Medido: a adoção grava, o
        # snapshot responde e `get_consolidated_balance` devolve zeros sem
        # estourar; qualquer `ensure_user` posterior (o próximo login) repara.
        # `adocao_registro_id`: a 1ª guarda (rastro sem dono) é REFEITA dentro do
        # `pluggy_item_lock`, ignorando a linha recém-gravada acima — e o aborto
        # APAGA essa linha. Sem a releitura, a decisão de adotar valia por uma
        # leitura de segundos atrás e a corrida com outra entrega ressuscitava
        # banco removido (Codex #313, P1); sem o desfazimento, a mesma corrida
        # deixava o item reivindicado e sem conexão para sempre (P0 do Tester).
        await _grava_reconexao(dono, remote, item_id, tinha_conexao_propria=False,
                               criar_usuario=False, adocao_registro_id=registro_id)
    except Exception as exc:
        # `motivo` sozinho não basta AQUI: `HTTPException` é o nome de quatro
        # desfechos com ações de operador diferentes — 402 (teto de bancos do
        # plano), 409 do estado que sumiu na espera do lock, 409 do item que
        # OUTRA entrega já atribuiu (a revalidação da adoção) e 503 (lock
        # ocupado); os dois 409 se separam pelo texto do `detail`. O
        # `str()` do `HTTPException` já é `"{status_code}: {detail}"`
        # (starlette), então não precisa de formatação nossa.
        await log_system_event(
            "warning", "of_webhook_adopt_skipped",
            "Item órfão não adotado",
            source="open_finance",
            details={"item_id": item_id, "user_id": dono, "motivo": type(exc).__name__,
                     "error": str(exc)[:200]},
        )
        return None

    # Daqui pra baixo a adoção JÁ aconteceu: nada pode desfazê-la nem virar 5xx
    # para a Pluggy. Auditoria é o mesmo evento que o `POST /pluggy-item` grava —
    # sem `request`, que aqui é o POST da Pluggy e não o do usuário.
    try:
        await asyncio.to_thread(
            record_audit_event, dono, AuditEvent.OPEN_FINANCE_CONNECTED,
            details={"provider": "pluggy", "item_id": item_id, "origin": "webhook_adopt"},
        )
        # Pular o sync em `UPDATING`/`CREATED` (`ITEM_UPDATING` é só esses dois)
        # só se paga quando vem OUTRO evento atrás — e isso vale de UM caminho: o
        # `item/created` que a Pluggy acabou de emitir. Ali o status é o normal de
        # item recém-nascido, o `item/updated` é ESPERADO logo atrás, e a essa
        # altura a conexão JÁ existe, então ele entra pelo caminho comum
        # (`len(conexoes) == 1`). É menos código rodando, não mais.
        # ESPERADO, não garantido: entrega de webhook é best-effort e a Pluggy não
        # promete o evento seguinte. Sondado: `item/created` adotado em `UPDATING`
        # + evento seguinte perdido = card em "Atualizando…" permanente. Teto
        # conhecido, IGUAL ao de antes deste conserto — fechá-lo é mexer na
        # máquina de estados do `pluggy_health`, que é outro PR.
        #
        # ADOÇÃO RETROATIVA NÃO TEM ESSE EVENTO (defeito de produção, relato do
        # dono): o item do `scripts/adotar_items_of_orfaos` foi abandonado dias
        # atrás e está congelado no status daquela sessão. Sem sync agendado e sem
        # evento nenhum a caminho, NADA lê a Pluggy por essa conexão — e o que
        # falta é o EXTRATO: zero conta, zero transação, carteira vazia.
        #
        # O QUE ESTE GATE ENTREGA, MEDIDO com sync de verdade e o remoto ainda em
        # `UPDATING` (que é o caso do dono: o `status` local só está `UPDATING`
        # porque `_grava_reconexao` copiou o remoto, e o GET do sync um instante
        # depois vê o mesmo):
        #   • `/accounts` trazendo conta → sync ok, `last_sync_at` carimbado,
        #     CONTAS E TRANSAÇÕES IMPORTADAS. É o ganho, e é imediato.
        #   • `/accounts` vazia E `/investments` vazia → `ok=False`, `last_sync_at`
        #     NULL, nada importado. As DUAS: o gate do sync é `not accounts and
        #     not investments` (grep em `core/services/pluggy_sync.py`), então
        #     conta zerada com carteira cheia — o caso corretora, que o próprio
        #     gate comenta — ainda importa.
        # O RÓTULO não muda em nenhum dos dois — segue "Atualizando…" —, e a versão
        # anterior deste comentário afirmava o contrário. Por quê: `connection_ui_state`
        # decide pelo ramo do `health` ANTES de olhar o `sem_sync`, e
        # `health.item_status in _UPDATING` devolve `updating` independentemente do
        # `last_sync_at` (`core/services/pluggy_health.py:498-504`). Quem tira o
        # rótulo de lá é a passada seguinte do job de saúde (medido com
        # `OF_HEALTH_MAX_AGE_SEC=0`: com conta → "Atualizado", sem conta → "Sem
        # dados"), e nos defaults isso leva de 12h a 18h — 15h em média, 18h é o
        # TOPO da faixa e não o comum (`OF_HEALTH_MAX_AGE_SEC` 12h + o tick de
        # `OF_REFRESH_INTERVAL_SEC` 6h, que cai em qualquer ponto dessas 6h), e
        # MAIS se houver deploy no meio. Nem as 18h são teto: o
        # tick DORME PRIMEIRO (`_open_finance_refresh`,
        # `frontend/finance_bot_websocket_custom.py`) e o Railway sobe container
        # novo a cada deploy — um redeploy pouco antes da passada reinicia as 6h
        # e empurra o rótulo para além das 18h. Ou seja: agendar o sync é
        # NECESSÁRIO e não é SUFICIENTE para o rótulo. O "Atualizando…" eterno da
        # tela é o outro PR; o que este fecha é o dinheiro que não entrava.
        #
        # Os outros status AGENDAM nos dois caminhos: `WAITING_USER_INPUT`,
        # `WAITING_USER_ACTION`, `LOGIN_ERROR`, `OUTDATED` e `ERROR` viram um
        # sync que provavelmente não traz nada — e isso NÃO vira estado errado na
        # tela, porque `connection_ui_state` testa `_NEEDS_USER` antes de
        # `no_accounts`. `_UPDATING` vem de `pluggy_health` — a fonte que o
        # `pluggy_sync` também usa; não é uma segunda lista de status (§0.7).
        fresco = last_event == "item/created"
        if not fresco or str(remote.get("status") or "").upper() not in ITEM_UPDATING:
            _schedule_pluggy_sync(item_id)
    except Exception as exc:
        await log_system_event(
            "warning", "of_webhook_adopt_incompleto",
            "Item adotado, mas auditoria/sync falharam",
            source="open_finance",
            details={"item_id": item_id, "user_id": dono, "motivo": type(exc).__name__,
                     "error": str(exc)[:200]},
        )
    return dono


def _bank_limit_enabled() -> bool:
    # Gate dormante: só bloqueia quando ligado no ambiente (como os outros gates do projeto).
    return (os.getenv("OF_BANK_LIMIT_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


async def _enforce_bank_limit(user_id: int, new_item_id: str | None = None) -> None:
    """Teto de conexões OF por plano.

    v2 (PLANS_V2_ENABLED): teto vem do tier — of_banks_max da escada
    (trial 1 / Essencial 1 / Plus 2 / Pro 5 / None = ilimitado). Ativo sempre
    que a escada estiver ligada, sem env extra.
    v1 (flag off): gate Fase 7 legado, dormante atrás de OF_BANK_LIMIT_ENABLED.

    P1: reconectar/renovar um banco JÁ conectado (mesmo provider_item_id) NÃO conta como
    banco novo — senão o usuário no limite ficava travado de reautorizar o próprio
    banco. Só bloqueia banco realmente novo.
    """
    from core.services.plan_service import plans_v2_enabled, get_user_limits

    if plans_v2_enabled():
        limit = (await asyncio.to_thread(get_user_limits, user_id)).get("of_banks_max")
        if limit is None:
            return  # ilimitado (Premium futuro)
        if new_item_id:
            existing = await asyncio.to_thread(get_open_finance_connection_by_item_id, str(new_item_id))
            if existing and int(existing.get("user_id")) == int(user_id):
                return  # upsert de item existente: reconexão, não é banco novo
        if limit <= 0:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "OF_BANK_LIMIT",
                    "limit": 0,
                    "message": "Conectar banco faz parte dos planos pagos — no Grátis a conexão "
                               "vale durante os 15 dias de teste. Assine pra reativar: /precos",
                },
            )
        count = await asyncio.to_thread(count_open_finance_connections, user_id)
        if count >= limit:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "OF_BANK_LIMIT",
                    "limit": limit,
                    "message": f"Seu plano conecta até {limit} banco{'s' if limit > 1 else ''}. "
                               "Faça upgrade pra conectar mais: /precos",
                },
            )
        return

    if not _bank_limit_enabled():
        return
    if await asyncio.to_thread(is_pro, user_id):
        return
    if new_item_id:
        existing = await asyncio.to_thread(get_open_finance_connection_by_item_id, str(new_item_id))
        if existing and int(existing.get("user_id")) == int(user_id):
            return  # upsert de item existente: reconexão, não é banco novo
    limit = int(os.getenv("OF_FREE_BANK_LIMIT", "1"))
    count = await asyncio.to_thread(count_open_finance_connections, user_id)
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "OF_BANK_LIMIT",
                "limit": limit,
                "message": f"No plano grátis você conecta {limit} banco. Assine o Pro para conectar mais.",
            },
        )


async def _ensure_of_access_allowed(user_id: int) -> None:
    """Barra a EMISSÃO do connect-token quando o plano não dá Open Finance nenhum
    (of_banks_max <= 0: Free pós-trial). Diferente do teto por contagem, esse caso é
    inequívoco — o usuário não tem banco pra adicionar nem reconexão liberada (banco
    do Free fica pausado; reativar = upgrade). Fecha o abuso direto do endpoint e evita
    item/consentimento órfão na Pluggy (cada conexão custa). Planos com teto > 0 seguem
    liberados aqui pra não travar reconexão de um banco existente — a contagem é cobrada
    no /pluggy-item, onde já se sabe se é banco novo ou upsert.
    """
    from core.services.plan_service import plans_v2_enabled, get_user_limits

    if plans_v2_enabled():
        limit = (await asyncio.to_thread(get_user_limits, user_id)).get("of_banks_max")
        if limit is not None and limit <= 0:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "OF_BANK_LIMIT",
                    "limit": 0,
                    "message": "Conectar banco faz parte dos planos pagos — no Grátis a conexão "
                               "vale durante os 15 dias de teste. Assine pra reativar: /precos",
                },
            )
        return

    # v1 legado: só barra quando o gate está ligado E o usuário não é Pro.
    if not _bank_limit_enabled():
        return
    if await asyncio.to_thread(is_pro, user_id):
        return
    limit = int(os.getenv("OF_FREE_BANK_LIMIT", "1"))
    if limit <= 0:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "OF_BANK_LIMIT",
                "limit": 0,
                "message": "Conectar banco faz parte dos planos pagos. Assine o Pro para conectar.",
            },
        )


class OpenFinanceMockConnectPayload(BaseModel):
    institution: str | None = None


class OpenFinancePluggyItemPayload(BaseModel):
    item: dict


@router.get("/open-finance/{user_id}")
async def open_finance_snapshot_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, **snapshot}))


# Só contas de pessoa física no modal (bate com o preview aprovado; evita os
# duplicados "… Empresas"). Pra incluir PJ, adicionar "BUSINESS_BANK".
_CONNECTABLE_TYPES = {"PERSONAL_BANK"}


@router.get("/open-finance/{user_id}/connectors")
async def open_finance_connectors_route(request: Request, user_id: int):
    """Catálogo completo de bancos da Pluggy pro modal "Conectar banco".

    Fluxo padrão: a escolha do banco acontece no site (modal com busca) e o widget da
    Pluggy abre já no banco escolhido. Retorna dicts enxutos (id/name/type/color/inv)."""
    shared.authorize_dashboard_access(request, user_id)
    try:
        raw = await asyncio.to_thread(
            list_pluggy_connectors, None, include_sandbox=PLUGGY_INCLUDE_SANDBOX
        )
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    banks = []
    for c in raw:
        if str(c.get("type") or "") not in _CONNECTABLE_TYPES:
            continue
        products = [str(p).upper() for p in (c.get("products") or [])]
        banks.append({
            "id": c.get("id"),
            "name": (c.get("name") or "").strip(),
            "type": c.get("type"),
            "color": (c.get("primaryColor") or "").lstrip("#"),
            "logo": c.get("imageUrl") or "",
            "inv": "INVESTMENTS" in products,
        })
    banks.sort(key=lambda b: b["name"].lower())
    return {"ok": True, "connectors": banks}


def _require_caixinha_access(user_id: int) -> None:
    # Caixinha (Open Finance) é feature paga — desacoplada do beta de agentes:
    # qualquer plano pago (Essencial+) tem acesso à UI de vínculo, não só o beta.
    from core.services.plan_service import require_min_tier
    if not require_min_tier(user_id, "essencial"):
        raise HTTPException(status_code=404, detail="Feature indisponível.")


@router.get("/open-finance/{user_id}/caixinhas")
async def open_finance_caixinhas_route(request: Request, user_id: int):
    """Banqueiro: caixinhas OF detectadas + metas do usuário, pra montar o vínculo."""
    shared.authorize_dashboard_access(request, user_id)
    await asyncio.to_thread(_require_caixinha_access, user_id)
    from db import list_caixinha_candidates, list_pockets

    candidates = await asyncio.to_thread(list_caixinha_candidates, user_id)
    pockets = await asyncio.to_thread(lambda: list_pockets(user_id, accrue=False))
    metas = [
        {"id": p["id"], "name": p["name"],
         "target_amount": float(p["target_amount"]) if p.get("target_amount") is not None else None}
        for p in pockets
    ]
    caixinhas = [
        {"of_investment_id": c["of_investment_id"], "name": c["name"],
         "balance": float(c["balance"] or 0),
         "pocket_id": c["pocket_id"], "pocket_name": c["pocket_name"]}
        for c in candidates
    ]
    return {"ok": True, "caixinhas": caixinhas, "metas": metas}


class CaixinhaBindBody(BaseModel):
    pocket_id: int
    of_investment_id: int | None = None


@router.post("/open-finance/{user_id}/caixinhas/bind")
async def open_finance_caixinha_bind_route(request: Request, user_id: int, body: CaixinhaBindBody):
    """Vincula (ou desvincula com of_investment_id=null) uma meta a uma caixinha OF."""
    shared.authorize_dashboard_access(request, user_id)
    await asyncio.to_thread(_require_caixinha_access, user_id)
    from db import bind_pocket_to_caixinha

    ok = await asyncio.to_thread(
        bind_pocket_to_caixinha, user_id, body.pocket_id, body.of_investment_id
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Não foi possível vincular (meta ou caixinha inválida).")
    return {"ok": True}


@router.post("/open-finance/{user_id}/connect-token")
async def open_finance_connect_token_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    # Barra só o caso inequívoco (plano sem OF): não emite token pra quem não pode
    # conectar nada, fechando o abuso direto do endpoint e evitando item órfão na Pluggy.
    # O TETO POR CONTAGEM (planos pagos no limite) NÃO é cobrado aqui de propósito — o
    # widget também reconecta um banco existente, e a contagem é validada no /pluggy-item,
    # onde já se sabe se o item é novo ou um upsert de um banco já conectado.
    await _ensure_of_access_allowed(user_id)

    webhook_url = (os.getenv("PLUGGY_WEBHOOK_URL") or "").strip()
    if not webhook_url and shared.DASHBOARD_URL.startswith("https://"):
        webhook_url = f"{shared.DASHBOARD_URL}/open-finance/pluggy/webhook"

    # Anexa o secret como token na URL (a Pluggy chama de volta preservando a query).
    # É como o webhook se autentica (a Pluggy não assina o corpo). Não duplica se já
    # veio com token (ex.: PLUGGY_WEBHOOK_URL setado à mão com o token).
    webhook_secret = (os.getenv("PLUGGY_WEBHOOK_SECRET") or "").strip()
    if webhook_url and webhook_secret and "token=" not in webhook_url:
        sep = "&" if "?" in webhook_url else "?"
        webhook_url = f"{webhook_url}{sep}token={webhook_secret}"

    try:
        token_data = await asyncio.to_thread(
            create_pluggy_connect_token,
            user_id,
            webhook_url or None,
        )
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Registra que ESTE usuário pediu um token. O `GET /items` da Pluggy devolve
    # 401, então sem este rastro um item criado e nunca reportado ao /pluggy-item
    # é invisível para nós. Guarda o HASH: o token bruto abre a conexão e NUNCA
    # pode ir para o banco.
    try:
        await asyncio.to_thread(
            register_item, user_id,
            token_hash=token_hash(token_data["accessToken"]), origin="connect_token",
        )
    except Exception as exc:  # noqa: BLE001 — rastro nunca derruba a emissão do token
        await log_system_event(
            "warning", "of_item_registry_failed", "Falha ao registrar connect token",
            source="open_finance", details={"error": str(exc)[:200]},
        )

    return {
        "ok": True,
        "accessToken": token_data["accessToken"],
        "includeSandbox": PLUGGY_INCLUDE_SANDBOX,
        "provider": "pluggy",
    }


# Por quanto tempo, depois da adoção pelo webhook, o `POST /pluggy-item` ainda é
# o HANDOFF dela — e não uma reconexão nova. `item/created` chega com o widget
# aberto e o `onSuccess` vem em seguida: o vão real é de segundos. Uma hora é
# folga, não alvo, e fora dela o desfecho é AUDITAR — o lado barato do erro.
JANELA_HANDOFF_WEBHOOK = timedelta(hours=1)


@router.post("/open-finance/{user_id}/pluggy-item")
async def open_finance_pluggy_item_route(request: Request, user_id: int, payload: OpenFinancePluggyItemPayload):
    """Registra o item que o widget da Pluggy acabou de criar.

    O dict `item` vem DO NAVEGADOR: nome do banco, status e id são todos escolhidos
    pelo cliente. A única coisa aproveitada dele é o `id`, e só para perguntar à
    Pluggy quem é o dono — o que grava é a resposta REMOTA. Duas checagens:

      • `clientUserId` do item (que nós mesmos setamos ao emitir o connect token)
        precisa bater com o usuário logado. É ESTA que discrimina: sem ela, o id
        de um item de outra pessoa vira uma conexão nesta conta;
      • se o item já pertence a outra conta aqui dentro, 409 e nada é gravado.

    Comparar `session_uid` com o `{user_id}` da URL seria redundante e não é o
    conserto: `shared.authorize_dashboard_access` já levanta 403 quando os dois
    diferem (frontend/routes/shared.py), então aqui eles são iguais por
    construção. `session_uid` é usado abaixo por clareza de origem, não por
    segurança adicional.
    """
    session_uid = shared.authorize_dashboard_access(request, user_id)
    item = payload.item if isinstance(payload.item, dict) else {}
    new_item_id = str(item.get("id") or item.get("itemId") or "").strip()
    if not new_item_id:
        raise HTTPException(status_code=400, detail="Item Pluggy sem id.")

    try:
        remote = await asyncio.to_thread(get_pluggy_item, new_item_id)
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        if getattr(exc, "status_code", None) == 404:
            raise HTTPException(status_code=404, detail="Item não existe na Pluggy.") from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    dono_remoto = remote.get("clientUserId")
    if str(dono_remoto or "") != str(session_uid):
        await log_system_event(
            "error", "of_item_owner_conflict",
            "Item Pluggy não pertence ao usuário da sessão",
            source="open_finance",
            details={"item_id": new_item_id, "origin": "pluggy_item_route"},
        )
        raise HTTPException(status_code=403, detail="Este item não pertence a esta conta.")

    conexoes_do_item = await asyncio.to_thread(get_connections_by_item_id, new_item_id)
    outros = [c for c in conexoes_do_item if int(c["user_id"]) != int(session_uid)]
    # Fato pré-lock reutilizado pela revalidação em _salva_item_sob_lock: se a
    # conexão própria existia AQUI e sumir durante a espera do lock, um
    # reset/disconnect interveio e a gravação é abortada (409).
    tinha_conexao_propria = any(int(c["user_id"]) == int(session_uid) for c in conexoes_do_item)
    if outros:
        await log_system_event(
            "error", "of_item_owner_conflict",
            "Item Pluggy já vinculado a outra conta",
            source="open_finance",
            details={"item_id": new_item_id, "connections": len(outros)},
        )
        raise HTTPException(status_code=409, detail="Este item já está vinculado a outra conta.")

    # A conexão que JÁ existia nasceu do webhook (que auditou por ela) ou é um
    # banco que o usuário está RECONECTANDO? `tinha_conexao_propria` sozinho não
    # separa os dois: o widget reconecta banco existente e o `avoidDuplicates`
    # devolve o MESMO itemId, então re-consentimento e conserto de LOGIN_ERROR
    # também chegam aqui com a conexão de pé — e sumiam de "Atividade da conta".
    # O fato que separa: `pluggy_item` no rastro = o NAVEGADOR já registrou este
    # item alguma vez, logo a conexão não é a que o webhook acabou de adotar.
    # Lido ANTES do `register_item` abaixo, que grava justamente essa origem.
    conexao_recem_adotada = tinha_conexao_propria and "pluggy_item" not in (
        await asyncio.to_thread(item_registry_origins, new_item_id))
    if conexao_recem_adotada:
        # ...e o webhook TEM de ter auditado de verdade. `record_audit_event`
        # ENGOLE falha de banco (core/audit.py:156) — o rastro prova a adoção, não
        # a auditoria —, então o insert dele podia falhar lá e esta guarda suprimir
        # aqui: NENHUM `OPEN_FINANCE_CONNECTED` para uma conexão que nasceu. Num
        # log de segurança duplicata é ruído e buraco é perda (Codex #313, P2).
        # Fecha junto o 2º caso do mesmo achado: conexão ANTERIOR ao registry não
        # tem rastro nenhum, e a 1ª reconexão dela caía como duplicata de um
        # webhook que nunca existiu.
        # `list_audit_events` (já existente, e já filtrada por `user_id`) em vez de
        # query nova: são os 50 últimos eventos do usuário, e a adoção que este POST
        # duplica aconteceu segundos atrás. Fora dessa janela o desfecho é auditar DE
        # NOVO, que é o lado barato do erro — e falha de banco cai do mesmo lado
        # porque quem captura `Exception` e devolve `[]` é a PRÓPRIA
        # `list_audit_events` (core/audit.py:95-97). Aqui não há `try`: o que
        # escapar dela vira 500 para o usuário, não degradação.
        # `isinstance(..., dict)` e não `or {}`: uma linha com `details` escalar
        # (`'"texto"'::jsonb`) estoura `AttributeError` no `.get`, e sem guarda o
        # POST fica 500 PERMANENTE para aquele usuário. Nenhum escritor de hoje
        # grava assim (os dois passam dict, e `Jsonb(details or {})` normaliza o
        # `None`) — é fronteira de dado vindo do banco, custa uma linha.
        # `created_at` (já vem no SELECT de `list_audit_events`) porque item+origem
        # NÃO correlaciona a evidência com ESTE POST: item adotado cujo navegador
        # nunca postou fica sem `pluggy_item` no registry PARA SEMPRE, e a
        # reconexão de dias depois consumia a auditoria histórica como se fosse a
        # duplicata da adoção de agora — a 1ª reconexão real sumia do log de
        # segurança (Codex #313, P2, 2ª rodada). A janela é a duração do próprio
        # handoff: sem coluna nova, sem marcador consumível, sem estado novo.
        handoff_desde = datetime.now(timezone.utc) - JANELA_HANDOFF_WEBHOOK
        conexao_recem_adotada = any(
            e["event"] == AuditEvent.OPEN_FINANCE_CONNECTED
            and isinstance(e.get("details"), dict)
            and e["details"].get("item_id") == new_item_id
            and e["details"].get("origin") == "webhook_adopt"
            and e["created_at"] >= handoff_desde
            for e in await asyncio.to_thread(list_audit_events, session_uid, 50)
        )

    await _enforce_bank_limit(session_uid, new_item_id)
    try:
        connection = await _grava_reconexao(
            session_uid, remote, new_item_id, tinha_conexao_propria)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detalhe_seguro(exc)) from exc

    await asyncio.to_thread(
        register_item, session_uid,
        provider_item_id=new_item_id, origin="pluggy_item",
        status=str(remote.get("status") or "") or None,
    )

    # Pula SÓ a duplicata do webhook. `item/created` chega antes do `onSuccess`
    # do widget (que só dispara em status final), então em produção a ordem comum
    # é webhook primeiro: a adoção grava a conexão E a auditoria, e este POST
    # chegava depois gravando o SEGUNDO `OPEN_FINANCE_CONNECTED` da MESMA conexão.
    # O que se suprime é "o webhook JÁ auditou ESTA conexão", não "o usuário já
    # tinha este banco" — reconexão legítima (re-consentimento, conserto de
    # LOGIN_ERROR) é evento de segurança e continua aparecendo, uma vez cada.
    # ponytail: dois tetos conhecidos, ambos de UM evento de auditoria e ambos
    # fechados pela mesma coisa — marcar a adoção na PRÓPRIA conexão (coluna
    # nova + migração), que é o custo que nenhum dos dois paga hoje:
    #   1. item adotado pelo webhook cujo navegador NUNCA postou (aba fechada):
    #      uma reconexão que caia DENTRO da `JANELA_HANDOFF_WEBHOOK` contada da
    #      adoção não audita. Fora da janela audita, e da 2ª em diante audita
    #      sempre: aí o registry já tem `pluggy_item`. O teto é UM evento, e só
    #      para quem reconecta o mesmo item na mesma hora em que ele foi adotado.
    #      O limite de 50 eventos do `list_audit_events` só ENCOLHE a supressão
    #      (evidência fora dos 50 = audita), então quem define este teto é o
    #      tempo, não o histórico do usuário.
    #   2. a janela invertida, que é DUPLICATA e não buraco: `_adota_item_orfao`
    #      commita a conexão e só depois grava o `record_audit_event` do webhook.
    #      Um `POST /pluggy-item` que caia nesse vão vê a conexão de pé e a
    #      evidência ainda não → audita, e o webhook audita em seguida: 2
    #      `OPEN_FINANCE_CONNECTED` para 1 conexão. Escolha deliberada, mesma
    #      régua do bloco acima — duplicata é ruído, buraco é perda.
    if not conexao_recem_adotada:
        await asyncio.to_thread(
            record_audit_event,
            user_id,
            AuditEvent.OPEN_FINANCE_CONNECTED,
            request=request,
            details={"provider": "pluggy", "item_id": (connection or {}).get("provider_item_id")},
        )

    # Sync inicial: puxa contas + transações do banco recém-conectado.
    _schedule_pluggy_sync(str((connection or {}).get("provider_item_id") or ""))

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "connection": connection, **snapshot}))


@router.post("/open-finance/{user_id}/sync")
async def open_finance_sync_route(request: Request, user_id: int):
    """Força um sync de todos os bancos Pluggy do usuário (leitura sob demanda)."""
    shared.authorize_dashboard_access(request, user_id)
    try:
        result = await asyncio.to_thread(sync_pluggy_user, user_id)
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "sync": result, **snapshot}))


@router.post("/open-finance/{user_id}/refresh")
async def open_finance_refresh_route(request: Request, user_id: int, wait: int | None = None):
    """Refresh manual (botão "Atualizar" e pull-to-refresh do app): pede pra Pluggy
    re-buscar do banco (PATCH /items), espera concluir e sincroniza. Difere do
    /sync, que só relê o que a Pluggy já tem sem forçar nova busca no banco.

    `?wait=` (teto 18s) existe porque o pull-to-refresh do app tem watchdog de 12s:
    com a espera padrão o gesto virava âmbar mesmo quando tudo dava certo.
    """
    shared.authorize_dashboard_access(request, user_id)
    espera = max(0, min(int(wait), 18)) if wait is not None else None
    t0 = time.monotonic()
    try:
        result = await asyncio.to_thread(refresh_and_sync_pluggy_user, user_id, wait_seconds=espera)
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Clique registrado com o usuário ANONIMIZADO (hash), sem token, credencial
    # ou valor — só quem, quando, quanto demorou e como terminou.
    itens = result.get("items") or []
    await log_system_event(
        "info" if result.get("ok") else "warning",
        "of_manual_refresh",
        f"Refresh manual de Open Finance ({'ok' if result.get('ok') else 'com pendências'})",
        source="open_finance",
        details={
            "user_hash": hashlib.sha256(str(user_id).encode()).hexdigest()[:16],
            "ok": bool(result.get("ok")),
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "items": [{"item_id": i.get("item_id"), "state": i.get("state"),
                       "reason": i.get("reason")} for i in itens],
        },
    )

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    # `ok` aqui é só "a requisição foi atendida" — NÃO é o veredito do refresh.
    # Quem diz se deu certo é `sync.ok` (conjunção por item) e `sync.items[]`, que
    # é o que o settings.html lê. Ler `data.ok` reintroduz o toast verde em cima
    # de um item perdido.
    return json.loads(shared.jdump({"ok": True, "sync": result, **snapshot}))


def _ip_prefix(request: Request) -> str:
    """IP do chamador truncado (/24 em v4, /48 em v6) — o suficiente pra ver um
    padrão de abuso, insuficiente pra identificar alguém."""
    ip = (request.client.host if request.client else "") or ""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    partes = ip.split(".")
    return ".".join(partes[:3]) + ".0/24" if len(partes) == 4 else "desconhecido"


def _verify_pluggy_webhook_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    signature = (signature_header or "").strip()
    if signature.startswith("sha256="):
        signature = signature.split("=", 1)[1]
    if not signature:
        return False

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return constant_time_eq(signature, expected)


# Headers de secret compartilhado aceitos (caso configurados no painel da Pluggy).
_PLUGGY_WEBHOOK_SECRET_HEADERS = ("x-webhook-token", "x-pluggy-token", "x-api-key")


def _authorize_pluggy_webhook(request: Request, raw_body: bytes, secret: str) -> bool:
    """Autentica o webhook da Pluggy.

    ⚠️ A Pluggy NÃO assina o corpo com HMAC (a doc dela só oferece IP fixo +
    header custom opcional). Então NÃO dá pra exigir `X-Pluggy-Signature` — isso
    rejeitava todo evento real com 401. Aceitamos um secret compartilhado que a
    GENTE controla ao registrar o webhook:

    1. token na URL (`?token=<secret>`) — a URL do webhook é registrada por nós, no
       connect-token e no painel; é o caminho principal e não depende de o painel
       suportar header custom.
    2. header com o secret (`X-Webhook-Token`/`X-Pluggy-Token`/`X-Api-Key`) — caso
       você prefira configurar um header no painel.
    3. assinatura HMAC (`X-Pluggy-Signature`) — mantida por compat/futuro; hoje a
       Pluggy não manda, mas se um dia mandar, continua valendo.

    Comparações em tempo constante. Sem secret configurado, o chamador já barra (503).
    """
    # 1. token na query string
    token = request.query_params.get("token") or ""
    if token and constant_time_eq(token, secret):
        return True
    # 2. header com o secret
    for header_name in _PLUGGY_WEBHOOK_SECRET_HEADERS:
        value = (request.headers.get(header_name) or "").strip()
        if value and constant_time_eq(value, secret):
            return True
    # 3. assinatura HMAC do corpo (compat)
    signature = request.headers.get("X-Pluggy-Signature") or ""
    if signature and _verify_pluggy_webhook_signature(raw_body, signature, secret):
        return True
    return False


def _float_finito(texto: str) -> float:
    """Hook de `json.loads`: aceita float normal, recusa nan/inf. Serve para
    `parse_float` (`1e400`) e `parse_constant` (`NaN`/`Infinity`/`-Infinity`)."""
    valor = float(texto)
    if not math.isfinite(valor):
        raise ValueError(f"número JSON não finito: {texto}")
    return valor


@router.post("/open-finance/pluggy/webhook")
async def open_finance_pluggy_webhook(request: Request):
    """
    Recebe eventos da Pluggy e responde rapido.
    Trabalho pesado de sync deve rodar fora do request.

    Autenticação: secret compartilhado via token na URL / header (a Pluggy não
    assina o corpo com HMAC). Ver `_authorize_pluggy_webhook`.
    """
    secret = (os.getenv("PLUGGY_WEBHOOK_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook não configurado.")

    raw_body = await request.body()
    if not _authorize_pluggy_webhook(request, raw_body, secret):
        # 401 mudo era um buraco de observabilidade: um webhook mal configurado
        # (ou uma tentativa de fora) não deixava rastro nenhum. O evento NÃO leva
        # secret, headers, corpo, nomes, contas, CPF, e-mail nem valores — só um
        # IP truncado e o horário.
        await log_system_event(
            "warning", "pluggy_webhook_unauthorized",
            "Webhook Pluggy recusado (credencial inválida)",
            source="open_finance",
            details={"ip_prefix": _ip_prefix(request), "has_token_param": bool(request.query_params.get("token"))},
        )
        raise HTTPException(status_code=401, detail="Não autorizado.")

    try:
        # parse_float/parse_constant: `NaN`, `Infinity`, `-Infinity` e `1e400` NÃO
        # são JSON (RFC 8259 não tem esses tokens) — o `json` do Python é que é
        # leniente e devolve float('nan')/float('inf'). Em QUALQUER chave do corpo
        # esse valor sobrevivia até o `Jsonb(event)` de update_pluggy_..._status
        # (db/open_finance.py), onde o json.dumps do psycopg emite o token cru e o
        # Postgres recusa: InvalidTextRepresentation → 500 — o laço de reenvio que
        # o comentário do `item` seis linhas abaixo existe para evitar. Recusar no
        # parse põe o caso na classe que este handler JÁ tratava com 400 desde
        # antes ("corpo que não é JSON"), sem política nova nem saneamento depois.
        # A metade de STRING da mesma via — NUL (`\u0000`) e surrogate solitário,
        # no valor OU na chave — é o que o `limpa_para_pg` fecha (#317). Nenhum
        # dos dois existe em `text`/`jsonb`, então eles davam 500 no `Jsonb(raw)`
        # de update_pluggy_open_finance_item_status, no param `text` do item_id,
        # no get_connections_by_item_id e na lista de transactionIds do `any(%s)`
        # de delete_open_finance_transactions (psycopg a adapta como text[]) — e
        # ainda faziam o `Jsonb(details)` do log_system_event perder a linha de
        # auditoria em silêncio (o `except Exception: print(...)` engole).
        # UM ponto cobre todos eles porque `item_id`, `event_name` e
        # `transactionIds` são DERIVADOS deste `event` já saneado, logo abaixo —
        # por isso o `db/` não muda. Inclui o `register_item` de item
        # desconhecido (~70 linhas abaixo), que grava `provider_item_id` e
        # `last_event`: MEDIDO com `{"event":"item/updated","itemId":"ZZ\ud800"}`
        # — na `main` é 500 e NÃO grava; aqui é 200 e grava
        # `provider_item_id='ZZ\ufffd\ufffd\ufffd'` com `user_id=None`. Dois
        # surrogates diferentes colapsam nessa mesma identidade e viram DUAS
        # linhas. É lixo novo no registry, não privilégio novo: exige o secret, e
        # a mesma capacidade já existia com qualquer id limpo desconhecido.
        # DENTRO do try de propósito: fora dele, um corpo fundo o bastante
        # viraria 500 em vez do 400 que já era o desfecho de "não é JSON".
        event = limpa_para_pg(
            json.loads(raw_body, parse_float=_float_finito, parse_constant=_float_finito)
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Webhook inválido.") from exc
    if not isinstance(event, dict):
        # Topo válido em JSON mas não-objeto (`[1,2,3]`, `"abc"`, `42`, `null`,
        # `true`) passava pelo parse e quebrava no `.get()` abaixo → 500 (#310).
        raise HTTPException(status_code=400, detail="Webhook inválido.")

    event_name = str(event.get("event") or event.get("type") or "")
    # `item` não-objeto vale como AUSENTE, não como 400: `{"item": null}` é corpo
    # que a própria Pluggy manda em evento sem item, e responder erro faz ela
    # REENVIAR — 500 em loop. O `.get("item", {})` só usava o default `{}` quando
    # a CHAVE faltava; com a chave presente e valor `null`/escalar/lista, o
    # `.get("id")` seguinte levantava AttributeError → 500 (#310). O `itemId` do
    # topo continua identificando o evento quando existir.
    item = event.get("item")
    item_id = str(event.get("itemId") or event.get("item_id")
                  or (item.get("id") if isinstance(item, dict) else None) or "")
    # `item/updated` NÃO escreve mais ACTIVE: quem afirma que sincronizou é o sync,
    # depois de consultar o item e puxar as contas. O webhook só diz o que a Pluggy
    # disse.
    status_by_event = {
        "item/created": "UPDATING",
        "item/error": "ERROR",
        "item/deleted": "DELETED",
    }
    status = status_by_event.get(event_name)
    if item_id and status:
        await asyncio.to_thread(update_pluggy_open_finance_item_status, item_id, status, event)

    # Antes de qualquer sync, resolve a POSSE do item. O `limit 1` sem `order by`
    # que existia aqui sorteava um dono quando o item aparecia em duas contas.
    conexoes = await asyncio.to_thread(get_connections_by_item_id, item_id) if item_id else []

    # transactions/deleted: a Pluggy manda os ids removidos — apaga direto, senão ficam
    # órfãos (um re-sync não os removeria, pois não voltam no list_transactions).
    if item_id and event_name == "transactions/deleted":
        deleted_ids = event.get("transactionIds") or event.get("transactionsIds") or []
        if isinstance(deleted_ids, list) and deleted_ids:
            await asyncio.to_thread(delete_open_finance_transactions, item_id, deleted_ids)
    elif item_id and event_name in PLUGGY_SYNC_EVENTS:
        if len(conexoes) == 1:
            _schedule_pluggy_sync(item_id)
        elif not conexoes:
            # Item que não conhecemos. Em `item/created` — e SÓ nele — pergunta à
            # Pluggy de quem ele é e adota. QUEM ele destrava está na docstring de
            # `_adota_item_orfao` ("Por que existe"), fonte única (CLAUDE.md §0.7);
            # aqui fica só o QUANDO, que é decisão deste chamador:
            #
            # Por que só nesse evento: `item/created` dispara UMA vez, na criação,
            # e é exatamente "item novo que o navegador não registrou". Evento
            # POSTERIOR (`item/updated`, `transactions/created`) num item sem
            # conexão significa item que JÁ TEVE conexão e foi removida — adotar
            # ali ressuscita o banco que o usuário acabou de tirar, com sync
            # agendado, que é o buraco que o `_disconnect_sob_lock` fechou pelo
            # outro lado. O delete do item na Pluggy é best-effort
            # (`delete_pluggy_items_best_effort`): o item sobrevive à falha e
            # continua mandando evento.
            adotado = (await _adota_item_orfao(item_id, event_name)
                       if event_name == "item/created" else None)
            if adotado is None:
                # Sem adoção: registra (o GET /items lista devolve 401, então este
                # é o único jeito de saber que ele existe) e NÃO sincroniza.
                # ponytail: INSERT puro, sem teto — sob entrega at-least-once,
                # cada retentativa de um item que já tem dono soma mais uma linha
                # `origin='webhook'`/`user_id NULL` aqui. Igual à `main` (não é
                # regressão) e inofensivo para os leitores, que perguntam
                # por rastro COM dono; se o volume incomodar, é um upsert por
                # (provider, item, origin) com contador.
                await log_system_event(
                    "warning", "of_webhook_item_unknown",
                    "Webhook de item sem conexão local",
                    source="open_finance", details={"item_id": item_id, "event": event_name},
                )
                try:
                    await asyncio.to_thread(
                        register_item, None, provider_item_id=item_id,
                        origin="webhook", last_event=event_name,
                    )
                except Exception as exc:  # noqa: BLE001 — rastro nunca derruba o 200
                    # Mesmo contrato do `of_item_registry_failed` na emissão do
                    # connect token: 5xx aqui vira retentativa em laço da Pluggy.
                    await log_system_event(
                        "warning", "of_item_registry_failed",
                        "Falha ao registrar item sem conexão",
                        source="open_finance",
                        details={"item_id": item_id, "error": str(exc)[:200]},
                    )
        else:
            # Dois donos possíveis: sincronizar um deles é sincronizar a carteira
            # do usuário errado. Recusa.
            await log_system_event(
                "error", "of_item_owner_conflict",
                "Item Pluggy ligado a mais de uma conexão — sync recusado",
                source="open_finance",
                details={"item_id": item_id, "connections": len(conexoes), "event": event_name},
            )

    await log_system_event(
        "info" if event_name != "item/error" else "warning",
        "pluggy_webhook_received",
        f"Webhook Pluggy recebido: {event_name or 'evento desconhecido'}",
        source="open_finance",
        details={"event": event_name, "item_id": item_id},
    )
    return {"received": True}


@router.post("/open-finance/{user_id}/mock-connect")
async def open_finance_mock_connect_route(request: Request, user_id: int, payload: OpenFinanceMockConnectPayload):
    shared.authorize_dashboard_access(request, user_id)
    result = await asyncio.to_thread(
        create_mock_open_finance_connection,
        user_id,
        payload.institution or "nubank",
    )

    await asyncio.to_thread(
        record_audit_event,
        user_id,
        AuditEvent.OPEN_FINANCE_CONNECTED,
        request=request,
        details={"provider": "mock", "institution": payload.institution or "nubank"},
    )

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "sync": result, **snapshot}))


def delete_pluggy_items_best_effort(user_id: int, item_ids: list[str] | None = None) -> list[str]:
    """Deleta os items do usuário na Pluggy (best-effort). Sem isso, remover
    a conexão apagava só o nosso registro e o item ficava órfão na Pluggy,
    bloqueando a reconexão ("já possui conexão com este acesso"). Falha por
    item: loga system_event e segue — não impede a limpeza local.

    Devolve os item ids que tentou deletar (a ENUMERAÇÃO): o reset compara
    com o que o DELETE local varreu e faz um 2º passe no que ficou de fora
    (item salvo entre a enumeração e o DELETE — Codex PR #217, 11º).
    `item_ids` explícito é esse 2º passe: pula a enumeração e deleta os dados.

    SÍNCRONO de propósito: o reset de conta (POST /settings/reset) o roda como
    hook de `reset_user_data`, DENTRO dos locks de item e numa thread — rota
    async chama via asyncio.to_thread. Extraído do disconnect
    (DELETE /open-finance/{user_id}) sem mudança de comportamento.
    """
    from core.observability import log_system_event_sync

    pluggy_item_ids = (list_pluggy_item_ids(user_id) if item_ids is None
                       else [i for i in item_ids if i])
    if not pluggy_item_ids:
        return []
    api_key = None
    try:
        api_key = create_pluggy_api_key()
    except Exception as exc:  # noqa: BLE001 — best-effort; segue pra limpeza local
        log_system_event_sync(
            "warning", "pluggy_disconnect_auth_failed",
            f"Sem apiKey pra deletar items no disconnect do user {user_id}: {exc}",
            source="open_finance", details={"user_id": user_id, "error": str(exc)[:200]},
        )
    if api_key:
        for item_id in pluggy_item_ids:
            try:
                delete_pluggy_item(item_id, api_key)
            except Exception as exc:  # noqa: BLE001 — best-effort por item
                log_system_event_sync(
                    "warning", "pluggy_item_delete_failed",
                    f"Falha ao deletar item {item_id} na Pluggy no disconnect: {exc}",
                    source="open_finance",
                    details={"item_id": item_id, "error": str(exc)[:200]},
                )
    return pluggy_item_ids


def _disconnect_sob_lock(user_id: int) -> int:
    """Disconnect segurando os locks dos items, na MESMA ordem do reset
    (locks → remoto → local; trade-off da rede dentro do lock documentado em
    db/privacy.reset_user_data — operação rara, disparada pelo usuário).

    Sem o lock, o disconnect deletava a conexão por baixo da reconexão: entre
    a releitura do guard de `_salva_item_sob_lock` e o insert cabia um delete,
    e a conexão ressuscitava com sync agendado. Com o lock o interleaving não
    existe: ou o disconnect espera a janela de ms da reconexão, ou completa
    antes e o guard responde 409. Lock ocupado até o teto → 503 "tente de
    novo", mesmo contrato do reset e do `_grava_reconexao`.
    """
    from db.open_finance_state import pluggy_items_lock

    with pluggy_items_lock(list_pluggy_item_ids(user_id)) as locked:
        if not locked:
            raise HTTPException(
                status_code=503,
                detail="Não foi possível desconectar agora: uma sincronização "
                       "bancária está em andamento. Tente de novo em alguns segundos.",
            )
        enumerados = delete_pluggy_items_best_effort(user_id)
        varridos: list[str] = []
        deleted = disconnect_open_finance_connection(user_id, swept_out=varridos)
        # 2º passe (Codex PR #217, 12º — irmão do 11º no reset): item salvo
        # ENTRE a enumeração acima e o delete local ficou órfão na Pluggy
        # ("já possui conexão com este acesso"). `varridos` só existe se o
        # delete commitou; deleta o que a enumeração não viu (normalmente
        # vazio). Best-effort — o helper já loga por item.
        tardios = sorted(set(varridos) - set(enumerados))
        if tardios:
            try:
                delete_pluggy_items_best_effort(user_id, tardios)
            except Exception as exc:  # noqa: BLE001 — mesmo contrato do 1º passe
                from core.observability import log_system_event_sync

                log_system_event_sync(
                    "warning", "pluggy_item_delete_failed",
                    f"2º passe do disconnect do user {user_id} falhou: {exc}",
                    source="open_finance",
                    details={"items": tardios, "error": str(exc)[:200]},
                )
        return deleted


@router.delete("/open-finance/{user_id}")
async def open_finance_disconnect_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)

    deleted = await asyncio.to_thread(_disconnect_sob_lock, user_id)

    if deleted:
        await asyncio.to_thread(
            record_audit_event,
            user_id,
            AuditEvent.OPEN_FINANCE_DISCONNECTED,
            request=request,
        )

    return {"ok": True, "deleted": deleted}
