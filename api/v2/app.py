"""Sub-app da /api/v2, montado pelo monólito em `/api/v2`.

Sub-app e não `include_router` para os tratadores de erro serem os DELE (o
envelope de `erros.py`) sem tocar nos globais do monólito. Rota nova vai num
router por assunto neste pacote, incluído aqui.
"""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.v2 import erros, me

# Documentação desligada como no app principal. `servers` deixa o `openapi()`
# (que o contrato do PR seguinte gera) com o prefixo do mount, e não relativo.
app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None,
              servers=[{"url": "/api/v2"}])

# StarletteHTTPException e não a do FastAPI: o 404/405 do router é a do Starlette.
app.add_exception_handler(StarletteHTTPException, erros.http_erro)
app.add_exception_handler(RequestValidationError, erros.validacao_erro)
app.add_exception_handler(Exception, erros.erro_interno)

app.include_router(me.router)
