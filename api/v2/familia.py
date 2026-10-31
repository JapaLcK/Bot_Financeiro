"""Plano Família: grupo e convites (E1-E9). O usuário vem sempre da sessão; nenhuma rota recebe user_id/group_id.

O titular vê de cada membro só `ref`, primeiro nome e status. `ref` é family_members.id resolvido SEMPRE com o
filtro do titular (db/family_views.member_user_id): ref de outro grupo é igual a ref inexistente (404).
Sem schema OpenAPI por ora: o gerador de tipos só entende GET/PUT/POST 200 com corpo, e o consumidor é o PR do app.
"""
import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from api.v2.sessao import usuario_atual, usuario_logado
from core.dashboard_links import base_url_publica
from core.services.family_access import _cobertura
from db import family as fam
from db import family_views as views
from frontend.routes.shared import JWT_SECRET

router = APIRouter(include_in_schema=False)

_HTTP = {"invite_invalid": 404, "not_member": 404, "no_group": 404, "user_not_found": 404,
         "already_in_family": 409, "confirmation_mismatch": 409, "coverage_required": 402, "full": 409, "owner_cannot_leave": 409}


class Membro(BaseModel):
    ref: str
    nome: str
    status: Literal["ativo", "suspenso"]


class VisaoTitular(BaseModel):
    papel: Literal["titular"] = "titular"
    capacidade: int
    membros: list[Membro]
    convites_ativos: int


class VisaoMembro(BaseModel):
    papel: Literal["membro"] = "membro"


class Convite(BaseModel):
    token: str
    link: str
    expira_em: datetime


class Dissolucao(BaseModel):
    membros: int
    convites: int
    consentimentos: int
    confirmacao: str


class TokenIn(BaseModel):
    token: str = Field(max_length=256)


class ConfirmacaoIn(BaseModel):
    confirmacao: str = Field(max_length=256)


class Vazio(BaseModel):
    pass


def _nao_achado() -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found"})


def _erro(exc: fam.FamilyError) -> HTTPException:
    """FamilyError -> envelope. O 404 é sempre o mesmo código (não diz se o grupo/ref existe)."""
    codigo = exc.args[0]
    status = _HTTP.get(codigo)
    if status is None:
        return exc  # código que a rota não conhece é bug: 500, não 409 inventado
    visivel = {402: "family_coverage_required"}.get(status) or (
        codigo if status == 409 or codigo == "invite_invalid" else "not_found")
    return HTTPException(status_code=status, detail={"error": visivel})


def _titular(uid: int) -> None:
    v = views.my_family(uid)
    if not v or v["role"] != "owner":
        raise _nao_achado()


def _sem_corpo() -> Response:
    # ponytail: 204 devolvido à mão com response_model=Vazio só para a varredura de rotas; sai quando o gerador de tipos aceitar 204.
    return Response(status_code=204)


async def _limite(bucket: str, uid: int, maximo: int, janela: int) -> None:
    from frontend.finance_bot_websocket_custom import _check_persistent_rate_limit  # lazy: ele importa a v2
    await _check_persistent_rate_limit(bucket, f"user:{uid}", maximo, janela)


