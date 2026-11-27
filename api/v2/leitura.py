"""Credencial de visualização da conta (condomínio): o DONO define usuário e senha de leitura.

`GET|PUT|DELETE /conta/leitura` (PUT/DELETE levam o código por e-mail pedido em `POST /conta/leitura/codigo`). O usuário vem da sessão; a senha e o hash nunca saem. Quem entra com
essa credencial tem sessão SOMENTE LEITURA (`POST /auth/leitura/login`, `db/leitura.py`); o leitor não
alcança estas rotas (guard + `usuario_sessao`).
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictStr

from api.v2.sessao import usuario_sessao
from db import (
    LeituraInvalida, UsernameEmUso, definir_credencial_leitura, desativar_credencial_leitura,
    obter_credencial_leitura, reauth_codes,
)
from db.connection import get_conn
from db.leitura import normalizar_usuario, validar_senha
from frontend.routes import shared

router = APIRouter(include_in_schema=False)


class Credencial(BaseModel):
    ativo: bool
    usuario: str | None = None


class CredencialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    usuario: StrictStr
    senha: StrictStr
    codigo: StrictStr


class CodigoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codigo: StrictStr


async def exigir_codigo(uid: int, codigo: str) -> None:
    """Consome o código por e-mail (400, não 401: a sessão está ótima, o interceptor não deve renovar nem deslogar) da finalidade LEITURA (uso único, expira, 5 tentativas).
    A tentativa gasta persiste mesmo na recusa (commit antes do raise)."""
    def _consome() -> str:
        with get_conn() as conn, conn.cursor() as cur:
            r = reauth_codes.confere_e_consome(cur, uid, reauth_codes.LEITURA, codigo)
            conn.commit()
        return r
    r = await asyncio.to_thread(_consome)
    if r != "ok":
        raise HTTPException(status_code=400, detail={"error": "codigo_invalido" if r == "invalido" else "codigo_expirado"})


@router.post("/conta/leitura/codigo")
@shared.limiter.limit("5/hour")  # por IP, como o da exclusão; o teto por conta (3/h) soma as duas finalidades
async def pedir_codigo(request: Request, uid: int = Depends(usuario_sessao)) -> dict:
    """Manda ao e-mail do dono o código que autoriza o PUT/DELETE (só o dono: o leitor é barrado pelo guard)."""
    from frontend.routes.conta_reauth import enviar_codigo
    return await enviar_codigo(uid, reauth_codes.LEITURA)


@router.get("/conta/leitura", response_model=Credencial)
async def ver(uid: int = Depends(usuario_sessao)) -> Credencial:
    return Credencial(**await asyncio.to_thread(obter_credencial_leitura, uid))


@router.put("/conta/leitura", response_model=Credencial)
async def definir(corpo: CredencialIn, uid: int = Depends(usuario_sessao)) -> Credencial:
    try:  # usuário/senha inválidos recusam ANTES de gastar o código (só o 409 de corrida o gasta)
        normalizar_usuario(corpo.usuario)
        validar_senha(corpo.senha)
    except LeituraInvalida as exc:
        raise HTTPException(status_code=422, detail={"error": "leitura_invalida", "message": str(exc)}) from None
    await exigir_codigo(uid, corpo.codigo)
    try:
        await asyncio.to_thread(definir_credencial_leitura, uid, corpo.usuario, corpo.senha)
    except LeituraInvalida as exc:
        raise HTTPException(status_code=422, detail={"error": "leitura_invalida", "message": str(exc)}) from None
    except UsernameEmUso:
        raise HTTPException(status_code=409, detail={"error": "usuario_em_uso"}) from None
    return Credencial(**await asyncio.to_thread(obter_credencial_leitura, uid))


@router.delete("/conta/leitura", response_model=Credencial)
async def desativar(corpo: CodigoIn, uid: int = Depends(usuario_sessao)) -> Credencial:
    await exigir_codigo(uid, corpo.codigo)
    await asyncio.to_thread(desativar_credencial_leitura, uid)
    return Credencial(**await asyncio.to_thread(obter_credencial_leitura, uid))
