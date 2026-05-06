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

import re

import db
from core.intent_classifier import IntentResult
from core.types import IncomingMessage
from utils_text import normalize_text

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
    greeting   as h_greeting,
)

# Limiar de confiança para executar sem pedir confirmação
CONFIDENCE_EXECUTE = 0.85

# Intents que exigem confirmação antes de executar (destrutivos)
DESTRUCTIVE_INTENTS = {
    "launches.delete",
    "launches.delete_bulk",
    "pockets.delete",
    "investments.delete",
}

OUT_OF_SCOPE_MSG = (
    "Só consigo ajudar com finanças pessoais: "
    "saldo, lançamentos, cartões, caixinhas e investimentos.\n"
    "Digite *ajuda* para ver o que posso fazer."
)

NOT_UNDERSTOOD_MSG = "Não entendi. Pode reformular?\nDigite *ajuda* para ver exemplos."


def _contextual_help_message(text: str, platform: str) -> str:
    return h_help.infer_contextual_fallback(text, platform)


def _should_redirect_launches_list_to_help(text: str) -> bool:
    norm = normalize_text(text)
    if not any(term in norm for term in ("gasto", "gastos", "despesa", "despesas", "lancamento", "lancamentos", "historico", "extrato")):
        return False

    allowed_patterns = (
        r"^(gastos?|despesas?|lancamentos?|historico|extrato)$",
        r"^(meus|minhas)\s+(gastos?|despesas?|lancamentos?)$",
        r"^(ver|mostrar|mostra|listar)\s+(meus\s+)?(gastos?|despesas?|lancamentos?|extrato)(\s+recentes?)?$",
        r"^(quais|qual)\s+(sao|foram|e|foi)?\s*(meus|os|minhas|as)?\s*(gastos?|despesas?|lancamentos?|ultimos?)$",
        r"^(o\s+que|quanto)\s+(gastei|gastos?|despesas?|lancamentos?)$",
        r"^(gastos?|despesas?)\s+(recentes?|ultimos?|da\s+semana|do\s+mes)$",
        r".*\b(hoje|ontem)\b.*",
    )
    if any(re.fullmatch(pattern, norm) for pattern in allowed_patterns):
        return False

    first = norm.split()[0] if norm.split() else ""
    return first in {"gasto", "gastos", "despesa", "despesas", "lancamento", "lancamentos", "historico", "extrato"}


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

    inferred_help = h_help.infer_help_from_text(text, platform)
    if inferred_help is not None:
        norm = normalize_text(text)
        if (
            "Não entendi exatamente" in inferred_help
            and any(term in norm for term in ("investimento", "investimentos", "aporte", "resgate", "cdb", "tesouro", "cdi"))
        ):
            return h_investments.list_investments(
                user_id,
                "Não entendi exatamente o pedido de investimentos. Aqui está sua carteira:",
            )
        return inferred_help

    # -----------------------------------------------------------------------
    # 0. Esclarecimento pendente — tem prioridade máxima
    #    Se o bot fez uma pergunta e está esperando resposta, usa esta mensagem
    #    para completar a intent original em vez de classificar do zero.
    # -----------------------------------------------------------------------
    clarif = h_pending.get_pending_clarification(user_id)
    if clarif:
        return _resolve_clarification(clarif, text, user_id, platform, external_id)

    pending = db.get_pending_action(user_id)
    if pending and pending.get("action_type") in {"credit_card_setup", "credit_card_set_primary", "credit_delete_card", "installment_pending", "pay_bill_choice"}:
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
        return _contextual_help_message(text, platform)

    # -----------------------------------------------------------------------
    # 3. Confiança muito baixa
    # -----------------------------------------------------------------------
    if confidence < 0.55:
        return _contextual_help_message(text, platform)

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
        "investments.deposit", "investments.withdraw",
        "funds.withdraw",
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

    if intent == "launches.delete_bulk":
        ids = entities.get("launch_ids") or []
        if not ids:
            return "Informe os IDs a apagar. Ex: *apagar id 757, 756*"
        ids_fmt = ", ".join(f"**#{i}**" for i in ids)
        db.set_pending_action(user_id, "delete_launch_bulk", {"launch_ids": ids})
        return (
            f"⚠️ Isso vai apagar os lançamentos {ids_fmt} e desfazer seus efeitos no saldo.\n"
            "Confirma? Responda **sim** ou **não**."
        )

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

    # --- saudações ---
    if intent == "greeting":
        resp = h_greeting.handle_greeting(text, user_id=user_id)
        return resp if resp is not None else "👋 Oi! Como posso te ajudar?"

    # --- saldo ---
    if intent == "balance.check":
        return h_balance.check(user_id)

    # --- lançamentos ---
    if intent == "launches.list":
        if _should_redirect_launches_list_to_help(text):
            return _contextual_help_message(text, platform)
        limit = int(entities.get("limit", 10))
        return h_launches.list_launches(user_id, limit=limit, entities=entities, original_text=text)

    if intent == "launches.add":
        return h_launches.add(user_id, text, entities, platform=platform)

    if intent == "launches.undo":
        return h_launches.undo(user_id)

    # --- cartões / crédito ---
    if intent == "credit.handle":
        resp = h_credit.handle(user_id, text)
        return resp if resp is not None else _contextual_help_message(text, platform)

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

    if intent == "funds.withdraw":
        return _execute_generic_withdraw(user_id, text, entities)

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
        return h_categories.delete(user_id, text)

    # --- relatório ---
    if intent == "report.daily":
        return h_report.daily(user_id)

    if intent == "report.enable":
        return h_report.enable(user_id)

    if intent == "report.set_hour":
        hour   = int(entities.get("hour",   9))
        minute = int(entities.get("minute", 0))
        return h_report.set_hour(user_id, hour, minute)

    if intent == "report.disable":
        return h_report.disable(user_id)

    # --- emails de engajamento ---
    if intent == "emails.resubscribe":
        import db as _db
        _db.set_engagement_opt_out(user_id, False)
        return "✅ Pronto! Você voltará a receber as dicas e insights do Piggy por email."

    if intent == "emails.unsubscribe":
        import db as _db
        _db.set_engagement_opt_out(user_id, True)
        return "👍 Ok! Você não vai mais receber os emails de dicas do Piggy.\nSeus emails de segurança (código de verificação etc.) continuam normais.\nQuer voltar a receber? É só mandar *reativar emails*."

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

    # --- CDI ---
    if intent == "cdi.check":
        return h_investments.check_cdi()

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
        "funds.withdraw":       "retirar de caixinha ou investimento",
        "investments.create":   "abrir investimentos",
        "investments.deposit":  "aportar em investimento",
        "investments.withdraw": "resgatar investimento",
        "categories.create":    "criar regra de categoria",
        "categories.delete":    "remover regra de categoria",
    }
    return labels.get(intent, intent)


