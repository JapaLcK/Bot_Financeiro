"""`/api/v2/metas`: as metas do /painel (regra em `db/metas.py`).

`GET /metas` lê; `POST /metas`, `/metas/editar` e `/metas/apagar` escrevem (id no corpo, CSRF do
pai: 403 `{"detail"}` fora do envelope). Dinheiro é `Decimal` e sai como TEXTO; `atual`, `alvo`,
`falta` e `percentual` são `null` quando não dá para saber (≠ zero). O cliente escolhe a fonte
pelo `id` de `/contas` ou pelo texto `banco` de `/investido`; nunca sai `provider_*_id`.
"""
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, Field

from api.v2.investido import _assinante as exige_investimentos
from api.v2.lancamentos import _DATA, _VALOR, _recusa, _valor
from api.v2.sessao import usuario_atual
from core.pg_text import limpa_para_pg
from core.services.plan_service import plan_gate_ok
from db import metas

router = APIRouter()
Tipo = Literal["meta", "reserva"]
_MAX_ID = 2**63 - 1


class FonteSaida(BaseModel):
    tipo: Literal["investido", "banco", "conta"]
    rotulo: str | None
    conta_id: int | None
    banco: str | None


class Meta(BaseModel):
    id: int
    nome: str
    tipo: Tipo
    meses: int | None
    data_alvo: str | None
    atual: Decimal | None
    alvo: Decimal | None
    falta: Decimal | None
    percentual: int | None
    por_mes: Decimal | None
    prazo_passou: bool
    motivos: list[Literal[metas.MOTIVOS]]
    fonte: FonteSaida


class CustoMensal(BaseModel):
    valor: Decimal | None
    recorrencias: int
    motivos: list[Literal[metas.MOTIVOS_CUSTO]]


class Metas(BaseModel):
    metas: list[Meta]
    custo_mensal: CustoMensal


class MetaId(BaseModel):  # o corpo de apagar e a resposta das escritas
    id: int = Field(strict=True)


def _nome(v: str) -> str:
    """Sem nenhum caractere visível (só espaço, zero-width, NUL) ou com caractere de controle
    (`\\n`, `\\t`, categoria Cc) é 422. NUL no meio de um nome bom é saneado (`limpa_para_pg`)."""
    v = v.strip()
    sem_nul = v.replace("\x00", "")
    if not any(c.isprintable() and not c.isspace() for c in sem_nul):
        raise ValueError("Não pode ser vazio.")
    if any(unicodedata.category(c) == "Cc" for c in sem_nul):
        raise ValueError("Sem caracteres de controle.")
    return limpa_para_pg(v)


Nome = Annotated[str, Field(max_length=60), AfterValidator(_nome)]


class Fonte(BaseModel):
    tipo: Literal["investido", "banco", "conta"]
    banco: str | None = Field(None, max_length=200)
    conta: int | None = Field(None, strict=True)


class NovaMeta(BaseModel):
    nome: Nome
    tipo: Tipo
    alvo: str | None = Field(None, pattern=_VALOR)
    meses: int | None = Field(None, strict=True)
    data_alvo: str | None = Field(None, pattern=_DATA)
    fonte: Fonte


class EdicaoMeta(BaseModel):
    # Campo ausente = não muda; `null` explícito é 422 em todos menos `data_alvo` (que limpa).
    # ponytail: o tipo diz não-nulo e o default é `None` — o pydantic não valida o default.
    id: int = Field(strict=True)
    nome: Nome = None
    tipo: Tipo = None
    alvo: str = Field(None, pattern=_VALOR)
    meses: int = Field(None, strict=True)
    data_alvo: str | None = Field(None, pattern=_DATA)
    fonte: Fonte = None


def _nao_achou():  # o MESMO corpo para id de outro usuário e inexistente
    return HTTPException(status_code=404, detail={"error": "meta_nao_encontrada"})


def _id(n: int) -> int:
    if not 0 < n <= _MAX_ID:
        raise _recusa("id", "Id fora da faixa.", "body")
    return n


def _meses(n: int | None) -> int | None:
    if n is not None and not 1 <= n <= 24:
        raise _recusa("meses", "Meses de 1 a 24.", "body")
    return n


def _data_alvo(texto: str | None) -> date | None:  # "não no passado" mora em `db/metas.py`
    if texto is None:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise _recusa("data_alvo", "Data inválida.", "body") from None


def _fonte(uid: int, f: Fonte) -> dict:
    if f.tipo != "conta":
        exige_investimentos(uid)  # o MESMO 403 `pro_required` de `/investido`
    return metas.resolver_fonte(uid, f.tipo, f.banco, f.conta)


@router.get("/metas", response_model=Metas)
def listar(uid: int = Depends(usuario_atual)) -> Metas:
    return Metas(**metas.listar(uid, plan_gate_ok(uid, "investments"), plan_gate_ok(uid, "subscriptions")))


@router.post("/metas", response_model=MetaId)
def criar(corpo: NovaMeta, uid: int = Depends(usuario_atual)) -> MetaId:
    try:
        dados = {"nome": corpo.nome, "tipo": corpo.tipo,
                 "alvo": _valor(corpo.alvo) if corpo.alvo is not None else None,
                 "meses": _meses(corpo.meses), "data_alvo": _data_alvo(corpo.data_alvo),
                 **_fonte(uid, corpo.fonte)}
        return MetaId(id=metas.criar(uid, dados))
    except metas.Invalida as e:
        raise _recusa(e.campo, e.msg, "body") from None
    except metas.ReservaJaExiste:
        raise HTTPException(status_code=409, detail={"error": "reserva_ja_existe"}) from None


@router.post("/metas/editar", response_model=MetaId)
def editar(corpo: EdicaoMeta, uid: int = Depends(usuario_atual)) -> MetaId:
    m = {}
    if corpo.nome is not None:
        m["nome"] = corpo.nome
    if corpo.tipo is not None:
        m["tipo"] = corpo.tipo
    if corpo.alvo is not None:
        m["alvo"] = _valor(corpo.alvo)
    if corpo.meses is not None:
        m["meses"] = _meses(corpo.meses)
    if "data_alvo" in corpo.model_fields_set:
        m["data_alvo"] = _data_alvo(corpo.data_alvo)
    if not m and corpo.fonte is None:  # nenhum campo: pedido sem efeito possível (≠ campo igual ao gravado: 200)
        raise _recusa("body", "Nada para editar.", "body")
    try:
        if corpo.fonte is not None:
            m |= _fonte(uid, corpo.fonte)
        metas.editar(uid, _id(corpo.id), m)
    except metas.Invalida as e:
        raise _recusa(e.campo, e.msg, "body") from None
    except metas.MetaNaoEncontrada:
        raise _nao_achou() from None
    return MetaId(id=corpo.id)


@router.post("/metas/apagar", response_model=MetaId)
def apagar(corpo: MetaId, uid: int = Depends(usuario_atual)) -> MetaId:
    try:
        metas.apagar(uid, _id(corpo.id))
    except metas.MetaNaoEncontrada:
        raise _nao_achou() from None
    return MetaId(id=corpo.id)
