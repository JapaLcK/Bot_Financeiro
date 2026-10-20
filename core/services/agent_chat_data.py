"""Retratos de consulta dos especialistas, sem executar sincronizações."""
import logging
from datetime import date, timedelta

from decimal import Decimal

import db

SNAPSHOT_ITEMS_LIMIT = 100
logger = logging.getLogger(__name__)


def _investido(user_id: int) -> dict:
    """O total dos bancos conectados: a mesma regra (db/investido.ler) do card e do /api/v2/investido."""
    from core.services.ai_chat.tools.investments import _get_investment_summary
    try:
        return _get_investment_summary(user_id, {})
    except Exception:  # sem o total dos bancos a conversa segue só com os recortes
        logger.exception("agent_chat: investido.ler falhou user_id=%s", user_id)
        return {"indisponivel": True}


def total_renda_fixa(investido: dict) -> str | None:
    """Renda fixa + Tesouro do card "Renda fixa". None = não se sabe (sem total ou parte sem saldo).
    A mesma regra do CardInvestido.tsx (foco renda_fixa); os dois testes leem tests/frontend/total_renda_fixa.json."""
    if investido.get("total_investido") is None:
        return None
    partes = [p for p in investido["por_tipo"] if p["tipo"] in ("renda_fixa", "tesouro")]
    if any(p["valor"] is None for p in partes):
        return None
    return str(sum((Decimal(p["valor"]) for p in partes), Decimal("0")).quantize(Decimal("0.01")))


def _snapshot_coverage(rows: list) -> dict:
    return {"total": len(rows), "incluidos": min(len(rows), SNAPSHOT_ITEMS_LIMIT),
            "truncado": len(rows) > SNAPSHOT_ITEMS_LIMIT}


