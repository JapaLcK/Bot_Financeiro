"""`GET /api/v2/lancamentos`: a lista do mês, só leitura (`db/lancamentos.py`).

`mes` como em `resumo-do-mes` (sem ele, o corrente; futuro ou fora de `AAAA-MM` = 422).
`cursor` é o `proximo` da página anterior, devolvido como veio: adulterado = 422.
`limite` de 1 a 100 (acima de 100 corta). `conta`/`cartao` de outro usuário dão a lista
vazia, igual a um id que não existe. Dinheiro é `Decimal` e sai como TEXTO; nada de
`provider_*_id`, `raw`, `external_id` ou id da transação do banco.
"""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.sessao import usuario_atual
from db import lancamentos, resumo_mes
from db.categories import CATEGORY_NAME_MAX_LEN

router = APIRouter()
_TETO = 100
_MAX_ID = 2**63 - 1


class Parcela(BaseModel):
    n: int
    total: int


class Lancamento(BaseModel):
    id: str
    data: str
    hora: str | None
    tipo: Literal["entrada", "saida"]
    interno: bool
    valor: Decimal
    moeda: str
    descricao: str | None
    mensagem: str | None
    categoria: str | None
    origem: Literal[lancamentos.ORIGENS]
    fundido: bool
    instituicao: str | None
    conta_id: int | None
    cartao_id: int | None
    parcela: Parcela | None
    fatura: str | None
    pode: list[Literal[lancamentos.PODE]]
    motivos: list[Literal[lancamentos.MOTIVOS_ITEM]]


class Lancamentos(BaseModel):
    mes: str
    itens: list[Lancamento]
    proximo: str | None
    motivos: list[Literal[resumo_mes.MOTIVOS]]


def _recusa(campo: str, msg: str):
    return RequestValidationError([{"loc": ("query", campo), "msg": msg, "type": "value_error"}])


@router.get("/lancamentos", response_model=Lancamentos)
def listar(mes: str | None = Query(None, pattern=PADRAO_MES),
           origem: Literal[lancamentos.ORIGENS] | None = None,
           conta: int | None = None,
           cartao: int | None = None,
           categoria: str | None = Query(None, max_length=CATEGORY_NAME_MAX_LEN),
           tipo: Literal["entrada", "saida"] | None = None,
           q: str | None = Query(None, max_length=200),
           cursor: str | None = Query(None, max_length=200),
           limite: int = 50,
           uid: int = Depends(usuario_atual)) -> Lancamentos:
    # Faixa à mão: `ge`/`le` no Query entrariam no contrato como minimum/maximum.
    for campo, valor in (("conta", conta), ("cartao", cartao)):
        if valor is not None and not 0 < valor <= _MAX_ID:
            raise _recusa(campo, "Id fora da faixa.")
    if limite < 1:
        raise _recusa("limite", "O limite mínimo é 1.")
    ano, m, agora = mes_pedido(mes)
    try:
        dados = lancamentos.pagina(uid, ano, m, agora, cursor=cursor, limite=min(limite, _TETO), q=q,
                                   origem=origem, conta=conta, cartao=cartao,
                                   categoria=categoria, tipo=tipo)
    except lancamentos.CursorInvalido as e:
        raise _recusa("cursor", str(e)) from None
    return Lancamentos(**dados)
