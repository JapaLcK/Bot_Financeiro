"""Simulador de compra v2 (Pro): `POST /simulador`. Não grava nada; a validação é a de
`decision_simulator.Simulacao` (fonte única) e a conta é `simular` sobre uma snapshot."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.v2.erros import _envelope
from api.v2.lancamentos import _DATA, _VALOR
from api.v2.previsao import BasePrevisao, CoberturaPrevisao, MotivoPrevisao
from api.v2.sessao import usuario_atual
from core.services import decision_simulator as ds
from core.services.decision_simulator import CASAS_DINHEIRO, DIAS, EXTENSO, Simulacao, simular
from core.services.plan_service import plan_gate_ok
from core.services.previsao_v2 import _motivos, apresentar_base, ler_snapshot

router = APIRouter()
CASAS_JUROS = 4  # no máximo; a faixa 0..20 é da `Simulacao`
_JUROS = rf"^[0-9]{{1,2}}(\.[0-9]{{1,{CASAS_JUROS}}})?$"

# Premissas para a pessoa (as da `ds.PREMISSAS` falam com a tool de IA, com nomes de campo). Fonte única na v2.
PREMISSAS_V2 = [
    f"Saldo de {DIAS} dias: o mesmo da previsão de saldo (saldo + receitas fixas − gastos fixos previstos − boletos − faturas observadas), mais a compra simulada.",
    f"O pior dia e os dias abaixo da reserva contam hoje, depois dos compromissos e da compra, até o dia {DIAS} inclusive ({DIAS + 1} datas).",
    "Na data da compra sai o preço cheio (à vista) ou a entrada (parcelado), mais os custos únicos.",
    "A primeira parcela e a despesa mensal nova saem um mês depois, no mesmo dia (ajustado ao fim do mês).",
    "Parcelado com juros usa a tabela Price; a diferença de centavos do arredondamento vai para a última parcela.",
    f"O total pago soma o que sai na compra e todas as parcelas, inclusive as que caem depois do dia {DIAS}. Não inclui a despesa mensal nova, que não tem fim definido.",
    "Receitas fixas só entram quando mensais ou anuais; gastos fixos entram em todas as frequências.",
    "Não inclui gastos avulsos futuros nem IOF, seguro ou tarifas que você não informou.",
    f"Valores em reais aceitam no máximo {EXTENSO[CASAS_DINHEIRO]} casas decimais; os juros, no máximo {EXTENSO[CASAS_JUROS]}.",
]

# Cada `value_error` da `Simulacao` (mensagem do servidor) -> (código estável, campo do cenário ou None
# quando o `loc` já o traz). A tela mapeia o código para o texto; o `msg` (voz da IA) nunca sai.
CODIGOS_ERRO = {
    ds.ERRO_CASAS: ("dinheiro_casas", None),
    ds.ERRO_ENTRADA_MAIOR: ("entrada_maior_que_preco", "entrada"),
    ds.ERRO_ENTRADA_PARCIAL: ("entrada_parcial_sem_parcelas", "parcelas"),
    ds.ERRO_ENTRADA_IGUAL: ("entrada_igual_ao_preco", "entrada"),
    ds.ERRO_JUROS_SEM_PARCELAS: ("juros_sem_parcelas", "parcelas"),
    ds.ERRO_DATA_PASSADO: ("data_no_passado", "data_compra"),
    ds.ERRO_DATA_CALENDARIO: ("data_fora_do_calendario", "data_compra"),
    ds.ERRO_FINANCIADO_PEQUENO: ("financiado_pequeno_demais", "parcelas"),
    ds.ERRO_NOMES_REPETIDOS: ("nomes_repetidos", None),  # loc ["body"]: vale para a lista inteira
}
CODIGO_GENERICO = "valor_invalido"


def _detalhe(e: dict) -> dict:
    loc = ["body", *e["loc"]]
    if e["type"] != "value_error":
        return {"loc": loc, "msg": e["msg"], "type": e["type"]}
    codigo, campo = CODIGOS_ERRO.get(str(e["ctx"]["error"]), (CODIGO_GENERICO, None))
    if campo and len(loc) == 3:  # erro do modelo do cenário: o campo vem da tabela
        loc.append(campo)
    return {"loc": loc, "msg": "Valor inválido.", "type": codigo}


class CenarioIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: str = Field(min_length=1, max_length=60)
    preco: str = Field(pattern=_VALOR)
    entrada: str | None = Field(None, pattern=_VALOR)
    custos_unicos: str | None = Field(None, pattern=_VALOR)
    despesa_mensal_nova: str | None = Field(None, pattern=_VALOR)
    parcelas: int | None = Field(None, strict=True)  # 1..420 é da `Simulacao`
    juros_mensal_pct: str | None = Field(None, pattern=_JUROS)
    data_compra: str | None = Field(None, pattern=_DATA)


class SimuladorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reserva_minima: str | None = Field(None, pattern=_VALOR)
    cenarios: list[CenarioIn] = Field(min_length=1, max_length=3)


class PontoSimulador(BaseModel):
    data: str
    saldo: Decimal | None


class PiorDiaSimulador(BaseModel):
    data: str
    saldo: Decimal


class ResumoAtual(BaseModel):
    saldo_final: Decimal | None
    pior_dia: PiorDiaSimulador | None
    dias_abaixo_da_reserva: int
    primeiro_dia_abaixo: str | None
    trajetoria: list[PontoSimulador]


class ResumoCenario(ResumoAtual):
    delta_vs_atual: Decimal | None
    compra_fora_da_janela: bool


class ParcelasForaDoHorizonte(BaseModel):
    quantidade: int
    valor: Decimal


class ContratoSimulador(BaseModel):
    data_compra: str
    a_vista: bool
    entrada: Decimal
    pago_na_compra: Decimal
    valor_financiado: Decimal
    parcelas: int
    parcela: Decimal
    primeira_parcela: str
    ultima_parcela: str
    total_pago: Decimal
    juros_totais: Decimal
    custos_unicos: Decimal
    despesa_mensal_nova: Decimal
    parcelas_fora_do_horizonte: ParcelasForaDoHorizonte


class CenarioOut(BaseModel):
    nome: str
    resumo: ResumoCenario
    contrato: ContratoSimulador


class Simulador(BaseModel):
    hoje: str
    calculado_em: datetime
    valido_ate: datetime | None
    dias: int
    estado: Literal["condicional", "a_conferir", "indisponivel"]
    motivos: list[MotivoPrevisao]
    premissas: list[str]
    cobertura: CoberturaPrevisao
    cabe_nas_premissas: bool
    orientacao: Literal["abster"]
    base: BasePrevisao
    reserva_minima: Decimal
    atual: ResumoAtual
    cenarios: list[CenarioOut]


def _assinante(uid: int = Depends(usuario_atual)) -> int:
    if not plan_gate_ok(uid, "simulator"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "simulator"})
    return uid


def _resumo(r: dict) -> dict:
    pior = r["pior_dia"]
    return {**{k: v for k, v in r.items() if k != "saldo_final_90"}, "saldo_final": r["saldo_final_90"],
            "pior_dia": {"data": pior["date"], "saldo": pior["saldo"]} if pior else None,
            "trajetoria": [{"data": p["date"], "saldo": p["saldo"]} for p in r["trajetoria"]]}


@router.post("/simulador", response_model=Simulador)
def simulador(corpo: SimuladorIn, uid: int = Depends(_assinante)):
    # exclude_none: campo ausente vale o default da `Simulacao`. O dinheiro vira float só
    # aqui, na fronteira da validação única; a conta e a resposta seguem em Decimal.
    dados = corpo.model_dump(exclude_none=True)
    for c in [*dados["cenarios"], dados]:
        for k in ("preco", "entrada", "custos_unicos", "despesa_mensal_nova", "reserva_minima", "juros_mensal_pct"):
            if k in c:
                c[k] = float(c[k])
    try:
        sim = Simulacao.model_validate(dados)
    except ValidationError as exc:  # sem `input`: ecoaria o corpo
        return _envelope(422, "validation_error", "Dados inválidos.", [_detalhe(e) for e in exc.errors(include_input=False)])
    snap = ler_snapshot(uid, DIAS)
    r = simular(snap, sim)
    return Simulador(
        hoje=r["today"], calculado_em=r["calculado_em"], valido_ate=r["valido_ate"], dias=DIAS,
        estado=r["estado"], motivos=_motivos(r["motivos"]), premissas=[*snap.premissas, *PREMISSAS_V2],
        cobertura=r["cobertura"], cabe_nas_premissas=r["cabe_nas_premissas"], orientacao=r["orientacao"],
        base=apresentar_base(snap.base),
        reserva_minima=r["reserva_minima"], atual=_resumo(r["atual"]),
        cenarios=[{"nome": c["nome"], "resumo": _resumo(c["resumo"]), "contrato": c["contrato"]}
                  for c in r["cenarios"]])