def _snapshot(user_id: int, kind: str) -> dict:
    """Dados adicionais de cada especialista, sempre consultados sem disparar runners."""
    if kind == "detetive":
        from core.services.assinaturas import listar_assinaturas
        from core.services.piggy_agents import (find_duplicate_charges,
                                                DETETIVE_DUP_LOOKBACK_DAYS, DETETIVE_DUP_MIN_VALOR)
        duplicates = find_duplicate_charges(user_id, date.today())
        lista = listar_assinaturas(user_id, date.today())
        recurring = lista["servicos"] + lista["outras"]
        return {"possiveis_duplicidades": duplicates[:50], "recorrencias": recurring[:50],
                "total_duplicidades": len(duplicates), "total_recorrencias": len(recurring),
                "cobertura_duplicidades": {"desde": date.today() - timedelta(days=DETETIVE_DUP_LOOKBACK_DAYS), "valor_minimo": DETETIVE_DUP_MIN_VALOR},
                "cobertura_recorrencias": {"fonte": "open_finance", "criterio": "recorrência detectada pela Pluggy (Recurring Payments); só despesas de conexões ativas, sem parcelas nem movimento interno"},
                "nota": "São indícios, não confirmação de erro. Nenhum lançamento foi alterado. Explicite a cobertura quando não houver achados; não exclua duplicidades fora desse período ou abaixo do valor mínimo."}
    if kind == "faria_limer":
        positions = db.list_rv_positions(user_id)
        brl = [p for p in positions if (p.get("currency") or "BRL").upper() == "BRL"]
        manual = db.list_investments(user_id, include_lots=False)
        return {"investido": _investido(user_id), "posicoes": positions[:SNAPSHOT_ITEMS_LIMIT], "resumo_brl": db.rv_portfolio_summary(user_id, positions=brl),
                "renda_fixa_brl": db.of_fixed_income_summary(user_id, currency="BRL"),
                "renda_fixa_manual": [{"name": r["name"], "balance": r["balance"], "last_date": r.get("last_date")} for r in manual[:SNAPSHOT_ITEMS_LIMIT]],
                "resumo_renda_fixa_manual_brl": {"balance": sum(r["balance"] for r in manual), "count": len(manual)},
                "cobertura": {"posicoes": _snapshot_coverage(positions), "renda_fixa_manual": _snapshot_coverage(manual)},
                "cobertura_renda_fixa": {"vale_para": "renda_fixa_brl e renda_fixa_manual (não para investido)", "moeda": "BRL", "outras_moedas_incluidas": False, "caixinhas_incluidas": False, "patrimonio_completo": False},
                "nota": "`investido` é o total dos bancos conectados, por tipo e por banco: a MESMA regra do card abaixo da resposta e do painel. Para quanto tenho, total da carteira e valor por tipo, use `investido.total_investido` e `investido.por_tipo`. `posicoes`, `resumo_brl` e `renda_fixa_brl` são recortes para composição e P&L de cada ativo: não os some como total. Investimento cadastrado à mão não está em `investido`: cite-o à parte, nunca somado. Não some moedas diferentes. Não some as listas parciais para calcular alocação; os resumos abrangem todos os registros consultados. `investido` só conta BRL e inclui a caixinha espelhada do banco (entra pela posição). Já `renda_fixa_brl` e `renda_fixa_manual` incluem apenas BRL e deixam fora a renda fixa vinculada a caixinhas: `cobertura_renda_fixa` vale para eles, não para `investido`. Zero nesses resumos não prova ausência de renda fixa nem permite concluir a alocação total. Explicite a cobertura: o cadastro pode não representar todo o patrimônio. Renda fixa serve apenas à análise de alocação; detalhes são do Barão e caixinhas são do Banqueiro. Se `investido.indisponivel` for true, o total dos bancos não pôde ser lido agora: diga isso e responda só com os recortes, sem inventar o total."}
    if kind == "barao":
        fixed_income = db.list_of_fixed_income(user_id, currency="BRL")
        # Saldos, taxas e unidades bastam; não carrega os lotes de cada aporte.
        manual = db.list_investments(user_id, include_lots=False)
        investido = _investido(user_id)
        return {"investido": investido, "total_renda_fixa": total_renda_fixa(investido),
                "renda_fixa": fixed_income[:SNAPSHOT_ITEMS_LIMIT],
                "investimentos_manuais": manual[:SNAPSHOT_ITEMS_LIMIT],
                "cobertura": {"renda_fixa": _snapshot_coverage(fixed_income), "investimentos_manuais": _snapshot_coverage(manual)},
                "cobertura_renda_fixa": {"vale_para": "as listas renda_fixa e investimentos_manuais (não para investido nem total_renda_fixa)", "moeda": "BRL", "outras_moedas_incluidas": False, "caixinhas_incluidas": False, "patrimonio_completo": False},
                "nota": "O total de renda fixa nos bancos é `total_renda_fixa`, o do card; inclui a caixinha espelhada do banco. A lista `renda_fixa` é detalhe por nome, sem a caixinha, e não é o total. Saldos e taxas são do último cadastro/sync (last_date), sem atualizar juros. Não são cotações atuais. Não prometa rendimentos. O rate cru depende de period/indexer; não interprete sem essas unidades. Detalhes de lotes não estão incluídos. Explicite a cobertura das listas; se truncadas, não trate sua soma como total nem descarte investimentos fora da amostra. As listas incluem apenas BRL e deixam fora a renda fixa vinculada a caixinhas, tema do Banqueiro; `cobertura_renda_fixa` vale só para elas. `total_renda_fixa` e `investido` só contam BRL e incluem a caixinha espelhada: nunca diga que esse total exclui caixinhas. O cadastro pode não representar todo o patrimônio. Se `investido.indisponivel` for true, o total dos bancos não pôde ser lido agora: diga isso e responda só com os recortes, sem inventar o total."}
    # Eventos só do próprio agente; não inclui alertas de outros especialistas.
    return {"alertas": db.list_agent_events(user_id, limit=20, kind=kind),
            "nota": "Alertas são históricos, confira os dados atuais nas ferramentas antes de afirmar valores atuais."}
