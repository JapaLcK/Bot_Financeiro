from core.types import OutgoingMessage
from parsers import parse_receita_despesa_natural
from db import ensure_user, add_launch_and_update_balance
from utils_text import fmt_brl

def handle_quick_entry(user_id: int, text: str) -> OutgoingMessage | None:
    parsed = parse_receita_despesa_natural(user_id, text)
    if not parsed:
        return None

    ensure_user(user_id)

    tipo = parsed["tipo"]
    valor = float(parsed["valor"])
    categoria = parsed.get("categoria")
    alvo = parsed.get("alvo")
    nota = parsed.get("nota")
    criado_em = parsed.get("criado_em")
    is_internal = parsed.get("is_internal_movement", False)

    launch_id, new_balance = add_launch_and_update_balance(
        user_id=user_id,
        tipo=tipo,
        valor=valor,
        alvo=alvo,
        nota=nota,
        categoria=categoria,
        criado_em=criado_em,
        is_internal_movement=is_internal,
    )

    emoji = "💸" if tipo == "despesa" else "💰"
    cat_txt = categoria or "outros"
    return OutgoingMessage(
        text=(
            f"{emoji} **{tipo.capitalize()} registrada**: {fmt_brl(valor)}\n"
            f"🏷️ Categoria: {cat_txt}\n"
            f"🏦 Conta: {fmt_brl(float(new_balance))}\n"
            f"ID:#{launch_id}"
        )
    )