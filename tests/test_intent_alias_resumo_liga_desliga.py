"""Roteamento do liga/desliga do resumo semanal e mensal (tier 2, _ALIAS_PATTERNS).

Os dois primeiros casos são os erros medidos por scripts/avaliar_intencao/avaliar.py:
"receber" não estava na lista de verbos de ativação e "para de" não estava na de
desativação, então o alias caía em report.weekly / report.monthly.
Os demais são controles: a frase de consulta e a de liga/desliga já simples
continuam indo para o intent certo.
"""
import pytest

from core.intent_classifier import classify


@pytest.mark.parametrize("texto, intent", [
    ("quero receber o resumo semanal todo domingo", "report.weekly_enable"),
    ("para de mandar o resumo mensal", "report.monthly_disable"),
    ("quero receber o resumo mensal", "report.monthly_enable"),
    ("para de mandar o resumo semanal", "report.weekly_disable"),
    ("liga o resumo semanal", "report.weekly_enable"),
    ("desativa o resumo semanal", "report.weekly_disable"),
    ("resumo da semana", "report.weekly"),
    ("resumo mensal", "report.monthly"),
])
def test_resumo_liga_desliga_roteia_intent_certo(texto, intent):
    assert classify(texto, allow_ai=False).intent == intent


@pytest.mark.parametrize("texto", [
    "não quero receber o resumo semanal",
    "nao vou receber o resumo mensal",
    "nao recebo o resumo mensal",
    "não quero mais receber o resumo semanal",
    "não desejo receber o resumo mensal",
])
def test_negacao_de_receber_nao_liga_o_resumo(texto):
    # "receber" negado é pedido para parar, nunca para ligar.
    assert classify(texto, allow_ai=False).intent not in (
        "report.weekly_enable", "report.monthly_enable")
