"""Leitura tipada da previsão condicional, com recursos e horizontes do plano."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.v2.sessao import usuario_atual
from core.services.plan_service import forecast_horizons_for, plan_gate_ok
from core.services.previsao_recorrencias import CONFIRMADA, NEGADA
from core.services.previsao_v2 import ler_previsao
from db.of_recurring import marcar, marcas

router = APIRouter()
FontePrevisao = Literal["receita_recorrente", "gasto_recorrente", "instancia", "fatura", "recorrencia_banco"]


class MotivoPrevisao(BaseModel):
    codigo: str
    direcao_do_erro: Literal["so_melhora", "so_piora", "ambos"]


class PeriodoPrevisao(BaseModel):
    inicio: str
    fim: str


class BasePrevisao(BaseModel):
    saldo: Decimal | None
    origem: Literal["manual", "consolidated", "unavailable"]
    contas_bancarias: int
    bancos_excluidos: bool
    motivos: list[str]


class MarcoPrevisao(BaseModel):
    dias: int
    data: str
    saldo: Decimal | None


class AncoraPrevisao(BaseModel):
    data: str
    saldo: Decimal | None


class OcorrenciaPrevisao(BaseModel):
    chave: str
    ciclo: str
    data: str | None
    fonte: FontePrevisao
    tipo: Literal["receita", "gasto_fixo", "boleto", "fatura_cartao"]
    nome: str
    valor: Decimal | None
    direcao: Literal["entrada", "saida"]
    qualidade_valor: Literal["conhecido", "estimado", "desconhecido"]
    qualidade_data: Literal["conhecida", "presumida", "desconhecida"]
    realizacao: Literal["prevista", "realizada", "a_conferir"]
    incluida_no_calculo: bool
    motivos: list[MotivoPrevisao]


class PontoPrevisao(BaseModel):
    data: str
    saldo: Decimal | None
    compromissos: list[OcorrenciaPrevisao]


class PiorDiaPrevisao(BaseModel):
    data: str
    saldo: Decimal
    desde: str | None
    causas: list[OcorrenciaPrevisao]
    despesa_variavel: Decimal | None  # a estimativa do dia a dia entre o último pico e o pior dia (não é compromisso)


class CategoriaEstimada(BaseModel):
    chave: str
    nome: str
    despesa_mensal: Decimal


class EstimativaVariavel(BaseModel):
    despesa_mensal: Decimal
    despesa_no_horizonte: Decimal
    janela: PeriodoPrevisao
    dias_de_historico: int
    janelas_completas: int
    categorias: list[CategoriaEstimada]


class GrupoCompromissos(BaseModel):
    chave: str
    fonte: FontePrevisao
    nome: str
    primeira_data: str | None
    ultima_data: str | None
    ocorrencias: list[OcorrenciaPrevisao]


class CoberturaPrevisao(BaseModel):
    fontes_incompletas: list[str]
    janela_conferencia_inicio: str | None
    inclui_estimativa_variavel: bool


class ReceitaDoBanco(BaseModel):
    chave: str
    nome: str
    valor: Decimal
    dia: int
    status: Literal["pendente", "confirmada", "negada"]


class RespostaReceitaIn(BaseModel):
    chave: str
    resposta: Literal["sim", "nao", "desfazer"]


class Previsao(BaseModel):
    hoje: str
    calculado_em: datetime
    valido_ate: datetime | None
    dias: int
    dias_permitidos: list[int]
    capacidades: list[Literal["marcos", "trajetoria", "pior_dia", "compromissos"]]
    base: BasePrevisao
    estado: Literal["condicional", "a_conferir", "indisponivel"]
    motivos: list[MotivoPrevisao]
    premissas: list[str]
    cobertura: CoberturaPrevisao
    cabe_nas_premissas: bool
    marcos: list[MarcoPrevisao]
    periodo: PeriodoPrevisao | None
    ancora: AncoraPrevisao | None
    trajetoria: list[PontoPrevisao] | None
    pior_dia: PiorDiaPrevisao | None
    compromissos: list[GrupoCompromissos] | None
    receitas_do_banco: list[ReceitaDoBanco]
    estimativa_variavel: EstimativaVariavel | None


def _assinante(uid: int = Depends(usuario_atual)) -> int:
    if not plan_gate_ok(uid, "forecast"):
        raise HTTPException(status_code=403, detail={"error": "pro_required", "feature": "forecast"})
    return uid


@router.get("/previsao", response_model=Previsao)
def previsao(dias: Literal["30", "60", "90"] | None = Query(None),
             uid: int = Depends(_assinante)) -> Previsao:
    permitidos = forecast_horizons_for(uid)
    efetivos = int(dias) if dias is not None else min(permitidos)
    if efetivos not in permitidos:
        raise HTTPException(status_code=403, detail={"error": "forecast_horizon_not_allowed"})
    detalhes = plan_gate_ok(uid, "cashflow")
    return Previsao(**ler_previsao(uid, efetivos, permitidos, detalhes))


_MARCA = {"sim": CONFIRMADA, "nao": NEGADA, "desfazer": "nenhuma"}


@router.post("/previsao/receita", response_model=Previsao)
def responder_receita(corpo: RespostaReceitaIn, uid: int = Depends(_assinante)) -> Previsao:
    """O usuário confirma (sim), nega (nao) ou desfaz a resposta sobre uma receita detectada no extrato.
    A chave tem de estar na lista do PRÓPRIO usuário; senão 404 e nada é gravado. Só o `desfazer` também
    vale para a marca de receita do próprio usuário que saiu da lista (a receita deixou de ser detectada)."""
    permitidos = forecast_horizons_for(uid)
    dias = min(permitidos)
    orfa = corpo.resposta == "desfazer" and corpo.chave.startswith("extrato#") and corpo.chave in marcas(uid)
    if not orfa and corpo.chave not in {r["chave"] for r in ler_previsao(uid, dias, permitidos, False)["receitas_do_banco"]}:
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    marcar(uid, corpo.chave, _MARCA[corpo.resposta])
    return Previsao(**ler_previsao(uid, dias, permitidos, plan_gate_ok(uid, "cashflow")))
