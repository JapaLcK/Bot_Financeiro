# core/handlers/greeting.py
"""
Responde saudações usando IA — respostas sempre variadas e naturais.
Fallback local por tipo de saudação caso a IA esteja indisponível.
"""
from __future__ import annotations

import logging
import os
import random
from datetime import datetime
from utils_text import normalize_text

logger = logging.getLogger(__name__)


def _get_display_name(user_id: int | None) -> str | None:
    """Retorna o nome de exibição do usuário (ou None se indisponível)."""
    if not user_id:
        return None
    try:
        from db import get_auth_user
        user = get_auth_user(int(user_id))
        if not user:
            return None
        name = (user.get("display_name") or "").strip()
        if not name:
            return None
        # usa só o primeiro nome para soar natural numa saudação
        return name.split()[0]
    except Exception as exc:
        logger.debug("greeting display name lookup failed: %s", exc)
        return None


def _personalize(message: str, name: str | None) -> str:
    """Insere o nome após a primeira saudação detectada na mensagem.
    Tenta ser sutil: 'Bom dia!' -> 'Bom dia, Lucas!'.
    """
    if not name:
        return message
    import re
    pattern = re.compile(
        r"(Bom dia|Boa tarde|Boa noite|Olá|Oi|Alô|Hey|Hello)([!?,.])",
        re.IGNORECASE,
    )

    def _sub(m):
        return f"{m.group(1)}, {name}{m.group(2)}"

    new, n = pattern.subn(_sub, message, count=1)
    return new if n else message

# ---------------------------------------------------------------------------
# Fallback local por tipo — usado se a IA falhar
# ---------------------------------------------------------------------------

_FALLBACK_BOM_DIA = [
    "☀️ Bom dia! Que seu dia comece cheio de energia!\nJá tem algum gasto pra registrar?",
    "🌅 Bom dia! Hora de começar o dia com as finanças em ordem!\nO que posso anotar pra você?",
    "☕ Bom dia! Café na mão e finanças em dia — combinação perfeita!\nVamos lá, o que aconteceu hoje cedo?",
    "🌞 Bom dia! Começando o dia do jeito certo: de olho no bolso! 💪\nTem algum gasto pra anotar?",
]

_FALLBACK_BOA_TARDE = [
    "🌤️ Boa tarde! Hora de ver como o dia está indo financeiramente!\nTem algum gasto pra registrar?",
    "🌻 Boa tarde! Já passou da metade do dia — bom momento pra atualizar as contas!\nAlguma coisa pra anotar?",
    "☀️ Boa tarde! Espero que esteja sendo um bom dia pro seu bolso também!\nO que posso fazer por você?",
    "🍃 Boa tarde! Finanças em dia é sinônimo de paz de espírito 🙌\nVamos registrar alguma coisa?",
]

_FALLBACK_BOA_NOITE = [
    "🌙 Boa noite! Hora de fechar o dia com as contas em dia!\nTem algum gasto que ainda não foi registrado?",
    "⭐ Boa noite! Antes de descansar, que tal um balanço do dia?\nO que aconteceu hoje?",
    "🌜 Boa noite! Final do dia é momento de balanço.\nQuer ver o resumo de hoje ou registrar algo?",
    "✨ Boa noite! Encerrar o dia sabendo onde foi cada real — isso é saúde financeira! 😊\nAlguma coisa pra anotar?",
]

_FALLBACK_OI = [
    "👋 Oi! Estou aqui pra te ajudar com suas finanças!\nO que posso fazer por você?",
    "😊 Oi! Que bom te ver por aqui! Pronto pra organizar as finanças?\nMe conta o que você precisa.",
    "🐷 Oi! Sou o Piggy, seu assistente financeiro pessoal!\nComo posso te ajudar agora?",
    "😄 Oi! Tô aqui e pronto! O que vamos fazer hoje com as suas finanças?",
]

_FALLBACK_OLA = [
    "😊 Olá! Bem-vindo! Posso te ajudar com gastos, saldo, cartões e muito mais!\nPor onde começamos?",
    "🐷 Olá! Sou o Piggy — aqui pra facilitar sua vida financeira!\nO que você precisa?",
    "👋 Olá! Que bom ter você aqui. Me conta, o que posso fazer por você hoje?",
    "🌟 Olá! Pronto pra colocar as finanças em ordem?\nDigite *ajuda* pra ver tudo que posso fazer.",
]


