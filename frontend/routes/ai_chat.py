"""Chat do Piggy no app em streaming: `POST /ai/chat/stream` e o 👍👎.

O gate (`require_pro_feature("ai_chat")`) mora no monólito e este módulo é
importado por ele, então chega por parâmetro (`criar_router`) em vez de import.
`/ai/chat` não muda: o build antigo, o web e o WhatsApp seguem nele.

SSE: só `data: <json>\\n\\n` e `: ping` a cada PING_S. Não há rota de "parar": o
abort do app é a desconexão, que liga o Event de cancelamento do runner. Vale com
N réplicas, sem estado compartilhado.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictInt, constr, field_validator, model_validator

from core.pg_text import limpa_para_pg
from core.services.ai_chat import streaming
from db.ai_feedback import MOTIVOS
from frontend.routes import shared

logger = logging.getLogger(__name__)

PING_S = 15.0
ERRO = "Tivemos um problema aqui. Tente de novo em instantes."
# O task do turno sobrevive à desconexão (precisa gravar o histórico e fechar a
# cota); o loop só guarda referência fraca dele.
_TURNOS: set[asyncio.Task] = set()


class AIChatBody(BaseModel):
    message: str
    # Os cards que o app sabe mostrar. Ausente (build antigo) = só os de sempre: o card que a própria tool emite
    # (`cards.DA_TOOL`) só sai se vier aqui, senão o app o descartaria e o dado sumiria.
    cards: list[constr(max_length=40)] = Field(default_factory=list, max_length=50)


TEXTO_MAX = 500


class FeedbackBody(BaseModel):
    nota: StrictInt | None  # StrictInt: true e 1.0 não são nota
    # Literal derivado de MOTIVOS: uma fonte só. Build 31 manda só `{nota}` (motivo/texto = None).
    motivo: Literal[tuple(MOTIVOS)] | None = None
    texto: str | None = None

    @field_validator("texto")
    @classmethod
    def _texto(cls, v):
        # Sem eco do valor na mensagem. Não trunca: passou de 500 é 422.
        v = limpa_para_pg(v).strip() if v is not None else None
        if len(v or "") > TEXTO_MAX:
            raise ValueError(f"texto passa de {TEXTO_MAX} caracteres")
        return v or None

    @model_validator(mode="after")
    def _regras_do_estado(self):
        if self.motivo is not None and self.nota != -1:
            raise ValueError("motivo só vale com nota -1")
        if self.texto is not None and self.motivo != "outro":
            raise ValueError("texto só vale com o motivo outro")
        return self

    @field_validator("nota")
    @classmethod
    def _so_mais_ou_menos_um(cls, v):
        if v not in (None, -1, 1):
            raise ValueError("nota deve ser 1, -1 ou null")
        return v


def texto_valido(message: str) -> str:
    """As validações de `/ai/chat` e `/ai/chat/stream` (400 antes de qualquer custo)."""
    text = (message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="message vazio.")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="message muito longo (máx 2000 chars).")
    return text


def _sse(evento: dict) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


async def _fluxo(user_id: int, text: str, monthly_limit: int, cards: tuple[str, ...] = ()):
    import db

    loop = asyncio.get_running_loop()
    fila: asyncio.Queue = asyncio.Queue()
    cancela = threading.Event()

    def emissor(evento):
        loop.call_soon_threadsafe(fila.put_nowait, evento)

    async def turno():
        try:
            r = await asyncio.to_thread(
                streaming.executar, user_id, text,
                monthly_limit=monthly_limit, emissor=emissor, cancela=cancela, cards=cards)
            if not r.interrompida:
                usado = await asyncio.to_thread(db.ai_get_usage_this_month, user_id)
                emissor({"tipo": "fim", "reply": r.reply, "message_id": r.message_id,
                         "usage": {"used": usado, "limit": monthly_limit}, "chips": r.chips})
        except Exception:
            logger.exception("ai_chat stream falhou pra user %s", user_id)
            emissor({"tipo": "erro", "mensagem": ERRO})
        finally:
            emissor(None)

    tarefa = asyncio.create_task(turno())
    _TURNOS.add(tarefa)
    tarefa.add_done_callback(_TURNOS.discard)
    try:
        while True:
            try:
                evento = await asyncio.wait_for(fila.get(), PING_S)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if evento is None:
                return
            yield _sse(evento)
    finally:
        cancela.set()  # desconexão (ou fim normal, onde já é tarde para importar)


def criar_router(pro) -> APIRouter:
    router = APIRouter()

    @router.post("/ai/chat/stream")
    @shared.limiter.limit("12/minute")
    async def ai_chat_stream(request: Request, body: AIChatBody, user_id: int = Depends(pro)):
        text = texto_valido(body.message)
        from core.services.plan_service import ai_monthly_limit_for

        monthly_limit = await asyncio.to_thread(ai_monthly_limit_for, user_id)
        return StreamingResponse(
            _fluxo(user_id, text, monthly_limit, tuple(body.cards)), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/ai/messages/{message_id}/feedback")
    # `shared_limit(scope=)` e não `limit()`: o limiter compartilhado usa o key_style
    # "url" do slowapi, e com ele cada `message_id` abriria um balde próprio (teto
    # decorativo). O stream, sem path param, serve com `limit()`.
    @shared.limiter.shared_limit("60/minute", scope="ai_feedback")
    async def ai_feedback(
        request: Request, body: FeedbackBody,
        message_id: int = Path(ge=1, le=2**63 - 1), user_id: int = Depends(pro),
    ):
        import db

        # Mesmo 404 para inexistente, de outro usuário ou de mensagem do usuário.
        if not await asyncio.to_thread(db.ai_set_feedback, user_id, message_id, body.nota, body.motivo, body.texto):
            raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
        return {"ok": True, "nota": body.nota}

    return router
