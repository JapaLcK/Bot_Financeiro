from core.types import OutgoingMessage
from parsers import parse_receita_despesa_natural
from db import (
    ensure_user, add_launch_and_update_balance, has_open_finance_connections,
    propose_manual_reconciliation,
)
from utils_text import fmt_brl
from core.services.category_service import learn_from_inference


def handle_quick_entry(user_id: int, text: str) -> OutgoingMessage | None:
    from core.handlers import credit as h_credit
    from core.handlers import forma_pagamento as fp

    credit_response = h_credit.try_handle_natural_credit_purchase(user_id, text)
    if credit_response is not None:
        return OutgoingMessage(text=credit_response)

    # Mesma regra do `core/handlers/launches.py::add` (Q40), mas sem pergunta
    # com estado: o Discord está morto, não vale criar pendência aqui.
    declarada = fp.detectar(text)
    if declarada == fp.DINHEIRO:
        text = fp.limpar(text)
    parsed = parse_receita_despesa_natural(user_id, text)
    if not parsed:
        return None
    decisao = fp.decidir(user_id, declarada)
    if decisao == fp.MISTO:
        return OutgoingMessage(text=fp.msg_misto())
    if decisao == fp.BANCO:
        return OutgoingMessage(text=fp.msg_banco(user_id, parsed["tipo"], parsed["valor"]))
    if decisao == fp.PERGUNTA:
        return OutgoingMessage(text=(
            "🐷 Não registrei. Com banco conectado, o que passou pelo banco chega "
            "pelo Open Finance. Se foi em espécie: *gastei 50 em dinheiro no mercado*"))

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

    # Lançamento manual é dinheiro em espécie: se o banco já importou o mesmo
    # gasto, vira pendência confirmável (nunca fusão). Não sobe exceção. Antes
    # do `learn_from_inference`, que não tem `try`: se ele estourar, a
    # pendência já nasceu.
    propose_manual_reconciliation(user_id, launch_id)

    learn_from_inference(
        user_id,
        nota or text,
        categoria or "outros",
        target_hint=alvo,
        reason=category_reason,
    )

    # Com banco conectado, o rótulo do saldo deixa claro que a Conta é a
    # Carteira Piggy.
    saldo_label = "👛 Saldo (Carteira Piggy)" if has_open_finance_connections(user_id) else "🏦 Conta"

    emoji = "💸" if tipo == "despesa" else "💰"
    cat_txt = categoria or "outros"
    # Rótulo igual ao da resposta de lançamento (`core/handlers/launches.py`):
    # com Open Finance conectado o saldo da Conta é o dinheiro em espécie
    # (Carteira Piggy), e o nome precisa deixar isso claro. `carteira_exibida`
    # já trata a falha: pós-commit, cai no cru em vez de subir (o chamador
    # relançaria o gasto).
    from db.accounts import carteira_exibida
    linha_saldo = f"{saldo_label}: {fmt_brl(float(carteira_exibida(user_id, new_balance)))}"
    return OutgoingMessage(
        text=(
            f"{emoji} **{tipo.capitalize()} registrada**: {fmt_brl(valor)}\n"
            f"🏷️ Categoria: {cat_txt}\n"
            f"{linha_saldo}\n"
            f"ID:#{user_seq}"
        )
    )
