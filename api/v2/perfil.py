"""`GET`/`PUT /api/v2/perfil`: o perfil do Resumo, no servidor (`auth_accounts.dashboard_profile`).

`null` = nunca escolheu. O PUT é escrita: o CSRF do monólito (cookie `csrf_token` +
header `x-csrf-token`) vale antes de chegar aqui.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from db.signup_quiz import PERFIL_PADRAO, PERFIS, gravar_perfil, ler_perfil

router = APIRouter()

PerfilV2 = Literal[PERFIS + (PERFIL_PADRAO,)]


class Perfil(BaseModel):
    perfil: PerfilV2 | None


class NovoPerfil(BaseModel):
    perfil: PerfilV2


@router.get("/perfil", response_model=Perfil)
def ler(uid: int = Depends(usuario_atual)) -> Perfil:
    return Perfil(perfil=ler_perfil(uid))


@router.put("/perfil", response_model=Perfil)
def gravar(corpo: NovoPerfil, uid: int = Depends(usuario_atual)) -> Perfil:
    if not gravar_perfil(uid, corpo.perfil):
        raise HTTPException(status_code=404, detail={"error": "conta_nao_encontrada"})
    return Perfil(perfil=corpo.perfil)
