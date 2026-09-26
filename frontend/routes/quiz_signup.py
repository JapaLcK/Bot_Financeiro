"""Cadastro pelo quiz de venda (XQuiz): a conta nasce SEM senha.

Serviço:   POST /xquiz/webhook     o XQuiz manda e-mail/nome/WhatsApp do formulário;
           aqui se grava a verificação sem senha e se envia o código de 6 dígitos.
           Token = env XQUIZ_WEBHOOK_TOKEN (constant_time_eq): sem env 503, errado 401.
           Falha de banco ou de envio: 503, para o XQuiz reenviar (o código vivo é reaproveitado).
Navegador: POST /auth/quiz/resend  a /q reenvia o código (com CSRF).

A conta é criada pelo /auth/verify-email de sempre, que a /q chama com e-mail e
código (frontend/quiz-resultado.js). Nome, telefone, e-mail e código são PII:
fora de log e de print — exceção só com `type(exc).__name__`.
"""
import asyncio
import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator

from core.admin_dashboard import log_system_event
from core.pg_text import recusa_veneno
from core.secure_compare import constant_time_eq
from core.services.email_service import send_account_exists_notice, send_verification_email
from db.reports import (
    AccountAlreadyExistsError, create_email_verification, get_auth_user, quiz_signup_pendente,
)
from frontend.routes.shared import DASHBOARD_URL
from utils_phone import normalize_phone_e164

router = APIRouter()

TETO_GLOBAL_WEBHOOK = (300, 3600)  # códigos/hora vindos do XQuiz, qualquer e-mail
OK = {"ok": True}


class _SemVeneno(BaseModel):
    """A borda do `_CorpoSemVeneno` do monólito, que não dá para importar aqui:
    o monólito importa este módulo antes de definir a classe."""

    @model_validator(mode="after")
    def _sem_veneno(self):
        return recusa_veneno(self)


class QuizLeadBody(_SemVeneno):
    # O formato real do XQuiz ainda não foi capturado. Até lá: JSON com os campos
    # no nível de cima, extras ignorados. Ajuste aqui e em `_tokens`, e só aqui.
    email: str
    nome: str | None = None
    whatsapp: str | None = None
    token: str | None = None


class QuizResendBody(_SemVeneno):
    email: str


def _tokens(request: Request, body: QuizLeadBody) -> list[str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth[:7].lower() == "bearer ":
        auth = auth[7:].strip()
    return [auth, request.headers.get("Token") or "", request.headers.get("X-Token") or "", body.token or ""]


def _nome(nome: str | None) -> str | None:
    nome = (nome or "").strip()
    return nome if 2 <= len(nome) <= 50 else None


def _telefone(whatsapp: str | None) -> str | None:
    try:
        return normalize_phone_e164(whatsapp) if whatsapp else None
    except ValueError:
        return None


async def _registra_falha(onde: str, exc: Exception | None = None) -> None:
    motivo = type(exc).__name__ if exc else "envio_falhou"
    await log_system_event("error", "quiz_signup_failed", f"{onde}: {motivo}", source="quiz")


async def _avisa_dono(exc: AccountAlreadyExistsError) -> None:
    """O mesmo aviso out-of-band do /auth/register: quem já tem conta recebe o
    e-mail, e a resposta não muda (anti-enumeração)."""
    try:
        owner = await asyncio.to_thread(get_auth_user, exc.existing_user_id) if exc.existing_user_id else None
        owner_email = (owner or {}).get("email")
        if owner_email:
            await asyncio.to_thread(send_account_exists_notice, owner_email, f"{DASHBOARD_URL}/login",
                                    f"{DASHBOARD_URL}/recuperar-senha")
    except Exception as notice_exc:
        await _registra_falha("aviso", notice_exc)


async def _envia_codigo(onde: str, email: str, code: str) -> None:
    try:
        if not await asyncio.to_thread(send_verification_email, email, code):
            await _registra_falha(onde)
    except Exception as exc:
        await _registra_falha(onde, exc)


@router.post("/xquiz/webhook")
async def xquiz_webhook(request: Request, body: QuizLeadBody):
    esperado = (os.getenv("XQUIZ_WEBHOOK_TOKEN") or "").strip()
    if not esperado:
        raise HTTPException(status_code=503, detail="Serviço não configurado.")
    if not any(t and constant_time_eq(t, esperado) for t in _tokens(request, body)):
        raise HTTPException(status_code=401, detail="Token inválido.")

    # Import tardio: o monólito importa este módulo antes de definir os helpers.
    from frontend.finance_bot_websocket_custom import (
        EMAIL_RATE_LIMITS, _check_persistent_rate_limit, _normalize_rate_limit_email,
    )
    email = _normalize_rate_limit_email(body.email)
    # Sem balde por IP: todo pedido vem do IP do XQuiz. O de e-mail é o do register.
    await _check_persistent_rate_limit("register", f"email:{email}", *EMAIL_RATE_LIMITS["register"])
    await _check_persistent_rate_limit("quiz-webhook", "global", *TETO_GLOBAL_WEBHOOK)

    try:
        code = await asyncio.to_thread(
            create_email_verification, email, None, _telefone(body.whatsapp),
            display_name=_nome(body.nome),
        )
    except AccountAlreadyExistsError as exc:
        await _avisa_dono(exc)
        return OK
    except Exception as exc:
        await _registra_falha("webhook", exc)
        return JSONResponse({"ok": False}, status_code=503)
    if not await asyncio.to_thread(send_verification_email, email, code):
        await _registra_falha("webhook")
        return JSONResponse({"ok": False}, status_code=503)
    return OK


@router.post("/auth/quiz/resend")
async def quiz_resend(request: Request, body: QuizResendBody, background_tasks: BackgroundTasks):
    """Reenvia o código de quem veio do quiz nas últimas 24h: o vivo, ou um novo
    com o mesmo nome e telefone. Resposta igual em todo caso (anti-enumeração) —
    e no mesmo tempo: o envio roda depois da resposta, senão a espera pelo SMTP
    revelaria quem fez o quiz."""
    from frontend.finance_bot_websocket_custom import _check_auth_rate_limits
    await _check_auth_rate_limits("register", request, body.email)
    email = body.email.strip().lower()
    try:
        pendente = await asyncio.to_thread(quiz_signup_pendente, email)
        if pendente:
            phone, nome = pendente
            code = await asyncio.to_thread(create_email_verification, email, None, phone, display_name=nome)
            background_tasks.add_task(_envia_codigo, "resend", email, code)
    except AccountAlreadyExistsError:
        pass
    except Exception as exc:
        await _registra_falha("resend", exc)
    return OK