def _detect_greeting_type(norm: str) -> str:
    """Classifica o tipo de saudação a partir do texto normalizado."""
    if norm.startswith("bom dia"):
        return "bom_dia"
    if norm.startswith("boa tarde"):
        return "boa_tarde"
    if norm.startswith("boa noite"):
        return "boa_noite"
    if any(norm.startswith(p) for p in ("oi", "oie")):
        return "oi"
    return "ola"  # olá, alô, hello, hey, eai, opa...


def _period_of_day() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "manhã"
    if 12 <= hour < 18:
        return "tarde"
    return "noite"


def _fallback_for_type(greeting_type: str) -> str:
    pools = {
        "bom_dia":   _FALLBACK_BOM_DIA,
        "boa_tarde": _FALLBACK_BOA_TARDE,
        "boa_noite": _FALLBACK_BOA_NOITE,
        "oi":        _FALLBACK_OI,
        "ola":       _FALLBACK_OLA,
    }
    return random.choice(pools.get(greeting_type, _FALLBACK_OI))


def _greeting_with_ai(text: str, greeting_type: str, name: str | None = None) -> str | None:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        period = _period_of_day()

        # Instrução específica por tipo de saudação
        type_instructions = {
            "bom_dia": (
                "O usuário mandou 'bom dia'. Responda com BOM DIA de volta — "
                "seja energético e animado, transmita disposição para o começo do dia. "
                "Faça uma pergunta leve sobre registrar o primeiro gasto ou ver o saldo do dia."
            ),
            "boa_tarde": (
                "O usuário mandou 'boa tarde'. Responda com BOA TARDE de volta — "
                "seja animado e receptivo, pergunte se tem alguma movimentação do dia pra atualizar "
                "ou se quer checar como estão as finanças."
            ),
            "boa_noite": (
                "O usuário mandou 'boa noite'. Responda com BOA NOITE de volta — "
                "seja mais tranquilo e acolhedor, sugira fechar o dia vendo o resumo ou registrando "
                "algum gasto que ainda não foi anotado."
            ),
            "oi": (
                "O usuário mandou um 'oi' (ou variação). Responda de forma descontraída e animada. "
                "Apresente-se como Piggy e convide o usuário a usar o assistente financeiro."
            ),
            "ola": (
                "O usuário mandou um 'olá' (ou variação). Responda de forma amigável e acolhedora. "
                "Apresente-se como Piggy e diga que pode ajudar com gastos, saldo, cartões e mais."
            ),
        }

        instruction = type_instructions.get(greeting_type, type_instructions["ola"])

        name_clause = (
            f"O nome do usuário é {name}. Inclua o nome dele logo após a saudação inicial "
            f"de forma natural (ex: 'Bom dia, {name}!'). Não repita o nome mais de uma vez. "
            if name
            else ""
        )

        system_prompt = (
            "Você é o Piggy, um assistente financeiro pessoal brasileiro — simpático, animado e acolhedor. "
            f"{instruction} "
            f"{name_clause}"
            f"O período do dia atual é: {period}. "
            "Use 1 emoji no início da mensagem. Seja breve: máximo 2-3 linhas. "
            "IMPORTANTE: varie o estilo, vocabulário e estrutura — nunca gere duas respostas iguais. "
            "Nunca diga que é uma IA ou que foi programado. Responda SEMPRE em português brasileiro informal."
        )

        resp = client.chat.completions.create(
            model=model,
            temperature=1.0,
            max_tokens=100,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )

        result = (resp.choices[0].message.content or "").strip()
        return result if result else None

    except Exception as e:
        logger.warning("greeting AI error: %s", e)
        return None


def handle_greeting(text: str, user_id: int | None = None) -> str:
    """
    Gera uma resposta de saudação via IA, personalizando com o nome do usuário
    quando disponível. Usa fallback local específico para o tipo de saudação
    se a IA falhar.
    """
    norm = normalize_text((text or "").strip())
    greeting_type = _detect_greeting_type(norm)

    name = _get_display_name(user_id)

    ai_response = _greeting_with_ai(text, greeting_type, name=name)
    if ai_response:
        return ai_response

    return _personalize(_fallback_for_type(greeting_type), name)
