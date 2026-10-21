"""`GET /api/v2/patrimonio`: patrimônio real, fotos diárias, trechos sem quebra e a variação do atual.

Projeção pública, sem a `base` bruta. O app (`/api/app/patrimonio`) monta a mesma rota e
o card do chat importa `patrimonio` daqui por `api.nativo.patrimonio`. A regra da quebra é
`db.patrimonio.quebra`; o histórico vai até 12 meses, e o plano pode cortar antes.
"""
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.nativo.dinheiro import Dinheiro, em_centavos, soma_arredondada_diverge
from api.v2.sessao import usuario_atual
from core.services.plan_service import history_earliest_date
from db.connection import get_conn
from db.patrimonio import calcular, quebra
from utils_date import day_tz, now_tz

router = APIRouter()
DIAS_MAX = 365


class Partes(BaseModel):
    carteira: Dinheiro
    bancos: Dinheiro
    investimentos_banco: Dinheiro
    caixinhas: Dinheiro
    investimentos_manuais: Dinheiro


# Datas em texto ISO (AAAA-MM-DD), como o resto da v2: o gerador de tipos recusa `format: date`.
class Foto(BaseModel):
    dia: str
    total: Dinheiro
    partes: Partes
    motivos: list[str]


class Trecho(BaseModel):
    desde: str
    ate: str


class Variacao(BaseModel):
    valor: Dinheiro
    desde: str
    ate: str


class Patrimonio(BaseModel):
    total: Dinheiro
    partes: Partes
    motivos: list[str]
    historico: list[Foto]
    historico_desde: str | None
    trechos: list[Trecho]  # crescente, nunca vazio: o último termina em hoje
    variacao: Variacao | None  # desde = trechos[-1].desde, ate = hoje; null com um ponto só


def _publico(dados: dict) -> dict:
    partes = {nome: dados[nome] for nome in Partes.model_fields}
    motivos = list(dados["motivos"])
    # A projeção em centavos preserva o total calculado/fotografado oficialmente.
    # Arredondar cada parte não autoriza recalcular esse total financeiro.
    if soma_arredondada_diverge(list(partes.values()), dados["total"]):
        motivos.append("arredondamento_por_grupo")
    return {"total": dados["total"], "partes": partes, "motivos": motivos}


def trechos(pontos: list[tuple[date, dict, Decimal]]) -> tuple[list[dict], dict | None]:
    """`pontos` = [(dia, base, total)] crescente, o último é hoje. Pura."""
    ini = 0
    cortes = []
    for i in range(1, len(pontos)):
        if quebra(pontos[i - 1][1], pontos[i][1]):
            cortes.append({"desde": pontos[ini][0].isoformat(), "ate": pontos[i - 1][0].isoformat()})
            ini = i
    hoje, _, total = pontos[-1]
    cortes.append({"desde": pontos[ini][0].isoformat(), "ate": hoje.isoformat()})
    if ini == len(pontos) - 1:
        return cortes, None  # um ponto só: não há de onde variar (nunca 0 inventado)
    # Diferença dos totais exibidos (centavos), como o app mostraria.
    valor = Decimal(em_centavos(total)) - Decimal(em_centavos(pontos[ini][2]))
    return cortes, {"valor": valor, "desde": pontos[ini][0].isoformat(), "ate": hoje.isoformat()}


@router.get("/patrimonio", response_model=Patrimonio)
def patrimonio(uid: int = Depends(usuario_atual)) -> Patrimonio:
    agora = now_tz()
    hoje = day_tz(agora)
    corte = max(history_earliest_date(uid, agora) or date.min, hoje - timedelta(days=DIAS_MAX))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        vivo = calcular(cur, uid)
        cur.execute("""select dia, total, carteira, bancos, investimentos_banco,
                              caixinhas, investimentos_manuais, motivos, base
                         from patrimonio_fotos
                        where user_id=%s and dia >= %s and dia <= %s order by dia""",
                    (uid, corte, hoje))
        rows = cur.fetchall()
        conn.rollback()
    fotos = [{"dia": r["dia"].isoformat(), **_publico(r)} for r in rows]
    # A foto de hoje (job das 18h) fica no histórico, mas o ponto de hoje é o ao vivo.
    pontos = [(r["dia"], r["base"], r["total"]) for r in rows if r["dia"] < hoje]
    pontos.append((hoje, vivo["base"], vivo["total"]))
    cortes, variacao = trechos(pontos)
    return Patrimonio(**_publico(vivo), historico=fotos,
                      historico_desde=fotos[0]["dia"] if fotos else None,
                      trechos=cortes, variacao=variacao)
