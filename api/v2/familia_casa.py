"""Plano Família, PR 5: a casa, metas e orçamentos do GRUPO (regra e isolamento em db/family_house.py).

Qualquer membro ativo (titular incluso) lê e edita. `ref` é o tipo + id da linha (`m12` meta, `o12` orçamento), sempre
resolvido com o grupo do chamador:
ref de outro grupo, inexistente, malformado, ex-membro e quem não tem família dão o mesmo 404 `not_found`.
Leitura com `usuario_logado` (sem plano vê); escritas com `usuario_atual` (sem plano 402). Escrita devolve a `Casa`
inteira; `PUT` troca o item inteiro e exige a `versao` lida (409 `edit_conflict`); movimento soma sempre.
Só BRL, implícito. Nenhum dado pessoal sai: `autor` é primeiro nome ou null (não é mais membro).
"""
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import AfterValidator, BaseModel, Field

from api.v2.familia import Vazio, _erro, _sem_corpo
from api.v2.lancamentos import _DATA, _VALOR, _recusa, _valor
from api.v2.metas import Nome, _data_alvo, _nome
from api.v2.sessao import usuario_atual, usuario_logado
from db import family as fam
from db import family_house as house
from db.categories import CATEGORY_NAME_MAX_LEN

router = APIRouter(include_in_schema=False)
def _categoria(v: str) -> str:
    """NFC e sem invisíveis de formato (Cf, ex. zero-width): "Café" NFD/NFC e "Ca\u200bfé" são a mesma categoria.
    ZWJ (emoji compostos) e ZWNJ (persa) ficam; sozinhos o `_nome` já recusa como vazio (não imprimíveis)."""
    v = "".join(c for c in unicodedata.normalize("NFC", v) if c in "\u200c\u200d" or unicodedata.category(c) != "Cf")
    return _nome(v)


Categoria = Annotated[str, Field(max_length=CATEGORY_NAME_MAX_LEN), AfterValidator(_categoria)]
Versao = Annotated[int, Field(strict=True, ge=1)]
Valor = Annotated[str, Field(pattern=_VALOR)]


class MetaCasa(BaseModel):
    ref: str
    nome: str
    alvo: Decimal
    guardado: Decimal
    falta: Decimal
    percentual: int
    prazo: date | None
    autor: str | None
    versao: int


class OrcamentoCasa(BaseModel):
    ref: str
    categoria: str
    limite: Decimal
    gasto: Decimal
    restante: Decimal
    autor: str | None
    versao: int


class Casa(BaseModel):
    mes: str
    metas: list[MetaCasa]
    orcamentos: list[OrcamentoCasa]


class MetaIn(BaseModel):
    nome: Nome
    alvo: Valor
    prazo: str | None = Field(None, pattern=_DATA)


class MetaEdicao(MetaIn):
    versao: Versao


class OrcamentoIn(BaseModel):
    categoria: Categoria
    limite: Valor


class OrcamentoEdicao(OrcamentoIn):
    versao: Versao


class Movimento(BaseModel):
    tipo: Literal["somar", "subtrair"]
    valor: Valor


def _reais(cents: int) -> Decimal:
    return Decimal(cents).scaleb(-2).quantize(Decimal("0.01"))


def _cents(texto: str) -> int:
    return int(_valor(texto) * 100)  # exato: o padrão limita a 2 casas


def _delta(m: Movimento) -> int:
    return _cents(m.valor) * (1 if m.tipo == "somar" else -1)


def _monta(c: dict) -> Casa:
    return Casa(
        mes=c["mes"].strftime("%Y-%m"),
        metas=[MetaCasa(ref=f"{house.META}{m['id']}", nome=m["nome"], alvo=_reais(m["alvo_cents"]),
                        guardado=_reais(m["guardado_cents"]), falta=_reais(max(0, m["alvo_cents"] - m["guardado_cents"])),
                        percentual=m["guardado_cents"] * 100 // m["alvo_cents"], prazo=m["prazo"],
                        autor=m["autor"], versao=m["versao"]) for m in c["metas"]],
        orcamentos=[OrcamentoCasa(ref=f"{house.ORC}{o['id']}", categoria=o["categoria"], limite=_reais(o["limite_cents"]),
                                  gasto=_reais(o["gasto_cents"]), restante=_reais(o["limite_cents"] - o["gasto_cents"]),
                                  autor=o["autor"], versao=o["versao"]) for o in c["orcamentos"]])


def _escreve(uid: int, escrita, *args) -> Casa:
    try:
        return _monta(escrita(uid, *args))
    except fam.FamilyError as exc:
        if exc.args[0] == "prazo_passado":
            raise _recusa("prazo", "O prazo não pode estar no passado.", "body") from None
        raise _erro(exc) from None


@router.get("/familia/casa", response_model=Casa)
def ver(resposta: Response, uid: int = Depends(usuario_logado)) -> Casa:
    resposta.headers["Cache-Control"] = "no-store"
    try:
        return _monta(house.casa(uid))
    except fam.FamilyError as exc:
        raise _erro(exc) from None


@router.post("/familia/casa/metas", status_code=201, response_model=Casa)
def criar_meta(corpo: MetaIn, uid: int = Depends(usuario_atual)) -> Casa:
    return _escreve(uid, house.criar_meta, corpo.nome, _cents(corpo.alvo), _data_alvo(corpo.prazo))


@router.put("/familia/casa/metas/{ref}", response_model=Casa)
def editar_meta(ref: str, corpo: MetaEdicao, uid: int = Depends(usuario_atual)) -> Casa:
    return _escreve(uid, house.editar_meta, ref, corpo.nome, _cents(corpo.alvo), _data_alvo(corpo.prazo), corpo.versao)


@router.delete("/familia/casa/metas/{ref}", response_model=Vazio)
def apagar_meta(ref: str, uid: int = Depends(usuario_atual)):
    _escreve(uid, house.apagar_meta, ref)
    return _sem_corpo()


@router.post("/familia/casa/metas/{ref}/movimentos", response_model=Casa)
def mover_meta(ref: str, corpo: Movimento, uid: int = Depends(usuario_atual)) -> Casa:
    return _escreve(uid, house.mover_meta, ref, _delta(corpo))


@router.post("/familia/casa/orcamentos", status_code=201, response_model=Casa)
def criar_orcamento(corpo: OrcamentoIn, uid: int = Depends(usuario_atual)) -> Casa:
    return _escreve(uid, house.criar_orcamento, corpo.categoria, _cents(corpo.limite))


@router.put("/familia/casa/orcamentos/{ref}", response_model=Casa)
def editar_orcamento(ref: str, corpo: OrcamentoEdicao, uid: int = Depends(usuario_atual)) -> Casa:
    return _escreve(uid, house.editar_orcamento, ref, corpo.categoria, _cents(corpo.limite), corpo.versao)


@router.delete("/familia/casa/orcamentos/{ref}", response_model=Vazio)
def apagar_orcamento(ref: str, uid: int = Depends(usuario_atual)):
    _escreve(uid, house.apagar_orcamento, ref)
    return _sem_corpo()


@router.post("/familia/casa/orcamentos/{ref}/movimentos", response_model=Casa)
def mover_orcamento(ref: str, corpo: Movimento, uid: int = Depends(usuario_atual)) -> Casa:
    return _escreve(uid, house.mover_orcamento, ref, _delta(corpo))
