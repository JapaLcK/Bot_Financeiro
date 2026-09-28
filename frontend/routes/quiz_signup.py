"""Cadastro pelo quiz de venda (XQuiz): a conta nasce SEM senha.

Serviço:   POST /xquiz/webhook     o XQuiz manda e-mail/nome/WhatsApp do formulário;
           aqui se grava a verificação sem senha e se envia o código de 6 dígitos.
           Token = env XQUIZ_WEBHOOK_TOKEN (constant_time_eq): sem env 503, errado 401.
           Falha de banco ou de envio: 503, para o XQuiz reenviar (o código vivo é reaproveitado).
Navegador: POST /auth/quiz/resend  a /q reenvia o código (com CSRF).
           POST /auth/quiz/conta   a /assinar cria a conta sem senha e sem código e
           entrega a sessão (`criada`); e-mail que já tem conta ou está no meio do
           register não muda nada e não ganha sessão (`tem_conta`, `logado`,
           `cadastro_pendente`; 409 `ocupado` com outro pedido do mesmo e-mail em voo).

A conta é criada pelo /auth/verify-email de sempre, que a /q chama com e-mail e
código (frontend/quiz-resultado.js). Nome, telefone, e-mail e código são PII:
fora de log e de print — exceção só com `type(exc).__name__`.
"""
import asyncio
import os
import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator
from slowapi.util import get_remote_address

from core.admin_dashboard import log_system_event
from core.pg_text import recusa_veneno
from core.secure_compare import constant_time_eq
from core.services.email_service import send_account_exists_notice, send_verification_email
from db.reports import (
    AccountAlreadyExistsError, create_email_verification, get_auth_user, quiz_signup_pendente,
)
from db.signup_quiz import criar_conta_sem_codigo, desfazer_conta_sem_codigo
from frontend.routes.shared import DASHBOARD_URL, signup_source_from_request
from utils_phone import normalize_phone_e164

router = APIRouter()

TETO_GLOBAL_WEBHOOK = (300, 3600)  # códigos/hora vindos do XQuiz, qualquer e-mail
LIMITE_IP_QUIZ = (10, 3600)  # contas/hora por IP na /assinar (dono, 2026-09-27)
OK = {"ok": True}
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")  # a mesma do cadastro.html


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


class QuizContaBody(_SemVeneno):
    email: str
    nome: str | None = None
    whatsapp: str | None = None
    aceitou_termos: bool = False


def _tokens(request: Request, body: QuizLeadBody) -> list[str]:
    auth = (request.headers.get("Authorization") or "").strip()
    if auth[:7].lower() == "bearer ":
        auth = auth[7:].strip()
    return [auth, request.headers.get("Token") or "", request.headers.get("X-Token") or "", body.token or ""]


def _nome(nome: str | None) -> str | None:
    nome = (nome or "").strip()
    # "{{" é o placeholder do XQuiz quando o campo vem vazio.
    return nome if 2 <= len(nome) <= 50 and "{{" not in nome else None


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


@router.post("/auth/quiz/conta")
async def quiz_conta(request: Request, response: Response, body: QuizContaBody,
                     background_tasks: BackgroundTasks):
    """A conta nasce na /assinar: sem senha, sem código, e já logada.

    Só `criada` ganha sessão. E-mail que já tem conta → `tem_conta` (ou `logado`,
    se a sessão do pedido já é dessa conta); e-mail no meio do /auth/register →
    `cadastro_pendente`. Nos dois, nem a conta nem o código pendente são tocados
    (`db/signup_quiz.criar_conta_sem_codigo`).
    Diz se o e-mail é cliente, e só aqui no site: aceito pelo dono, com os limites.
    """
    from frontend.finance_bot_websocket_custom import (
        EMAIL_RATE_LIMITS, _check_persistent_rate_limit, _get_current_user, _sessao_de_conta_nova,
    )
    email = body.email.strip().lower()
    if not _EMAIL.fullmatch(email):
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    if not body.aceitou_termos:
        raise HTTPException(status_code=400, detail="É necessário aceitar a Política de Privacidade.")
    try:
        telefone = normalize_phone_e164(body.whatsapp)
    except ValueError:
        raise HTTPException(status_code=400, detail="Informe um número de WhatsApp válido com DDD.")
    # Sem teto global: numa rota pública ele deixaria qualquer um derrubar o funil.
    await _check_persistent_rate_limit("quiz", f"ip:{get_remote_address(request)}", *LIMITE_IP_QUIZ)
    # Balde próprio: no "register" o anônimo gastava o teto do /auth/register da vítima.
    await _check_persistent_rate_limit("quiz-conta", f"email:{email}", *EMAIL_RATE_LIMITS["register"])

    try:
        result = await asyncio.to_thread(criar_conta_sem_codigo, email, telefone, _nome(body.nome),
                                         signup_source_from_request(request))
    except Exception as exc:
        await _registra_falha("conta", exc)
        raise HTTPException(status_code=503, detail="Não deu para criar a conta. Tente de novo.")
    if result["estado"] == "tem_conta":
        try:
            sessao = await _get_current_user(request, None)
        except HTTPException:
            sessao = None
        return {"estado": "logado" if sessao == result["user_id"] else "tem_conta"}
    if result["estado"] == "ocupado":  # outro pedido do mesmo e-mail: tente de novo
        return JSONResponse({"estado": "ocupado"}, status_code=409)
    if result["estado"] != "criada":
        return {"estado": result["estado"]}

    user_id = int(result["user_id"])
    try:
        credenciais = await _sessao_de_conta_nova(
            request, response, background_tasks, user_id=user_id, email=email,
            origem_url=f"{DASHBOARD_URL}/assinar",
        )
    except Exception as exc:
        # Conta commitada sem sessão: desfaz, e o retry recria e sai logado. O 503 não
        # leva os Set-Cookie que já estejam no `response` (test_sessao_falha_*).
        await _registra_falha("sessao", exc)
        try:
            await asyncio.to_thread(desfazer_conta_sem_codigo, user_id, result["conta_id"])
        except Exception as exc2:
            await _registra_falha("desfazer", exc2)
        raise HTTPException(status_code=503, detail="Não deu para criar a conta. Tente de novo.")
    return {"estado": "criada", "user_id": user_id, **credenciais}
