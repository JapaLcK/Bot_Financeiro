from __future__ import annotations

import re
from collections import defaultdict

from ai_router import classify_category_with_gpt
from db import (
    add_credit_purchase,
    add_credit_purchase_installments,
    card_name_exists,
    clear_pending_action,
    create_card,
    delete_card,
    get_card_by_id,
    get_card_id_by_name,
    get_current_open_bill_id,
    get_default_card_id,
    get_pending_action,
    get_memorized_category,
    get_open_bill_summary,
    list_cards,
    list_installment_groups,
    list_open_bills,
    pay_bill_amount,
    set_card_limit,
    set_default_card,
    set_pending_action,
    update_card_reminder_settings,
)
from utils_date import extract_date_from_text, fmt_br, now_tz, today_tz
from utils_text import fmt_brl, normalize_text, parse_money


def _pick_card_id(user_id: int, card_name: str | None):
    if card_name:
        card_id = get_card_id_by_name(user_id, card_name)
        return card_id, card_name
    card_id = get_default_card_id(user_id)
    return card_id, "padrão"


def _find_card_name_in_text(user_id: int, text: str) -> str | None:
    norm = normalize_text(text)
    cards = list_cards(user_id)
    for card in sorted(cards, key=lambda c: len(normalize_text(c["name"])), reverse=True):
        name_norm = normalize_text(card["name"])
        if name_norm and name_norm in norm:
            return card["name"]
    return None


