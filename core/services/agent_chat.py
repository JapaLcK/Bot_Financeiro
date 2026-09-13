"""Conversas especialistas: contexto efêmero autenticado e consultas por domínio.

Não lê/escreve ai_messages nem ai_pending_actions. O navegador guarda o contexto
criptografado apenas na memória da página; reload inicia uma conversa vazia.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import date

from cryptography.fernet import Fernet, InvalidToken

import db
from core.services.ai_chat.tools import get_tool
from core.services.ai_chat.runner import MAX_TOKENS, OPENAI_TIMEOUT, OPENAI_MAX_RETRIES

from .agent_chat_data import _snapshot, SNAPSHOT_ITEMS_LIMIT
from .agent_chat_errors import (ChatError, ModelResponseError, AnswerPolicyError, DataQueryError, handle_failure)
from .agent_chat_policy import (completion_options, routing_format, REVIEW_FORMAT, response_text, response_json, routing_prompt, answer_prompt, review_prompt)

MODEL = (os.getenv("AGENT_CHAT_MODEL") or "").strip() or "gpt-5.4-mini"

DOMAINS = {
    "xerife": ("Xerife", "gastos fora do padrão, gastos exorbitantes e limites de categorias"),
    "detetive": ("Detetive", "assinaturas, cobranças recorrentes e lançamentos possivelmente duplicados"),
    "carteiro": ("Carteiro", "contas, boletos e vencimentos"),
    "reporter": ("Repórter", "resumo financeiro, entradas, saídas e saldo"),
    "cofre": ("Banqueiro", "caixinhas, metas e aportes nas caixinhas"),
    "barao": ("Barão", "dinheiro parado e renda fixa"),
    "faria_limer": ("Faria Limer", "ações, FIIs, composição e diversificação da carteira"),
}
READ_TOOLS = {
    "xerife": {"get_largest_expenses", "get_category_spend", "get_top_categories", "get_budget_status", "get_spending_trend"},
    "detetive": set(),
    "carteiro": {"get_bills_to_pay"},
    "reporter": {"get_balance", "get_period_summary", "compare_periods", "get_spending_trend"},
    "cofre": {"list_pockets"},
    "barao": {"get_balance"},
    "faria_limer": set(),
}


def _cipher() -> Fernet:
    secret = os.getenv("JWT_SECRET", "").strip()
    if not secret:
        raise ChatError("unavailable", 503, "Conversa indisponível. Tente novamente em alguns minutos.")
    key = hmac.new(secret.encode(), b"pigbank:agent-chat:context:v1", hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def decode_context(token: str | None, user_id: int, kind: str) -> list[dict]:
    if not token:
        return []
    try:
        data = json.loads(_cipher().decrypt(token.encode(), ttl=86400))
        if data["user_id"] != user_id or data["kind"] != kind:
            raise ValueError("context owner")
        return data["messages"]
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise ChatError("invalid_context", 400, "Essa conversa expirou. Inicie uma nova conversa.") from None


def encode_context(user_id: int, kind: str, messages: list[dict]) -> str:
    return _cipher().encrypt(json.dumps({
        "user_id": user_id, "kind": kind, "messages": messages[-20:],
    }, ensure_ascii=False).encode()).decode()


def plan_allows_chat(user_id: int) -> bool:
    """Direito de conversar, separado da ativação gratuita permitida no legado."""
    from core.services import plan_service as plans
    if plans.plans_v2_enabled():
        return plans.agents_energy_budget(user_id) > 0
    return plans.is_pro(user_id)


def access_state(user_id: int, kind: str) -> str:
    from core.services import plan_service as plans
    from core.services.plan_limits import agent_energy_cost
    if kind not in DOMAINS:
        return "unavailable"
    if not plan_allows_chat(user_id):
        return "upgrade"
    agents = db.list_agents(user_id)
    active = any(a["kind"] == kind and a["status"] == "active" for a in agents)
    if plans.plans_v2_enabled():
        budget = plans.agents_energy_budget(user_id)
        if budget <= 0:
            return "upgrade"
        used = sum(agent_energy_cost(a["kind"]) for a in agents if a["status"] == "active")
        # Revalida também agentes ativos após downgrade, antes do sweep.
        if used > budget or (not active and used + agent_energy_cost(kind) > budget):
            return "no_energy"
    return "ready" if active else "activate"


def require_access(user_id: int, kind: str) -> None:
    state = access_state(user_id, kind)
    if state != "ready":
        raise ChatError(state, 403 if state != "unavailable" else 404, {
            "upgrade": "Seu plano não inclui a conversa com este agente.",
            "no_energy": "Falta energia para conversar com este agente. Veja as opções de plano.",
            "activate": "Ative este agente para iniciar a conversa.",
            "unavailable": "Agente indisponível.",
        }[state])


def _snapshot_schema() -> dict:
    return {"type": "function", "function": {"name": "consultar_dados_do_agente",
            "description": "Consulta os dados do próprio tema, incluindo duplicidades e recorrências no Detetive e carteira no Faria Limer.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}


def execute_read(user_id: int, kind: str, name: str, args: dict):
    if name == "consultar_dados_do_agente":
        return _snapshot(user_id, kind)
    tool = get_tool(name) if name in READ_TOOLS[kind] else None
    if tool is None or tool.is_write:
        return {"error": "Consulta fora do tema ou alteração não permitida."}
    # O cadastro genérico destas consultas sincroniza dados. Aqui usamos
    # variantes puras; args do modelo nunca podem reativar sync/accrual.
    if name == "get_bills_to_pay":
        from core.services.ai_chat.tools.bills import _get_bills_to_pay
        return _get_bills_to_pay(user_id, args, sync=False)
    if name == "list_pockets":
        from core.services.ai_chat.tools.pockets import _list_pockets
        return _list_pockets(user_id, args, accrue=False)
    if getattr(tool, "has_side_effects", False):
        return {"error": "Consulta com efeitos colaterais não permitida."}
    return tool.execute(user_id, args)


def _route(client, kind: str, text: str, history: list[dict]) -> dict:
    response = client.chat.completions.create(
        **completion_options(MODEL, 900, 0),
        response_format=routing_format(list(DOMAINS)),
        messages=[{"role": "system", "content": routing_prompt(kind, DOMAINS)},
                  {"role": "user", "content": json.dumps({"history": history[-6:], "message": text}, ensure_ascii=False)}],
    )
    result = response_json(response)
    parts = result.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > 8:
        raise ModelResponseError("Invalid routing")
    own, redirects, outside, needs_data = [], [], False, False
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("question"), str) or not part["question"].strip():
            raise ModelResponseError("Invalid routing part")
        target, question = part.get("kind"), part["question"].strip()[:2000]
        if target == kind:
            own.append(question)
            needs_data = needs_data or part.get("needs_data") is True
        elif target == "outside":
            outside = True
        elif target in DOMAINS:
            existing = next((r for r in redirects if r["kind"] == target), None)
            if existing:
                existing["question"] = (existing["question"] + "\n" + question)[:2000]
            else:
                redirects.append({"kind": target, "question": question})
    if not own and not redirects and not outside:
        raise ModelResponseError("No valid routing target")
    return {"own_question": "\n".join(own)[:2000], "redirects": redirects,
            "outside": outside, "needs_data": needs_data}


def _answer(client, user_id: int, kind: str, question: str, history: list[dict], *, needs_data: bool = False) -> str:
    name, domain = DOMAINS[kind]
    messages = [{"role": "system", "content": answer_prompt(name, domain, date.today().isoformat())}]
    messages += history + [{"role": "user", "content": question}]
    schemas = [_snapshot_schema()]
    for name in sorted(READ_TOOLS[kind]):
        tool = get_tool(name)
        if tool and not tool.is_write:
            schemas.append(tool.schema)
    repairs = 0
    consulted = False
    for _ in range(6):
        response = client.chat.completions.create(
            **completion_options(MODEL, MAX_TOKENS, 0.2, with_tools=True), messages=messages, tools=schemas,
            tool_choice="required" if needs_data and not consulted else "auto")
        if not response.choices or getattr(response.choices[0], "finish_reason", None) in {"length", "content_filter"}:
            raise ModelResponseError("Incomplete model response")
        msg = response.choices[0].message
        if getattr(msg, "refusal", None):
            raise ModelResponseError("Refused model response")
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            reply = response_text(response)
            if needs_data and not consulted:
                raise ModelResponseError("Required data consultation missing")
            try:
                evidence = [m for m in messages if m["role"] == "tool"]
                _check_answer(client, kind, question, reply, evidence=evidence)
                return reply
            except AnswerPolicyError as exc:
                if repairs:
                    raise
                repairs += 1
                messages += [{"role": "assistant", "content": reply}, {"role": "system", "content": (
                    "Revise a resposta anterior antes de enviar. Motivo da revisão: " + exc.reason + ". "
                    "Responda à pergunta atual com conteúdo educativo permitido e respeite a cobertura dos dados. "
                    "Não indique operações nem responda outro tema. Não mencione o verificador ao usuário.")}]
                continue
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [c.model_dump() for c in calls]})
        if len(calls) > 8:
            raise ModelResponseError("Too many tool calls")
        for call in calls:
            try:
                args = json.loads(call.function.arguments or "{}")
                if not isinstance(args, dict):
                    raise ModelResponseError("Invalid tool arguments")
            except (ValueError, TypeError) as exc:
                raise ModelResponseError("Invalid tool arguments") from exc
            try:
                result = execute_read(user_id, kind, call.function.name, args)
            except Exception as exc:
                raise DataQueryError("Data query failed") from exc
            allowed = call.function.name == "consultar_dados_do_agente" or call.function.name in READ_TOOLS[kind]
            if allowed and isinstance(result, dict) and result.get("error"):
                raise DataQueryError("Data query returned an error")
            consulted = consulted or allowed
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result, ensure_ascii=False, default=str)})
    raise ModelResponseError("Tool loop exhausted")


def _check_answer(client, kind: str, question: str, reply: str, *, evidence: list | None = None) -> None:
    """Revisa a resposta com a cobertura dos dados, permitindo uma correção no loop."""
    result = client.chat.completions.create(
        **completion_options(MODEL, 180, 0), response_format=REVIEW_FORMAT,
        messages=[{"role": "system", "content": review_prompt(*DOMAINS[kind])},
                  {"role": "user", "content": json.dumps({"question": question, "reply": reply,
                    "evidence": evidence or []}, ensure_ascii=False, default=str)}],
    )
    verdict = response_json(result)
    if not isinstance(verdict.get("valid"), bool):
        raise ModelResponseError("Invalid answer review")
    if not verdict["valid"]:
        reason = verdict.get("reason")
        raise AnswerPolicyError(reason if reason in {"wrong_domain", "financial_action", "unsupported_claim"} else "policy")


def chat(user_id: int, kind: str, text: str, context: str | None = None) -> dict:
    from core.services.plan_service import ai_monthly_limit_for
    from db.ai_chat import try_consume_usage
    stage = "access"
    try:
        require_access(user_id, kind)
        stage = "context"
        history = decode_context(context, user_id, kind)
        stage = "quota_read"
        limit = ai_monthly_limit_for(user_id)
        used = db.ai_get_usage_this_month(user_id)
        if used >= limit:
            raise ChatError("quota_exhausted", 429, "Você atingiu a cota mensal compartilhada da IA. Ela renova no próximo mês.")
        stage = "configuration"
        from openai import OpenAI
        client = OpenAI(api_key=(os.getenv("OPENAI_API_KEY") or "").strip(), timeout=OPENAI_TIMEOUT, max_retries=OPENAI_MAX_RETRIES)
        stage = "routing"
        route = _route(client, kind, text, history)
        own = route["own_question"].strip()
        if own and not route["redirects"] and not route["outside"]:
            own = text  # Um classificador não reescreve a intenção de uma pergunta integralmente própria.
        stage = "access_redirect"
        redirects = [{**r, "name": DOMAINS[r["kind"]][0], "access": access_state(user_id, r["kind"])} for r in route["redirects"]]
        stage = "answering"
        reply = _answer(client, user_id, kind, own, history, needs_data=route.get("needs_data", False)) if own else ""
        if redirects:
            names = ", ".join(r["name"] for r in redirects)
            subject = "Essa outra parte" if reply else "Esse assunto"
            reply += ("\n\n" if reply else "") + f"{subject} é com {names}. Você pode continuar pelo botão abaixo."
        if route["outside"] or (not own and not redirects):
            reply += ("\n\n" if reply else "") + "Esse assunto está fora dos temas dos nossos agentes. Posso ajudar dentro do meu tema."
        # Guarda apenas a parte própria; perguntas de outros domínios não viram
        # exemplos/contexto para o especialista em turnos futuros.
        next_history = history + ([{"role": "user", "content": own}, {"role": "assistant", "content": reply}] if own else [])
        stage = "context"
        next_context = encode_context(user_id, kind, next_history)
        stage = "access_final"
        require_access(user_id, kind)  # acesso pode mudar enquanto o modelo responde
        if own:
            stage = "quota"
            consumed = try_consume_usage(user_id, limit)
            if consumed is None:
                raise ChatError("quota_exhausted", 429, "Você atingiu a cota mensal compartilhada da IA.")
            used = consumed
        return {"reply": reply, "context": next_context, "redirects": redirects, "usage": {"used": used, "limit": limit}}
    except ChatError:
        raise
    except Exception as exc:
        raise handle_failure(exc, user_id, kind, stage) from None
