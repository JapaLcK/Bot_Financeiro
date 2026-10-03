"""`GET /api/v2/lancamentos`: a lista do mês (`db/lancamentos.py`). E a escrita do PR 2a:
`POST /lancamentos/carteira`, `/lancamentos/editar` e `/lancamentos/apagar` (abaixo).

`mes` como em `resumo-do-mes` (sem ele, o corrente; futuro ou fora de `AAAA-MM` = 422).
`cursor` é o `proximo` da página anterior, devolvido como veio: adulterado = 422.
`limite` de 1 a 100 (acima de 100 corta). `conta`/`cartao` de outro usuário dão a lista
vazia, igual a um id que não existe. Dinheiro é `Decimal` e sai como TEXTO; nada de
`provider_*_id`, `raw`, `external_id` ou id da transação do banco.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field

from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.sessao import usuario_atual
from core.pg_text import limpa_para_pg
from db import lancamentos, resumo_mes
from db.categories import CATEGORY_NAME_MAX_LEN
from utils_date import day_tz, now_tz

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


def _recusa(campo: str, msg: str, onde: str = "query"):
    return RequestValidationError([{"loc": (onde, campo), "msg": msg, "type": "value_error"}])


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


# ── escrita (PR 2a) ─────────────────────────────────────────────────────────
# `[0-9]` e nunca `\d`: o regex é Unicode e "١٢" passaria. Rotas `def` (o banco é síncrono),
# POST com o id no corpo; o CSRF é o `csrf_middleware` do pai, que cobre /api/v2.
# ponytail: sem chave de idempotência; dois POST iguais gravam dois lançamentos. Acrescentar
# Idempotency-Key (coluna pedido_id com índice único parcial, entrando na exportação, no reset
# e na exclusão) quando a tela (PR 4) mostrar duplicata real.
_VALOR = r"^[0-9]{1,9}(\.[0-9]{1,2})?$"
_DATA = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
_ID = r"^[lc][1-9][0-9]{0,18}$"


def _nao_achou():  # o MESMO corpo para id de outro usuário e id inexistente
    return HTTPException(status_code=404, detail={"error": "lancamento_nao_encontrado"})


def _nao_editavel():
    return HTTPException(status_code=409, detail={"error": "nao_editavel"})


class NovoLancamento(BaseModel):
    tipo: Literal["entrada", "saida"]
    valor: str = Field(pattern=_VALOR)
    descricao: str = Field(max_length=200)
    categoria: str | None = Field(None, max_length=CATEGORY_NAME_MAX_LEN)
    data: str | None = Field(None, pattern=_DATA)


class IdLancamento(BaseModel):
    id: str = Field(pattern=_ID)


class Edicao(IdLancamento):
    categoria: str | None = Field(None, max_length=CATEGORY_NAME_MAX_LEN)
    descricao: str | None = Field(None, max_length=200)
    data: str | None = Field(None, pattern=_DATA)


class LancamentoId(BaseModel):
    id: str


def _id(texto: str) -> tuple[str, int]:
    n = int(texto[1:])
    if n > _MAX_ID:
        raise _recusa("id", "Id fora da faixa.", "body")
    return texto[0], n


def _texto(campo: str, valor: str | None) -> str | None:
    """NUL e surrogate dariam 500 no Postgres; só espaço = vazio = 422."""
    if valor is None:
        return None
    valor = limpa_para_pg(valor).strip()
    if not valor:
        raise _recusa(campo, "Não pode ser vazio.", "body")
    return valor


def _dia(uid: int, texto: str | None, agora: datetime) -> date | None:
    """`corte <= data <= hoje` (a janela do plano), num instante só."""
    from core.services.plan_service import history_earliest_date

    if texto is None:
        return None
    try:
        dia = date.fromisoformat(texto)
    except ValueError:
        raise _recusa("data", "Data inválida.", "body") from None
    corte = history_earliest_date(uid, agora)
    if dia > day_tz(agora) or (corte and dia < corte):
        raise _recusa("data", "Data fora da janela permitida.", "body")
    return dia


@router.post("/lancamentos/carteira", response_model=LancamentoId)
def lancar_na_carteira(corpo: NovoLancamento, uid: int = Depends(usuario_atual)) -> LancamentoId:
    """Dinheiro em espécie, sempre na Carteira (Q40: o v2 não recebe forma de pagamento)."""
    from core.services.carteira import lancar
    from core.services.plan_limits import PlanLimitExceeded
    from core.services.plan_service import check_can_create_launch

    valor = Decimal(corpo.valor)
    if valor <= 0:
        raise _recusa("valor", "O valor deve ser maior que zero.", "body")
    descricao, categoria = _texto("descricao", corpo.descricao), _texto("categoria", corpo.categoria)
    agora = now_tz()
    dia = _dia(uid, corpo.data, agora)
    try:
        check_can_create_launch(uid)
    except PlanLimitExceeded:
        raise HTTPException(status_code=403, detail={"error": "plan_limit"}) from None
    feito = lancar(uid, "receita" if corpo.tipo == "entrada" else "despesa", valor, descricao, None,
                   categoria, datetime.combine(dia, agora.timetz()) if dia else None)
    return LancamentoId(id=f"l{feito['launch_id']}")


@router.post("/lancamentos/editar", response_model=LancamentoId)
def editar(corpo: Edicao, uid: int = Depends(usuario_atual)) -> LancamentoId:
    """Cada campo contra o `pode` da linha, sob lock (`db/lancamentos.pode_da_linha`)."""
    from db.accounts import update_launch_fields
    from db.cards import update_credit_transaction_fields
    from db.categories import ensure_user_category, resolve_category_input

    tabela, n = _id(corpo.id)
    if corpo.categoria is None and corpo.descricao is None and corpo.data is None:
        raise RequestValidationError([{"loc": ("body",), "msg": "Nada para editar.",
                                       "type": "value_error"}])
    descricao = _texto("descricao", corpo.descricao)
    dia = _dia(uid, corpo.data, now_tz())
    categoria = None
    if corpo.categoria is not None:
        categoria = resolve_category_input(uid, _texto("categoria", corpo.categoria), create=True)
        if not categoria:
            raise _recusa("categoria", "Categoria inválida.", "body")
    try:
        if tabela == "l":
            mudou = update_launch_fields(uid, n, categoria=categoria, alvo=descricao, dia=dia,
                                         exigir_pode=True)
        elif dia is not None:  # compra no cartão não troca de data (P5)
            raise lancamentos.NaoEditavel("data")
        else:
            mudou = update_credit_transaction_fields(uid, n, categoria=categoria, nota=descricao,
                                                     exigir_pode=True)
    except lancamentos.NaoEditavel:
        raise _nao_editavel() from None
    if not mudou:
        raise _nao_achou()
    if categoria:
        ensure_user_category(uid, categoria)  # depois do UPDATE, com o portão de plano, como o /app
    return LancamentoId(id=corpo.id)


@router.post("/lancamentos/apagar", response_model=LancamentoId)
def apagar(corpo: IdLancamento, uid: int = Depends(usuario_atual)) -> LancamentoId:
    """Só lançamento (`l`): compra no cartão não apaga pelo v2."""
    from db.accounts import (InvestmentLotHasWithdrawal, LaunchNoEffects, LaunchUnsafeRollback,
                             delete_launch_and_rollback)

    tabela, n = _id(corpo.id)
    if tabela == "c":
        raise _nao_editavel()
    try:
        delete_launch_and_rollback(uid, n, exigir_pode=True)
    except LookupError:
        raise _nao_achou() from None
    except (lancamentos.NaoEditavel, LaunchNoEffects, LaunchUnsafeRollback, InvestmentLotHasWithdrawal):
        raise _nao_editavel() from None
    return LancamentoId(id=corpo.id)
