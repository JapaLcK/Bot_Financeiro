"""`GET`/`POST /api/v2/guia`: o guia do /painel (#728), estado em `db/guia.py`.

`PASSOS` é a fonte única do roteiro (falas, âncoras, ações, `dado`): o cliente desenha
o selo "exemplo" a partir de `dado`. Passo 1 depende do Saiu real (`motivo_resumo`); 2 e
3 são sobre blocos de exemplo e estão sempre disponíveis. Estado, na ordem:
`concluido` (todos os passos feitos) > `dispensado` > `em_andamento` (já oferecido, pelo
convite ou pela Ajuda, ou já fez algum: o cliente não mostra o convite sozinho, só retoma
pela Ajuda) > `oferecer` (nada feito, nunca oferecido, passo 1 disponível) > `indisponivel`. `motivo` do topo = o do passo 1 quando ele está indisponível, só nos dois
que podem tê-lo (`em_andamento` e `indisponivel`).

O POST é escrita: o CSRF do monólito vale antes. `feito` sem `passo` = 422 no
envelope; `passo` fora do roteiro = 422 pelo Literal; `feito` de passo indisponível = 409
`passo_indisponivel`, sem gravar. Nas outras ações `passo` é ignorado.

`DICAS`: a dica de primeiro uso de cada tela (o cliente a mostra uma vez; `vista` = já
apareceu). Só vem a dica da tela que o plano dá (`_RECURSO`). `POST /guia/dica` carimba a
1ª vez sem tocar o guia; id fora de `DICAS` = 422 pelo Literal. Dica que o plano não dá
grava e é inofensiva: o GET não a devolve.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.plan_service import plan_gate_ok
from db import guia

router = APIRouter()

PASSOS = (
    {"id": "resumo.saiu", "tela": "resumo", "ancora": "resumo.saiu", "acao": "mes.trocado",
     "dado": "real", "avanca": "cliente",
     "fala": {"titulo": "O que saiu este mês",
              "apresenta": "Esse é o que saiu da sua conta este mês, direto do seu banco.",
              "texto": "Toca aqui pra comparar com o mês passado."}},
    {"id": "gastos.categoria", "tela": "gastos", "ancora": "categorias.lista", "acao": "categoria.aberta",
     "dado": "exemplo", "avanca": "cliente",
     "fala": {"titulo": "Pra onde foi o dinheiro",
              "apresenta": "Aqui ficam suas categorias: pra onde foi cada real do mês. "
                           "Estes números ainda são de exemplo.",
              "texto": "Toca numa categoria pra ver de onde vem o total."}},
    {"id": "piggy.pergunta", "tela": "piggy", "ancora": "piggy.pergunta", "acao": "piggy.perguntou",
     "dado": "exemplo", "avanca": "cliente",
     "fala": {"titulo": "Pergunta pro Piggy",
              "apresenta": "Aqui você conversa comigo. As respostas ainda são de exemplo; "
                           "a conversa de verdade chega em breve.",
              "texto": "Toca numa pergunta pronta e me vê responder."}},
)
IDS = [p["id"] for p in PASSOS]

DICAS = (
    {"id": "assinaturas.marcas", "tela": "assinaturas", "titulo": "Como eu acho suas assinaturas",
     "texto": "Eu acho essas cobranças no extrato que chega do seu banco pelo Open Finance. "
              "“Ignorar” e “É assinatura” só me ensinam o que é o quê: nada é cancelado aqui."},
)
DICA_IDS = [d["id"] for d in DICAS]
_RECURSO = {"assinaturas": "subscriptions"}  # a tela → o recurso do plano (api/v2/assinaturas.py)

PassoId = Literal[tuple(IDS)]
DicaId = Literal[tuple(DICA_IDS)]
Motivo = Literal["sem_dados", "sincronizando", "conexao_com_erro"]


class Fala(BaseModel):
    """`apresenta`: o bloco, antes do "Entendi"; `texto`: o que tocar, depois dele."""
    titulo: str
    apresenta: str
    texto: str


class Passo(BaseModel):
    id: PassoId
    tela: Literal["resumo", "gastos", "piggy"]
    ancora: str
    acao: str
    dado: Literal["real", "exemplo"]
    fala: Fala
    avanca: Literal["cliente"]
    disponivel: bool
    motivo: Motivo | None
    feito: bool


class Dica(BaseModel):
    id: DicaId
    tela: Literal["assinaturas"]
    titulo: str
    texto: str
    vista: bool


class Guia(BaseModel):
    estado: Literal["oferecer", "em_andamento", "concluido", "dispensado", "indisponivel"]
    motivo: Motivo | None
    passos: list[Passo]
    dicas: list[Dica]


class AcaoGuia(BaseModel):
    acao: Literal["visto", "feito", "dispensar", "reabrir"]
    passo: PassoId | None = None


class DicaIn(BaseModel):
    dica: DicaId


def _disponivel(passo: str, motivo1: str | None) -> bool:
    return passo != IDS[0] or motivo1 is None


def _guia(linha: dict | None, motivo1: str | None, uid: int) -> Guia:
    feitos = (linha or {}).get("feitos") or {}
    vistas = (linha or {}).get("dicas") or {}
    if linha and linha["concluido_em"]:
        estado = "concluido"
    elif linha and linha["dispensado_em"]:
        estado = "dispensado"
    elif feitos or (linha and linha["oferecido_em"]):
        estado = "em_andamento"
    else:
        estado = "oferecer" if motivo1 is None else "indisponivel"
    passos = [Passo(**p, disponivel=_disponivel(p["id"], motivo1), motivo=None if i else motivo1,
                    feito=p["id"] in feitos) for i, p in enumerate(PASSOS)]
    dicas = [Dica(**d, vista=d["id"] in vistas) for d in DICAS if plan_gate_ok(uid, _RECURSO[d["tela"]])]
    return Guia(estado=estado, motivo=motivo1 if estado in ("em_andamento", "indisponivel") else None,
                passos=passos, dicas=dicas)


@router.get("/guia", response_model=Guia)
def ler(uid: int = Depends(usuario_atual)) -> Guia:
    return _guia(guia.ler(uid), guia.motivo_resumo(uid), uid)


@router.post("/guia", response_model=Guia)
def registrar(corpo: AcaoGuia, uid: int = Depends(usuario_atual)) -> Guia:
    if corpo.acao == "feito" and corpo.passo is None:
        raise RequestValidationError([{"loc": ("body", "passo"), "msg": "Falta o passo.",
                                       "type": "value_error"}])
    motivo1 = guia.motivo_resumo(uid)
    if corpo.acao == "feito" and not _disponivel(corpo.passo, motivo1):
        raise HTTPException(status_code=409, detail={"error": "passo_indisponivel"})
    return _guia(guia.registrar(uid, corpo.acao, corpo.passo, IDS), motivo1, uid)


@router.post("/guia/dica", response_model=Guia)
def dica(corpo: DicaIn, uid: int = Depends(usuario_atual)) -> Guia:
    return _guia(guia.registrar(uid, "dica", corpo.dica, IDS), guia.motivo_resumo(uid), uid)