def _extract_unknown_card_candidate(text: str) -> str | None:
    norm = normalize_text(text)
    patterns = [
        r"\bmeu\s+([a-z0-9]+)\s+(?:vence|fecha)\b",
        r"\bfatura\s+do\s+([a-z0-9]+)\b",
        r"\bfatura\s+de\s+([a-z0-9]+)\b",
        r"\bcartao\s+([a-z0-9]+)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, norm)
        if m:
            candidate = (m.group(1) or "").strip()
            if candidate and candidate not in {"principal", "padrao", "padrão", "cartao", "fatura"}:
                return candidate
    return None


def _get_primary_or_single_card(user_id: int) -> dict | None:
    cards = list_cards(user_id)
    if not cards:
        return None
    current = next((c for c in cards if c.get("is_default")), None)
    if current:
        return current
    if len(cards) == 1:
        return cards[0]
    return None


def _resolve_card_from_context(user_id: int, text: str) -> tuple[dict | None, str | None]:
    cards = list_cards(user_id)
    if not cards:
        return None, "📭 Você ainda não tem cartões cadastrados."

    explicit_name = _find_card_name_in_text(user_id, text)
    if explicit_name:
        card_id = get_card_id_by_name(user_id, explicit_name)
        if not card_id:
            return None, f"❌ Não achei o cartão '{explicit_name}'."
        return get_card_by_id(user_id, card_id), None

    if any(x in normalize_text(text) for x in ("deste cartao", "desse cartao", "cartao atual", "cartao principal", "padrao", "padrão")):
        current = _get_primary_or_single_card(user_id)
        if current:
            return current, None
        return None, "Você tem mais de um cartão. Me diga qual deles você quer consultar. Ex: **fatura nubank**."

    return None, None


def _infer_category(user_id: int, desc: str) -> str:
    raw_norm = normalize_text(desc)
    categoria = get_memorized_category(user_id, raw_norm) or "outros"
    if categoria == "outros":
        try:
            categoria_gpt = classify_category_with_gpt(raw_norm)
            if categoria_gpt:
                categoria = categoria_gpt
        except Exception:
            pass
    return categoria


def _is_yes(text: str) -> bool:
    return normalize_text(text) in {"sim", "s", "yes", "y", "quero", "claro", "ok", "pode"}


def _is_no(text: str) -> bool:
    return normalize_text(text) in {"nao", "não", "n", "no", "cancelar", "cancela", "agora nao", "agora não"}


def _is_delete(text: str) -> bool:
    return normalize_text(text) in {"excluir", "excluir cartao", "excluir cartão", "deletar", "apagar", "remover", "delete"}


def _prompt_duplicate_card(user_id: int, card_name: str, payload: dict, minutes: int = 20) -> str:
    """
    Salva o pending com step 'duplicate_card_name' e devolve a mensagem de aviso.
    Preserva closing_day / due_day no payload se já foram coletados.
    """
    existing_id = get_card_id_by_name(user_id, card_name)
    existing_card = get_card_by_id(user_id, existing_id) if existing_id else None

    payload["step"] = "duplicate_card_name"
    payload["existing_card_id"] = existing_id
    payload["existing_card_name"] = card_name
    set_pending_action(user_id, "credit_card_setup", payload, minutes=minutes)

    existing_info = ""
    if existing_card:
        existing_info = (
            f"\n📋 Cartão atual: fecha dia {existing_card['closing_day']} / vence dia {existing_card['due_day']}"
        )

    return (
        f"⚠️ Já existe um cartão chamado **{card_name}**.{existing_info}\n\n"
        "• Digite um **novo nome** para criar com outro nome\n"
        "• **excluir** para remover o cartão existente\n"
        "• **cancelar** para desistir"
    )


def _parse_day(text: str) -> int | None:
    norm = normalize_text(text)
    m = re.search(r"\b(\d{1,2})\b", norm)
    if not m:
        return None
    day = int(m.group(1))
    if 1 <= day <= 31:
        return day
    return None


def _parse_card_name_from_create(text: str) -> str | None:
    m = re.search(r"criar\s+cart[aã]o\s+(.+)$", text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).strip()
    raw = re.sub(r"\s+fecha\s+\d{1,2}.*$", "", raw, flags=re.IGNORECASE).strip()
    return raw or None


def _card_summary(card: dict) -> str:
    reminder_txt = "desativado"
    if card.get("reminders_enabled"):
        reminder_txt = f"{int(card.get('reminders_days_before') or 3)} dia(s) antes"
    principal = "Sim" if card.get("is_default") else "Não"
    limit_txt = fmt_brl(float(card["credit_limit"])) if card.get("credit_limit") else "não definido"
    return (
        f"• Nome: {card['name']}\n"
        f"• Fechamento: dia {card['closing_day']}\n"
        f"• Vencimento: dia {card['due_day']}\n"
        f"• Limite: {limit_txt}\n"
        f"• Cartão principal: {principal}\n"
        f"• Lembrete: {reminder_txt}"
    )


def _ask_credit_limit_or_finish(user_id: int, payload: dict) -> str:
    """Pergunta sobre limite de crédito antes de finalizar o setup, se ainda não perguntou."""
    if payload.get("credit_limit_asked"):
        return _finish_card_setup(user_id, int(payload["card_id"]), ask_primary=bool(payload.get("ask_primary")))
    payload["credit_limit_asked"] = True
    payload["step"] = "credit_limit_ask"
    set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
    return "Deseja definir um limite de crédito para este cartão? Ex: **5000** ou **não**."


def _finish_card_setup(user_id: int, card_id: int, ask_primary: bool) -> str:
    card = get_card_by_id(user_id, card_id)
    if not card:
        clear_pending_action(user_id)
        return "❌ Não consegui localizar o cartão recém-criado."

    if ask_primary:
        set_pending_action(
            user_id,
            "credit_card_setup",
            {"step": "set_primary", "card_id": card_id},
            minutes=20,
        )
        return (
            f"✅ Cartão **{card['name']}** registrado com sucesso!\n"
            f"Confira os detalhes:\n{_card_summary(card)}\n\n"
            f"Deseja tornar o **{card['name']}** seu cartão principal? Responda **sim** ou **não**."
        )

    clear_pending_action(user_id)
    return (
        f"✅ Cartão **{card['name']}** registrado com sucesso!\n"
        f"Confira os detalhes:\n{_card_summary(card)}"
    )


def start_card_create_flow(user_id: int, text: str = "") -> str:
    existing_cards = list_cards(user_id)
    inferred_name = _parse_card_name_from_create(text)

    # Detecta duplicata imediatamente ao inferir o nome
    if inferred_name and card_name_exists(user_id, inferred_name):
        payload = {
            "card_name": inferred_name,
            "existing_count": len(existing_cards),
            "ask_primary": len(existing_cards) > 0,
        }
        return _prompt_duplicate_card(user_id, inferred_name, payload)

    payload = {
        "step": "name" if not inferred_name else "closing_day",
        "card_name": inferred_name,
        "existing_count": len(existing_cards),
    }
    set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
    if inferred_name:
        return f"Perfeito. Quando fecha a fatura do cartão **{inferred_name}**?"
    return "Qual cartão deseja registrar?"


def _ask_set_primary_flow(user_id: int, card_name: str | None = None) -> str:
    cards = list_cards(user_id)
    if not cards:
        return "📭 Você ainda não tem cartões cadastrados."

    if card_name:
        card_id = get_card_id_by_name(user_id, card_name)
        if not card_id:
            return f"❌ Não achei o cartão '{card_name}'."
        set_pending_action(
            user_id,
            "credit_card_set_primary",
            {"card_id": card_id},
            minutes=20,
        )
        return f"Deseja tornar o cartão **{card_name}** o seu principal? Responda **sim** ou **não**."

    lines = ["Qual cartão você quer definir como principal?"]
    for c in cards:
        badge = " (atual)" if c.get("is_default") else ""
        lines.append(f"• {c['name']}{badge}")
    set_pending_action(user_id, "credit_card_set_primary", {"step": "choose"}, minutes=20)
    return "\n".join(lines)


def _resolve_set_primary(user_id: int, text: str, pending: dict) -> str | None:
    payload = dict(pending.get("payload") or {})
    answer = (text or "").strip()

    if payload.get("step") == "choose":
        if _is_no(answer):
            clear_pending_action(user_id)
            return "Perfeito. Mantive o cartão principal atual."
        card_name = _find_card_name_in_text(user_id, answer) or answer.strip()
        card_id = get_card_id_by_name(user_id, card_name)
        if not card_id:
            return "Não encontrei esse cartão. Me diga o nome exatamente como aparece na lista."
        set_pending_action(user_id, "credit_card_set_primary", {"card_id": card_id}, minutes=20)
        card = get_card_by_id(user_id, card_id)
        return f"Deseja tornar o cartão **{card['name']}** o seu principal? Responda **sim** ou **não**."

    card_id = payload.get("card_id")
    if not card_id:
        clear_pending_action(user_id)
        return None
    card = get_card_by_id(user_id, int(card_id))
    if not card:
        clear_pending_action(user_id)
        return "❌ Não achei esse cartão."

    if _is_yes(answer):
        set_default_card(user_id, int(card_id))
        clear_pending_action(user_id)
        card = get_card_by_id(user_id, int(card_id))
        return f"✅ O cartão **{card['name']}** agora é o seu principal.\n{_card_summary(card)}"

    if _is_no(answer):
        clear_pending_action(user_id)
        return "Perfeito. Mantive o cartão principal atual."

    return f"Responda **sim** para tornar **{card['name']}** o principal ou **não** para cancelar."


def resolve_pending(user_id: int, text: str, pending: dict | None = None) -> str | None:
    pending = pending or get_pending_action(user_id)
    if not pending:
        return None

    if pending.get("action_type") == "credit_card_set_primary":
        return _resolve_set_primary(user_id, text, pending)

    if pending.get("action_type") != "credit_card_setup":
        return None

    payload = dict(pending.get("payload") or {})
    step = payload.get("step")
    answer = (text or "").strip()

    if _is_no(answer) and step not in {"reminder_opt_in", "set_primary", "duplicate_card_name", "confirm_delete_existing_card"}:
        clear_pending_action(user_id)
        return "❌ Cadastro de cartão cancelado."

    # ── Novo step: nome duplicado detectado ──────────────────────────────────
    if step == "duplicate_card_name":
        if _is_no(answer):
            clear_pending_action(user_id)
            return "❌ Cadastro de cartão cancelado."

        if _is_delete(answer):
            # Pede confirmação antes de excluir
            existing_name = payload.get("existing_card_name", "")
            payload["step"] = "confirm_delete_existing_card"
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            return (
                f"⚠️ Tem certeza que deseja **excluir** o cartão **{existing_name}**?\n"
                "Isso irá remover todas as faturas e transações associadas.\n\n"
                "Responda **sim** para confirmar ou **não** para cancelar."
            )

        # Usuário digitou um novo nome
        new_name = answer.strip()
        if not new_name:
            return "Digite o novo nome do cartão ou **excluir** para remover o existente."

        # Verifica se o novo nome também é duplicado
        if card_name_exists(user_id, new_name):
            payload["existing_card_name"] = new_name
            payload["existing_card_id"] = get_card_id_by_name(user_id, new_name)
            existing_card = get_card_by_id(user_id, payload["existing_card_id"])
            existing_info = ""
            if existing_card:
                existing_info = f"\n📋 fecha dia {existing_card['closing_day']} / vence dia {existing_card['due_day']}"
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            return (
                f"⚠️ Já existe um cartão chamado **{new_name}**.{existing_info}\n\n"
                "Digite outro nome ou **excluir** para remover o existente."
            )

        payload["card_name"] = new_name

        # Se closing_day e due_day já foram coletados (via comando inline), cria direto
        if payload.get("closing_day") and payload.get("due_day"):
            card_id = create_card(
                user_id=user_id,
                name=new_name,
                closing_day=int(payload["closing_day"]),
                due_day=int(payload["due_day"]),
            )
            first_card = int(payload.get("existing_count") or 0) == 0
            if first_card:
                set_default_card(user_id, card_id)
            payload["card_id"] = card_id
            payload["step"] = "reminder_opt_in"
            payload["ask_primary"] = not first_card
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            card = get_card_by_id(user_id, card_id)
            first_card_txt = "\nComo este é seu primeiro cartão, ele já foi definido como principal." if first_card else ""
            return (
                f"✅ Cartão **{card['name']}** registrado com sucesso! Confira os detalhes:\n"
                f"{_card_summary(card)}"
                f"{first_card_txt}\n\n"
                "Gostaria de receber notificações antes do vencimento da fatura? Responda **sim** ou **não**."
            )

        # Sem dias coletados ainda → continua o fluxo normal
        payload["step"] = "closing_day"
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
        return f"Quando fecha a fatura do cartão **{new_name}**?"

    # ── Novo step: confirmação de exclusão do cartão existente ───────────────
    if step == "confirm_delete_existing_card":
        existing_id = payload.get("existing_card_id")
        existing_name = payload.get("existing_card_name", "")

        if _is_yes(answer) and existing_id:
            deleted = delete_card(user_id, int(existing_id))
            if not deleted:
                clear_pending_action(user_id)
                return f"❌ Não consegui excluir o cartão **{existing_name}**. Tente novamente."

            # Após excluir, pergunta se quer criar um cartão com o mesmo nome agora
            clear_pending_action(user_id)
            return (
                f"✅ Cartão **{existing_name}** excluído com sucesso.\n\n"
                f"Se quiser criar um novo cartão com esse nome, use:\n"
                f"**criar cartao {existing_name} fecha X vence Y**"
            )

        if _is_no(answer):
            # Volta para o step de nome duplicado
            payload["step"] = "duplicate_card_name"
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            return (
                f"Tudo bem. O cartão **{existing_name}** foi mantido.\n\n"
                "Digite um **novo nome** para o cartão ou **cancelar** para desistir."
            )

        return f"Responda **sim** para excluir **{existing_name}** ou **não** para cancelar."

    # ─────────────────────────────────────────────────────────────────────────

    if step == "name":
        name = answer
        if normalize_text(name).startswith("criar cartao") or normalize_text(name).startswith("criar cartão"):
            name = _parse_card_name_from_create(answer) or ""
        name = name.strip()
        if not name:
            return "Qual é o nome do cartão? Ex: **Nubank**"

        # Detecta duplicata antes de pedir os dias
        if card_name_exists(user_id, name):
            payload["card_name"] = name
            return _prompt_duplicate_card(user_id, name, payload)

        payload["card_name"] = name
        payload["step"] = "closing_day"
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
        return f"Quando fecha a fatura do cartão **{name}**?"

    if step == "closing_day":
        closing_day = _parse_day(answer)
        if closing_day is None:
            return "Me diga o dia de fechamento com um número entre **1** e **31**. Ex: **dia 1**."
        payload["closing_day"] = closing_day
        payload["step"] = "due_day"
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
        return f"Quando vence a fatura do cartão **{payload['card_name']}**?"

    if step == "due_day":
        due_day = _parse_day(answer)
        if due_day is None:
            return "Me diga o dia de vencimento com um número entre **1** e **31**. Ex: **dia 8**."

        payload["due_day"] = due_day
        card_name = payload["card_name"]

        # Detecta duplicata na última etapa (edge case: nome entrado antes de existir outro cartão igual)
        if card_name_exists(user_id, card_name):
            return _prompt_duplicate_card(user_id, card_name, payload)

        card_id = create_card(
            user_id=user_id,
            name=card_name,
            closing_day=int(payload["closing_day"]),
            due_day=due_day,
        )
        payload["card_id"] = card_id
        first_card = int(payload.get("existing_count") or 0) == 0
        if first_card:
            set_default_card(user_id, card_id)

        payload["step"] = "reminder_opt_in"
        payload["ask_primary"] = not first_card
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)

        card = get_card_by_id(user_id, card_id)
        first_card_txt = "\nComo este é seu primeiro cartão, ele já foi definido como principal." if first_card else ""
        return (
            f"✅ Cartão **{card['name']}** registrado com sucesso! Confira os detalhes:\n"
            f"{_card_summary(card)}"
            f"{first_card_txt}\n\n"
            "Gostaria de receber notificações antes do vencimento da fatura? Responda **sim** ou **não**."
        )

    if step == "reminder_opt_in":
        card_id = int(payload["card_id"])
        if _is_yes(answer):
            payload["step"] = "reminder_days"
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            return "Quantos dias antes do vencimento você quer ser avisado? Ex: **1**, **3** ou **5**."

        update_card_reminder_settings(user_id, card_id, enabled=False)
        return _ask_credit_limit_or_finish(user_id, payload)

    if step == "reminder_days":
        card_id = int(payload["card_id"])
        days_before = _parse_day(answer)
        if days_before is None:
            return "Me diga em quantos dias antes devo avisar. Ex: **3**."
        update_card_reminder_settings(user_id, card_id, enabled=True, days_before=days_before)
        return _ask_credit_limit_or_finish(user_id, payload)

    if step == "credit_limit_ask":
        card_id = int(payload["card_id"])
        if _is_no(answer):
            return _finish_card_setup(user_id, card_id, ask_primary=bool(payload.get("ask_primary")))
        limit_val = parse_money(answer)
        if limit_val is None or float(limit_val) <= 0:
            return "Me diga o valor do limite. Ex: **5000** ou responda **não** para pular."
        set_card_limit(user_id, card_id, float(limit_val))
        # O limite já aparece no _card_summary dentro de _finish_card_setup — não precisa prefixar
        return _finish_card_setup(user_id, card_id, ask_primary=bool(payload.get("ask_primary")))

    if step == "set_primary":
        card_id = int(payload["card_id"])
        card = get_card_by_id(user_id, card_id)
        if not card:
            clear_pending_action(user_id)
            return "❌ Não achei esse cartão para definir como principal."
        if _is_yes(answer):
            set_default_card(user_id, card_id)
            clear_pending_action(user_id)
            card = get_card_by_id(user_id, card_id)
            return f"✅ Perfeito. O cartão **{card['name']}** agora é o seu principal.\n{_card_summary(card)}"
        clear_pending_action(user_id)
        return f"Perfeito. Mantive o cartão principal atual.\n{_card_summary(card)}"

    clear_pending_action(user_id)
    return None


