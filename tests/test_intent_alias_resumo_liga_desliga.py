"""Roteamento do liga/desliga do resumo semanal e mensal (tier 2, _ALIAS_PATTERNS).

Os dois primeiros casos são os erros medidos por scripts/avaliar_intencao/avaliar.py:
"receber" não estava na lista de verbos de ativação e "para de" não estava na de
desativação, então o alias caía em report.weekly / report.monthly.
Os demais são controles: a frase de consulta e a de liga/desliga já simples
continuam indo para o intent certo, e "receber" negado ou em pergunta nunca liga.
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


@pytest.mark.parametrize("texto, intent", [
    # opt-out com "receber" negado é desligar, não consulta nem ligar
    ("não quero receber o resumo semanal", "report.weekly_disable"),
    ("nao vou receber o resumo mensal", "report.monthly_disable"),
    ("nao recebo o resumo mensal", "report.monthly_disable"),
    ("não quero mais receber o resumo semanal", "report.weekly_disable"),
    ("não desejo receber o resumo mensal", "report.monthly_disable"),
])
def test_receber_negado_desliga_o_resumo(texto, intent):
    assert classify(texto, allow_ai=False).intent == intent


@pytest.mark.parametrize("texto, intent", [
    # a negação vale só para a cláusula dela: o "quero receber" depois é ativação
    ("não quero o resumo mensal, quero receber o resumo semanal", "report.weekly_enable"),
    ("não quero o resumo semanal mas quero receber o resumo mensal", "report.monthly_enable"),
])
def test_negacao_nao_vaza_para_a_clausula_seguinte(texto, intent):
    assert classify(texto, allow_ai=False).intent == intent


@pytest.mark.parametrize("texto", [
    # perguntas sobre o recebimento não ativam o resumo
    "quando vou receber o resumo mensal?",
    "vou receber o resumo mensal?",
    "como faço para receber o resumo mensal?",
])
def test_pergunta_sobre_recebimento_nao_liga_o_resumo(texto):
    assert classify(texto, allow_ai=False).intent not in (
        "report.weekly_enable", "report.monthly_enable")
