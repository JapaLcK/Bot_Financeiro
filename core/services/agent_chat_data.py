"""Retratos de consulta dos especialistas, sem executar sincronizações."""
from datetime import date, timedelta

import db

SNAPSHOT_ITEMS_LIMIT = 100


def _snapshot_coverage(rows: list) -> dict:
    return {"total": len(rows), "incluidos": min(len(rows), SNAPSHOT_ITEMS_LIMIT),
            "truncado": len(rows) > SNAPSHOT_ITEMS_LIMIT}


def _snapshot(user_id: int, kind: str) -> dict:
    """Dados adicionais de cada especialista, sempre consultados sem disparar runners."""
    if kind == "detetive":
        from core.services.piggy_agents import (find_duplicate_charges, find_recurring_charges,
                                                DETETIVE_DUP_LOOKBACK_DAYS, DETETIVE_DUP_MIN_VALOR,
                                                DETETIVE_MIN_VALOR, DETETIVE_MIN_MESES, _detetive_cutoff)
        duplicates = find_duplicate_charges(user_id, date.today())
        recurring = find_recurring_charges(user_id, date.today())
        return {"possiveis_duplicidades": duplicates[:50], "recorrencias": recurring[:50],
                "total_duplicidades": len(duplicates), "total_recorrencias": len(recurring),
                "cobertura_duplicidades": {"desde": date.today() - timedelta(days=DETETIVE_DUP_LOOKBACK_DAYS), "valor_minimo": DETETIVE_DUP_MIN_VALOR},
                "cobertura_recorrencias": {"desde": _detetive_cutoff(date.today()), "valor_minimo": DETETIVE_MIN_VALOR, "minimo_meses": DETETIVE_MIN_MESES},
                "nota": "São indícios, não confirmação de erro. Nenhum lançamento foi alterado. Explicite a cobertura quando não houver achados; não exclua duplicidades fora desse período ou abaixo do valor mínimo."}
    if kind == "faria_limer":
        positions = db.list_rv_positions(user_id)
        brl = [p for p in positions if (p.get("currency") or "BRL").upper() == "BRL"]
        manual = db.list_investments(user_id, include_lots=False)
        return {"posicoes": positions[:SNAPSHOT_ITEMS_LIMIT], "resumo_brl": db.rv_portfolio_summary(user_id, positions=brl),
                "renda_fixa_brl": db.of_fixed_income_summary(user_id, currency="BRL"),
                "renda_fixa_manual": [{"name": r["name"], "balance": r["balance"], "last_date": r.get("last_date")} for r in manual[:SNAPSHOT_ITEMS_LIMIT]],
                "resumo_renda_fixa_manual_brl": {"balance": sum(r["balance"] for r in manual), "count": len(manual)},
                "cobertura": {"posicoes": _snapshot_coverage(positions), "renda_fixa_manual": _snapshot_coverage(manual)},
                "cobertura_renda_fixa": {"moeda": "BRL", "outras_moedas_incluidas": False, "caixinhas_incluidas": False, "patrimonio_completo": False},
                "nota": "Não some moedas diferentes. Não some as listas parciais para calcular alocação; os resumos abrangem todos os registros consultados. A renda fixa inclui apenas BRL; USD e outras moedas ficam fora, assim como a renda fixa vinculada a caixinhas. Zero nesses resumos não prova ausência de renda fixa nem permite concluir a alocação total. Explicite a cobertura: o cadastro pode não representar todo o patrimônio. Renda fixa serve apenas à análise de alocação; detalhes são do Barão e caixinhas são do Banqueiro."}
    if kind == "barao":
        fixed_income = db.list_of_fixed_income(user_id, currency="BRL")
        # Saldos, taxas e unidades bastam; não carrega os lotes de cada aporte.
        manual = db.list_investments(user_id, include_lots=False)
        return {"renda_fixa": fixed_income[:SNAPSHOT_ITEMS_LIMIT],
                "investimentos_manuais": manual[:SNAPSHOT_ITEMS_LIMIT],
                "cobertura": {"renda_fixa": _snapshot_coverage(fixed_income), "investimentos_manuais": _snapshot_coverage(manual)},
                "cobertura_renda_fixa": {"moeda": "BRL", "outras_moedas_incluidas": False, "caixinhas_incluidas": False, "patrimonio_completo": False},
                "nota": "Saldos e taxas são do último cadastro/sync (last_date), sem atualizar juros. Não são cotações atuais. Não prometa rendimentos. O rate cru depende de period/indexer; não interprete sem essas unidades. Detalhes de lotes não estão incluídos. Explicite a cobertura das listas; se truncadas, não trate sua soma como total nem descarte investimentos fora da amostra. A renda fixa inclui apenas BRL; USD e outras moedas ficam fora, assim como a renda fixa vinculada a caixinhas, tema do Banqueiro. O cadastro pode não representar todo o patrimônio."}
    # Eventos só do próprio agente; não inclui alertas de outros especialistas.
    return {"alertas": db.list_agent_events(user_id, limit=20, kind=kind),
            "nota": "Alertas são históricos, confira os dados atuais nas ferramentas antes de afirmar valores atuais."}
