"""Roteamento do liga/desliga do resumo semanal e mensal (tier 2, _ALIAS_PATTERNS).

Os dois primeiros casos são os erros medidos por scripts/avaliar_intencao/avaliar.py:
"receber" não estava na lista de verbos de ativação e "para de" não estava na de
desativação, então o alias caía em report.weekly / report.monthly.

Escopo reduzido de propósito (decisão de 2026-10-08): liga/desliga por "receber"
e "para de mandar" só casa a frase exata listada aqui, com cortesia opcional.
Variações (plural, complemento, dúvida, correção, pergunta, outro substantivo)
não ligam nem desligam: caem na consulta ou na IA.
"""
import pytest

from core.intent_classifier import classify

_TOGGLES = (
    "report.weekly_enable", "report.weekly_disable",
    "report.monthly_enable", "report.monthly_disable",
)


@pytest.mark.parametrize("texto, intent", [
    # medidos pelo avaliar.py
    ("quero receber o resumo semanal todo domingo", "report.weekly_enable"),
    ("para de mandar o resumo mensal", "report.monthly_disable"),
    # forma exata do ligar/desligar
    ("quero receber o resumo mensal", "report.monthly_enable"),
    ("desejo receber o resumo semanal", "report.weekly_enable"),
    ("para de mandar o resumo semanal", "report.weekly_disable"),
    ("não quero receber o resumo semanal", "report.weekly_disable"),
    ("eu não quero receber o resumo mensal", "report.monthly_disable"),
    ("não quero mais receber o resumo semanal", "report.weekly_disable"),
    ("não desejo receber o resumo mensal", "report.monthly_disable"),
    # cortesia antes e depois
    ("por favor, não quero receber o resumo semanal", "report.weekly_disable"),
    ("não quero receber o resumo semanal por favor", "report.weekly_disable"),
    ("quero receber o resumo mensal todo mês", "report.monthly_enable"),
    # ligar/desligar genéricos continuam como antes
    ("liga o resumo semanal", "report.weekly_enable"),
    ("desativa o resumo semanal", "report.weekly_disable"),
    ("resumo da semana", "report.weekly"),
    ("resumo mensal", "report.monthly"),
])
def test_resumo_liga_desliga_roteia_intent_certo(texto, intent):
    assert classify(texto, allow_ai=False).intent == intent


@pytest.mark.parametrize("texto", [
    # fora da forma exata: não liga nem desliga (vai para consulta ou IA)
    "não consigo receber o resumo semanal",
    "não sei se quero receber o resumo semanal",
    "não quero o resumo mensal, quero receber o resumo semanal",
    "quando vou receber o resumo mensal?",
    "quero receber o resumo semanal?",
    "não quero receber o resumo mensal?",
    "para de mandar o resumo semanal?",
    "ligar resumo semanal?",
    "desligar resumo mensal?",
    "quero receber o resumo semanal, mas não quero mais",
    "não quero receber o resumo semanal, na verdade quero sim",
    "quero receber o resumo semanal todo domingo não",
    "para de mandar o resumo semanal todo errado",
    "não quero receber os relatórios semanais",
    "não quero receber nenhum resumo semanal",
    "não quero receber o resumo semanal dos meus gastos",
    "não quero receber o resumo toda semana",
])
def test_forma_fora_do_escopo_nao_liga_nem_desliga(texto):
    assert classify(texto, allow_ai=False).intent not in _TOGGLES


def test_resumo_mensal_com_dia_chega_a_ia_e_nao_vira_recorrente(monkeypatch):
    # "resumo mensal todo dia 1" tem número e "mensal": o predicado de recorrência
    # manda para a IA antes dos aliases. O teste prova que a chamada à IA acontece
    # (cliente falso, sem rede) e que o prompt separa resumo de recorrente.
    from types import SimpleNamespace

    import openai

    from core.intent_classifier import _SYSTEM_PROMPT

    chamadas = []

    class _FakeCompletions:
        def create(self, **kwargs):
            chamadas.append(kwargs)
            conteudo = '{"intent": "report.monthly_enable", "confidence": 0.95}'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=conteudo))])

    class _FakeClient:
        def __init__(self, api_key=None):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(openai, "OpenAI", _FakeClient)

    texto = "quero receber o resumo mensal todo dia 1"
    resultado = classify(texto)  # sem allow_ai=False: precisa cair na IA

    assert len(chamadas) == 1
    assert chamadas[0]["messages"][1]["content"] == texto
    assert resultado.intent == "report.monthly_enable"
    assert "NUNCA recurring.add" in _SYSTEM_PROMPT
    assert "PONTUAL" in _SYSTEM_PROMPT  # pedido com data é consulta, não toggle
    assert "NÃO é pedido de ligar/desligar" in _SYSTEM_PROMPT  # pergunta não é toggle
