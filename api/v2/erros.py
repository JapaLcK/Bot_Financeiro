"""Envelope único de erro da /api/v2: `{"error": {"code", "message", "details"?}}`.

Os tratadores são do SUB-APP: os do monólito não alcançam o que o sub-app
montado trata. Ficam de fora (nascem nos middlewares do pai, antes do sub-app)
o 403 do CSRF e o 422 do `query_venenosa_middleware`, que saem `{"detail": ...}`.
"""
import sys
import traceback
from http.client import responses

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect

from core.admin_dashboard import log_system_event, status_do_erro
from core.pg_text import limpa_para_pg

_CODIGOS = {
    401: "unauthenticated",
    402: "payment_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    429: "rate_limited",
    503: "service_unavailable",
}


# O envelope como contrato (o `default` do OpenAPI em `app.py`). A resposta real
# continua saindo de `_envelope`; `tests/test_api_v2_contrato.py` valida respostas
# reais contra estes modelos.
class DetalheErro(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class CorpoErro(BaseModel):
    code: str
    message: str
    details: list[DetalheErro] | None = None


class ErroV2(BaseModel):
    error: CorpoErro


def _envelope(status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    erro = {"code": code, "message": message}
    if details is not None:
        erro["details"] = details
    return JSONResponse(status_code=status, content={"error": erro}, headers=headers)


async def http_erro(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # `detail={"error": "<código>"}` escolhe o código: é o formato do 402 do
    # `_enforce_subscription_gate` e o do 404 `dashboard_v2_disabled`.
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("error"), str):
        code = detail["error"]
    else:
        code = _CODIGOS.get(exc.status_code, "http_error")
    message = detail if isinstance(detail, str) else responses.get(exc.status_code, "Erro")
    # `exc.headers` carrega o WWW-Authenticate do 401 (sem ele o auth-refresh.js
    # não renova a sessão), o Allow do 405 e o Retry-After do 429.
    return _envelope(exc.status_code, code, message, headers=getattr(exc, "headers", None))


async def validacao_erro(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Só `loc`/`msg`/`type`. O `input` ecoa o corpo inteiro (senha em claro,
    # medido no monólito) e o `ctx` é do validador; a `msg` passa pelo
    # `limpa_para_pg` para surrogate não virar 500 na serialização.
    details = limpa_para_pg([
        {"loc": list(e.get("loc") or ()), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ])
    return _envelope(422, "validation_error", "Dados inválidos.", details)


def sem_reraise(app):
    """Por fora do `ServerErrorMiddleware` do sub-app (ver `app.py`): ele re-levanta
    toda exceção depois de chamar `erro_interno` ("We always continue to raise"), e
    ela sairia do app ASGI inteiro — traceback de novo no uvicorn, e o `TestClient`
    a levantaria no teste. Depois de a resposta começar, `erro_interno` já registrou:
    engole. Antes, deixa subir: é o `ClientDisconnect`, que o pai responde 499.
    ASGI puro e não `BaseHTTPMiddleware`, que atrapalharia o SSE."""
    async def _app(scope, receive, send):
        comecou = False

        async def _send(msg):
            nonlocal comecou
            comecou = comecou or msg["type"] == "http.response.start"
            await send(msg)

        try:
            await app(scope, receive, _send)
        except Exception:
            if not comecou:
                raise
    return _app


async def erro_interno(request: Request, exc: Exception) -> JSONResponse:
    # O SSE que explode no meio chega embrulhado no `ExceptionGroup` do task group
    # do FastAPI: a classificação e o log são da exceção de dentro.
    if isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    # Cliente sumiu: levantar de novo ANTES de responder faz a exceção sair do sub-app
    # e cair no `except ClientDisconnect` do `admin_error_logging_middleware` do
    # pai (499, sem evento) — a regra fica só lá.
    if isinstance(exc, ClientDisconnect):
        raise exc
    # O resto o pai não enxerga (o sub-app já respondeu): sem o
    # `log_system_event` aqui, o erro some do painel de admin.
    status, message = status_do_erro(exc)
    tb = "".join(traceback.format_exception(exc))
    err = str(exc) or exc.__class__.__name__
    print(f"[unhandled] {request.method} {request.url.path}: {exc.__class__.__name__}: {err}\n{tb}",
          file=sys.stderr, flush=True)
    await log_system_event(
        "error", "http_unhandled_exception", err,
        source=f"{request.method} {request.url.path}",
        details={"query_keys": sorted(request.query_params.keys()), "status_code": status,
                 "exc_type": exc.__class__.__name__, "traceback": tb[-2000:]},
    )
    return _envelope(status, _CODIGOS.get(status, "internal_error"), message)
