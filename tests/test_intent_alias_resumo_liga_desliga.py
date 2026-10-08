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
    # a cadência repetida no desligar não impede o desligar
    ("não quero mais receber o resumo semanal todo domingo", "report.weekly_disable"),
    ("não quero receber o resumo mensal todo mês", "report.monthly_disable"),
    # "eu" e cadência por dia da semana (normalizada de "segunda-feira")
    ("eu não quero receber o resumo semanal", "report.weekly_disable"),
    ("não quero receber o resumo semanal toda segunda-feira", "report.weekly_disable"),
    ("quero receber o resumo semanal toda segunda-feira", "report.weekly_enable"),
    # a cadência sozinha basta como período ("toda semana", "todo mês")
    ("não quero receber o resumo toda semana", "report.weekly_disable"),
    ("quero receber o resumo todo mês", "report.monthly_enable"),
    ("não quero receber o resumo todo mês", "report.monthly_disable"),
    # pronome objeto entre "de" e o verbo
    ("para de me mandar o resumo semanal", "report.weekly_disable"),
    ("para de me enviar o resumo mensal", "report.monthly_disable"),
    # determinante possessivo e dia da semana como período
    ("não quero receber meu resumo semanal", "report.weekly_disable"),
    ("para de me mandar meu resumo mensal", "report.monthly_disable"),
    ("não quero receber o meu resumo semanal", "report.weekly_disable"),
    # plural
    ("não quero receber os relatórios semanais", "report.weekly_disable"),
    ("para de me mandar os resumos mensais", "report.monthly_disable"),
    ("não quero receber o resumo semanal todos os domingos", "report.weekly_disable"),
    # determinante negativo só no desligar: "nenhum" no ligar seria contradição
    ("não quero receber nenhum resumo semanal", "report.weekly_disable"),
    ("não quero receber nenhum relatório mensal", "report.monthly_disable"),
    # cadência no plural sozinha como período
    ("não quero receber o resumo todas as semanas", "report.weekly_disable"),
    ("quero receber o resumo todos os meses", "report.monthly_enable"),
    ("quero receber o resumo mensal todos os meses", "report.monthly_enable"),
    ("para de me mandar mais o resumo semanal", "report.weekly_disable"),
    ("para de receber mais o resumo mensal", "report.monthly_disable"),
    ("para de me mandar o meu resumo mensal", "report.monthly_disable"),
    ("não quero receber o resumo toda segunda-feira", "report.weekly_disable"),
    # cortesia antes do comando também não muda o pedido
    ("por favor, não quero receber o resumo semanal", "report.weekly_disable"),
    ("por favor quero receber o resumo mensal", "report.monthly_enable"),
    # cortesia no fim não muda o pedido
    ("não quero receber o resumo semanal por favor", "report.weekly_disable"),
    ("para de mandar o resumo mensal por favor", "report.monthly_disable"),
    ("quero receber o resumo semanal por favor", "report.weekly_enable"),
    # "mais" depois do verbo também é pedido de parar
    ("não quero receber mais o resumo semanal", "report.weekly_disable"),
    ("não desejo receber mais o relatório mensal", "report.monthly_disable"),
    # formas de período por substantivo, como "resumo da semana" e "resumo do mes"
    ("não quero receber o resumo da semana", "report.weekly_disable"),
    ("quero receber o resumo do mês", "report.monthly_enable"),
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
    # cadência só com dia/período reconhecido: "todo errado" é queixa, não cadência
    "para de mandar o resumo semanal todo errado",
    "para de mandar o resumo mensal todo quebrado",
    # correção no fim da frase: o comando inteiro não é o pedido
    "quero receber o resumo semanal, mas não quero mais",
    "não quero receber o resumo semanal, na verdade quero sim",
    # negação colada na cadência não é cadência
    "quero receber o resumo semanal todo domingo não",
    # pergunta sobre o recebimento
    "quando vou receber o resumo mensal?",
    # "para de" como preposição, não como comando de parar
    "como faço para de novo receber o resumo semanal?",
])
def test_frase_ambigua_nao_liga_nem_desliga(texto):
    assert classify(texto, allow_ai=False).intent not in _TOGGLES
