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
import os
import random
import time

import psycopg

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.admin_dashboard import log_system_event
from core.audit import AuditEvent, record_audit_event
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
    list_pluggy_item_ids,
    pluggy_item_lock,
    register_item,
    save_pluggy_open_finance_item,
    token_hash,
    update_pluggy_open_finance_item_status,
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
                         tinha_conexao_propria: bool = False) -> tuple[dict, bool]:
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
        return save_pluggy_open_finance_item(user_id, remote, budget_ms=resto), True


async def _grava_reconexao(
    user_id: int, remote: dict, item_id: str, tinha_conexao_propria: bool = False,
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
                tinha_conexao_propria)
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

    await _enforce_bank_limit(session_uid, new_item_id)
    try:
        connection = await _grava_reconexao(
            session_uid, remote, new_item_id, tinha_conexao_propria)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await asyncio.to_thread(
        register_item, session_uid,
        provider_item_id=new_item_id, origin="pluggy_item",
        status=str(remote.get("status") or "") or None,
    )

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
    return hmac.compare_digest(signature, expected)


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
    if token and hmac.compare_digest(token, secret):
        return True
    # 2. header com o secret
    for header_name in _PLUGGY_WEBHOOK_SECRET_HEADERS:
        value = (request.headers.get(header_name) or "").strip()
        if value and hmac.compare_digest(value, secret):
            return True
    # 3. assinatura HMAC do corpo (compat)
    signature = request.headers.get("X-Pluggy-Signature") or ""
    if signature and _verify_pluggy_webhook_signature(raw_body, signature, secret):
        return True
    return False


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
        event = json.loads(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Webhook inválido.") from exc

    event_name = str(event.get("event") or event.get("type") or "")
    item_id = str(event.get("itemId") or event.get("item_id") or event.get("item", {}).get("id") or "")
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
            # Item que não conhecemos: registra (o GET /items lista devolve 401,
            # então este é o único jeito de saber que ele existe) e NÃO sincroniza.
            await log_system_event(
                "warning", "of_webhook_item_unknown",
                "Webhook de item sem conexão local",
                source="open_finance", details={"item_id": item_id, "event": event_name},
            )
            await asyncio.to_thread(
                register_item, None, provider_item_id=item_id,
                origin="webhook", last_event=event_name,
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
