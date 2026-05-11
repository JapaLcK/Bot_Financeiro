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

from ._context import CURRENT_PLATFORM
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


def chat(
    user_id: int,
    user_text: str,
    *,
    monthly_limit: int = 1000,
    platform: str = "dashboard",
) -> str:
    """
    Processa uma mensagem do user e retorna a resposta da IA.

    `platform` é propagada via contextvar pras tools que se comportam
    diferente por canal (ex: `add_launch` aciona pending action de botão
    "trocar categoria" só em `platform="whatsapp"`).

    NÃO checa plano Pro — quem chama (endpoint / bot) que decide se gateia.
    Aplica rate limit mensal aqui (incrementa contador APÓS resposta bem-sucedida).
    """
    token = CURRENT_PLATFORM.set(platform)
    try:
        return _chat_inner(user_id, user_text, monthly_limit=monthly_limit)
    finally:
        CURRENT_PLATFORM.reset(token)


def _chat_inner(user_id: int, user_text: str, *, monthly_limit: int) -> str:
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

        terminal_msg: str | None = None
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}

            history_content, this_terminal = _dispatch_tool(user_id, name, args)

            db.ai_append_message(
                user_id,
                "tool",
                history_content,
                tool_call_id=tc.id,
                tool_name=name,
            )
            messages.append({
                "role": "tool",
                "content": history_content,
                "tool_call_id": tc.id,
                "name": name,
            })

            if this_terminal is not None and terminal_msg is None:
                terminal_msg = this_terminal

        # Write auto-executado entrega a resposta final sem 2º round-trip.
        # Se múltiplas tools rodaram no mesmo turno, vence a primeira terminal.
        if terminal_msg is not None:
            return terminal_msg

    logger.warning("MAX_TOOL_LOOPS atingido pra user %s", user_id)
    return ERROR_MSG


def _dispatch_tool(user_id: int, name: str, args: dict[str, Any]) -> tuple[str, str | None]:
    """
    Despacha a tool e retorna (history_content, terminal_msg).

      history_content: string que vira a `tool` message no histórico/OpenAI.
      terminal_msg: se não-None, é a resposta final pro user — o runner sai
        do loop sem chamar OpenAI de novo. Usado por writes auto-executados
        (`is_write=True, requires_confirmation=False`).
    """
    tool = get_tool(name)
    if tool is None:
        return (
            json.dumps({"error": f"tool desconhecida: {name}"}, ensure_ascii=False),
            None,
        )

    if tool.is_write and tool.requires_confirmation:
        # Pre-check: se a tool define validate() e ele retorna erro, pula a
        # confirmação e mostra direto pro user. Evita IA pedir "confirma
        # apagar #X?" pra X que ela inventou e nem existe.
        if tool.validate is not None:
            err = tool.validate(user_id, args)
            if err:
                history = json.dumps(
                    {"status": "validation_failed", "message": err},
                    ensure_ascii=False,
                )
                return (history, err)

        summary_fn = tool.summary
        summary = summary_fn(args) if summary_fn else f"executar {name} com {args}"
        db.ai_set_pending_action(user_id, name, args, summary)
        return (
            json.dumps(
                {
                    "status": "pending_user_confirmation",
                    "summary": summary,
                    "args": args,
                    "instruction": "Use o template 3 para mostrar o resumo ao user e pedir 'sim' ou 'não'. NÃO confirme automaticamente.",
                },
                ensure_ascii=False,
            ),
            None,
        )

    if tool.is_write:
        # Auto-execute: ação rolou; a mensagem retornada é a resposta final.
        try:
            user_msg = tool.execute(user_id, args)
        except Exception as e:
            logger.error("erro em auto-write %s: %s", name, e)
            return (
                json.dumps({"error": str(e)}, ensure_ascii=False),
                ERROR_MSG,
            )
        history = json.dumps(
            {"status": "done", "message": user_msg}, ensure_ascii=False, default=str
        )
        return (history, user_msg if isinstance(user_msg, str) else str(user_msg))

    # Read tool
    try:
        result = tool.execute(user_id, args)
    except Exception as e:
        logger.error("erro em read tool %s: %s", name, e)
        result = {"error": str(e)}
    return (json.dumps(result, ensure_ascii=False, default=str), None)


__all__ = ["chat"]
