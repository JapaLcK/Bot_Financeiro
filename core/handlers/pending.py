# core/handlers/pending.py
"""
Resolve ações pendentes: confirmações de delete, lançamentos de mídia e esclarecimentos.
"""
from __future__ import annotations
import db
from utils_text import fmt_brl


def resolve_delete(user_id: int, confirmed: bool) -> str | None:
    """
    Verifica se existe uma pending_action para o usuário e a resolve.
    Trata: deletes de lançamento/caixinha/investimento e confirmação de lançamento via mídia.
    Retorna mensagem de resposta, ou None se não havia pending reconhecido.
    """
    pending = db.get_pending_action(user_id)
    if not pending:
        return None

    action_type = pending.get("action_type")

    # ── Confirmação de lançamento extraído de imagem ─────────────────────────
    if action_type == "confirm_media_launch":
        payload = pending.get("payload", {})
        db.clear_pending_action(user_id)

        if not confirmed:
            return "❌ Lançamento cancelado. Se quiser corrigir, escreva o comando manualmente."

        # Caminho novo: prévia estruturada. Registra EXATAMENTE o que o usuário
        # confirmou — categoria e data da prévia — sem re-inferir do texto. É o
        # que corrige "Amazônia" virar "compras online" na re-classificação.
        if payload.get("valor") is not None and payload.get("categoria"):
            from core.handlers.launches import add_from_entities

            criado_em = None
            data_iso = payload.get("data")
            if data_iso:
                from datetime import date as _date, datetime as _datetime, time as _time
                from utils_date import _tz
                try:
                    _d = _date.fromisoformat(data_iso)
                    criado_em = _datetime.combine(_d, _time(12, 0), tzinfo=_tz())
                except (ValueError, TypeError):
                    criado_em = None

            alvo = payload.get("alvo") or ""
            resp = add_from_entities(
                user_id,
                tipo=payload.get("tipo") or "despesa",
                valor=float(payload["valor"]),
                alvo=alvo,
                nota=alvo or None,
                categoria=payload.get("categoria") or "outros",
                # != "ai": respeita a categoria que o usuário confirmou; não
                # dispara o cross-check com regras locais.
                category_reason="image_confirmed",
                criado_em=criado_em,
                platform=payload.get("platform") or "whatsapp",
            )
            return f"✅ Lançamento registrado!\n{resp}"

        # Fallback legado: pendências antigas que só guardaram o texto.
        text = payload.get("text", "")
        if not text:
            return "⚠️ Não encontrei os dados do lançamento para confirmar. Tente digitar manualmente."

        from core.services.quick_entry import handle_quick_entry
        msg_out = handle_quick_entry(user_id, text)
        if msg_out:
            return f"✅ Lançamento registrado!\n{msg_out.text}"
        return f"⚠️ Não consegui registrar automaticamente. Tente: `{text}`"

    # ── Oferta "virar gasto fixo" (despesa que se repetiu) ───────────────────
    if action_type == "confirm_recurring_offer":
        payload = pending.get("payload", {})
        db.clear_pending_action(user_id)

        if not confirmed:
            # grava a recusa pra não re-sugerir a mesma combinação (merchant+valor)
            try:
                from db.recurring import dismiss_recurring_suggestion
                dismiss_recurring_suggestion(
                    user_id, payload.get("merchant_key", ""), payload.get("amount"),
                )
            except Exception:
                pass
            return "Ok, não marco como gasto fixo. 👍"

        from db.recurring import create_recurring_expense, mark_recurring_charged
        try:
            rec = create_recurring_expense(
                user_id,
                payload.get("name") or "Gasto fixo",
                float(payload.get("amount") or 0),
                payload.get("category") or "outros",
                int(payload.get("due_day") or 1),
                "account",
                payment_mode="autopay",
            )
            # A despesa deste mês que disparou a oferta JÁ foi lançada. O recorrente
            # nasce com due_day=hoje e start_date=hoje, então o charger autopay o
            # consideraria "vence hoje" e debitaria de novo no mesmo dia, dobrando o
            # lançamento. Marca o mês corrente como já cobrado — 1ª cobrança
            # automática cai só no mês que vem.
            from utils_date import today_tz
            mark_recurring_charged(user_id, rec["id"], today_tz().strftime("%Y-%m"))
        except (ValueError, TypeError) as e:
            return f"Não consegui criar o gasto fixo agora ({e}). Você pode cadastrar na aba *Recorrentes* do dashboard."
        return (
            f"✅ Pronto! *{rec['name']}* agora é *gasto fixo*: "
            f"{fmt_brl(rec['amount'])} todo dia {rec['due_day']}. "
            f"A Piggy lança sozinha. 🐷"
        )

    # só trata deletes abaixo
    if action_type not in ("delete_launch", "delete_launch_bulk", "delete_pocket", "delete_investment"):
        return None

    payload = pending.get("payload", {})

    if not confirmed:
        db.clear_pending_action(user_id)
        return "❌ Ação cancelada."

    if action_type == "delete_launch":
        launch_id = payload.get("launch_id")
        # display_id é o user_seq mostrado pro usuário; cai pro id interno
        # quando o pending foi criado em código antigo sem essa key.
        display_id = payload.get("display_id") or launch_id
        try:
            db.delete_launch_and_rollback(user_id, launch_id)
            db.clear_pending_action(user_id)
            return f"✅ Lançamento **#{display_id}** apagado e saldo revertido."
        except Exception as e:
            db.clear_pending_action(user_id)
            return f"Erro ao apagar lançamento #{display_id}: {e}"

    if action_type == "delete_launch_bulk":
        ids = payload.get("launch_ids", [])
        display_ids_map = payload.get("display_ids") or {}
        failed = []
        for lid in ids:
            try:
                db.delete_launch_and_rollback(user_id, lid)
            except Exception:
                failed.append(lid)
        ok_ids = [i for i in ids if i not in failed]
        db.clear_pending_action(user_id)
        # converte ids internos pra user_seq pra exibição (fallback: id interno)
        def _disp(lid):
            return display_ids_map.get(str(lid), display_ids_map.get(lid, lid))
        parts = []
        if ok_ids:
            parts.append("✅ Apagados: " + ", ".join(f"**#{_disp(i)}**" for i in ok_ids))
        if failed:
            parts.append("⚠️ Falha: " + ", ".join(f"#{_disp(i)}" for i in failed))
        return "\n".join(parts) or "Nada foi apagado."

    if action_type == "delete_pocket":
        pocket_name = payload.get("pocket_name")
        try:
            db.delete_pocket(user_id, pocket_name)
            db.clear_pending_action(user_id)
            return f"✅ Caixinha **{pocket_name}** deletada."
        except Exception as e:
            db.clear_pending_action(user_id)
            return f"Erro ao deletar caixinha: {e}"

    if action_type == "delete_investment":
        investment_name = payload.get("investment_name")
        try:
            db.delete_investment(user_id, investment_name)
            db.clear_pending_action(user_id)
            return f"✅ Investimento **{investment_name}** deletado."
        except Exception as e:
            db.clear_pending_action(user_id)
            return f"Erro ao deletar investimento: {e}"

    db.clear_pending_action(user_id)
    return None


def get_pending_clarification(user_id: int) -> dict | None:
    """
    Retorna o pending de esclarecimento se existir, ou None.
    """
    pending = db.get_pending_action(user_id)
    if pending and pending.get("action_type") == "clarification":
        return pending
    return None
