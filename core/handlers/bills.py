# core/handlers/bills.py
"""
Confirmação de pagamento de CONTAS A PAGAR (boletos) pelo bot.

Quando o usuário manda "paguei a luz" / "quitei o boleto da internet", tenta
casar com uma conta a pagar PENDENTE (recorrente payment_mode='manual'). Se
casar, marca como paga — o que cria o lançamento de despesa (debita +
categoriza) via db.bills.mark_bill_paid. Se NÃO casar nenhuma conta pendente,
retorna None pra deixar a mensagem virar um lançamento avulso normal.

Só o pagamento é tratado aqui; a CRIAÇÃO da conta a pagar é feita em
core/handlers/recurring.py (payment_mode='manual'). Ver [[db/bills.py]].
"""
from __future__ import annotations

import re

from utils_text import fmt_brl, normalize_text, parse_money

_PAY_RE = re.compile(r"^(ja\s+)?(paguei|quitei)\b")
_STOP_TOKENS = {
    "o", "a", "os", "as", "de", "do", "da", "dos", "das", "meu", "minha",
    "conta", "boleto", "boletos", "fatura", "reais", "real", "rs", "r",
    "ja", "hoje", "ontem", "esse", "essa", "esta", "este",
}


def try_pay_from_text(user_id: int, text: str) -> str | None:
    """Se o texto for 'paguei/quitei <conta>' E houver uma conta a pagar
    pendente que casa, marca como paga e retorna a confirmação. Senão None."""
    norm = normalize_text(text or "")
    if not _PAY_RE.match(norm):
        return None

    from db.bills import list_bills, mark_bill_paid

    pend = [b for b in list_bills(user_id, include_paid=False) if b.get("status") == "pending"]
    if not pend:
        return None

    # valor real do boleto, se o usuário disser ("paguei 152 de luz")
    try:
        amount = parse_money(text)
    except Exception:
        amount = None
    if amount is not None and amount <= 0:
        amount = None

    # alvo: tira o verbo, o valor e stopwords → sobra o "nome" da conta
    target = _PAY_RE.sub("", norm).strip()
    target = re.sub(r"\b\d[\d.,]*\b", " ", target)
    target = " ".join(t for t in target.split() if t not in _STOP_TOKENS).strip()

    def _score(b: dict) -> int:
        bn = normalize_text(b.get("name") or "")
        if not bn:
            return 0
        if bn in norm or (target and (bn in target or target in bn)):
            return 3
        toks = [t for t in bn.split() if len(t) > 2 and t not in _STOP_TOKENS]
        if target and any(t in target.split() for t in toks):
            return 2
        if any(t in norm.split() for t in toks):
            return 1
        return 0

    scored = sorted(((_score(b), b) for b in pend), key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]

    if best_score == 0:
        # Nenhuma conta casou pelo nome. Se o usuário NÃO deu um alvo específico
        # (respondeu só "paguei" / "paguei essa conta" — como o lembrete pede) e
        # só existe UMA conta pendente, paga ela. Se houver várias, pergunta qual.
        # Se o alvo era específico e não casou, deixa virar lançamento avulso.
        if target:
            return None
        if len(pend) == 1:
            best = pend[0]
        else:
            nomes = ", ".join(b.get("name") or "?" for b in pend[:5])
            return f"Você tem contas a pagar pendentes: {nomes}. Qual delas você pagou?"
    else:
        # empate real e usuário não deu pista suficiente → pergunta qual
        ties = [b for s, b in scored if s == best_score]
        if len(ties) > 1 and best_score < 3:
            nomes = ", ".join(b.get("name") or "?" for b in ties[:5])
            return f"Você tem contas a pagar pendentes: {nomes}. Qual delas você pagou?"

    # Conta de valor variável (água/luz) sem valor informado: guarda qual conta
    # originou a pergunta. Assim a resposta natural (só "132,50") não cai no
    # classificador/na IA sem contexto.
    if best.get("variable_amount") and amount is None:
        from db import set_pending_action

        nome = (best.get("name") or "conta")
        set_pending_action(
            user_id,
            "bill_amount_expected",
            {"bill_id": int(best["id"]), "bill_name": nome},
        )
        return (
            f"A conta de *{nome}* tem valor variável. Quanto veio este mês?\n"
            "Pode mandar só o valor, por exemplo: *132,50*"
        )

    paid = mark_bill_paid(user_id, int(best["id"]), amount)
    if paid is None:
        return None
    val = paid.get("paid_amount") or paid.get("amount") or 0
    return (
        f"✅ Conta paga: *{paid.get('name')}* — {fmt_brl(val)} lançado e "
        f"categorizado. Tá tudo em dia! 🐷"
    )


def resolve_bill_amount(user_id: int, text: str, pending: dict) -> str | None:
    """Conclui uma conta variável quando a resposta é somente um valor.

    Texto que não seja estritamente monetário abandona a pergunta e volta ao
    roteamento normal, em vez de extrair por engano um número de outro comando.
    """
    raw = (text or "").strip()
    if not re.fullmatch(r"(?:R\$\s*)?\d[\d.,\s]*(?:\s*(?:reais?|rs))?", raw, re.I):
        from db import clear_pending_action

        clear_pending_action(user_id)
        return None

    amount = parse_money(raw)
    if amount is None or amount <= 0:
        nome = pending.get("payload", {}).get("bill_name") or "conta"
        return f"O valor da *{nome}* precisa ser maior que zero. Quanto veio este mês?"

    from db import clear_pending_action
    from db.bills import mark_bill_paid

    payload = pending.get("payload") or {}
    paid = mark_bill_paid(user_id, int(payload["bill_id"]), amount)
    clear_pending_action(user_id)
    if paid is None:
        return "Essa conta não está mais pendente."
    val = paid.get("paid_amount") or paid.get("amount") or 0
    return (
        f"✅ Conta paga: *{paid.get('name')}* — {fmt_brl(val)} lançado e "
        f"categorizado. Tá tudo em dia! 🐷"
    )


__all__ = ["resolve_bill_amount", "try_pay_from_text"]