def _nonce(uid: int, p: dict) -> str:
    """Sem estado: muda se o grupo ou qualquer membro/contagem mudar entre ver e confirmar (trocar um membro por outro também)."""
    ids = ",".join(map(str, p["ids_membros"]))
    msg = f"familia-dissolucao:{uid}:{p['group_id']}:{ids}:{p['convites']}:{p['consentimentos']}"
    return hmac.new(JWT_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _visao_titular(uid: int) -> VisaoTitular:
    return VisaoTitular(**views.owner_view(uid))


@router.get("/familia", response_model=VisaoTitular | VisaoMembro)
def ver(uid: int = Depends(usuario_logado)):
    v = views.my_family(uid)
    if not v:
        raise _nao_achado()
    return _visao_titular(uid) if v["role"] == "owner" else VisaoMembro()


@router.post("/familia", status_code=201, response_model=VisaoTitular)
def criar(uid: int = Depends(usuario_atual)) -> VisaoTitular:
    if not _cobertura(uid):
        raise HTTPException(status_code=402, detail={"error": "family_coverage_required"})
    try:
        fam.create_group(uid)
    except fam.FamilyError as exc:
        raise _erro(exc) from None
    return _visao_titular(uid)


@router.post("/familia/convites", status_code=201, response_model=Convite)
async def convidar(uid: int = Depends(usuario_atual)) -> Convite:
    await asyncio.to_thread(_titular, uid)
    if not await asyncio.to_thread(_cobertura, uid):  # titular sem cobertura ou grupo suspenso não emite convite
        raise HTTPException(status_code=402, detail={"error": "family_coverage_required"})
    v = await asyncio.to_thread(views.owner_view, uid)
    if len(v["membros"]) + 1 >= v["capacidade"]:  # +1 = o titular; checagem de cortesia, o aceite é quem trava
        raise HTTPException(status_code=409, detail={"error": "full"})
    await _limite("family-invite-create", uid, 20, 86400)
    try:
        token = await asyncio.to_thread(fam.create_invite, uid)
    except fam.FamilyError as exc:
        raise _erro(exc) from None
    # ponytail: expira_em calculado aqui (o banco grava now()+INVITE_DAYS); difere em ms. Ler do banco se alguém comparar.
    return Convite(token=token, link=f"{base_url_publica()}/familia/entrar#t={token}",
                   expira_em=datetime.now(timezone.utc) + timedelta(days=fam.INVITE_DAYS))


@router.delete("/familia/convites", response_model=Vazio)
def revogar(corpo: TokenIn, uid: int = Depends(usuario_atual)):
    _titular(uid)
    if not fam.revoke_invite(uid, corpo.token):
        raise _nao_achado()
    return _sem_corpo()


@router.post("/familia/convites/aceitar", response_model=VisaoMembro)
async def aceitar(corpo: TokenIn, uid: int = Depends(usuario_logado)) -> VisaoMembro:
    await _limite("family-invite-accept", uid, 10, 3600)
    try:
        await asyncio.to_thread(fam.accept_invite, corpo.token, uid)
    except fam.FamilyError as exc:
        raise _erro(exc) from None
    return VisaoMembro()


@router.delete("/familia/membros/{ref}", response_model=Vazio)
def remover(ref: str, uid: int = Depends(usuario_atual)):
    _titular(uid)
    membro = views.member_user_id(uid, ref)
    if membro is None:
        raise _nao_achado()
    try:
        fam.remove_member(uid, membro)
    except fam.FamilyError as exc:
        raise _erro(exc) from None
    return _sem_corpo()


@router.post("/familia/sair", response_model=Vazio)
def sair(uid: int = Depends(usuario_logado)):
    v = views.my_family(uid)
    if not v:
        raise _nao_achado()
    if v["role"] == "owner":
        raise HTTPException(status_code=409, detail={"error": "owner_cannot_leave"})
    try:
        fam.leave_family(uid)
    except fam.FamilyError as exc:
        raise _erro(exc) from None
    return _sem_corpo()


@router.get("/familia/dissolucao", response_model=Dissolucao)
def previa_dissolucao(uid: int = Depends(usuario_atual)) -> Dissolucao:
    _titular(uid)
    p = views.dissolve_preview(uid)
    if not p:
        raise _nao_achado()
    return Dissolucao(membros=p["membros"], convites=p["convites"], consentimentos=p["consentimentos"],
                      confirmacao=_nonce(uid, p))


@router.post("/familia/dissolucao", response_model=Vazio)
def dissolver(corpo: ConfirmacaoIn, uid: int = Depends(usuario_atual)):
    _titular(uid)

    def confere(cur) -> bool:  # de novo sob o lock do grupo: um aceite entre a leitura e a dissolução muda o nonce
        q = views.dissolve_preview_cur(cur, uid)
        return bool(q) and hmac.compare_digest(corpo.confirmacao.encode(), _nonce(uid, q).encode())

    try:
        if not fam.dissolve_group(uid, confere):
            raise _nao_achado()
    except fam.FamilyError as exc:
        raise _erro(exc) from None
    return _sem_corpo()
