"""Card com os dados do tema, abaixo da resposta de um agente (webapp: parts/CardAgente.tsx).

Só diz QUAL card (e o mês, no resumo); os números o painel lê da rota v2 do tipo (`GATES` = o gate dela), a mesma
regra do que o agente leu. O card mostra o MESMO período do texto: período que não é o da rota = sem card."""
from __future__ import annotations

import re

# (agente, consulta) -> tipo do card
CARDS = {("faria_limer", "consultar_dados_do_agente"): "investido", ("barao", "consultar_dados_do_agente"): "renda_fixa",
         ("detetive", "consultar_dados_do_agente"): "assinaturas", ("reporter", "get_period_summary"): "resumo_mes",
         ("xerife", "get_budget_status"): "orcamentos"}
# tipo -> feature do plan_gate_ok da rota v2 que o card lê; fora daqui a rota não tem gate (resumo-do-mes, orcamentos-categoria)
GATES = {"investido": "investments", "renda_fixa": "investments", "assinaturas": "subscriptions"}
MES = re.compile(r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")  # o PADRAO_MES de api/v2/resumo_mes.py


def _card(tipo: str, resultado) -> dict | None:
    if tipo == "resumo_mes":
        # get_period_summary de meses inteiros leva as datas do mês (fim = último dia) e cartao_por "fatura": é o resumo-do-mes.
        # Período livre, vários meses ou mês futuro (a rota dá 422) = sem card.
        r = resultado if isinstance(resultado, dict) else {}
        ini, fim = str(r.get("start_date"))[:7], str(r.get("end_date"))[:7]
        if r.get("cartao_por") != "fatura" or ini != fim or not MES.match(ini) or "mes_futuro" in (r.get("motivos") or []):
            return None
        return {"tipo": tipo, "mes": ini}
    if tipo == "orcamentos" and not (isinstance(resultado, dict) and resultado.get("budgets")):
        return None  # com `categoria` o texto fala de uma só (o card traria todas); sem orçamento não há o que mostrar
    return {"tipo": tipo}


def card_da_resposta(user_id: int, kind: str, consultas: list) -> dict | None:
    from core.services.plan_service import plan_gate_ok
    cards = [_card(CARDS[(kind, nome)], res) for nome, _, res in consultas if (kind, nome) in CARDS]
    # Duas consultas que dão cards diferentes (dois meses): o texto fala dos dois, nenhum card o representa.
    if not cards or any(c is None or c != cards[0] for c in cards):
        return None
    gate = GATES.get(cards[0]["tipo"])
    return None if gate and not plan_gate_ok(user_id, gate) else cards[0]
