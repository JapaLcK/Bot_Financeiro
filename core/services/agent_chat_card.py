"""Card com os dados do tema, abaixo da resposta de um agente (webapp: parts/CardAgente.tsx).

Só diz QUAL card; os números o painel lê de /api/v2/investido, a mesma regra do snapshot do agente."""
from __future__ import annotations

# (agente, consulta) -> tipo do card
CARDS = {("faria_limer", "consultar_dados_do_agente"): "investido", ("barao", "consultar_dados_do_agente"): "renda_fixa"}


def card_da_resposta(user_id: int, kind: str, consultas: list) -> dict | None:
    from core.services.plan_service import plan_gate_ok
    tipo = next((CARDS[(kind, nome)] for nome, _, _ in consultas if (kind, nome) in CARDS), None)
    if tipo is None or not plan_gate_ok(user_id, "investments"):  # o gate de /api/v2/investido
        return None
    return {"tipo": tipo}
