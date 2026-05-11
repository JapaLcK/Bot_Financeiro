"""
core/services/ai_chat/runner.py — orquestração do chat conversacional com IA.

Fluxo:
  1. User manda texto → chat(user_id, text) -> str
  2. Se há pending action → checa se o texto é confirmação/cancelamento. Se for,
     executa/cancela e retorna direto (não chama OpenAI).
  3. Senão, salva msg do user, monta contexto (últimas N msgs) + system prompt,
     chama OpenAI com tools.
  4. Loop function calling:
       - Read tool → executa, devolve resultado pra IA continuar.
       - Write tool → NÃO executa. Vira pending action. IA é informada e
         responde com template 3 ("vou X, confirma?").
  5. Resposta final é salva como assistant message e retornada.

Regra de ouro: writes SEMPRE pedem confirmação humana antes de executar.

Tools, system prompt, detecção de confirma/cancela e saneamento de histórico
estão em módulos próprios. Este arquivo só orquestra.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any

import db

from .confirmations import is_cancel, is_confirm
from .history import trim_history_for_openai
from .system_prompt import SYSTEM_PROMPT
from .tools import SCHEMAS, WRITE_TOOL_NAMES, get_tool


logger = logging.getLogger(__name__)


MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE = 0.3
MAX_TOOL_LOOPS = 6


LIMIT_MSG_TEMPLATE = (
    "🐷 Você usou todas suas {limit} perguntas de IA esse mês.\n"
    "Zera no dia 1. Enquanto isso, dá pra usar o dashboard: https://pigbankai.com/app"
)


ERROR_MSG = (
    "🐷 Deu ruim aqui — tenta de novo. Se persistir, fala com a gente: suporte@pigbankai.com"
)


def chat(user_id: int, user_text: str, *, monthly_limit: int = 1000) -> str:
    """
    Processa uma mensagem do user e retorna a resposta da IA.

    NÃO checa plano Pro — quem chama (endpoint / bot) que decide se gateia.
    Aplica rate limit mensal aqui (incrementa contador APÓS resposta bem-sucedida).
    """
    user_id = int(user_id)
    user_text = (user_text or "").strip()
    if not user_text:
        return "🐷 Manda sua pergunta aí, tô ouvindo."

    # 1. Pending action? Processa primeiro.
    pending = db.ai_get_pending_action(user_id)
    if pending:
        if is_confirm(user_text):
            result = _execute_pending(user_id, pending)
            db.ai_clear_pending_action(user_id)
            db.ai_append_message(user_id, "user", user_text)
            db.ai_append_message(user_id, "assistant", result)
            return result
        if is_cancel(user_text):
            db.ai_clear_pending_action(user_id)
            msg = "👍 Beleza, não fiz nada."
            db.ai_append_message(user_id, "user", user_text)
            db.ai_append_message(user_id, "assistant", msg)
            return msg
        # User mudou de assunto — descarta pending e segue com nova msg
        db.ai_clear_pending_action(user_id)

    # 2. Rate limit mensal
    used = db.ai_get_usage_this_month(user_id)
    if used >= monthly_limit:
        return LIMIT_MSG_TEMPLATE.format(limit=monthly_limit)

    # 3. Salva msg do user
    db.ai_append_message(user_id, "user", user_text)

    # 4. Monta contexto + chama OpenAI
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY ausente — chat IA indisponível")
        return ERROR_MSG

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
    except Exception as e:
        logger.error("falha ao inicializar OpenAI: %s", e)
        return ERROR_MSG

    history = db.ai_get_recent_messages(user_id, limit=db.AI_DEFAULT_CONTEXT_WINDOW)
    history = trim_history_for_openai(history)

    today_str = date.today().strftime("%d/%m/%Y")
    system_with_date = SYSTEM_PROMPT + f"\n\nData de hoje: {today_str}."

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_with_date}] + history

    final_text = _run_tool_loop(client, user_id, messages)

    db.ai_append_message(user_id, "assistant", final_text)
    db.ai_increment_usage(user_id)
    return final_text


def _execute_pending(user_id: int, pending: dict[str, Any]) -> str:
    """Executa a ação pendente e retorna mensagem de sucesso (template 4)."""
    name = pending["tool_name"]
    args = pending["tool_args"]

    tool = get_tool(name)
    if tool is None or not tool.is_write:
        logger.error("pending invalido: tool %s nao encontrada ou nao-write", name)
        return "🐷 Não consegui completar essa ação."

    try:
        return tool.execute(user_id, args)
    except Exception as e:
        logger.error("erro ao executar pending %s: %s", name, e)
        return ERROR_MSG


def _run_tool_loop(client, user_id: int, messages: list[dict[str, Any]]) -> str:
    for _ in range(MAX_TOOL_LOOPS):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                messages=messages,
                tools=SCHEMAS,
            )
        except Exception as e:
            logger.error("erro na chamada OpenAI: %s", e)
            return ERROR_MSG

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            return (msg.content or "").strip() or ERROR_MSG

        # Persistir assistant message com tool_calls
        tool_calls_dicts = [tc.model_dump() for tc in tool_calls]
        db.ai_append_message(
            user_id,
            "assistant",
            msg.content,
            tool_calls=tool_calls_dicts,
        )
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": tool_calls_dicts,
        })

        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            tool_result = _dispatch_tool(user_id, name, args)

            db.ai_append_message(
                user_id,
                "tool",
                tool_result,
                tool_call_id=tc.id,
                tool_name=name,
            )
            messages.append({
                "role": "tool",
                "content": tool_result,
                "tool_call_id": tc.id,
                "name": name,
            })

    logger.warning("MAX_TOOL_LOOPS atingido pra user %s", user_id)
    return ERROR_MSG


def _dispatch_tool(user_id: int, name: str, args: dict[str, Any]) -> str:
    """
    Executa a tool ou cria pending action (se for write).
    Retorna sempre uma string JSON pronta pra virar `tool` message na OpenAI.
    """
    tool = get_tool(name)
    if tool is None:
        return json.dumps({"error": f"tool desconhecida: {name}"}, ensure_ascii=False)

    if tool.is_write:
        summary_fn = tool.summary
        summary = summary_fn(args) if summary_fn else f"executar {name} com {args}"
        db.ai_set_pending_action(user_id, name, args, summary)
        return json.dumps(
            {
                "status": "pending_user_confirmation",
                "summary": summary,
                "args": args,
                "instruction": "Use o template 3 para mostrar o resumo ao user e pedir 'sim' ou 'não'. NÃO confirme automaticamente.",
            },
            ensure_ascii=False,
        )

    try:
        result = tool.execute(user_id, args)
    except Exception as e:
        logger.error("erro em read tool %s: %s", name, e)
        result = {"error": str(e)}
    return json.dumps(result, ensure_ascii=False, default=str)


__all__ = ["chat"]
