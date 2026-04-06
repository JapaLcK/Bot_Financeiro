# core/handlers/pending.py
"""
Resolve ações pendentes (confirmações de delete, esclarecimentos, etc.)
"""
from __future__ import annotations
import db
from utils_text import fmt_brl


def resolve(user_id: int, confirmed: bool) -> str | None:
    """
    Verifica se existe uma pending_action para o usuário.
    Se sim, executa ou cancela de acordo com confirmed (True=sim, False=não).
    Retorna a mensagem de resposta, ou None se não havia pending.
    """
    pending = db.get_pending_action(user_id)
    if not pending:
        return None

    action_type = pending.get("action_type")
    payload     = pending.get("payload", {})

    # Cancela sempre que confirmed=False
    if not confirmed:
        db.clear_pending_action(user_id)
        return "❌ Ação cancelada."

    # --- delete_launch ---
    if action_type == "delete_launch":
        launch_id = payload.get("launch_id")
        try:
            db.delete_launch_and_rollback(launch_id, user_id)
            db.clear_pending_action(user_id)
            return f"✅ Lançamento **#{launch_id}** apagado e saldo revertido."
        except Exception as e:
            db.clear_pending_action(user_id)
            return f"Erro ao apagar lançamento #{launch_id}: {e}"

    # --- delete_pocket ---
    if action_type == "delete_pocket":
        pocket_name = payload.get("pocket_name")
        try:
            db.delete_pocket(user_id, pocket_name)
            db.clear_pending_action(user_id)
            return f"✅ Caixinha **{pocket_name}** deletada."
        except Exception as e:
            db.clear_pending_action(user_id)
            return f"Erro ao deletar caixinha: {e}"

    # --- delete_investment ---
    if action_type == "delete_investment":
        investment_name = payload.get("investment_name")
        try:
            db.delete_investment(user_id, investment_name)
            db.clear_pending_action(user_id)
            return f"✅ Investimento **{investment_name}** deletado."
        except Exception as e:
            db.clear_pending_action(user_id)
            return f"Erro ao deletar investimento: {e}"

    # tipo desconhecido — limpa e segue
    db.clear_pending_action(user_id)
    return None
