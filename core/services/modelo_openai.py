"""Modelo de chat da OpenAI e os kwargs de geração: uma fonte só (CLAUDE.md §0.7)."""
import os

CHAT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
VISION_MODEL = "gpt-4o"


def completion_options(model: str, budget: int | None, temperature: float, *, with_tools: bool = False) -> dict:
    """`budget=None`: sem teto de saída (a chamada nunca mandou `max_tokens`)."""
    if model.startswith("gpt-5.4-mini"):
        # Na integração qualificada, o provedor exige none quando há ferramentas.
        teto = {} if budget is None else {"max_completion_tokens": max(budget, 2048)}
        return {"model": model, **teto, "reasoning_effort": "none" if with_tools else "low"}
    teto = {} if budget is None else {"max_tokens": budget}
    return {"model": model, **teto, "temperature": temperature}
