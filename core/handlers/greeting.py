# core/handlers/greeting.py
"""
Respostas de saudação com rotação de mensagens.
Cobre: bom dia, boa tarde, boa noite, oi, olá, alô, e variações.
"""
from __future__ import annotations

import random
from utils_text import normalize_text

# ---------------------------------------------------------------------------
# Banco de mensagens — rotação aleatória por tipo de saudação
# ---------------------------------------------------------------------------

_BOM_DIA = [
    "☀️ Bom dia! Espero que seu dia comece cheio de energia!\nEstá pronto pra registrar o primeiro gasto do dia? 💰",
    "🌅 Bom diaaaa! Que o dia de hoje seja produtivo e sem surpresas no bolso! 😄\nQuer já começar anotando alguma coisa?",
    "☕ Bom dia! Café na mão e finanças em dia — essa é a combinação perfeita!\nO que vamos registrar hoje?",
    "🌞 Bom dia! Começando o dia do jeito certo: de olho no dinheiro! 💪\nJá tem algum gasto pra anotar?",
    "✨ Bom dia! Cada dia é uma nova chance de cuidar melhor das suas finanças.\nVamos lá, o que aconteceu hoje cedo?",
]

_BOA_TARDE = [
    "🌤️ Boa tarde! A tarde chegou — hora de conferir como o dia está indo financeiramente! 📊\nTem algum gasto pra registrar?",
    "☀️ Boa tarde! Espero que a tarde esteja sendo boa pro seu bolso também! 😄\nO que posso anotar pra você?",
    "🌻 Boa tarde! Já passou da metade do dia — ótimo momento pra atualizar as contas!\nTem algo pra registrar?",
    "🍃 Boa tarde! Que bom te ver por aqui. Finanças em dia é sinônimo de paz de espírito! 🙌\nVamos registrar alguma coisa?",
    "🌈 Boa tarde! A tarde é perfeita pra fazer um check das suas finanças.\nAlgum gasto ou receita pra anotar?",
]

_BOA_NOITE = [
    "🌙 Boa noite! Hora de fechar o dia com as contas em dia! 📒\nTem algum gasto do dia que ainda não foi registrado?",
    "⭐ Boa noite! Antes de descansar, que tal checar como o dia foi financeiramente? 💤\nO que aconteceu hoje?",
    "🌜 Boa noite! Encerrar o dia sabendo onde foi cada real — isso é saúde financeira de verdade! 😊\nAlguma coisa pra anotar antes de dormir?",
    "✨ Boa noite! Final do dia é momento de balanço. Quer ver o resumo de hoje ou registrar algo?",
    "🌟 Boa noite! Que seu descanso seja tranquilo! Mas antes — tem algum gasto do dia pra registrar? 😄",
]

_OI = [
    "👋 Oi! Tudo bem? Estou aqui pra te ajudar a cuidar das suas finanças!\nO que posso fazer por você hoje?",
    "😊 Oi! Boa sorte em que eu possa ajudar hoje!\nQuer registrar um gasto, ver seu saldo ou consultar alguma coisa?",
    "🐷 Oi! Sou o PigBank, seu assistente financeiro pessoal!\nComo posso te ajudar agora?",
    "👋 Oi! Que bom te ver por aqui! Pronto pra organizar as finanças?\nMe diz o que você precisa!",
    "😄 Oi! Tô aqui e pronto! O que vamos fazer hoje com as suas finanças?",
]

_OLA = [
    "😊 Olá! Bem-vindo! Estou aqui pra te ajudar com suas finanças pessoais.\nO que posso fazer por você?",
    "🐷 Olá! Sou o PigBank AI. Posso te ajudar a registrar gastos, ver saldo, faturas e muito mais!\nPor onde começamos?",
    "👋 Olá! Que bom ter você aqui! Me conta, o que você precisa hoje?",
    "🌟 Olá! Pronto pra colocar as finanças em ordem? Pode perguntar à vontade!\nDigite *ajuda* pra ver tudo que posso fazer.",
    "😄 Olá! Tô aqui pra facilitar a sua vida financeira! O que vamos fazer?",
]

# ---------------------------------------------------------------------------
# Mapeamento de padrões normalizados → pool de mensagens
# ---------------------------------------------------------------------------

_GREETING_POOLS: list[tuple[tuple[str, ...], list[str]]] = [
    (("bom dia",),                      _BOM_DIA),
    (("boa tarde",),                     _BOA_TARDE),
    (("boa noite",),                     _BOA_NOITE),
    (("ola", "alo", "hello", "hey"),     _OLA),
    (("oi", "oie", "oii", "oiii"),       _OI),
]


def handle_greeting(text: str) -> str | None:
    """
    Retorna uma saudação aleatória se o texto for uma saudação conhecida.
    Retorna None se não for saudação — deixa o router continuar normalmente.
    """
    norm = normalize_text((text or "").strip())

    for triggers, pool in _GREETING_POOLS:
        if norm in triggers:
            return random.choice(pool)

    return None
