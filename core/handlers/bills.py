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
from math import isfinite

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
        from db import claim_pending_action

        nome = (best.get("name") or "conta")
        # `claim_pending_action` respeita a ordem de prioridade escrita em
        # db/pending.py: desaloja oferta de conveniência ("categoria errada?"),
        # cede para outra PERGUNTA. Só quando cede é que a pergunta sai sem
        # contexto salvo — e aí o texto abaixo pede a forma completa, que
        # funciona sem estado nenhum.
        guardou = claim_pending_action(
            user_id,
            "bill_amount_expected",
            {"bill_id": int(best["id"]), "bill_name": nome},
        )
        if not guardou:
            return (
                f"A conta de *{nome}* tem valor variável. Quanto veio este mês?\n"
                f"Manda assim: *paguei {nome.lower()} 132,50*"
            )
        return (
            f"A conta de *{nome}* tem valor variável. Quanto veio este mês?\n"
            "Pode mandar só o valor, por exemplo: *132,50*"
        )

    try:
        paid = mark_bill_paid(user_id, int(best["id"]), amount)
    except ValueError as exc:
        if str(exc) != "VALOR_INVALIDO":
            raise
        # Valor que o `mark_bill_paid` recusa: não finito ("paguei luz" com 400
        # dígitos) ou que arredonda para R$ 0,00 ("paguei luz 0,001"). É
        # digitação, não falha do sistema — sem traduzir aqui, o handler global
        # responde "Ocorreu um erro interno" com stack trace no log.
        nome = best.get("name") or "conta"
        return (
            f"O valor da *{nome}* precisa ser maior que zero.\n"
            f"Manda assim: *paguei {str(nome).lower()} 132,50*"
        )
    if paid is None:
        return None
    val = paid.get("paid_amount") or paid.get("amount") or 0
    return (
        f"✅ Conta paga: *{paid.get('name')}* — {fmt_brl(val)} lançado e "
        f"categorizado. Tá tudo em dia! 🐷"
    )



# Enchimento falado antes do número: "foi 132", "acho que 132", "uns 132",
# "veio 132 reais", "deu 132,50". É `fullmatch`, então TODA palavra antes do
# número tem que estar nesta lista — "gastei 132 no mercado" não casa e a
# pergunta é abandonada para o roteamento normal, que é o certo.
_ENCHIMENTO = (r"(?:foi|era|eh|e|de|da|do|deu|veio|custou|saiu|ficou|acho|que"
               r"|uns|umas|um|uma|tipo|mais|ou|menos|deve|ter|dado|ai)")
_UNIDADE = r"(?:reais?|real|rs|pila|conto|contos|mango|mangos)"
# O sinal é capturado de propósito: `parse_money("-10")` devolve 10.0, então
# sem o grupo (1) um "-10" viraria pagamento de R$ 10.
# NADA de `\s` dentro do número: com ele, "132 50" colava em 13250 e pagava
# R$ 13.250,00 sem confirmação.
_VALOR_RE = re.compile(
    rf"(?:{_ENCHIMENTO}\s+)*(?:R\$\s*)?(-\s*)?\d[\d.,]*(?:\s*{_UNIDADE})?[.!]?",
    re.I)
# Só dígitos, separadores e espaço, mas que não casou como valor: "132 50",
# "1.2.3.4", "132\n50". O bot ACABOU de pedir um número, então isso é digitação
# errada — re-pergunta em vez de abandonar (e em vez de pagar 13.250).
_NUMERO_AMBIGUO_RE = re.compile(r"[\d.,\s]+")


def limpa_pontuacao_final(raw: str) -> str:
    """Tira o ponto/exclamação do FIM antes de entregar ao `parse_money`.

    Mesma armadilha do `\\s` comentada acima, por outra porta: "132,50." tem
    vírgula E ponto, o `parse_money` decide o decimal pelo ÚLTIMO separador,
    toma a vírgula por milhar e devolve 13250.0 — R$ 13.250,00 no lugar de
    R$ 132,50 ("0,50." vira 50, "9,99." vira 999). O `_VALOR_RE` aceita `[.!]?`
    no fim de propósito (quem escreve "132,50." está respondendo, não mudando de
    assunto), então quem tem de limpar é quem chama.

    Não corrigido no `parse_money`: o bug é dele e é anterior a este PR, mas ele
    é chamado por dezenas de fluxos e mexer nele aqui é troca de bug conhecido
    por bug novo. Issue separada.
    """
    return (raw or "").rstrip(" .!")


