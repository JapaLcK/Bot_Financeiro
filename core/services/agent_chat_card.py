"""Card com os dados do tema, abaixo da resposta de um agente (webapp: parts/CardAgente.tsx).

Só diz QUAL card (e o mês, no resumo); os números o painel lê da rota v2 do tipo (`GATES` = o gate dela), a mesma
regra do que o agente leu. O card mostra o MESMO período do texto: período que não é o da rota = sem card."""
from __future__ import annotations

import calendar
import re

from api.v2.resumo_mes import PADRAO_MES
from utils_date import now_tz

# (agente, consulta) -> tipo do card
CARDS = {("faria_limer", "consultar_dados_do_agente"): "investido", ("barao", "consultar_dados_do_agente"): "renda_fixa",
         ("detetive", "consultar_dados_do_agente"): "assinaturas", ("reporter", "get_period_summary"): "resumo_mes",
         ("reporter", "compare_periods"): "comparacao_meses",
         ("xerife", "get_budget_status"): "orcamentos", ("carteiro", "get_bills_to_pay"): "contas", ("cofre", "list_pockets"): "caixinhas"}
# tipo -> feature do plan_gate_ok da rota v2 que o card lê; fora daqui a rota não tem gate (resumo-do-mes, orcamentos-categoria,
# caixinhas, e comparacao_meses: lê o resumo-do-mes duas vezes, e a comparação só chega aqui se a tool passou do financial_comparison)
GATES = {"investido": "investments", "renda_fixa": "investments", "assinaturas": "subscriptions", "contas": "recurring_expenses"}
# Consultas que não têm período (o saldo de agora): não mudam do que o texto fala. Qualquer outra consulta fora de CARDS é de um
# período ou recorte que o card não mostra (get_spending_trend, get_category_spend…) = sem card.
SEM_PERIODO = {"get_balance"}
DIA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MES = re.compile(PADRAO_MES)  # o padrão da rota; tests/test_agent_chat_card.py prende a cópia TS (lib/agentes.ts) a ele


def _card(tipo: str, resultado, args=None) -> dict | None:
    if tipo == "contas" and ((args or {}).get("include_paid") or not (isinstance(resultado, dict) and "bills" in resultado)):
        return None  # com as pagas o texto fala de mais que o card (a rota só traz as pendentes); sem `bills` a consulta falhou
    if tipo == "contas":
        # "vence amanhã" e "vencida" dependem do dia: o card leva o dia da consulta e some se a rota já está noutro (Codex no #1200)
        hoje = resultado.get("today")
        return {"tipo": tipo, "hoje": hoje} if isinstance(hoje, str) and DIA.match(hoje) else None
    if tipo == "caixinhas" and not (isinstance(resultado, dict) and "pockets" in resultado):
        return None
    if tipo == "resumo_mes":
        # get_period_summary de meses inteiros leva as datas do mês (fim = último dia) e cartao_por "fatura": é o resumo-do-mes.
        # Período livre, vários meses ou mês futuro (a rota dá 422) = sem card.
        r = resultado if isinstance(resultado, dict) else {}
        ini, fim = str(r.get("start_date"))[:7], str(r.get("end_date"))[:7]
        if r.get("cartao_por") != "fatura" or ini != fim or not MES.match(ini) or "mes_futuro" in (r.get("motivos") or []):
            return None
        # Os avisos são os que o agente viu, não os da rota: para um mês inteiro a consulta tira o "início do histórico"
        # que só cortou o mês anterior, e a rota o mantém (ela compara com o anterior) (Manager no PR 3b).
        return {"tipo": tipo, "mes": ini, "motivos": [m for m in r.get("motivos") or [] if isinstance(m, str)]}
    if tipo == "comparacao_meses":
        return _comparacao_meses(resultado)
    if tipo == "orcamentos":
        # com `categoria` o texto fala de uma só (o card traria todas); sem orçamento não há o que mostrar; sem o mês da consulta
        # (execute_read o carimba) o card não sabe se a rota, que só serve o mês corrente, ainda é o mesmo mês
        if not (isinstance(resultado, dict) and resultado.get("budgets") and MES.match(str(resultado.get("mes")))):
            return None
        return {"tipo": tipo, "mes": resultado["mes"]}
    return {"tipo": tipo}


def _comparacao_meses(r) -> dict | None:
    """compare_periods de exatamente dois meses civis inteiros, cartão pela fatura, o mais antigo contra o mais novo: o card lê o
    resumo-do-mes dos dois e a diferença é a do texto. Período livre, lado com vários meses, ordem trocada, mês futuro, corte do plano
    (`inicio_do_historico`), sem base e lado cortado (`pedido`) = sem card. Os meses vêm das datas do resultado, nunca dos argumentos."""
    if not isinstance(r, dict) or not isinstance(r.get("base"), dict) or r["base"].get("cartao") != "fatura":
        return None
    if r.get("despesa") is None or r.get("receita") is None or r.get("motivo") in ("inicio_do_historico", "sem_base"):
        return None
    meses = []
    for lado in ("anterior", "atual"):
        w = r.get(lado)
        if not isinstance(w, dict) or "pedido" in w:
            return None
        ini, fim = str(w.get("inicio")), str(w.get("fim"))
        mes = ini[:7]
        if not MES.match(mes) or ini != f"{mes}-01" or fim != f"{mes}-{calendar.monthrange(int(mes[:4]), int(mes[5:]))[1]:02d}":
            return None  # dia 1 e último dia do mesmo mês, nada além disso
        meses.append(mes)
    hoje = now_tz()
    if not meses[0] < meses[1] <= f"{hoje.year:04d}-{hoje.month:02d}":
        return None
    return {"tipo": "comparacao_meses", "meses": meses, "motivos": [m for m in r.get("motivos") or [] if isinstance(m, str)]}


def card_da_resposta(user_id: int, kind: str, consultas: list) -> dict | None:
    from core.services.plan_service import plan_gate_ok
    if any((kind, nome) not in CARDS and nome not in SEM_PERIODO for nome, _, _ in consultas):
        return None
    cards = [_card(CARDS[(kind, nome)], res, args) for nome, args, res in consultas if (kind, nome) in CARDS]
    # Duas consultas que dão cards diferentes (dois meses): o texto fala dos dois, nenhum card o representa.
    if not cards or any(c is None or c != cards[0] for c in cards):
        return None
    gate = GATES.get(cards[0]["tipo"])
    return None if gate and not plan_gate_ok(user_id, gate) else cards[0]
