from core.types import OutgoingMessage
from parsers import parse_receita_despesa_natural
from db import ensure_user, add_launch_and_update_balance, has_open_finance_connections
from utils_text import fmt_brl
from core.services.category_service import learn_from_inference

def handle_quick_entry(user_id: int, text: str) -> OutgoingMessage | None:
    from core.handlers import credit as h_credit

    credit_response = h_credit.try_handle_natural_credit_purchase(user_id, text)
    if credit_response is not None:
        return OutgoingMessage(text=credit_response)

    parsed = parse_receita_despesa_natural(user_id, text)
    if not parsed:
        return None

    ensure_user(user_id)

    tipo = parsed["tipo"]
    valor = float(parsed["valor"])
    categoria = parsed.get("categoria")
    category_reason = parsed.get("category_reason")
    alvo = parsed.get("alvo")
    nota = parsed.get("nota")
    criado_em = parsed.get("criado_em")
    is_internal = parsed.get("is_internal_movement", False)

    launch_id, user_seq, new_balance = add_launch_and_update_balance(
        user_id=user_id,
        tipo=tipo,
        valor=valor,
        alvo=alvo,
        nota=nota,
        categoria=categoria,
        criado_em=criado_em,
        is_internal_movement=is_internal,
    )

    learn_from_inference(
        user_id,
        nota or text,
        categoria or "outros",
        target_hint=alvo,
        reason=category_reason,
    )

    # Lançamento manual é dinheiro em espécie: sem reconciliação com transações
    # do Open Finance (a tx do banco, se existir, é outro fato e entra separada
    # pelo sync). Com banco conectado, o rótulo do saldo deixa claro que a
    # Conta é a Carteira Piggy.
    saldo_label = "👛 Saldo (Carteira Piggy)" if has_open_finance_connections(user_id) else "🏦 Conta"

    emoji = "💸" if tipo == "despesa" else "💰"
    cat_txt = categoria or "outros"
    return OutgoingMessage(
        text=(
            f"{emoji} **{tipo.capitalize()} registrada**: {fmt_brl(valor)}\n"
            f"🏷️ Categoria: {cat_txt}\n"
            f"{saldo_label}: {fmt_brl(float(new_balance))}\n"
            f"ID:#{user_seq}"
        )
    )
