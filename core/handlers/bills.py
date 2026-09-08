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

from core.response_formatter import wrap_wa_markup
from utils_text import (_ENCHIMENTO, _TRACOS, fmt_brl, limpa_pontuacao_final,
                        normalize_text, parse_money, valor_perigoso)

_PAY_RE = re.compile(r"^(ja\s+)?(paguei|quitei)\b")
_STOP_TOKENS = {
    "o", "a", "os", "as", "de", "do", "da", "dos", "das", "meu", "minha",
    "conta", "boleto", "boletos", "fatura", "reais", "real", "rs", "r",
    "ja", "hoje", "ontem", "esse", "essa", "esta", "este",
}



def pergunta_de_valor_sem_contexto(user_id: int, nome: str) -> str:
    """Texto da pergunta de valor de conta variável quando o `claim` PERDEU a
    linha de `pending_actions` para outra pergunta que continua de pé.

    Sem a pendência gravada, o número solto não fecha nada — e a forma completa
    ("paguei luz 132,50") TAMBÉM não funciona neste estado, então não pode ser
    anunciada. Medido (19 tipos de pendência que podem estar vivos, texto
    `paguei luz 132,50` por `route()`): em 8 deles a mensagem é consumida antes
    de chegar no `try_pay_from_text` — as 3 variantes de `clarification`,
    `multi_launch_values`, `credit_card_setup`, `credit_card_set_primary`,
    `credit_delete_card`, `installment_pending`, `pay_bill_choice`, e o
    `recategorize_launch_text` (que no WhatsApp é consumido em
    `wa_runtime.py:887`, antes do `handle_incoming`). Na `clarification` de
    `launches.add` o estrago é dinheiro no lugar errado: o 132,50 vira o valor
    do lançamento VELHO e a conta fica sem pagar.

    `cancelar` também não serve de conselho universal: limpa a linha em 17 dos
    19, mas no `recategorize_launch_text` vira o nome da categoria.

    O único caminho que vale em TODOS é terminar a pergunta que está de pé —
    é só isso que este texto pede. Quando a pendência carrega a pergunta em
    mãos (toda `clarification` grava `payload["question"]`), ela é citada; nos
    outros tipos não há rótulo legível e o texto fica genérico.
    """
    from db import get_pending_action

    try:
        pend = get_pending_action(user_id) or {}
        pergunta = (pend.get("payload") or {}).get("question")
    except Exception:
        pergunta = None
    espera = f'esperando: "{pergunta}"' if pergunta else "esperando resposta."
    rotulo = wrap_wa_markup(nome)
    return (
        f"A conta de {rotulo} tem valor variável, mas antes tem outra pergunta "
        f"minha {espera}\n"
        f"Me responde ela primeiro; a {rotulo} fica pendente aqui e a gente "
        f"resolve o valor em seguida."
    )


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
        # contexto salvo — e aí o texto abaixo pede que ela seja terminada
        # primeiro (a forma completa NÃO funciona nesse estado; ver
        # `pergunta_de_valor_sem_contexto`).
        guardou = claim_pending_action(
            user_id,
            "bill_amount_expected",
            {"bill_id": int(best["id"]), "bill_name": nome},
        )
        if not guardou:
            return pergunta_de_valor_sem_contexto(user_id, nome)
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
        f"✅ Conta paga: {wrap_wa_markup(paid.get('name'))} — {fmt_brl(val)} lançado e "
        f"categorizado. Tá tudo em dia! 🐷"
    )



# `_ENCHIMENTO` vem do `utils_text`: enchimento falado antes do número ("foi
# 132", "acho que 132", "uns 132", "veio 132 reais"). É `fullmatch`, então TODA
# palavra antes do número tem que estar naquela lista — "gastei 132 no mercado"
# não casa e a pergunta é abandonada para o roteamento normal, que é o certo.
# A MESMA lista decide, no `_sinal_negativo`, se o que vem antes de um traço é
# conteúdo; por isso ela mora lá e não aqui.
_UNIDADE = r"(?:reais?|real|rs|pila|conto|contos|mango|mangos)"
# O grupo (1) do sinal ficou aqui só como documentação da FORMA: quem recusa
# negativo agora é a tabela-verdade do `utils_text._sinal_negativo`, que decide
# por POSIÇÃO do traço, trata as dez grafias do `_TRACOS` e o "menos" falado.
# Sozinho, este grupo recusava "-10" e deixava passar "luz - 132"; a tabela
# separa sinal de separador de prosa.
# NADA de `\s` dentro do número: com ele, "132 50" colava em 13250 e pagava
# R$ 13.250,00 sem confirmação.
_VALOR_RE = re.compile(
    rf"(?:{_ENCHIMENTO}\s+)*(?:R\$\s*)?(-\s*)?\d[\d.,]*(?:\s*{_UNIDADE})?[.!]?",
    re.I)
# Só dígitos, separadores e espaço, mas que não casou como valor: "132 50",
# "1.2.3.4", "132\n50". O bot ACABOU de pedir um número, então isso é digitação
# errada — re-pergunta em vez de abandonar (e em vez de pagar 13.250).
_NUMERO_AMBIGUO_RE = re.compile(r"[\d.,\s]+")


