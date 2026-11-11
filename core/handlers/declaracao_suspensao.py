"""Outro comando com a confirmação da declaração viva (#1315, R3-1 A e o ajuste do dono): ela é SUSPENSA antes de o
comando rodar e, no fim do turno, volta ou se encerra (`desfecho`, chamado pelo `handle_incoming`).

A pendência do roteador vira `confirm_declaracao_suspensa` com o mesmo payload (a da IA sai da tabela dela e a marca
leva os dados dela): o "sim" que chegar no meio acha a marca, não grava e recebe `AINDA_TERMINANDO` (R4-2), nunca o T19
de "cancelada". A marca é oferta de conveniência: o comando que precisar da linha a desaloja (e aí a confirmação acaba).
No livro a ação continua `armada` até o desfecho.
"""
from __future__ import annotations

from contextvars import ContextVar

import db
from core.handlers import declaracao_banco as decl
from core.handlers import declaracao_banco_textos as tx
from core.handlers import declaracao_confirma as dc

SUSPENSA: ContextVar[tuple | None] = ContextVar("declaracao_suspensa", default=None)


def suspende(user_id: int, texto: str, platform: str) -> None:
    """Uma resposta ("sim"/"não") nunca é outro comando: a do roteador o gancho já decidiu, e a exata da IA vai ao runner."""
    wa = platform == "whatsapp"
    if not decl.ligada(user_id) or decl.e_sim(texto, wa) or decl.e_nao(texto, wa):
        return
    viva = dc._viva(user_id, platform, db.get_pending_action(user_id))
    if not viva:
        return
    porta, pend = viva
    minutos = dc.PRAZO.total_seconds() / 60
    if porta == "roteador":
        if not db.advance_pending_action(user_id, dc.TIPO, pend["payload"], pend["payload"], minutos,
                                         new_action_type="confirm_declaracao_suspensa",  # literal: o registro
                                         old_created_at=pend["created_at"]):
            return
    else:
        if not db.ai_consume_pending_action(user_id, pend):
            return
        # sem a linha (outra pergunta do roteador nela), o "sim" no meio recebe o T19 de sempre
        db.claim_pending_action(user_id, "confirm_declaracao_suspensa", {"_ia": _da_ia(pend)}, minutes=minutos)
    SUSPENSA.set((user_id, viva, texto))


def _da_ia(pend: dict) -> dict:
    return {k: pend[k] for k in ("tool_name", "tool_args", "summary")}


def desfecho(msg, saida: str, nao: bool, nao_ajudou: bool) -> str | None:
    """Depois do comando que rodou com a confirmação suspensa: o texto no lugar da resposta (`saida`), ou None (a
    resposta fica e a confirmação se encerra). `nao`: o fluxo leu um "não" sem pendência (o `confirm.no` do
    classificador, "Nada a cancelar."): vale como "não" à confirmação, se mandado depois dela (R3-2 A). `nao_ajudou`
    (ou a saudação, pelo intent do classificador local: R4-3): a resposta e, depois dela, a mesma pergunta, com a
    mesma ação, se a marca ainda está na linha."""
    from core.intent_classifier import classify
    user_id, viva, texto = SUSPENSA.get()
    saudou = classify(texto or "", allow_ai=False).intent == "greeting"
    porta, pend = viva
    atual = db.get_pending_action(user_id)
    nossa = (pend["payload"] if porta == "roteador" else {"_ia": _da_ia(pend)})
    marca = atual if atual and atual.get("action_type") == dc.SUSPENSA_TIPO and atual["payload"] == nossa else None
    if nao and dc._depois(dc._segundos(msg), pend["created_at"]):
        marca and db.consume_pending_action(user_id, marca)
        dc._encerra(user_id, viva)
        return tx.NAO_FIZ_NADA
    if nao or nao_ajudou or saudou:
        volta = None
        if porta == "roteador":
            if marca and db.advance_pending_action(user_id, dc.SUSPENSA_TIPO, marca["payload"], marca["payload"],
                                                   dc.PRAZO.total_seconds() / 60,
                                                   new_action_type="confirm_declaracao_banco",  # literal: o registro
                                                   old_created_at=marca["created_at"]):
                volta = pend
        else:
            marca and db.consume_pending_action(user_id, marca)
            volta = db.ai_set_pending_action(user_id, pend["tool_name"], pend["tool_args"], pend["summary"])
        if volta:
            pergunta = dc._pergunta((porta, volta), msg.platform)
            return f"{tx.ENVIADA_ANTES}\n\n{pergunta}" if nao else f"{saida}\n\n{pergunta}"
    marca and db.consume_pending_action(user_id, marca)
    dc._encerra(user_id, viva)
    return None
