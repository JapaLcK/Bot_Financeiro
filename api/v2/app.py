"""Sub-app da /api/v2, montado pelo monólito em `/api/v2`.

Sub-app e não `include_router` para os tratadores de erro serem os DELE (o
envelope de `erros.py`) sem tocar nos globais do monólito. Rota nova vai num
router por assunto neste pacote, incluído aqui.
"""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.v2 import assinaturas, contas, erros, eventos, me, perfil, resumo_mes


class _AppV2(FastAPI):
    # O `sem_reraise` por fora da pilha inteira (o `ServerErrorMiddleware` é a camada
    # mais externa dela). Pelo atributo do módulo: o controle negativo o troca.
    def build_middleware_stack(self):
        return erros.sem_reraise(super().build_middleware_stack())


# Documentação desligada como no app principal. `servers` deixa o `openapi()`
# (que o contrato do PR seguinte gera) com o prefixo do mount, e não relativo.
# `responses.default` põe o envelope de erro no contrato (os tipos TS saem daí).
app = _AppV2(openapi_url=None, docs_url=None, redoc_url=None,
             servers=[{"url": "/api/v2"}],
             responses={"default": {"model": erros.ErroV2, "description": "Erro no envelope"}})

# StarletteHTTPException e não a do FastAPI: o 404/405 do router é a do Starlette.
app.add_exception_handler(StarletteHTTPException, erros.http_erro)
app.add_exception_handler(RequestValidationError, erros.validacao_erro)
app.add_exception_handler(Exception, erros.erro_interno)

app.include_router(me.router)
app.include_router(eventos.router)
app.include_router(perfil.router)
app.include_router(contas.router)
app.include_router(assinaturas.router)
app.include_router(resumo_mes.router)
