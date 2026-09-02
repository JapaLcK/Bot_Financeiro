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
import unicodedata
from datetime import date
from typing import Any

import db

from ._context import CURRENT_PLATFORM, CURRENT_USER_MESSAGE
from .confirmations import is_cancel, is_confirm
from .history import trim_history_for_openai
from .sanitizer import detect_trend_window, strip_markdown_headers
from .system_prompt import SYSTEM_PROMPT
from .tools import SCHEMAS, WRITE_TOOL_NAMES, get_tool


logger = logging.getLogger(__name__)


MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
TEMPERATURE = 0.3
MAX_TOOL_LOOPS = 6
# Teto de tokens de saída por chamada — impede que uma resposta (ou um prompt
# abusivo) gere saída ilimitada e estoure a conta de LLM. Respostas do chat são
# curtas; 1024 é folgado. Ajustável por env.
MAX_TOKENS = int(os.getenv("AI_CHAT_MAX_TOKENS", "1024"))

# Sem timeout o SDK usa read=600s × 2 retries: uma lentidão/rate-limit da
# OpenAI vira spinner infinito no widget. Caps p/ cair em ERROR_MSG em segundos.
OPENAI_TIMEOUT = float(os.getenv("OPENAI_CHAT_TIMEOUT", "30"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_CHAT_MAX_RETRIES", "1"))


LIMIT_MSG_TEMPLATE = (
    "🐷 Você usou todas suas {limit} perguntas de IA esse mês.\n"
    "Zera no dia 1. Enquanto isso, dá pra usar o dashboard: https://pigbankai.com/app"
)


ERROR_MSG = (
    "🐷 Deu ruim aqui — tenta de novo. Se persistir, fala com a gente: suporte@pigbankai.com"
)


# Comandos óbvios que mapeiam direto pra uma tool — pulam o LLM. Evita
# o LLM replicar padrões antigos do histórico (ex: pedir "dashboard" e ele
# listar saldo+gastos+investimentos em vez de devolver o link).
_FAST_PATH_DASHBOARD: frozenset[str] = frozenset({
    "dashboard", "painel", "web",
    "ver dashboard", "ver painel",
    "abrir dashboard", "abrir painel",
    "abre dashboard", "abre painel",
    "abre o dashboard", "abre o painel",
    "link", "link do dashboard", "link do painel", "link dashboard", "link painel",
    "manda dashboard", "manda painel",
    "manda o dashboard", "manda o painel",
    "meu dashboard", "meu painel",
})


def _fast_path_norm(text: str) -> str:
    t = (text or "").strip().lower()
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    # remove pontuação básica do início/fim
    return t.strip(" .,!?:;")


def _try_fast_path(user_id: int, user_text: str) -> str | None:
    norm = _fast_path_norm(user_text)
    if norm in _FAST_PATH_DASHBOARD:
        from core.handlers.dashboard import open_dashboard
        return open_dashboard(user_id)
    return None


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
    token_pf = CURRENT_PLATFORM.set(platform)
    token_msg = CURRENT_USER_MESSAGE.set((user_text or "").strip())
    try:
        return _chat_inner(user_id, user_text, monthly_limit=monthly_limit)
    finally:
        CURRENT_USER_MESSAGE.reset(token_msg)
        CURRENT_PLATFORM.reset(token_pf)


# Quem PERDE o CAS do `ai_consume_pending_action` sabe exatamente um fato: a
# linha que ele leu não está mais lá. Não sabe qual dos TRÊS consumidores a
# levou — executar, cancelar ou abandonar por mudança de assunto — então nenhuma
# frase pode afirmar desfecho. Vale igual pro "sim" e pro "não" perdedores: os
# dois conhecem o mesmo fato, e duas frases diferentes pro mesmo fato só existem
# pra uma delas estar errada.
_CAS_PERDIDO = (
    "🐷 Essa confirmação já foi resolvida em outra janela — "
    "dá uma olhada no seu extrato pra ver como ficou."
)


def _chat_inner(user_id: int, user_text: str, *, monthly_limit: int) -> str:
    user_id = int(user_id)
    user_text = (user_text or "").strip()
    if not user_text:
        return "🐷 Manda sua pergunta aí, tô ouvindo."

    # 1. Pending action? Processa primeiro.
    pending = db.ai_get_pending_action(user_id)
    if pending:
        if is_confirm(user_text):
            # ANTES de executar, não depois: o clear posterior não protegeria nada
            # — dois POSTs simultâneos em /ai/chat já teriam executado os dois.
            # Efeito colateral DESEJADO de consumir antes: pendência que a
            # própria tool REARMA durante o `_execute_pending` sobrevive ao
            # turno. Antes, o clear posterior apagava a pergunta recém-feita e
            # o "sim" seguinte do usuário caía no vazio. Não "conserte" de
            # volta pondo o consumo depois.
            if not db.ai_consume_pending_action(user_id, pending):
                # Dizer "já foi processada" fazia o usuário acreditar que o
                # histórico sumiu quando estava intacto — e relançar tudo à mão
                # duplica dado. O cancel vence a maioria das corridas (17 de 20,
                # medido).
                return _CAS_PERDIDO
            result = _execute_pending(user_id, pending)
            db.ai_append_message(user_id, "user", user_text)
            db.ai_append_message(user_id, "assistant", result)
            return result
        if is_cancel(user_text):
            # Perder o CAS aqui NÃO é "não havia nada pra cancelar": outra
            # requisição do mesmo usuário consumiu a linha. Mas ela pode ter
            # EXECUTADO, CANCELADO ou só ABANDONADO por mudança de assunto —
            # três consumidores, e o bool não diz qual. Nem "não fiz nada" (a
            # mentira de quem apagou tudo) nem "já tinha sido executada" (a
            # mentira de quem não apagou nada) servem: é o mesmo fato do "sim"
            # perdedor, e por isso a mesma frase.
            if db.ai_consume_pending_action(user_id, pending):
                msg = "👍 Beleza, não fiz nada."
            else:
                msg = _CAS_PERDIDO
            db.ai_append_message(user_id, "user", user_text)
            db.ai_append_message(user_id, "assistant", msg)
            return msg
        # User mudou de assunto — descarta pending e segue com nova msg.
        # Aqui o retorno é ignorado DE PROPÓSITO: perder o CAS significa que
        # outra requisição consumiu a linha, e não há resposta a corrigir — a
        # mensagem nova do usuário é atendida do mesmo jeito.
        db.ai_consume_pending_action(user_id, pending)

    # 2. Rate limit mensal
    used = db.ai_get_usage_this_month(user_id)
    if used >= monthly_limit:
        return LIMIT_MSG_TEMPLATE.format(limit=monthly_limit)

    # 2b. Fast-path: comandos óbvios pulam o LLM (ver _FAST_PATH_DASHBOARD).
    fast = _try_fast_path(user_id, user_text)
    if fast is not None:
        db.ai_append_message(user_id, "user", user_text)
        db.ai_append_message(user_id, "assistant", fast)
        return fast

    # 3. Salva msg do user
    db.ai_append_message(user_id, "user", user_text)

    # 4. Monta contexto + chama OpenAI
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY ausente — chat IA indisponível")
        return ERROR_MSG

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, timeout=OPENAI_TIMEOUT, max_retries=OPENAI_MAX_RETRIES)
    except Exception as e:
        logger.error("falha ao inicializar OpenAI: %s", e)
        return ERROR_MSG

    history = db.ai_get_recent_messages(user_id, limit=db.AI_DEFAULT_CONTEXT_WINDOW)
    history = trim_history_for_openai(history)
    # Limpa `###` que possa ter ficado em mensagens antigas — senão o LLM
    # faz few-shot a partir do próprio histórico e replica o erro.
    for m in history:
        if m.get("role") == "assistant" and isinstance(m.get("content"), str):
            m["content"] = strip_markdown_headers(m["content"])

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


def _log_unsupported_claims(user_id: int, reply: str, messages: list[dict[str, Any]]) -> None:
    """Guarda de afirmações em modo SÓ LOG.

    Pergunta: todo número que a IA afirmou veio de algum resultado de tool
    deste turno (ou da própria mensagem do user)? Número que não veio de lugar
    nenhum o modelo produziu sozinho — foi assim que apareceu o limite de
    cartão repetido depois de alterado (`docs/qa_whatsapp_pilot_*`).

    NÃO altera a resposta e NÃO levanta, em nenhuma hipótese: a guarda tem um
    falso positivo conhecido (conta feita pelo modelo — se as tools deram 100 e
    50 e ele escreve "R$ 150,00", o 150 não está na evidência), e segurar
    resposta correta seria pior que o defeito que ela caça. Modo log existe
    justamente pra medir essa taxa antes de qualquer decisão mais dura.

    Só o texto ESCRITO pelo modelo passa por aqui. Os outros retornos do loop
    são `ERROR_MSG` e o `terminal_msg` de write auto-executado — template de
    código, não afirmação de IA.

    `messages` tem que ser só o DESTE TURNO (o chamador fatia). O preço dessa
    escolha é ruído: quando a IA reaproveita legitimamente um dado que uma tool
    devolveu num turno anterior ("e quanto disso foi salário?"), o valor sai
    como não sustentado. Aceito de propósito — em modo log ruído se mede, e
    aceitar o histórico como evidência cega a guarda pra dado obsoleto, que é
    a classe que ela nasceu pra pegar.
    """
    try:
        from core.services.ai_guard import check

        evidencia = [m.get("content") or "" for m in messages if m.get("role") == "tool"]
        nao_sustentadas = [
            c for c in check(reply, evidencia, user_text=CURRENT_USER_MESSAGE.get())
            if not c.supported
        ]
        if not nao_sustentadas:
            return

        from core.observability import log_system_event_sync

        log_system_event_sync(
            # `info`, não `warning`: o painel de saúde do admin lê
            # `backend_warnings_24h` como "problemas de backend", e isto não é
            # um. É observação sobre a IA, e tem contador próprio
            # (`ai_claim_unsupported_24h`). Warning aqui poluía o número que o
            # dono usa pra saber se o servidor está mal.
            "info",
            "ai_claim_unsupported",
            f"{len(nao_sustentadas)} afirmação(ões) numérica(s) sem evidência de tool",
            source="ai_chat/guard",
            user_id=user_id,
            details={
                # Os tokens, não a resposta inteira: são o que torna o log
                # acionável, e limitar a eles evita despejar texto livre na
                # tabela de observabilidade.
                "tokens": [c.token for c in nao_sustentadas][:10],
                "kinds": sorted({c.kind for c in nao_sustentadas}),
                "n_afirmacoes": len(nao_sustentadas),
                "n_resultados_de_tool": len(evidencia),
            },
        )
    except Exception:
        logger.debug("guarda de afirmações falhou — ignorado", exc_info=True)


def _run_tool_loop(client, user_id: int, messages: list[dict[str, Any]]) -> str:
    # Fronteira do turno. `messages` CHEGA aqui já com o histórico persistido,
    # que inclui as `role="tool"` de turnos anteriores. Passar a lista inteira
    # pra guarda fazia ela dar como sustentado justamente o valor OBSOLETO que
    # ela existe pra pegar — reproduzido: com o limite antigo (100.000) no
    # histórico e o novo (8.000) no turno atual, a resposta que repetia 100.000
    # não gerava evento nenhum. O harness sempre fatiou por turno; era o wiring
    # de produção que divergia do que foi validado.
    inicio_do_turno = len(messages)
    for _ in range(MAX_TOOL_LOOPS):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                messages=messages,
                tools=SCHEMAS,
                max_tokens=MAX_TOKENS,
            )
        except Exception as e:
            logger.error("erro na chamada OpenAI: %s", e)
            return ERROR_MSG

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            final = strip_markdown_headers((msg.content or "").strip())
            _log_unsupported_claims(user_id, final, messages[inicio_do_turno:])
            return final or ERROR_MSG

        # Persistir assistant message com tool_calls — limpa `###`
        # também aqui pra não contaminar histórico futuro.
        cleaned_content = strip_markdown_headers(msg.content)
        tool_calls_dicts = [tc.model_dump() for tc in tool_calls]

        # Override defensivo (Bug 2): se o LLM escolheu `report_out_of_scope`
        # mas o user pediu tendência ("tendência deste ano", "evolução mês a
        # mês"...), reescreve o tool_call ANTES de persistir/despachar.
        # Reescrever antes da persistência evita mismatch entre o `name` no
        # assistant.tool_calls e a tool response que de fato roda.
        _maybe_override_trend(tool_calls_dicts, user_id)

        db.ai_append_message(
            user_id,
            "assistant",
            cleaned_content,
            tool_calls=tool_calls_dicts,
        )
        messages.append({
            "role": "assistant",
            "content": cleaned_content,
            "tool_calls": tool_calls_dicts,
        })

        terminal_msg: str | None = None
        for tc_dict in tool_calls_dicts:
            tc_id = tc_dict["id"]
            name = tc_dict["function"]["name"]
            try:
                args = json.loads(tc_dict["function"]["arguments"] or "{}")
            except Exception:
                args = {}

            history_content, this_terminal = _dispatch_tool(user_id, name, args)

            db.ai_append_message(
                user_id,
                "tool",
                history_content,
                tool_call_id=tc_id,
                tool_name=name,
            )
            messages.append({
                "role": "tool",
                "content": history_content,
                "tool_call_id": tc_id,
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


def _maybe_override_trend(tool_calls_dicts: list[dict[str, Any]], user_id: int) -> None:
    """Reescreve `report_out_of_scope` → `get_spending_trend` in-place.

    Pro Bug 2: o LLM ignora a regra de tendência mesmo com prompt reforçado.
    Quando o user pediu tendência (heurística em `detect_trend_window`) E o
    LLM escolheu fallback, troca o `function.name`/`function.arguments` no
    próprio dict. Mantém o `id` original pra preservar coerência com a tool
    response que vai vir em seguida.
    """
    user_text = CURRENT_USER_MESSAGE.get()
    months = detect_trend_window(user_text)
    if months is None:
        return
    for tc in tool_calls_dicts:
        fn = tc.get("function") or {}
        if fn.get("name") == "report_out_of_scope":
            logger.info(
                "ai_chat: override report_out_of_scope → get_spending_trend(months=%d) user=%s",
                months,
                user_id,
            )
            fn["name"] = "get_spending_trend"
            fn["arguments"] = json.dumps({"months": months})


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
