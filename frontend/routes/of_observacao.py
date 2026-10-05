"""Webhook `item/error` como gatilho de OBSERVAÇÃO (Onda 5, PR-C2). Sem router.

O webhook não grava veredito sobre a conexão: relê o item na Pluggy
(`GET /items`) e entrega a leitura a `observar_item`, o ponto único de escrita da
observação (`core/services/pluggy_sync.py`). Só se a releitura NÃO confirma (erro de
rede, 429/5xx, 404, ou duas derrotas de versão) grava a PISTA: `ERROR` sem apagar o
motivo, sem tocar `raw` e sem tocar `health`; quem resolve é a retentativa do B2
(`pista_de_erro`) e o sync que ela agenda. 404 NÃO grava `item_missing` aqui: o
disjuntor contra credencial errada mora no job de saúde.

Mora aqui, e não em `core/`, porque `_INFLIGHT`/`_DIRTY` são memória do laço de
`routes/open_finance.py` (mesmo padrão de `of_retentativa.py`). A tarefa OCUPA o
slot de `_INFLIGHT` do item; o `_on_sync_done` de lá solta o slot e roda a rodada
suja (um sync, que relê). Item já em voo: só marca `_DIRTY` e não lê.

Tabelas de estado e de concorrência: `docs/open_finance_estados.md` §2.2.
"""

from __future__ import annotations

import asyncio

from core.admin_dashboard import log_system_event
from core.services.pluggy import create_pluggy_api_key, get_pluggy_item
from core.services.pluggy_sync import observar_item
from db import get_linha_para_observar, mark_sync_result
from db.open_finance_state import _TERMINAL
from core.services.pluggy_health import _NEEDS_USER
from frontend.routes import open_finance as _of

# Observações simultâneas, a observação INTEIRA (leitura da linha, GET, escrita, logs;
# tudo em thread), não só o GET: uma rajada de `item/error` (queda da Pluggy) não pode
# encher o executor padrão, compartilhado com o resto do app (o próprio webhook faz o
# lookup de posse nele). Quem passa do teto espera no semáforo como tarefa asyncio,
# sem thread. O permit é pego UMA vez, em `_tarefa` (nada abaixo o pega de novo).
_MAX_SIMULTANEAS = 4
_semaforo = asyncio.Semaphore(_MAX_SIMULTANEAS)


def _le_item(item_id: str) -> dict:
    return get_pluggy_item(item_id, create_pluggy_api_key())


async def _pista(connection_id: int, versao: object) -> None:
    """`ERROR` com o motivo e o `health` intactos, pela versão lida ANTES do GET (sem
    reler: reler anularia o CAS). Se qualquer escritor mexeu na linha desde então
    (sync, job, reconexão), grava 0 linhas: o escritor mais novo tem informação mais
    fresca que a de um GET que falhou."""
    await asyncio.to_thread(
        mark_sync_result, connection_id, ok=None, status="ERROR",
        status_reason=None, versao_vista=versao)


async def _observa(item_id: str, connection_id: int, user_id: int, visto: dict) -> None:
    """`visto["versao"]`: o `updated_at` da última leitura da linha, para a `_tarefa`
    poder gravar a pista se algo inesperado levantar (sem versão lida, não grava)."""
    for tentativa in (1, 2):
        linha = await asyncio.to_thread(get_linha_para_observar, connection_id, user_id)
        if linha is None or str(linha["status"] or "").upper() in _TERMINAL:
            return   # apagada, PAUSED ou DELETED: nada a observar (sem GET, sem log)
        visto["versao"] = linha["updated_at"]
        try:
            item = await asyncio.to_thread(_le_item, item_id)
        except Exception:  # noqa: BLE001 — 429/5xx/timeout/404: não confirmou, pista
            return await _pista(connection_id, linha["updated_at"])
        if await asyncio.to_thread(observar_item, linha, item):
            status_item = str(item.get("status") or "").upper()
            # Mesma pergunta do `resolve_connection_state` ("o item está em erro?"):
            # `_NEEDS_USER` ou `ERROR`; item que o resolvedor grava como erro confirma o
            # evento e NÃO diverge.
            if status_item not in _NEEDS_USER and status_item != "ERROR":
                # V6 (plano da C2): o webhook diz erro e o item volta vivo. A
                # observação vence; o evento deixa a Onda 8 medir a frequência.
                await log_system_event(
                    "info", "of_observacao_diverge",
                    "Webhook disse erro e a releitura devolveu item vivo",
                    source="open_finance", user_id=user_id,
                    details={"item_id": item_id, "status_evento": "ERROR",
                             "status_releitura": status_item},
                )
            return
    await log_system_event(
        "warning", "of_observacao_perdeu_corrida",
        "Observação do webhook perdeu a versão da linha duas vezes",
        source="open_finance", user_id=user_id, details={"item_id": item_id})
    # `linha` é a leitura da 2ª tentativa, que `observar_item` acabou de perder: a
    # versão está velha, então a pista grava 0 linhas (no-op de propósito) e o
    # escritor que ganhou fica. Só o log registra.
    await _pista(connection_id, linha["updated_at"])


async def _tarefa(item_id: str, connection_id: int, user_id: int) -> None:
    """Tratamento de topo da tarefa de fundo (o webhook já respondeu 200): falha
    inesperada vira log estruturado e, se der, a pista. Nunca texto da exceção
    (#541: mensagem de erro de banco leva host/porta); só o tipo. Se o banco é
    justamente o que caiu, a pista e o log falham e são engolidos: o slot de
    `_INFLIGHT` solta pelo callback de qualquer jeito."""
    async with _semaforo:   # o permit cobre a tarefa toda, inclusive o log e a pista
        visto: dict = {}
        try:
            await _observa(item_id, connection_id, user_id, visto)
        except Exception as exc:  # noqa: BLE001
            try:
                await log_system_event(
                    "warning", "of_observacao_falhou",
                    "Observação do webhook falhou por erro inesperado",
                    source="open_finance", user_id=user_id,
                    details={"item_id": item_id, "tipo_do_erro": type(exc).__name__})
            except Exception:  # noqa: BLE001
                pass
            if "versao" in visto:   # sem versão lida não há CAS: fica só o log
                try:
                    await _pista(connection_id, visto["versao"])
                except Exception:  # noqa: BLE001
                    pass


def agenda_observacao(item_id: str, conexao: dict) -> None:
    """Chamada pelo webhook com a linha que `get_connections_by_item_id` resolveu
    (a posse do item). Em voo: coalesce em `_DIRTY` (a rodada suja é um sync, que
    relê). Senão ocupa o slot de `_INFLIGHT`, inclusive enquanto a tarefa espera o
    permit do semáforo (é o coalescer: evento no meio vira `_DIRTY`); o callback
    solta o slot também se a tarefa for cancelada nessa espera."""
    if item_id in _of._INFLIGHT:
        _of._DIRTY.add(item_id)
        return
    task = asyncio.create_task(
        _tarefa(item_id, conexao["id"], conexao["user_id"]),
        name=f"pluggy_observa_{item_id}")
    _of._INFLIGHT[item_id] = task
    task.add_done_callback(lambda _t: _of._on_sync_done(item_id))
