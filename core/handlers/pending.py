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

        text = payload.get("text", "")
        if not text:
            return "⚠️ Não encontrei os dados do lançamento para confirmar. Tente digitar manualmente."

        # Processa o texto montado como se o usuário tivesse digitado
        from core.services.quick_entry import handle_quick_entry
        msg_out = handle_quick_entry(user_id, text)
        if msg_out:
            return f"✅ Lançamento registrado!\n{msg_out.text}"
        return f"⚠️ Não consegui registrar automaticamente. Tente: `{text}`"

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
