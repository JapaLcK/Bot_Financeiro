# core/intent_router.py
"""
Recebe um IntentResult + mensagem original e decide o que fazer:
  - confiança alta   → executa direto
  - ação destrutiva  → cria pending e pede confirmação
  - needs_clarif     → salva estado e faz pergunta
  - confiança baixa  → pede reformulação
  - out_of_scope     → resposta padrão
  - confirm.yes/no   → tenta resolver pending
"""
from __future__ import annotations

import db
from core.intent_classifier import IntentResult
from core.types import IncomingMessage

# handlers
from core.handlers import (
    balance    as h_balance,
    credit     as h_credit,
    launches   as h_launches,
    pockets    as h_pockets,
    investments as h_investments,
    report     as h_report,
    help_handler as h_help,
    categories as h_categories,
    dashboard  as h_dashboard,
    account    as h_account,
    pending    as h_pending,
)

# Limiar de confiança para executar sem pedir confirmação
CONFIDENCE_EXECUTE = 0.85

# Intents que exigem confirmação antes de executar (destrutivos)
DESTRUCTIVE_INTENTS = {
    "launches.delete",
    "pockets.delete",
    "investments.delete",
}

OUT_OF_SCOPE_MSG = (
    "Só consigo ajudar com finanças pessoais: "
    "saldo, lançamentos, cartões, caixinhas e investimentos.\n"
    "Digite *ajuda* para ver o que posso fazer."
)

NOT_UNDERSTOOD_MSG = "Não entendi. Pode reformular?\nDigite *ajuda* para ver exemplos."


