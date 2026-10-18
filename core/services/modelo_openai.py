"""Modelo de chat da OpenAI e os kwargs de geração: uma fonte só (CLAUDE.md §0.7)."""
import os

CHAT_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
VISION_MODEL = "gpt-6-luna"  # literal: reverter a visão não depende da env do texto


def completion_options(model: str, budget: int | None, temperature: float, *, with_tools: bool = False) -> dict:
    """`budget=None`: sem teto de saída (a chamada nunca mandou `max_tokens`)."""
    if model.startswith("gpt-5.4-mini"):
        # Na integração qualificada, o provedor exige none quando há ferramentas.
        teto = {} if budget is None else {"max_completion_tokens": max(budget, 2048)}
        return {"model": model, **teto, "reasoning_effort": "none" if with_tools else "low"}
    if model == "gpt-6-luna":
        # Sondado na API em 2026-10-17: max_tokens dá 400; com effort low as ferramentas dão 400, none funciona;
        # com none o raciocínio é 0, então o teto é o budget exato e temperature vale.
        teto = {} if budget is None else {"max_completion_tokens": budget}
        return {"model": model, **teto, "reasoning_effort": "none", "temperature": temperature}
    teto = {} if budget is None else {"max_tokens": budget}
    return {"model": model, **teto, "temperature": temperature}
