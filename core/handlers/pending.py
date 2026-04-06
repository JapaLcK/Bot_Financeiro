# core/handlers/pending.py
"""
Resolve ações pendentes: confirmações de delete e esclarecimentos.
"""
from __future__ import annotations
import db
from utils_text import fmt_brl


def resolve_delete(user_id: int, confirmed: bool) -> str | None:
    """
    Verifica se existe uma pending_action de DELETE para o usuário.
    Retorna mensagem de resposta, ou None se não havia pending de delete.
    """
    pending = db.get_pending_action(user_id)
    if not pending:
        return None

    action_type = pending.get("action_type")

    # só trata deletes aqui
    if action_type not in ("delete_launch", "delete_launch_bulk", "delete_pocket", "delete_investment"):
        return None

    payload = pending.get("payload", {})

    if not confirmed:
        db.clear_pending_action(user_id)
        return "❌ Ação cancelada."

    if action_type == "delete_launch":
        launch_id = payload.get("launch_id")
        try:
            db.delete_launch_and_rollback(launch_id, user_id)
            db.clear_pending_action(user_id)
            return f"✅ Lançamento **#{launch_id}** apagado e saldo revertido."
        except Exception as e:
            db.clear_pending_action(user_id)
            return f"Erro ao apagar lançamento #{launch_id}: {e}"

    if action_type == "delete_launch_bulk":
        ids = payload.get("launch_ids", [])
        failed = []
        for lid in ids:
            try:
                db.delete_launch_and_rollback(lid, user_id)
            except Exception:
                failed.append(lid)
        ok_ids = [i for i in ids if i not in failed]
        db.clear_pending_action(user_id)
        parts = []
        if ok_ids:
            parts.append("✅ Apagados: " + ", ".join(f"**#{i}**" for i in ok_ids))
        if failed:
            parts.append("⚠️ Falha: " + ", ".join(f"#{i}" for i in failed))
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