def resolve_bill_amount(user_id: int, text: str, pending: dict) -> str | None:
    """Conclui uma conta variável quando a resposta é somente um valor.

    Texto que não seja estritamente monetário abandona a pergunta e volta ao
    roteamento normal, em vez de extrair por engano um número de outro comando.
    A aceitação é o `_VALOR_RE` — a mensagem INTEIRA tem que ser o valor. É a
    porta mais estrita das quatro, de propósito: aqui o `parse_money` roda sobre
    a frase toda, e afrouxar para "tem um número dentro" fez, medido,
    "codigo 8888 valor 132" pagar R$ 8.888,00, "no dia 20 foram 450" pagar
    R$ 20,00 e "dia 12/05 132" pagar R$ 12,00.

    Esta porta NÃO consulta o `abandona_pergunta_de_valor` (o passo 1 das
    portas 2, 3 e 4; a numeração está em `core/intent_router.py`). Medido sobre
    o alfabeto inteiro do `_VALOR_RE` — 44.289 strings (7 números × 9 unidades
    × 703 prefixos do `_ENCHIMENTO`), todas casando o `fullmatch`: ZERO
    classificam num intent de `ABANDONA` com `allow_ai=False`. O portão de
    forma já é mais estrito que o passo 1, então ele não teria entrada
    alcançável aqui. A enumeração está em
    `tests/test_bill_amount_pending.py::test_porta_1_nao_precisa_do_passo_1_alfabeto_inteiro_do_valor_re`.

    ORDEM: o abandono por forma vem ANTES do dano. O contrário prendia o
    usuário: `quanto gastei em 12 05`, `resumo do mes 08 2026` e `apagar 0`
    (nenhum dos três casa o `_VALOR_RE`) morriam no "Não entendi o valor da
    *Luz*" / "precisa ser maior que zero" com a pendência de pé, e a repetição
    idêntica dava a mesma recusa.

    Os traços são normalizados ANTES da forma, não só dentro do
    `valor_perigoso`: `−10` (U+2212, o que o teclado do iOS produz) não casa o
    `_VALOR_RE`, e sem esta linha ele abandonaria a pergunta em silêncio em vez
    de recusar com ela viva. O "menos" falado não precisa da mesma normalização
    aqui: ele está no `_ENCHIMENTO`, então "menos 10" já casa a forma e chega ao
    `valor_perigoso`, que o recusa.
    """
    raw = (text or "").strip().translate(_TRACOS)
    nome = (pending.get("payload") or {}).get("bill_name") or "conta"
    nao_entendi = (f"Não entendi o valor da *{nome}*. Manda só o número, "
                   f"por exemplo: *132,50*")
    if not _VALOR_RE.fullmatch(raw):
        if _NUMERO_AMBIGUO_RE.fullmatch(raw):
            return nao_entendi
        # CONDICIONAL, não clear: entre a leitura desta pendência e agora,
        # outra tarefa pode ter posto uma confirmação nova no lugar — que já
        # apareceu na tela do usuário. Apagar por cima a deixaria órfã. Só
        # abandonamos a pergunta se ela ainda for a que lemos.
        from db import consume_pending_action

        consume_pending_action(user_id, pending)
        return None

    # A forma é de valor. Agora o DANO, sobre o texto JÁ limpo — o
    # `parse_money` precisa da limpeza ("1.500." vira `None` sem ela). O
    # `valor_perigoso` faz a dele por dentro e não depende desta linha.
    limpo = limpa_pontuacao_final(raw)
    perigo = valor_perigoso(limpo, parse_money(limpo))
    if perigo == "nao_positivo":
        return f"O valor da *{nome}* precisa ser maior que zero. Quanto veio este mês?"
    if perigo == "nao_entendi":
        return nao_entendi

    amount = parse_money(limpo)
    if amount is None:
        # Casou o formato mas o `parse_money` não extraiu nada ("1.2.3.4",
        # "1,,,2"): dizer "precisa ser maior que zero" para um texto que não é
        # negativo confunde. Mesma mensagem do número ambíguo.
        return nao_entendi
    # Centavos: o `valor_perigoso` já recusou o que arredonda para zero.
    amount = round(amount, 2)

    from db import consume_pending_action, restore_pending_on_error
    from db.bills import mark_bill_paid

    payload = pending.get("payload") or {}

    # REIVINDICA antes de pagar. Duas respostas concorrentes (Discord, ou duas
    # plataformas ligadas) leem a mesma pendência e as duas chegam no
    # `mark_bill_paid`, que cria o lançamento que debita o saldo ANTES da
    # atualização condicional de status: só uma conta muda de status, mas os
    # DOIS lançamentos existem. Quem perde o compare-and-swap sai sem fazer
    # nada — o vencedor responde. Mesmo desenho do PR #128 na fila de
    # multi-lançamento.
    if not consume_pending_action(user_id, pending):
        return None

    # Devolve a pergunta se o pagamento estourar: sem isso o usuário perde a
    # pendência e o valor que digitou, e a conta continua em aberto sem ninguém
    # avisar. Prazo 10 min, o mesmo do `claim` que a armou (:144).
    with restore_pending_on_error(user_id, pending):
        paid = mark_bill_paid(user_id, int(payload["bill_id"]), amount)
    if paid is None:
        return "Essa conta não está mais pendente."
    val = paid.get("paid_amount") or paid.get("amount") or 0
    return (
        f"✅ Conta paga: {wrap_wa_markup(paid.get('name'))} — {fmt_brl(val)} lançado e "
        f"categorizado. Tá tudo em dia! 🐷"
    )


__all__ = ["resolve_bill_amount", "try_pay_from_text"]
