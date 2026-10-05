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
from frontend.routes import open_finance as _of

# Observações simultâneas no trecho bloqueante (2 requisições HTTP em thread): uma
# rajada de `item/error` (queda da Pluggy) não pode encher o executor padrão e
# atrasar o banco do resto do app. Quem passa do teto espera aqui, sem thread.
_MAX_SIMULTANEAS = 4
_semaforo = asyncio.Semaphore(_MAX_SIMULTANEAS)

# Status da releitura que CONFIRMA o `item/error` (o resto é item vivo: diverge).
_STATUS_DE_ERRO = {"ERROR", "LOGIN_ERROR"}


def _le_item(item_id: str) -> dict:
    return get_pluggy_item(item_id, create_pluggy_api_key())


async def _pista(connection_id: int, user_id: int) -> None:
    """`ERROR` com o motivo e o `health` intactos. Relê a linha para pegar a versão
    atual: a pista não pode derrubar o que outro escritor gravou depois."""
    linha = await asyncio.to_thread(get_linha_para_observar, connection_id, user_id)
    if linha is not None:
        await asyncio.to_thread(
            mark_sync_result, connection_id, ok=None, status="ERROR",
            status_reason=None, versao_vista=linha["updated_at"])


async def _observa(item_id: str, connection_id: int, user_id: int) -> None:
    for tentativa in (1, 2):
        linha = await asyncio.to_thread(get_linha_para_observar, connection_id, user_id)
        if linha is None or str(linha["status"] or "").upper() in _TERMINAL:
            return   # apagada, PAUSED ou DELETED: nada a observar (sem GET, sem log)
        try:
            async with _semaforo:
                item = await asyncio.to_thread(_le_item, item_id)
        except Exception:  # noqa: BLE001 — 429/5xx/timeout/404: não confirmou, pista
            return await _pista(connection_id, user_id)
        if await asyncio.to_thread(observar_item, linha, item):
            status_item = str(item.get("status") or "").upper()
            if status_item not in _STATUS_DE_ERRO:
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
    await _pista(connection_id, user_id)


def agenda_observacao(item_id: str, conexao: dict) -> None:
    """Chamada pelo webhook com a linha que `get_connections_by_item_id` resolveu
    (a posse do item). Em voo: coalesce em `_DIRTY` (a rodada suja é um sync, que
    relê). Senão ocupa o slot de `_INFLIGHT`."""
    if item_id in _of._INFLIGHT:
        _of._DIRTY.add(item_id)
        return
    task = asyncio.create_task(
        _observa(item_id, conexao["id"], conexao["user_id"]),
        name=f"pluggy_observa_{item_id}")
    _of._INFLIGHT[item_id] = task
    task.add_done_callback(lambda _t: _of._on_sync_done(item_id))
