"""
core/services/ai_chat/tools/simulator.py — simulador de decisão de compra (Pro).

Mesma validação da rota `POST /simulator/{user_id}`
(`core.services.decision_simulator.Simulacao`).
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ._base import Tool


def _aviso_saldo(result: dict[str, Any]) -> str:
    """Saldo de partida incompleto ou não confirmado: os números parecem precisos e
    não são. Mesma distinção do card do dashboard (`_forecastBanksWarning` em
    `frontend/dashboard.js`, que é JS e não importa daqui). Vazio quando está ok."""
    if result["balance_source"] == "unavailable":
        return ("Não foi possível confirmar seu saldo consolidado agora: esta simulação pode "
                "não incluir o saldo dos seus bancos.")
    if result["banks_excluded"]:
        return ("Seus bancos conectados não estão somados nesta simulação: ela usa só o saldo "
                "da sua Carteira.")
    return ""


def _simulate_purchase(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Gate soft: abaixo do tier de `FEATURE_MIN_TIER_V2["simulator"]` (o mesmo da
    rota) devolve o convite, sem número."""
    from core.services.plan_service import plan_gate_ok
    if not plan_gate_ok(user_id, "simulator"):
        return {
            "error": "pro_required",
            "message": ("O simulador de compras faz parte do plano Pro. "
                        "Quer que eu te mostre como assinar?"),
        }
    from core.services.decision_simulator import Simulacao, simulate
    try:
        simulacao = Simulacao.model_validate(args)
    except ValidationError as exc:
        return {"error": "invalid_args",
                "errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}
    result = simulate(user_id, simulacao)
    result["aviso_saldo"] = _aviso_saldo(result)
    result["note"] = ("Se aviso_saldo não vier vazio, REPITA esse texto na resposta ANTES dos "
                      "números. O pior dia e os dias abaixo da reserva incluem hoje após a compra "
                      "e os 90 dias seguintes (91 datas). Mostre, para cada cenário, os DOIS efeitos: o saldo nos próximos 90 dias "
                      "(saldo_final_90, pior_dia, dias_abaixo_da_reserva, delta_vs_atual) e o custo "
                      "do contrato inteiro (total_pago, juros_totais, parcelas_fora_do_horizonte). "
                      "total_pago = pago_na_compra (tudo o que sai no dia da compra, custos únicos "
                      "inclusos) + todas as parcelas; NÃO inclui a despesa mensal nova, que é "
                      "recorrente e entra à parte. No à vista, entrada é 0. Se um cenário vier com "
                      "compra_fora_da_janela=true, avise que "
                      "a compra está datada depois dos 90 dias, então o saldo da janela não muda: "
                      "ali só vale o resumo do contrato. Não recomende nem aponte cenário vencedor: "
                      "a decisão é do usuário.")
    return result


_CENARIO = {
    "type": "object",
    "properties": {
        "nome": {"type": "string", "description": "Rótulo curto do cenário, ex: 'À vista', 'Entrada 36k'."},
        "preco": {"type": "number", "description": "Preço total do bem, em reais."},
        "entrada": {"type": "number", "description": "Entrada paga na data da compra. Só com parcelas > 1; omita quando for à vista."},
        "parcelas": {"type": "integer", "description": "Número de parcelas, de 2 a 420. OMITA para compra À VISTA (preço cheio na data da compra)."},
        "juros_mensal_pct": {"type": "number", "description": "Juros ao mês em %, ex: 1.49. 0 = parcelado sem juros."},
        "data_compra": {"type": "string", "description": "AAAA-MM-DD, hoje ou depois. Omitir = hoje."},
        "custos_unicos": {"type": "number", "description": "Custos de uma vez só na compra (documentação, frete)."},
        "despesa_mensal_nova": {"type": "number", "description": "Gasto mensal novo que a compra traz (seguro, manutenção)."},
    },
    "required": ["nome", "preco"],
}


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "simulate_purchase",
                "description": (
                    "Simula uma compra grande (feature Pro): à vista, parcelado sem juros ou "
                    "financiado com juros (tabela Price), comparando 1 a 3 cenários com o cenário "
                    "atual. Use quando o usuário pergunta 'e se eu comprar X?', 'compensa dar mais "
                    "entrada?', 'à vista ou parcelado?'. Não invente números: pergunte preço, entrada, "
                    "parcelas e juros que faltarem. Só o preço, sem parcelas, é à vista. Responda mostrando os dois efeitos de cada "
                    "cenário (saldo em 90 dias e custo do contrato) SEM recomendar um cenário. Se "
                    "vier 'pro_required', ofereça o upgrade com jeitinho, não despeje número nenhum."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reserva_minima": {"type": "number", "description": "Opcional: saldo mínimo que o usuário quer manter; conta os dias abaixo dele."},
                        "cenarios": {"type": "array", "items": _CENARIO, "minItems": 1, "maxItems": 3},
                    },
                    "required": ["cenarios"],
                },
            },
        },
        is_write=False,
        execute=_simulate_purchase,
    ),
]
