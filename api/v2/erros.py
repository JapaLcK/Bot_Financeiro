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


async def erro_interno(request: Request, exc: Exception) -> JSONResponse:
    # Cliente sumiu: levantar de novo ANTES de responder faz a exceção sair do sub-app
    # e cair no `except ClientDisconnect` do `admin_error_logging_middleware` do
    # pai (499, sem evento) — a regra fica só lá.
    if isinstance(exc, ClientDisconnect):
        raise exc
    # O resto o pai não enxerga (o sub-app já respondeu): sem o
    # `log_system_event` aqui, o erro some do painel de admin.
    # Limite conhecido: depois desta resposta o `ServerErrorMiddleware` do sub-app
    # re-levanta a exceção ("We always continue to raise"), e ela sai do app ASGI
    # inteiro: o uvicorn deve logar o traceback de novo (não visto no Railway) e o
    # `TestClient` a levanta no teste. Engolir pede `BaseHTTPMiddleware`, que
    # atrapalha o SSE: fica para o PR 4.
    status, message = status_do_erro(exc)
    tb = "".join(traceback.format_exception(exc))
    err = str(exc) or exc.__class__.__name__
    print(f"[unhandled] {request.method} {request.url.path}: {exc.__class__.__name__}: {err}\n{tb}",
          file=sys.stderr, flush=True)
    await log_system_event(
        "error", "http_unhandled_exception", err,
        source=f"{request.method} {request.url.path}",
        details={"query": dict(request.query_params), "status_code": status,
                 "exc_type": exc.__class__.__name__, "traceback": tb[-2000:]},
    )
    return _envelope(status, _CODIGOS.get(status, "internal_error"), message)