def agrupamento_de_milhar_ok(raw: str) -> bool:
    """False quando o ponto de milhar está malformado ("1.23.456").

    O `parse_money` decide o significado do ponto pelo TAMANHO do último grupo:
    se tem 3 dígitos, apaga TODOS os pontos. Então "1.23.456" (erro de
    digitação) vira 123456.0 e "1.2.345" vira 12345.0 — a conta seria paga com
    valor inflado, em silêncio, logo depois de o bot pedir "manda só o número".

    Regra: com dois ou mais pontos, todo grupo depois do primeiro precisa ter
    exatamente 3 dígitos (1.234.567 ✓, 1.23.456 ✗); com um ponto só, valem 3
    (milhar: 1.200) ou 1-2 (decimal: 132.50) — a mesma heurística que o
    `parse_money` já usa. Vírgula presente manda: os pontos antes dela são
    milhar, e o que vem depois é a casa decimal (1.132,50 ✓, 1.23.456,00 ✗).

    Não corrigido no `parse_money` pelo mesmo motivo do `limpa_pontuacao_final`:
    dezenas de fluxos chamam aquilo. Issue separada.
    """
    m = re.search(r"\d[\d.,\s]*", raw or "")
    if not m:
        return True
    num = m.group(0).strip().replace(" ", "")
    if "," in num:
        num = num[:num.rfind(",")]
    grupos = num.split(".")
    if len(grupos) == 1:
        return True
    if len(grupos) == 2:
        return len(grupos[1]) in (1, 2, 3)
    return all(len(g) == 3 for g in grupos[1:])


def resolve_bill_amount(user_id: int, text: str, pending: dict) -> str | None:
    """Conclui uma conta variável quando a resposta é somente um valor.

    Texto que não seja estritamente monetário abandona a pergunta e volta ao
    roteamento normal, em vez de extrair por engano um número de outro comando.
    """
    raw = (text or "").strip()
    nome = (pending.get("payload") or {}).get("bill_name") or "conta"
    casou = _VALOR_RE.fullmatch(raw)
    if not casou:
        if _NUMERO_AMBIGUO_RE.fullmatch(raw):
            return (f"Não entendi o valor da *{nome}*. Manda só o número, "
                    f"por exemplo: *132,50*")
        # CONDICIONAL, não clear: entre a leitura desta pendência e agora,
        # outra tarefa pode ter posto uma confirmação nova no lugar — que já
        # apareceu na tela do usuário. Apagar por cima a deixaria órfã. Só
        # abandonamos a pergunta se ela ainda for a que lemos.
        from db import advance_pending_action

        advance_pending_action(
            user_id, "bill_amount_expected", pending.get("payload") or {}, None)
        return None

    limpo = limpa_pontuacao_final(raw)
    amount = parse_money(limpo) if agrupamento_de_milhar_ok(limpo) else None
    # Arredonda para centavos ANTES de validar: "0,001" passava no `> 0`,
    # gravava paid_amount=0.001 e respondia "R$ 0,00 lançado" — mensagem e dado
    # divergentes. Agora vira 0.0 e cai na validação abaixo.
    if amount is not None:
        amount = round(amount, 2)
    if amount is None:
        # Casou o formato mas o `parse_money` não extraiu nada ("1.2.3.4",
        # "1,,,2"): dizer "precisa ser maior que zero" para um texto que não é
        # negativo confunde. Mesma mensagem do número ambíguo.
        return (f"Não entendi o valor da *{nome}*. Manda só o número, "
                f"por exemplo: *132,50*")
    # `isfinite` junto com o `<= 0`: `parse_money("1"*400)` devolve `inf`, que
    # passa no `> 0` e só seria recusado lá no `mark_bill_paid` — depois de a
    # pendência já ter sido reivindicada, virando "erro interno" para o usuário.
    # Aqui a pergunta continua de pé e ele responde de novo.
    if casou.group(1) or not isfinite(amount) or amount <= 0:
        return f"O valor da *{nome}* precisa ser maior que zero. Quanto veio este mês?"

    from db import advance_pending_action, create_pending_action_if_absent
    from db.bills import mark_bill_paid

    payload = pending.get("payload") or {}

    # REIVINDICA antes de pagar. Duas respostas concorrentes (Discord, ou duas
    # plataformas ligadas) leem a mesma pendência e as duas chegam no
    # `mark_bill_paid`, que cria o lançamento que debita o saldo ANTES da
    # atualização condicional de status: só uma conta muda de status, mas os
    # DOIS lançamentos existem. Quem perde o compare-and-swap sai sem fazer
    # nada — o vencedor responde. Mesmo desenho do PR #128 na fila de
    # multi-lançamento.
    if not advance_pending_action(user_id, "bill_amount_expected", payload, None):
        return None

    try:
        paid = mark_bill_paid(user_id, int(payload["bill_id"]), amount)
    except Exception:
        # Devolve a pergunta: sem isso o usuário perde a pendência e o valor
        # que digitou, e a conta continua em aberto sem ninguém avisar.
        #
        # CONDICIONAL, não upsert: entre a reivindicação e a falha, outra
        # tarefa pode ter armado uma pendência nova (uma confirmação que já
        # apareceu na tela do usuário). Gravar por cima deixaria aquela órfã.
        # Se já há algo lá, a pergunta desta conta é a que se perde — e ela é
        # recuperável mandando "paguei a luz" de novo.
        create_pending_action_if_absent(
            user_id, "bill_amount_expected", payload)
        raise
    if paid is None:
        return "Essa conta não está mais pendente."
    val = paid.get("paid_amount") or paid.get("amount") or 0
    return (
        f"✅ Conta paga: *{paid.get('name')}* — {fmt_brl(val)} lançado e "
        f"categorizado. Tá tudo em dia! 🐷"
    )


__all__ = ["limpa_pontuacao_final", "resolve_bill_amount", "try_pay_from_text"]