def _execute_generic_withdraw(user_id: int, text: str, entities: dict) -> str:
    amount = entities.get("amount")
    target_name = (entities.get("target_name") or "").strip()
    target_kind = entities.get("target_kind")

    if target_kind == "pocket":
        return h_pockets.withdraw(user_id, text, {"pocket_name": target_name, "amount": amount})
    if target_kind == "investment":
        return h_investments.withdraw(user_id, text, {"investment_name": target_name, "amount": amount})

    if not amount or float(amount) <= 0:
        return "Qual o valor? Tente: *saquei 200 da reserva de emergência*"

    if not target_name:
        return "Você quer retirar de qual caixinha ou investimento?"

    norm_target = normalize_text(target_name)

    pockets = db.list_pockets(user_id) or []
    investments = db.accrue_all_investments(user_id) or []

    pocket_matches = [p for p in pockets if normalize_text(p.get("name") or "") == norm_target]
    investment_matches = [i for i in investments if normalize_text(i.get("name") or "") == norm_target]

    if len(pocket_matches) == 1 and not investment_matches:
        return h_pockets.withdraw(user_id, text, {"pocket_name": pocket_matches[0]["name"], "amount": amount})

    if len(investment_matches) == 1 and not pocket_matches:
        return h_investments.withdraw(user_id, text, {"investment_name": investment_matches[0]["name"], "amount": amount})

    if pocket_matches and investment_matches:
        return (
            f"Encontrei esse nome tanto em caixinha quanto em investimento: **{target_name}**.\n"
            f"Você quer retirar da *caixinha* ou do *investimento*?"
        )

    return (
        f"Não encontrei **{target_name}** nem em caixinhas nem em investimentos.\n"
        f"Use *listar caixinhas* ou *listar investimentos* para ver os nomes disponíveis."
    )
