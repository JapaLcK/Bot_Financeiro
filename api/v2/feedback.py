"""`POST /api/v2/feedback`: o feedback da tela Ajuda do /painel, entregue por e-mail ao time.

Não grava no banco. O usuário e o e-mail da conta vêm SÓ do servidor (sessão e `db.privacy`); o corpo traz
apenas `tipo` e `mensagem`, então não escolhe conta, destino nem assunto. O destino é constante do módulo e
nunca sai em resposta, em erro ou no front. A mensagem é texto livre do usuário: escapada no HTML e fora de
qualquer log. Sem assinatura vigente a tela não abre (o /painel serve o paywall), então `usuario_atual`.
"""
import asyncio
from html import escape
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints

from api.v2.sessao import usuario_atual
from core.services.email_service import send_email
from db.privacy import get_user_email

router = APIRouter()

DESTINO = "lucas@pigbankai.com"
ASSUNTO = {"ideia": "Ideia", "problema": "Problema", "elogio": "Elogio"}
MAX = 4000


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tipo: Literal["ideia", "problema", "elogio"]
    mensagem: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX)]


class Enviado(BaseModel):
    enviado: bool = True


@router.post("/feedback", response_model=Enviado)
async def enviar(corpo: FeedbackIn, uid: int = Depends(usuario_atual)) -> Enviado:
    from frontend.finance_bot_websocket_custom import _check_persistent_rate_limit  # lazy: ele importa a v2
    await _check_persistent_rate_limit("feedback", f"user:{uid}", 5, 3600)  # conta antes de enviar: falha também gasta
    email = await asyncio.to_thread(get_user_email, uid)
    rotulo = ASSUNTO[corpo.tipo]
    texto = f"Tipo: {rotulo}\nUsuário: {uid}\nE-mail da conta: {email or 'sem e-mail'}\n\n{corpo.mensagem}"
    html = (f"<p><strong>Tipo:</strong> {rotulo}<br><strong>Usuário:</strong> {uid}<br>"
            f"<strong>E-mail da conta:</strong> {escape(email or 'sem e-mail')}</p>"
            f"<p style=\"white-space:pre-wrap\">{escape(corpo.mensagem)}</p>")
    ok = await asyncio.to_thread(
        send_email, DESTINO, f"Feedback do painel: {rotulo}", html, text_body=texto,
        headers={"Reply-To": email} if email else None)
    if not ok:
        raise HTTPException(status_code=502, detail={"error": "feedback_nao_enviado"})
    return Enviado()
