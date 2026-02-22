import os
import asyncio
from fastapi import FastAPI, Request, HTTPException
from adapters.whatsapp.wa_client import send_text
from adapters.whatsapp.wa_parse import extract_incoming

from core.types import IncomingMessage, Attachment
from core.handle_incoming import handle_incoming


app = FastAPI()

WA_VERIFY_TOKEN = os.getenv("WA_VERIFY_TOKEN", "")


@app.get("/wa/webhook")
def verify_webhook(hub_mode: str = "", hub_challenge: str = "", hub_verify_token: str = ""):
    # Meta manda esses query params como:
    # hub.mode, hub.challenge, hub.verify_token
    # FastAPI substitui "." por "_" em parâmetros automaticamente em alguns setups,
    # então também vamos ler direto do Request num passo abaixo se precisar.
    if hub_verify_token == WA_VERIFY_TOKEN and hub_challenge:
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Invalid verify token")


@app.post("/wa/webhook")
async def receive_webhook(req: Request):
    payload = await req.json()

    from_phone, text, msg_id = extract_incoming(payload)
    if not from_phone or not text:
        # pode ser status update, delivery, etc.
        return {"ok": True}

    # monta IncomingMessage pro core
    msg = IncomingMessage(
        platform="whatsapp",
        user_id=str(from_phone),      # aqui user_id = telefone (por enquanto)
        text=text,
        attachments=[],               # depois a gente adiciona mídia/arquivo
    )

    # roda core fora do event loop (pra não travar)
    result = await asyncio.to_thread(handle_incoming, msg)

    reply = result.get("text") or "Ok."
    # Envia resposta via Cloud API
    await asyncio.to_thread(send_text, from_phone, reply)

    return {"ok": True}