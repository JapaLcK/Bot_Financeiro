"""Lançamento manual na Carteira Piggy (dinheiro em espécie): o miolo do `POST /launches`
do /app e do `POST /api/v2/lancamentos/carteira`.

Não confere o teto do plano nem a forma de pagamento (Q40): cada rota faz a dela antes.
WhatsApp e quick_entry têm fluxo próprio (ofertas, pendências) sobre o mesmo escritor.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def lancar(user_id: int, tipo: str, valor, alvo: str | None, nota: str | None,
           categoria: str | None, criado_em: datetime | None = None) -> dict:
    """`tipo` receita|despesa; `categoria` é a explícita (None = inferir pela nota)."""
    from core.services.category_service import infer_category, learn_from_inference
    from db import add_launch_and_update_balance, propose_manual_reconciliation
    from utils_text import is_internal_category

    nota = nota or alvo or f"{tipo} registrada pelo dashboard"
    inferred = infer_category(user_id, nota, categoria)
    categoria = inferred.category or "outros"
    is_internal = is_internal_category(categoria)
    launch_id, user_seq, new_balance = add_launch_and_update_balance(
        user_id, tipo, valor, alvo, nota, categoria, criado_em, is_internal)

    # Depois do commit nada sobe: o lançamento já existe, e um erro aqui faria a
    # retentativa gravar o mesmo gasto de novo. A pendência com o banco não levanta.
    propose_manual_reconciliation(user_id, launch_id)
    try:
        learn_from_inference(user_id, nota, categoria, target_hint=alvo, reason=inferred.reason)
    except Exception:
        logger.exception("learn_from_inference falhou depois do commit (user %s, lancamento %s)",
                         user_id, launch_id)
    return {"launch_id": launch_id, "user_seq": user_seq, "new_balance": new_balance,
            "categoria": categoria, "nota": nota, "is_internal": is_internal}