def handle(user_id: int, text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None

    t_low = t.lower().strip()
    t_norm = normalize_text(t)

    if any(x in t_norm for x in ("mudar", "trocar", "definir", "colocar")) and any(x in t_norm for x in ("cartao principal", "cartao padrao", "cartao padrão", "principal")):
        return _ask_set_primary_flow(user_id, _find_card_name_in_text(user_id, t))

    if any(x in t_norm for x in ("fecha dia", "vence dia")) and "cartao" in t_norm and not any(x in t_norm for x in ("criar", "registrar", "novo cartao")):
        cards = list_cards(user_id)
        if not cards:
            return "📭 Você ainda não tem cartões cadastrados."
        target_day = _parse_day(t)
        if target_day is not None:
            if "fecha" in t_norm:
                matches = [c for c in cards if int(c["closing_day"]) == target_day]
                if matches:
                    names = ", ".join(f"**{c['name']}**" for c in matches)
                    return f"💳 Cartão(ões) que fecham dia {target_day}: {names}"
                return f"Não encontrei cartão com fechamento no dia {target_day}."
            if "vence" in t_norm:
                matches = [c for c in cards if int(c["due_day"]) == target_day]
                if matches:
                    names = ", ".join(f"**{c['name']}**" for c in matches)
                    return f"💳 Cartão(ões) que vencem dia {target_day}: {names}"
                return f"Não encontrei cartão com vencimento no dia {target_day}."

    if "cartao principal" in t_norm or "cartao padrao" in t_norm or "cartao padrão" in t_norm:
        if any(x in t_norm for x in ("qual", "quais", "meu", "atual")):
            cards = list_cards(user_id)
            if not cards:
                return "📭 Você ainda não tem cartões cadastrados."
            current = next((c for c in cards if c.get("is_default")), None)
            if current:
                return (
                    f"💳 Seu cartão principal é **{current['name']}**.\n"
                    f"Fechamento: dia {current['closing_day']} | Vencimento: dia {current['due_day']}"
                )
            return "Você tem cartões cadastrados, mas ainda não definiu um principal."

    if "fatura" in t_norm or "faturas" in t_norm:
        if any(x in t_norm for x in ("mostrar", "mostra", "ver", "quais", "minhas", "tenho", "quanto", "valor", "em aberto", "atual")):
            card, error = _resolve_card_from_context(user_id, t)
            if error:
                return error
            if card:
                t_low = f"fatura {card['name']}"
            elif "faturas" in t_norm or "minhas" in t_norm:
                t_low = "faturas"
            else:
                current = _get_primary_or_single_card(user_id)
                if current:
                    t_low = f"fatura {current['name']}"
                else:
                    return "Você tem mais de um cartão. Me diga qual deles quer consultar. Ex: **quanto tenho na fatura do Nubank?**"

    if any(x in t_norm for x in ("vence quando", "fecha quando")):
        card, error = _resolve_card_from_context(user_id, t)
        if error:
            return error
        if card:
            if "vence" in t_norm:
                return f"💳 O cartão **{card['name']}** vence no dia **{card['due_day']}**."
            return f"💳 O cartão **{card['name']}** fecha no dia **{card['closing_day']}**."

        candidate = _extract_unknown_card_candidate(t)
        if candidate:
            return (
                f"Não encontrei um cartão chamado **{candidate}**.\n"
                f"Se quiser, posso te ajudar a cadastrar esse cartão agora. É só me mandar:\n"
                f"**criar cartao {candidate}**"
            )

    if ("cartao" in t_norm or "cartoes" in t_norm) and "fatura" not in t_norm:
        if any(x in t_norm for x in ("quais", "meus", "tenho", "registrado", "registrados", "listar", "mostrar", "mostra", "ver")):
            t_low = "listar cartoes"

    if t_low.startswith("criar cartao") or t_low.startswith("criar cartão"):
        m = re.search(r"criar\s+cart[aã]o\s+(.+?)\s+fecha\s+(\d{1,2})\s+vence\s+(\d{1,2})", t, re.IGNORECASE)
        if not m:
            return start_card_create_flow(user_id, t)

        name = m.group(1).strip()
        fecha = int(m.group(2))
        vence = int(m.group(3))

        # Bloqueia duplicata antes de criar
        if card_name_exists(user_id, name):
            existing_count = len(list_cards(user_id))
            payload = {
                "card_name": name,
                "closing_day": fecha,
                "due_day": vence,
                "existing_count": existing_count,
                "ask_primary": existing_count > 0,
            }
            return _prompt_duplicate_card(user_id, name, payload)

        try:
            existing_count = len(list_cards(user_id))
            card_id = create_card(user_id=user_id, name=name, closing_day=fecha, due_day=vence)
            if existing_count == 0:
                set_default_card(user_id, card_id)
            set_pending_action(
                user_id,
                "credit_card_setup",
                {
                    "step": "reminder_opt_in",
                    "card_id": card_id,
                    "ask_primary": existing_count > 0,
                },
                minutes=20,
            )
            card = get_card_by_id(user_id, card_id)
            first_card_txt = "\nComo este é seu primeiro cartão, ele já foi definido como principal." if existing_count == 0 else ""
            return (
                f"✅ Cartão **{name}** registrado com sucesso! Confira os detalhes:\n"
                f"{_card_summary(card)}"
                f"{first_card_txt}\n\n"
                "Gostaria de receber notificações antes do vencimento da fatura? Responda **sim** ou **não**."
            )
        except Exception as e:
            return f"❌ Erro criando cartão: {e}"

    if t_low.startswith("padrao ") or t_low.startswith("padrão "):
        name = re.sub(r"^padr[aã]o\s+", "", t, flags=re.IGNORECASE).strip()
        card_id = get_card_id_by_name(user_id, name)
        if not card_id:
            return f"❌ Não achei o cartão '{name}'. Crie com: criar cartao {name} fecha 10 vence 17"

        set_default_card(user_id, card_id)
        return f"✅ Cartão padrão definido: {name}"

    if t_low in ("cartoes", "cartões", "listar cartoes", "listar cartões"):
        cards = list_cards(user_id)
        if not cards:
            return "📭 Você ainda não tem cartões. Crie com: criar cartao nubank fecha 10 vence 17"

        lines = ["💳 **Seus cartões:**"]
        for c in cards:
            badge = " (padrão)" if c.get("is_default") else ""
            lines.append(f"- {c['name']}{badge} — fecha dia {c['closing_day']} / vence dia {c['due_day']}")
        return "\n".join(lines)

    if t_low.startswith("credito"):
        rest = t[len("credito"):].strip()
        if not rest:
            return "Use: credito 120 mercado OU credito nubank 120 mercado"

        dt_evento, rest2 = extract_date_from_text(rest)
        if dt_evento is None:
            dt_evento = now_tz()
        purchased_at = dt_evento.date()

        valor = parse_money(rest2)
        if valor is None:
            return "❌ Não achei o valor. Ex: credito 120 mercado"

        tokens = rest2.split()
        card_name = None
        if tokens and parse_money(tokens[0]) is None:
            card_name = tokens[0]
            rest_desc = " ".join(tokens[1:])
        else:
            rest_desc = rest2

        nota = normalize_text(rest_desc)
        categoria = _infer_category(user_id, rest_desc)

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            if card_name:
                return f"❌ Não achei o cartão '{card_name}'. Crie com: criar cartao {card_name} fecha 10 vence 17"
            return "❓ Você não tem cartão padrão. Defina com: padrao NOME (ou crie: criar cartao nubank fecha 10 vence 17)"

        try:
            tx_id, due, _bill_id = add_credit_purchase(
                user_id=user_id,
                card_id=card_id,
                valor=float(valor),
                categoria=categoria,
                nota=nota,
                purchased_at=purchased_at,
            )
            return (
                f"💳 Compra no crédito registrada: {fmt_brl(valor)}\n"
                f"📅 Data da compra: {fmt_br(purchased_at)}\n"
                f"📌 Fatura atual: {fmt_brl(due)}\n"
                f"ID crédito: CT#{tx_id}"
            )
        except Exception as e:
            return f"❌ Erro registrando compra no crédito: {e}"

    if t_low in ("criar parcelas", "criar parcela", "parcelas"):
        return "Use: `parcelar 300 em 3x no cartao nubank` (ex: `parcelar 120 em 4x no cartao nubank`)"

    if t_low.startswith("parcelei "):
        t_low = "parcelar " + t_low[len("parcelei "):]

    if t_low.startswith("parcelar"):
        valor = parse_money(t_low)
        if valor is None:
            return "Use: parcelar 300 em 3x no cartao nubank"

        n = 1
        mx = re.search(r"(\d+)\s*x", t_low)
        if mx:
            try:
                n = int(mx.group(1))
            except Exception:
                n = 1

        card_name = None
        m = re.search(r"(?:no\s+)?cart[aã]o\s+(.+)$", t_low)
        if m:
            card_name = m.group(1).strip()

        dt_evento, rest2 = extract_date_from_text(t)
        if dt_evento is None:
            dt_evento = now_tz()
        purchased_at = dt_evento.date()

        desc_clean = rest2
        desc_clean = re.sub(r"\bparcelei?\b", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = re.sub(r"\b\d+[\.,]?\d*\b", "", desc_clean)
        desc_clean = re.sub(r"\b\d+\s*x\b", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = re.sub(r"\bem\b", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = re.sub(r"\bno\s+cart[aã]o\s+\S+", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = re.sub(r"\bcart[aã]o\s+\S+", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = " ".join(desc_clean.split())

        nota = normalize_text(desc_clean) if desc_clean.strip() else normalize_text(t)
        categoria = _infer_category(user_id, desc_clean or t)

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            if card_name:
                return f"❌ Não achei o cartão '{card_name}'. Crie com: criar cartao {card_name} fecha 10 vence 17"
            return "❓ Qual cartão?\nEx: `parcelei 500 em 5x no cartao nubank`\nDica: defina um padrão com `padrao nubank`."

        try:
            ret = add_credit_purchase_installments(
                user_id=user_id,
                card_id=card_id,
                valor_total=float(valor),
                categoria=categoria,
                nota=nota,
                purchased_at=purchased_at,
                installments=n,
            )
            result = ret[0] if isinstance(ret, tuple) else ret
            total = float(ret[1]) if isinstance(ret, tuple) and len(ret) >= 2 else float(valor)
            tx_ids = result.get("tx_ids") or []
            group_id = result.get("group_id")
            ids_str = ", ".join(f"#{x}" for x in tx_ids[:10]) if tx_ids else "(sem ids)"
            if len(tx_ids) > 10:
                ids_str += " ..."
            return (
                f"💳 Parcelado no cartão ({resolved_name}): R$ {float(valor):.2f} em {n}x\n"
                f"📌 Total lançado nas faturas: R$ {float(total):.2f}\n"
                f"Grupo: {group_id}\n"
                f"IDs: {ids_str}"
            )
        except Exception as e:
            return f"❌ Erro ao parcelar no cartão: {e}"

    if t_low.startswith("pagar fatura") or t_low.startswith("paguei fatura"):
        rest = re.sub(r"^paguei?\s+fatura", "", t, flags=re.IGNORECASE).strip()
        tokens = rest.split() if rest else []
        amount = None
        card_name = None

        if tokens:
            last_val = parse_money(tokens[-1])
            if last_val is not None:
                amount = float(last_val)
                tokens = tokens[:-1]
            if tokens:
                card_name = " ".join(tokens).strip()

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            return "❓ Você não tem cartão padrão. Defina com: padrao NOME"

        try:
            bill_id = get_current_open_bill_id(user_id, card_id, today_tz())
            if not bill_id:
                return "📭 Nenhuma fatura aberta do período atual para pagar."

            res = pay_bill_amount(user_id, card_id, resolved_name, amount, bill_id=bill_id)
            if isinstance(res, dict) and res.get("error") == "amount_too_high":
                return (
                    "❌ Valor maior do que o em aberto.\n"
                    f"Em aberto: {fmt_brl(res['due'])} | Total: {fmt_brl(res['total'])} | Já pago: {fmt_brl(res['paid_amount'])}"
                )
            if isinstance(res, dict) and res.get("error") == "invalid_amount":
                return "❌ Valor inválido. Use: pagar fatura 300"
            if not res:
                return "📭 Nada para pagar."
            return (
                f"✅ Pagamento registrado: {fmt_brl(res['paid'])}\n"
                f"Conta agora: {fmt_brl(res['new_balance'])}\n"
                f"ID lançamento: #{res['launch_id']}"
            )
        except Exception as e:
            return f"❌ Erro ao pagar fatura: {e}"

    if t_low in ("faturas", "listar faturas", "faturas abertas", "listar faturas abertas", "listar fatura", "listar faturas em aberto"):
        try:
            rows = list_open_bills(user_id)
            if not rows:
                return "📭 Nenhuma fatura em aberto."

            months = [
                "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
            ]
            groups = defaultdict(list)
            for r in rows:
                total = float(r["total"] or 0)
                paid = float(r["paid_amount"] or 0)
                due = max(0.0, total - paid)
                if total == 0.0 and paid == 0.0 and due == 0.0:
                    continue
                ps = r["period_start"]
                groups[(ps.year, ps.month)].append((r, total, paid, due))

            if not groups:
                return "📭 Nenhuma fatura em aberto (as futuras zeradas foram ocultadas)."

            lines = ["🧾 **Faturas em aberto (por mês):**", ""]
            for (y, m) in sorted(groups.keys()):
                lines.append(f"📅 **{months[m-1]}/{y}:**")
                items = sorted(groups[(y, m)], key=lambda it: (it[0]["card_name"] or "").lower())
                for (r, total, paid, due) in items:
                    lines.append(f"• {r['card_name']}: Total {fmt_brl(total)} | Pago {fmt_brl(paid)} | Em aberto {fmt_brl(due)}")
                lines.append("")
            return "\n".join(lines).strip()
        except Exception as e:
            return f"❌ Erro ao listar faturas: {e}"

    if t_low.startswith("fatura ") or t_low == "fatura":
        card = None
        error = None

        if t_low != "fatura":
            requested_name = t_low.split(" ", 1)[1].strip()
            card_id = get_card_id_by_name(user_id, requested_name)
            if card_id:
                card = get_card_by_id(user_id, card_id)
            else:
                card, error = _resolve_card_from_context(user_id, requested_name)
        else:
            card, error = _resolve_card_from_context(user_id, t)
            if card is None and error is None:
                card = _get_primary_or_single_card(user_id)
                if card is None:
                    error = "Você tem mais de um cartão. Me diga qual deles quer consultar. Ex: **fatura nubank**."

        if error:
            return error
        if not card:
            return "❓ Não consegui identificar qual cartão você quer consultar."

        try:
            res = get_open_bill_summary(user_id, int(card["id"]), as_of=today_tz())
            if not res:
                return f"📭 Nenhuma fatura aberta para {card['name']}."

            bill, items = res
            total = float(bill["total"] or 0)
            paid = float(bill.get("paid_amount", 0) or 0)
            due = max(0.0, total - paid)
            lines = [
                f"💳 Fatura atual ({card['name']}) {fmt_br(bill['period_start'])} → {fmt_br(bill['period_end'])}",
                f"Total: {fmt_brl(total)} | Pago: {fmt_brl(paid)} | Em aberto: {fmt_brl(due)}",
            ]
            # mostra uso do limite se definido
            card_with_limit = get_card_by_id(user_id, int(card["id"]))
            if card_with_limit and card_with_limit.get("credit_limit"):
                lim = float(card_with_limit["credit_limit"])
                avail = max(0.0, lim - total)
                pct = round((total / lim) * 100) if lim > 0 else 0
                lines.append(f"Limite: {fmt_brl(lim)} | Disponível: {fmt_brl(avail)} ({100 - pct}%)")
            lines.append("")
            for it in items[:10]:
                parcela = ""
                if it.get("installment_no") and it.get("installments_total"):
                    parcela = f" [{it['installment_no']}/{it['installments_total']}]"
                lines.append(
                    f"• {fmt_brl(it['valor'])} | {it['categoria'] or 'outros'} | {fmt_br(it['purchased_at'])} | {it['nota'] or ''}{parcela}"
                )
            if len(items) > 10:
                lines.append(f"\n… e mais {len(items) - 10} lançamento(s).")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Erro ao buscar fatura: {e}"

    # ── Limite de crédito ─────────────────────────────────────────────────────
    # "definir limite nubank 5000" / "limite do nubank 5000" / "limite 3000"
    _limit_set_match = re.match(
        r"^(?:definir|setar|colocar|mudar|alterar)\s+limite"
        r"(?:\s+(?:do|de|no|da)\s+)?"
        r"(?P<card>[a-zA-ZÀ-ú0-9 ]+?)?\s+"
        r"(?P<val>[\d,.]+)$",
        t_low.strip(),
    )
    if not _limit_set_match:
        # "limite [cartão] [valor]" sem prefixo de ação
        _limit_set_match = re.match(
            r"^limite\s+(?:(?:do|de|no|da)\s+)?(?P<card>[a-zA-ZÀ-ú0-9 ]+?)?\s*(?P<val>[\d,.]+)$",
            t_low.strip(),
        )
    if _limit_set_match:
        raw_val = _limit_set_match.group("val") or ""
        raw_card = (_limit_set_match.group("card") or "").strip()
        amount = parse_money(raw_val)
        if amount is None or float(amount) <= 0:
            return "❌ Valor inválido. Ex: *definir limite nubank 5000*"

        card_name_hint = raw_card if raw_card else _find_card_name_in_text(user_id, t)
        card_id, resolved_name = _pick_card_id(user_id, card_name_hint)
        if not card_id:
            return "❓ Não encontrei o cartão. Verifique o nome com: *cartões*"

        ok = set_card_limit(user_id, card_id, float(amount))
        if not ok:
            return "❌ Não consegui atualizar o limite."
        return f"✅ Limite do **{resolved_name}** definido em {fmt_brl(float(amount))}."

    # "ver limite [cartão]" / "qual limite do nubank"
    _limit_view_match = re.search(r"\blimite\b", t_norm)
    if _limit_view_match and not any(x in t_norm for x in ("definir", "setar", "colocar", "mudar", "alterar")):
        card_name_hint = _find_card_name_in_text(user_id, t)
        card_id, resolved_name = _pick_card_id(user_id, card_name_hint)
        if not card_id:
            cards = list_cards(user_id)
            if not cards:
                return "📭 Você ainda não tem cartões cadastrados."
            lines = ["💳 **Limites dos cartões:**"]
            for c in cards:
                lim = fmt_brl(float(c["credit_limit"])) if c.get("credit_limit") else "não definido"
                badge = " ⭐" if c.get("is_default") else ""
                lines.append(f"• {c['name']}{badge}: {lim}")
            return "\n".join(lines)

        card = get_card_by_id(user_id, card_id)
        if not card:
            return "❓ Cartão não encontrado."
        lim = card.get("credit_limit")
        if lim is None:
            return f"💳 **{resolved_name}** não tem limite definido.\nDefina com: *definir limite {resolved_name} 5000*"

        # busca uso atual (fatura aberta)
        try:
            res = get_open_bill_summary(user_id, card_id, as_of=today_tz())
            used = float(res[0]["total"] or 0) if res else 0.0
        except Exception:
            used = 0.0
        lim_f = float(lim)
        avail = max(0.0, lim_f - used)
        pct = round((used / lim_f) * 100) if lim_f > 0 else 0
        bar_filled = round(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        return (
            f"💳 **{resolved_name}** — Limite de crédito\n"
            f"Limite total:  {fmt_brl(lim_f)}\n"
            f"Usado:         {fmt_brl(used)} ({pct}%)\n"
            f"Disponível:    {fmt_brl(avail)}\n"
            f"[{bar}]"
        )

    # ── Pagar fatura com saldo da conta ───────────────────────────────────────
    if re.search(r"pagar\s+fatura\s+com\s+saldo|pagar\s+com\s+saldo|usar\s+saldo\s+para\s+pagar", t_norm):
        card_name_hint = _find_card_name_in_text(user_id, t)
        card_id, resolved_name = _pick_card_id(user_id, card_name_hint)
        if not card_id:
            return "❓ Você não tem cartão padrão. Informe o nome do cartão: *pagar fatura nubank com saldo*"

        amount_match = re.search(r"([\d,.]+)", t)
        amount = float(parse_money(amount_match.group(1))) if amount_match and parse_money(amount_match.group(1)) else None

        try:
            bill_id = get_current_open_bill_id(user_id, card_id, today_tz())
            if not bill_id:
                return "📭 Nenhuma fatura aberta para pagar."

            res = pay_bill_amount(user_id, card_id, resolved_name, amount, bill_id=bill_id)
            if isinstance(res, dict) and res.get("error") == "amount_too_high":
                return (
                    f"❌ Valor maior que o em aberto ({fmt_brl(res['due'])}).\n"
                    f"Use: *pagar fatura {resolved_name} com saldo {fmt_brl(res['due'])}*"
                )
            if isinstance(res, dict) and res.get("error") == "invalid_amount":
                return "❌ Valor inválido."
            if not res:
                return "📭 Nada para pagar."
            return (
                f"✅ Pagamento da fatura **{resolved_name}** realizado!\n"
                f"Valor pago: {fmt_brl(res['paid'])}\n"
                f"Saldo da conta: {fmt_brl(res['new_balance'])}"
            )
        except Exception as e:
            return f"❌ Erro ao pagar fatura: {e}"

    if t_low in ("parcelamentos", "listar parcelamentos"):
        rows = list_installment_groups(user_id, limit=15)
        if not rows:
            return "📭 Você não tem parcelamentos registrados."

        lines = ["📦 **Parcelamentos ativos:**"]
        for r in rows:
            n_total = int(r.get("n_total") or r.get("n_registered") or 0)
            n_pending = int(r.get("n_pending") or 0)
            if n_pending == 0:
                continue
            n_paid = n_total - n_pending
            total = float(r.get("total") or 0)
            pending = float(r.get("total_pending") or 0)
            nota = (r.get("nota") or "").strip()
            desc = f" — {nota}" if nota else ""
            progress = f"{n_paid}/{n_total} pagas"
            lines.append(
                f"• {r.get('card_name', '?')}{desc}\n"
                f"  💰 Total: {fmt_brl(total)} | Restante: {fmt_brl(pending)} ({progress})\n"
                f"  🔑 grupo: `{r.get('group_id') or ''}`"
            )

        if len(lines) == 1:
            return "✅ Você não tem parcelamentos em aberto."
        return "\n".join(lines)

    return None
