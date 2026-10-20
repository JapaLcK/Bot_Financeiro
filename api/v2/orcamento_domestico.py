"""`/api/v2/orcamento-domestico`: a renda do mês dividida em 6 potes (regra em
`db/household_budget.py`, que conta em float; aqui o float vira `Decimal` de 2 casas na saída).
Plus ou Pro (`household_budget` em `FEATURE_MIN_TIER_V2`). Sempre passa o mês explícito ao
módulo (sem ele, ele usa o `date.today()` do servidor). Dinheiro e % saem como TEXTO. O CSRF
dos POSTs é o `csrf_middleware` do pai, que cobre `/api/v2/*`."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.v2.lancamentos import _recusa
from api.v2.resumo_mes import PADRAO_MES, mes_pedido
from api.v2.sessao import usuario_atual
from core.services.plan_service import plan_gate_ok
from db import household_budget as hb

router = APIRouter()
_PCT = r"^[0-9]{1,3}(\.[0-9]{1,2})?$"  # `[0-9]` e nunca `\d`: o regex é Unicode
_VALOR = r"^[0-9]{1,9}(\.[0-9]{1,2})?$"  # como o de lancamentos, mas 0 é renda válida


class Renda(BaseModel):
    valor: Decimal
    origem: Literal["calculada", "informada"]


class PorCategoria(BaseModel):
    categoria: str
    total: Decimal


class LancamentoPote(BaseModel):
    data: str  # AAAA-MM-DD
    descricao: str
    categoria: str
    valor: Decimal
    origem: Literal["conta", "cartao"]


class Pote(BaseModel):
    chave: str
    nome: str
    cor: str
    pct: Decimal
    teto: Decimal
    gasto: Decimal
    restante: Decimal  # negativo = passou do teto
    guardado: bool  # Metas/Liberdade: passar do teto não é estouro (regra do servidor)
    usado_pct: Decimal | None  # null sem teto (renda 0)
    categorias: list[str]  # nomes de exibição das categorias que caem no pote
    por_categoria: list[PorCategoria]
    lancamentos: list[LancamentoPote]  # do mês, mais novo primeiro; a soma é o `gasto`


class Total(BaseModel):
    teto: Decimal
    gasto: Decimal
    restante: Decimal
    restante_livre: Decimal  # restante sem o excedente dos potes guardados; negativo = estouro de verdade
    usado_pct: Decimal | None


class OrcamentoDomestico(BaseModel):
    mes: str
    renda: Renda
    potes: list[Pote]
    total: Total


class PoteIn(BaseModel):
    chave: str = Field(max_length=40)
    pct: str = Field(pattern=_PCT)


class PotesIn(BaseModel):
    mes: str = Field(pattern=PADRAO_MES)
    potes: list[PoteIn]


class RendaIn(BaseModel):
    mes: str = Field(pattern=PADRAO_MES)
    valor: str | None = Field(..., pattern=_VALOR)  # obrigatório e anulável: null = usar a calculada


def _orcamentista(uid: int = Depends(usuario_atual)) -> int:
    if not plan_gate_ok(uid, "household_budget"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "household_budget"})
    return uid


def _d(x) -> Decimal | None:
    """float do módulo -> Decimal de 2 casas (já vem arredondado a 2): texto estável, sem "5000.0"."""
    return None if x is None else Decimal(str(x)).quantize(Decimal("0.01"))


def _monta(uid: int, mes: str) -> OrcamentoDomestico:
    s = hb.get_household_budget_status(uid, mes)
    t = s["totals"]
    return OrcamentoDomestico(
        mes=s["month"],
        renda=Renda(valor=_d(s["income"]["amount"]),
                    origem="informada" if s["income"]["source"] == "override" else "calculada"),
        potes=[Pote(chave=b["key"], nome=b["label"], cor=b["color"], pct=_d(b["pct"]),
                    teto=_d(b["budget_amount"]), gasto=_d(b["spent"]), restante=_d(b["remaining"]), guardado=b["guarda"],
                    usado_pct=_d(b["used_pct"]), categorias=b["categories"],
                    por_categoria=[PorCategoria(categoria=c["categoria"], total=_d(c["total"])) for c in b["by_category"]],
                    lancamentos=[LancamentoPote(data=x["data"], descricao=x["descricao"], categoria=x["categoria"],
                                                valor=_d(x["valor"]), origem=x["origem"]) for x in b["launches"]])
                for b in s["buckets"]],
        total=Total(teto=_d(t["budget_amount"]), gasto=_d(t["spent"]), restante=_d(t["remaining"]), restante_livre=_d(t["remaining_free"]),
                    usado_pct=_d(t["used_pct"])),
    )


def _mes(mes: str | None, onde: str) -> str:
    ano, m, _ = mes_pedido(mes, onde)
    return f"{ano:04d}-{m:02d}"


@router.get("/orcamento-domestico", response_model=OrcamentoDomestico)
def ler(mes: str | None = Query(None, pattern=PADRAO_MES), uid: int = Depends(_orcamentista)) -> OrcamentoDomestico:
    return _monta(uid, _mes(mes, "query"))


@router.post("/orcamento-domestico/potes", response_model=OrcamentoDomestico)
def salvar_potes(corpo: PotesIn, uid: int = Depends(_orcamentista)) -> OrcamentoDomestico:
    mes = _mes(corpo.mes, "body")
    pcts = {p.chave: Decimal(p.pct) for p in corpo.potes}
    if len(pcts) != len(corpo.potes):
        raise _recusa("potes", "Mande os 6 potes, uma vez cada.", "body")
    try:
        hb.save_config(uid, pcts)
    except ValueError as e:
        raise _recusa("potes", {"PCT_SOMA_INVALIDA": "A soma precisa fechar 100%.",
                                "PCT_INVALIDO": "Cada pote vai de 0 a 100%."}.get(
                                    str(e), "Mande os 6 potes, uma vez cada."), "body") from None
    return _monta(uid, mes)


@router.post("/orcamento-domestico/renda", response_model=OrcamentoDomestico)
def salvar_renda(corpo: RendaIn, uid: int = Depends(_orcamentista)) -> OrcamentoDomestico:
    mes = _mes(corpo.mes, "body")
    if corpo.valor is None:
        hb.clear_income_override(uid, mes)
    else:
        try:
            hb.set_income_override(uid, mes, Decimal(corpo.valor))
        except ValueError:
            raise _recusa("valor", "Valor inválido.", "body") from None
    return _monta(uid, mes)
