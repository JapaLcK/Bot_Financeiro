"""Roteamento do liga/desliga do resumo semanal e mensal (tier 2, _ALIAS_PATTERNS).

Os dois primeiros casos são os erros medidos por scripts/avaliar_intencao/avaliar.py:
"receber" não estava na lista de verbos de ativação e "para de" não estava na de
desativação, então o alias caía em report.weekly / report.monthly.

Liga/desliga por "receber" é ancorado no início da frase: só o pedido explícito
liga ou desliga. Relato de problema de entrega, dúvida e frase contrastiva nunca
ligam nem desligam — caem na consulta (read-only).
"""
import pytest

from core.intent_classifier import classify

_TOGGLES = (
    "report.weekly_enable", "report.weekly_disable",
    "report.monthly_enable", "report.monthly_disable",
)


@pytest.mark.parametrize("texto, intent", [
    ("quero receber o resumo semanal todo domingo", "report.weekly_enable"),
    ("para de mandar o resumo mensal", "report.monthly_disable"),
    ("quero receber o resumo mensal", "report.monthly_enable"),
    ("para de mandar o resumo semanal", "report.weekly_disable"),
    ("liga o resumo semanal", "report.weekly_enable"),
    ("desativa o resumo semanal", "report.weekly_disable"),
    ("resumo da semana", "report.weekly"),
    ("resumo mensal", "report.monthly"),
    # pedido explícito de parar de receber
    ("não quero receber o resumo semanal", "report.weekly_disable"),
    ("não quero mais receber o resumo semanal", "report.weekly_disable"),
    ("não desejo receber o resumo mensal", "report.monthly_disable"),
    # sinônimos de substantivo aceitos pelas outras regras de relatório
    ("não quero receber o relatório semanal", "report.weekly_disable"),
    ("quero receber o relatório mensal", "report.monthly_enable"),
])
def test_resumo_liga_desliga_roteia_intent_certo(texto, intent):
    assert classify(texto, allow_ai=False).intent == intent


@pytest.mark.parametrize("texto", [
    # problema de entrega: não é pedido de desligar
    "não consigo receber o resumo semanal",
    "não estou conseguindo receber o resumo mensal",
    # dúvida: não é pedido de ligar nem de desligar
    "não sei se quero receber o resumo semanal",
    "não tenho certeza se quero receber o resumo mensal",
    # frase contrastiva: a negação não vale para a cláusula seguinte
    "não quero o resumo mensal, quero receber o resumo semanal",
    # pergunta sobre o recebimento
    "quando vou receber o resumo mensal?",
    # "para de" como preposição, não como comando de parar
    "como faço para de novo receber o resumo semanal?",
])
def test_frase_ambigua_nao_liga_nem_desliga(texto):
    assert classify(texto, allow_ai=False).intent not in _TOGGLES