def route(result: IntentResult, msg: IncomingMessage) -> str:
    """
    Ponto de entrada único do roteador.
    Retorna o texto de resposta (ainda não formatado por plataforma).
    """
    user_id  = int(msg.user_id)
    text     = (msg.text or "").strip()
    platform = msg.platform
    external_id = getattr(msg, "external_id", None) or ""

    intent     = result.intent
    confidence = result.confidence
    entities   = result.entities or {}

    # -----------------------------------------------------------------------
    # 0. Esclarecimento pendente — tem prioridade máxima
    #    Se o bot fez uma pergunta e está esperando resposta, usa esta mensagem
    #    para completar a intent original em vez de classificar do zero.
    # -----------------------------------------------------------------------
    clarif = h_pending.get_pending_clarification(user_id)
    if clarif:
        return _resolve_clarification(clarif, text, user_id, platform, external_id)

    pending = db.get_pending_action(user_id)
    if pending and pending.get("action_type") in {"credit_card_setup", "credit_card_set_primary"}:
        resp = h_credit.resolve_pending(user_id, text, pending)
        if resp is not None:
            return resp

    # -----------------------------------------------------------------------
    # 1. Confirmações (sim / não) para ações destrutivas
    # -----------------------------------------------------------------------
    if intent == "confirm.yes":
        resp = h_pending.resolve_delete(user_id, confirmed=True)
        return resp if resp is not None else NOT_UNDERSTOOD_MSG

    if intent == "confirm.no":
        resp = h_pending.resolve_delete(user_id, confirmed=False)
        return resp if resp is not None else "Nada a cancelar."

    # -----------------------------------------------------------------------
    # 2. Fora do escopo
    # -----------------------------------------------------------------------
    if intent == "out_of_scope":
        return OUT_OF_SCOPE_MSG

    # -----------------------------------------------------------------------
    # 3. Confiança muito baixa
    # -----------------------------------------------------------------------
    if confidence < 0.55:
        return NOT_UNDERSTOOD_MSG

    # -----------------------------------------------------------------------
    # 4. Precisa de esclarecimento
    # -----------------------------------------------------------------------
    if result.needs_clarification and result.clarification_question:
        # salva intent + entities parciais para retomar quando o usuário responder
        db.set_pending_action(
            user_id,
            "clarification",
            {
                "intent":    intent,
                "entities":  entities,
                "question":  result.clarification_question,
                "orig_text": text,
            },
        )
        return result.clarification_question

    # -----------------------------------------------------------------------
    # 5. Ações destrutivas → pede confirmação antes de executar
    # -----------------------------------------------------------------------
    if intent in DESTRUCTIVE_INTENTS:
        return _handle_destructive(intent, user_id, entities, text)

    # -----------------------------------------------------------------------
    # 6. Confiança moderada (entre 0.55 e 0.85) para ações que modificam dados
    # -----------------------------------------------------------------------
    WRITE_INTENTS = {
        "launches.add", "pockets.create", "pockets.deposit", "pockets.withdraw",
        "investments.create", "investments.deposit", "investments.withdraw",
        "categories.create", "categories.delete",
    }
    if intent in WRITE_INTENTS and confidence < CONFIDENCE_EXECUTE:
        label = _intent_label(intent)
        return f"Entendi como *{label}*. Confirma? Responda **sim** ou **não**."

    # -----------------------------------------------------------------------
    # 7. Executa direto
    # -----------------------------------------------------------------------
    return _execute(intent, user_id, text, entities, platform, external_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handle_destructive(intent: str, user_id: int, entities: dict, text: str) -> str:
    if intent == "launches.delete":
        launch_id = entities.get("launch_id")
        if not launch_id:
            return "Qual o ID do lançamento para apagar? Ex: *apagar #42*"
        return h_launches.propose_delete(user_id, int(launch_id))

    if intent == "pockets.delete":
        pocket_name = entities.get("pocket_name")
        if not pocket_name:
            return "Qual caixinha quer deletar? Ex: *excluir caixinha viagem*"
        return h_pockets.propose_delete(user_id, pocket_name)

    if intent == "investments.delete":
        investment_name = entities.get("investment_name")
        if not investment_name:
            return "Qual investimento quer deletar? Ex: *excluir investimento CDB Nubank*"
        return h_investments.propose_delete(user_id, investment_name)

    return NOT_UNDERSTOOD_MSG


def _execute(intent: str, user_id: int, text: str, entities: dict, platform: str, external_id: str) -> str:

    # --- saldo ---
    if intent == "balance.check":
        return h_balance.check(user_id)

    # --- lançamentos ---
    if intent == "launches.list":
        limit = int(entities.get("limit", 10))
        return h_launches.list_launches(user_id, limit=limit, entities=entities, original_text=text)

    if intent == "launches.add":
        return h_launches.add(user_id, text, entities)

    if intent == "launches.undo":
        return h_launches.undo(user_id)

    # --- cartões / crédito ---
    if intent == "credit.handle":
        resp = h_credit.handle(user_id, text)
        return resp if resp is not None else OUT_OF_SCOPE_MSG

    # --- caixinhas ---
    if intent == "pockets.list":
        return h_pockets.list_pockets(user_id)

    if intent == "pockets.create":
        name = entities.get("name") or ""
        return h_pockets.create(user_id, name)

    if intent == "pockets.deposit":
        return h_pockets.deposit(user_id, text, entities)

    if intent == "pockets.withdraw":
        return h_pockets.withdraw(user_id, text, entities)

    # --- investimentos ---
    if intent == "investments.list":
        return h_investments.list_investments(user_id)

    if intent == "investments.create":
        raw_name = entities.get("raw_name") or ""
        return h_investments.create(user_id, raw_name, text)

    if intent == "investments.deposit":
        return h_investments.deposit(user_id, text, entities)

    if intent == "investments.withdraw":
        return h_investments.withdraw(user_id, text, entities)

    # --- categorias ---
    if intent == "categories.list":
        return h_categories.list_categories(user_id)

    if intent == "categories.create":
        return h_categories.create(user_id, text)

    if intent == "categories.delete":
        keyword = entities.get("keyword") or text.replace("remover destinatario", "").strip()
        return h_categories.delete(user_id, keyword)

    # --- relatório ---
    if intent == "report.daily":
        return h_report.daily(user_id)

    if intent == "report.enable":
        return h_report.enable(user_id)

    if intent == "report.disable":
        return h_report.disable(user_id)

    # --- dashboard ---
    if intent == "dashboard.open":
        return h_dashboard.open_dashboard(user_id)

    # --- ajuda ---
    if intent == "help":
        parts = text.split(maxsplit=1)
        section_arg = parts[1] if len(parts) > 1 else None
        if section_arg:
            return h_help.help_section(section_arg, platform)
        return h_help.help_general(platform)

    if intent == "help.tutorial":
        return h_help.tutorial(platform)

    # --- vinculação ---
    if intent == "account.link":
        code = entities.get("code")
        return h_account.link(platform, external_id, code)

    if intent == "account.vincular":
        code = entities.get("code", "")
        return h_account.vincular(platform, external_id, code)

    # fallback final
    return OUT_OF_SCOPE_MSG


def _resolve_clarification(clarif: dict, user_response: str, user_id: int, platform: str, external_id: str) -> str:
    """
    O bot tinha feito uma pergunta e está esperando a resposta do usuário.
    Combina a resposta com as entidades originais e re-executa a intent.
    """
    from utils_date import extract_date_from_text

    payload          = clarif.get("payload", {})
    original_intent  = payload.get("intent", "launches.list")
    original_entities = dict(payload.get("entities") or {})
    orig_text        = payload.get("orig_text", "")

    # limpa o pending antes de executar
    db.clear_pending_action(user_id)

    # tenta extrair data da resposta do usuário
    dt, _ = extract_date_from_text(user_response)
    if not dt:
        # tenta extrair do texto original (ex: "quanto gastei dia 4")
        dt, _ = extract_date_from_text(orig_text)

    if dt:
        original_entities["date_filter"] = dt.date().isoformat()

    # se o usuário negou / cancelou explicitamente
    resp_norm = user_response.strip().lower()
    if resp_norm in ("nao", "não", "n", "cancelar", "cancela"):
        return "❌ Consulta cancelada."

    # re-executa a intent original com as entidades completas
    return _execute(original_intent, user_id, orig_text or user_response, original_entities, platform, external_id)


def _intent_label(intent: str) -> str:
    labels = {
        "launches.add":         "registrar lançamento",
        "pockets.create":       "criar caixinha",
        "pockets.deposit":      "depositar em caixinha",
        "pockets.withdraw":     "retirar de caixinha",
        "investments.create":   "criar investimento",
        "investments.deposit":  "aportar em investimento",
        "investments.withdraw": "resgatar investimento",
        "categories.create":    "criar regra de categoria",
        "categories.delete":    "remover regra de categoria",
    }
    return labels.get(intent, intent)
