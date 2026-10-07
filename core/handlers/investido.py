"""core/handlers/investido.py — o "meus investimentos" do WhatsApp (`investments.list`).

Lê a regra única `db/investido.py` (a mesma do painel e da tool da IA): só o que está
nos bancos conectados. Investimento cadastrado à mão não aparece aqui (fica no banco
de dados e nos fluxos de aporte/resgate de `core/handlers/investments.py`).
"""
from __future__ import annotations

from core.handlers.investments import _investment_dashboard_link
from db import investido
from utils_text import fmt_brl


def _linha(nome: str, valor) -> str:
    return f"• {nome} — {'saldo não informado pelo banco' if valor is None else fmt_brl(valor)}"


def carteira(user_id: int) -> str:
    r = investido.ler(user_id)
    if r["total"] is None:
        porque = ("conecte seu banco no painel para eu ver" if "sem_banco_conectado" in r["motivos"]
                  else "seu banco ainda não terminou a primeira atualização")
        return f"Ainda não sei quanto você tem investido: {porque}.\n\n{_investment_dashboard_link(user_id)}"
    blocos = [f"📈 **{fmt_brl(r['total'])}** investidos nos bancos conectados"]
    if r["por_tipo"]:
        blocos.append("Por tipo:\n" + "\n".join(_linha(p["rotulo"], p["valor"]) for p in r["por_tipo"]))
    if r["por_banco"]:
        blocos.append("Por banco:\n" + "\n".join(_linha(p["banco"], p["valor"]) for p in r["por_banco"]))
    if r["motivos"]:
        blocos.append("_O valor pode estar desatualizado ou incompleto; confira no painel._")
    blocos.append(_investment_dashboard_link(user_id))
    return "\n\n".join(blocos)
