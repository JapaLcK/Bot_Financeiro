# core/handlers/greeting.py
"""
Responde saudações usando IA — respostas sempre variadas e naturais.
Fallback local caso a IA esteja indisponível.
"""
from __future__ import annotations

import os
import random
from datetime import datetime
from utils_text import normalize_text

# ---------------------------------------------------------------------------
# Fallback local (usado se a IA falhar)
# ---------------------------------------------------------------------------

_FALLBACK: list[str] = [
    "👋 Oi! Estou aqui pra te ajudar com suas finanças. O que posso fazer por você?",
    "😊 Olá! Que bom te ver! Posso te ajudar com gastos, saldo, cartões e muito mais.",
    "🐷 Oi! Sou o PigBank, seu assistente financeiro. Como posso ajudar hoje?",
    "☀️ Olá! Pronto pra cuidar das suas finanças? Me conta o que você precisa!",
]


def _period_of_day() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "manhã"
    if 12 <= hour < 18:
        return "tarde"
    return "noite"


def _greeting_with_ai(text: str) -> str | None:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        period = _period_of_day()
        system_prompt = (
            "Você é o PigBank AI, um assistente financeiro pessoal simpático, animado e acolhedor. "
            "O usuário acabou de te mandar uma saudação. "
            f"Agora é {period}. "
            "Responda de forma calorosa e personalizada para o período do dia. "
            "Se for manhã: transmita energia e disposição, pergunte algo como se já está pronto pra registrar o primeiro gasto do dia. "
            "Se for tarde: seja animado, pergunte se tem algo pra atualizar. "
            "Se for noite: seja mais tranquilo, sugira fechar o dia com as contas em dia. "
            "Para saudações simples (oi, olá, etc.): seja amigável, apresente-se brevemente e convide o usuário a usar o bot. "
            "Use 1 emoji no início. Seja breve (2-3 linhas no máximo). "
            "Não repita sempre a mesma estrutura — varie o estilo e as palavras. "
            "Nunca mencione que é uma IA ou que foi programado. Responda sempre em português brasileiro."
        )

        resp = client.chat.completions.create(
            model=model,
            temperature=0.9,
            max_tokens=120,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        )

        return (resp.choices[0].message.content or "").strip() or None

    except Exception as e:
        print(f"[greeting] AI error: {e}")
        return None


def handle_greeting(text: str) -> str:
    """
    Gera uma resposta de saudação via IA.
    Usa fallback local se a IA não estiver disponível.
    """
    ai_response = _greeting_with_ai(text)
    if ai_response:
        return ai_response
    return random.choice(_FALLBACK)
