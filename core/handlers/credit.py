from __future__ import annotations

import logging
import re
from collections import defaultdict

# Helper único das portas destrutivas (a docstring dele lista quais e explica o
# critério de nível). Ele nunca põe `str(e)` no log.
from core.intent_classifier import classify, NEGATIVAS_EXATAS
from core.observability import _log_falha
from core.services.category_service import infer_category, learn_from_inference
from core.services.plan_limits import PlanLimitExceeded
from db import (
    add_credit_purchase,
    add_credit_purchase_installments,
    card_name_exists,
    clear_pending_action,
    consume_pending_action,
    create_card,
    delete_card,
    get_card_by_id,
    get_card_credit_usage,
    get_card_id_by_name,
    get_current_open_bill_id,
    get_default_card_id,
    get_installment_group_summaries,
    get_pending_action,
    get_open_bill_summary,
    list_cards,
    list_installment_groups,
    list_open_bills,
    pay_bill_amount,
    resolve_installment_group_id,
    restore_pending_on_error,
    set_card_limit,
    set_default_card,
    set_pending_action,
    undo_credit_transaction,
    undo_installment_group,
    update_card_reminder_settings,
)
from db.cards import MAX_CARD_NAME_LEN
from utils_date import extract_date_from_text, fmt_br, now_tz, today_tz
from utils_text import (
    fmt_brl, normalize_text, parse_money, parse_pt_number,
    PT_PHRASE, PT_VALUE, PT_NUM_ALT_NO_ARTICLE, PT_NUM_ALT,
    MEMORY_STOP_TOKENS,
)

logger = logging.getLogger(__name__)

_CARD_CREATE_VERBS = (
    "criar",
    "cadastrar",
    "registrar",
    "adicionar",
    "incluir",
)

_MONTH_NAMES_PT = [
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]

_MONTH_BY_TOKEN: dict[str, int] = {
    "janeiro": 1, "jan": 1,
    "fevereiro": 2, "fev": 2,
    "marco": 3, "mar": 3,
    "abril": 4, "abr": 4,
    "maio": 5, "mai": 5,
    "junho": 6, "jun": 6,
    "julho": 7, "jul": 7,
    "agosto": 8, "ago": 8,
    "setembro": 9, "set": 9, "sete": 9,
    "outubro": 10, "out": 10,
    "novembro": 11, "nov": 11,
    "dezembro": 12, "dez": 12,
}


def _parse_month_year_token(token: str) -> tuple[int | None, int | None]:
    """Reconhece tokens de mês/ano: 'maio', 'mai', '05/2026', '5/26', '05-2026'."""
    if not token:
        return None, None
    norm = normalize_text(token)
    if norm in _MONTH_BY_TOKEN:
        return _MONTH_BY_TOKEN[norm], None
    m = re.match(r"^(\d{1,2})[\/\-](\d{2,4})$", norm)
    if m:
        mo = int(m.group(1))
        yr = int(m.group(2))
        if 1 <= mo <= 12:
            if yr < 100:
                yr += 2000
            return mo, yr
    return None, None


def _bill_due(row: dict) -> float:
    total = float(row.get("total") or 0)
    paid = float(row.get("paid_amount") or 0)
    return max(0.0, total - paid)


def _format_bill_label(row: dict) -> str:
    pe = row["period_end"]
    return f"{row['card_name']} — {_MONTH_NAMES_PT[pe.month - 1]}/{pe.year}"


def _execute_pay_bill(user_id: int, bill_row: dict, amount: float | None) -> str:
    card_id = int(bill_row["card_id"])
    bill_id = int(bill_row["id"])
    card_name = bill_row["card_name"]
    try:
        res = pay_bill_amount(user_id, card_id, card_name, amount, bill_id=bill_id)
        if isinstance(res, dict) and res.get("error") == "amount_too_high":
            return (
                "❌ Valor maior do que o em aberto.\n"
                f"Em aberto: {fmt_brl(res['due'])} | Total: {fmt_brl(res['total'])} | Já pago: {fmt_brl(res['paid_amount'])}"
            )
        if isinstance(res, dict) and res.get("error") == "invalid_amount":
            return "❌ Valor inválido. Use: pagar fatura 300"
        if not res:
            return "📭 Nada para pagar nessa fatura."
        from db import display_id_for as _display_id_for
        return (
            f"✅ Pagamento registrado: {fmt_brl(res['paid'])} ({_format_bill_label(bill_row)})\n"
            f"Conta agora: {fmt_brl(res['new_balance'])}\n"
            f"ID lançamento: #{_display_id_for(user_id, res['launch_id'])}"
        )
    except Exception as e:
        return f"❌ Erro ao pagar fatura: {e}"


def _ask_which_bill(user_id: int, candidates: list[dict], amount: float | None) -> str:
    set_pending_action(
        user_id,
        "pay_bill_choice",
        {
            "bill_ids": [int(r["id"]) for r in candidates],
            "amount": amount,
        },
        minutes=10,
    )
    lines = ["🧾 Há mais de uma fatura em aberto. Qual você quer pagar?", ""]
    for i, r in enumerate(candidates, start=1):
        lines.append(f"{i}. {_format_bill_label(r)} — em aberto {fmt_brl(_bill_due(r))}")
    lines.append("")
    lines.append("Responda com o número (ex: *1*) ou *cancelar*.")
    return "\n".join(lines)


def _handle_pay_bill_command(user_id: int, text: str) -> str | None:
    """
    Trata variações do comando `pagar`:
      - `pagar` / `paguei`                           → cartão padrão, fatura atual
      - `pagar fatura`                               → idem
      - `pagar fatura maio` / `pagar fatura 05/2026` → todas as faturas do mês
      - `pagar Nubank`                               → fatura(s) abertas do Nubank
      - `pagar Nubank maio`                          → fatura específica
      - `pagar fatura Nubank 500`                    → paga R$500 dessa fatura
    Retorna None se não conseguiu identificar (deixa o caller cair no fallback).
    """
    t = (text or "").strip()
    if not t:
        return None

    rest = re.sub(r"^(?:pagar|paguei)\b\s*(?:a\s+)?(?:fatura\b\s*)?", "", t, flags=re.IGNORECASE).strip()
    tokens = rest.split() if rest else []

    amount: float | None = None
    if tokens:
        last_val = parse_money(tokens[-1])
        if last_val is not None:
            amount = float(last_val)
            tokens = tokens[:-1]

    month: int | None = None
    year: int | None = None
    leftover: list[str] = []
    for tok in tokens:
        mo, yr = _parse_month_year_token(tok)
        if mo is not None:
            month = mo
            if yr is not None:
                year = yr
            continue
        leftover.append(tok)

    card_query = " ".join(leftover).strip() if leftover else ""

    card_id: int | None = None
    if card_query:
        cid = get_card_id_by_name(user_id, card_query)
        if cid is None:
            resolved_name = _find_card_name_in_text(user_id, card_query)
            if resolved_name:
                cid = get_card_id_by_name(user_id, resolved_name)
        if cid is None:
            # Token não é cartão conhecido. Se nem mês nem fatura na frase, deixa o
            # roteador decidir (ex.: "pagar boleto" não é função do bot).
            if month is None and not re.search(r"\bfatura\b", t, re.IGNORECASE):
                return None
            return f"❌ Não achei o cartão '{card_query}'. Veja seus cartões com: *cartoes*"
        card_id = int(cid)

    try:
        rows = list_open_bills(user_id)
    except Exception as e:
        return f"❌ Erro ao consultar faturas: {e}"

    if card_id is None and month is None:
        # Sem critério explícito → cartão padrão, fatura atual
        default_id = get_default_card_id(user_id)
        if not default_id:
            return (
                "❓ Você não tem cartão padrão. Defina com: *padrao NOME* "
                "ou tente *pagar fatura NomeDoCartão*."
            )
        card_id = int(default_id)

    candidates = [dict(r) for r in rows if _bill_due(r) > 0]
    if card_id is not None:
        candidates = [r for r in candidates if int(r["card_id"]) == card_id]
    if month is not None:
        candidates = [
            r for r in candidates
            if r["period_end"].month == month and (year is None or r["period_end"].year == year)
        ]

    if not candidates:
        if card_id is not None and month is not None:
            ref = f"{_MONTH_NAMES_PT[month - 1]}" + (f"/{year}" if year else "")
            return f"📭 Nenhuma fatura em aberto desse cartão para {ref}."
        if card_id is not None:
            return "📭 Nenhuma fatura em aberto desse cartão."
        if month is not None:
            ref = f"{_MONTH_NAMES_PT[month - 1]}" + (f"/{year}" if year else "")
            return f"📭 Nenhuma fatura em aberto para {ref}."
        return "📭 Nenhuma fatura em aberto."

    if len(candidates) == 1:
        return _execute_pay_bill(user_id, candidates[0], amount)

    return _ask_which_bill(user_id, candidates, amount)


def _resolve_pay_bill_choice(user_id: int, text: str, pending: dict) -> str | None:
    answer = (text or "").strip()
    norm = normalize_text(answer)
    if not answer or norm in ("nao", "cancelar", "cancela", "n"):
        # Abandono: condicional, para não apagar uma pendência que outra tarefa
        # armou entre a leitura e agora.
        consume_pending_action(user_id, pending)
        return "❌ Pagamento cancelado."

    payload = dict(pending.get("payload") or {})
    bill_ids: list[int] = [int(x) for x in (payload.get("bill_ids") or [])]
    amount = payload.get("amount")
    if amount is not None:
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = None

    try:
        rows = list_open_bills(user_id)
    except Exception as e:
        consume_pending_action(user_id, pending)
        return f"❌ Erro ao consultar faturas: {e}"

    candidates = [dict(r) for r in rows if int(r["id"]) in bill_ids and _bill_due(r) > 0]
    if not candidates:
        consume_pending_action(user_id, pending)
        return "📭 Nenhuma das faturas listadas continua em aberto."

    chosen: dict | None = None

    m_num = re.match(r"^#?(\d+)$", norm)
    if m_num:
        idx = int(m_num.group(1)) - 1
        if 0 <= idx < len(candidates):
            chosen = candidates[idx]

    if chosen is None:
        mo, _yr = _parse_month_year_token(answer)
        if mo is not None:
            month_matches = [r for r in candidates if r["period_end"].month == mo]
            if len(month_matches) == 1:
                chosen = month_matches[0]
            elif len(month_matches) > 1:
                return _ask_which_bill(user_id, month_matches, amount)

    if chosen is None:
        cid = get_card_id_by_name(user_id, answer)
        if cid is None:
            # `_card_name_da_resposta`, NÃO `_find_card_name_in_text`: aqui a
            # mensagem é RESPOSTA, e casar o nome do cartão dentro de uma frase
            # fazia `excluir cartao nubank` PAGAR a fatura.
            cid = _card_name_da_resposta(user_id, answer)
        if cid is not None:
            card_matches = [r for r in candidates if int(r["card_id"]) == int(cid)]
            if len(card_matches) == 1:
                chosen = card_matches[0]
            elif len(card_matches) > 1:
                return _ask_which_bill(user_id, card_matches, amount)

    if chosen is None:
        # RE-PERGUNTA (regra acima): nada aqui é gatilho — `sim` não é
        # referência de fatura, então manter a pergunta viva não arma nada. E o
        # portão já garantiu que texto não reconhecido NÃO paga.
        lines = ["❓ Não entendi qual fatura. Responda com o número:", ""]
        for i, r in enumerate(candidates, start=1):
            lines.append(f"{i}. {_format_bill_label(r)} — em aberto {fmt_brl(_bill_due(r))}")
        lines.append("")
        lines.append("Ou *cancelar* para abortar.")
        return "\n".join(lines)

    # Porteiro: `_execute_pay_bill` paga. Se a linha já não é a que lemos,
    # outra tarefa consumiu esta escolha — pagar de novo duplicaria o débito.
    if not consume_pending_action(user_id, pending):
        return None
    return _execute_pay_bill(user_id, chosen, amount)


def _pick_card_id(user_id: int, card_name: str | None):
    if card_name:
        card_id = get_card_id_by_name(user_id, card_name)
        return card_id, card_name
    card_id = get_default_card_id(user_id)
    return card_id, "padrão"


def _find_card_name_in_text(user_id: int, text: str) -> str | None:
    norm = normalize_text(text)
    cards = list_cards(user_id)
    for card in sorted(cards, key=lambda c: len(normalize_text(c["name"])), reverse=True):
        name_norm = normalize_text(card["name"])
        if name_norm and re.search(rf"\b{re.escape(name_norm)}\b", norm):
            return card["name"]
    return None


# Palavras que só enfeitam a RESPOSTA e não fazem parte do nome do cartão.
_FILLER = {"a", "o", "as", "os", "um", "uma", "do", "da", "de", "dos", "das",
           "no", "na", "nos", "nas", "em", "meu", "minha", "meus", "minhas",
           "fatura", "faturas", "cartao", "conta",
           "esse", "essa", "este", "esta"}


# Maneiras de dizer "eu escolho X" — o que se poda do COMEÇO, junto com o
# `_FILLER`. Allowlist de propósito, e a razão é estrutural, não de gosto:
# enquanto a poda era livre ("qualquer prefixo, salvo veto de verbo"), TODO
# verbo fora do veto virava porta. Medido no #323, com `pay_bill_choice`
# pendente e fatura de R$ 300 aberta: `somei 50 no nubank`, `depositei 50 no
# nubank`, `investi 50 no nubank`, `saquei 50 no nubank`, `transferi 50 no
# nubank` e `coloquei 50 no nubank` PAGAVAM a fatura, e nenhum desses verbos
# estava no veto — eles moram nas regexes de caixinha, investimento e saque, não
# nas de lançamento. Varrida palavra a palavra, a alternância inicial do
# `_ALIAS_PATTERNS` tinha MAIORIA ainda casando. Estender o veto com elas é a
# sexta rodada da mesma revisão: "verbo de comando do bot inteiro" é ABERTO.
#
# Invertido, o conjunto que precisa ser enumerado é "maneiras de dizer que
# ESCOLHO um cartão", que é pequeno e fechado. Verbo desconhecido para de ser
# porta por CONSTRUÇÃO — não por lembrar dele.
#
# Só entra o que é seleção ou fala; NUNCA verbo que descreve operação.
# `coloca`, `bota`, `poe`, `deixa`, `guarda`, `junta` ficam de FORA mesmo
# parecendo conversacionais: são a alternância literal do `pockets.deposit`
# (`core/intent_classifier.py`), e `coloca o nubank` — resposta plausível, cujo
# custo de recusa é uma re-pergunta — não paga a porta que `coloca ... no
# nubank` abriria.
#
# `acho`, `ai` e `que` também estão no `_FALA_E_MOEDA`/`_UNIDADE_DE_CARTAO`
# abaixo. Não é a mesma regra em dois lugares (§0.7): lá eles dizem "isto ainda
# é um número", aqui dizem "isto ainda não é o nome". Coincidem no vocabulário
# de enchimento de fala, não na decisão.
_SELECAO = {"quero", "pode", "ser", "escolho", "prefiro", "vai", "manda",
            "acho", "que", "ai"}

_PODAVEL_NO_PREFIXO = _FILLER | _SELECAO


# Cortesia de FIM de mensagem. Conjunto pequeno e fechado DE PROPÓSITO, e
# deliberadamente diferente do `_FILLER`: as duas pontas não correm o mesmo
# risco. No começo, o perigo é o comando vir ANTES do nome
# (`excluir cartao nubank`); no fim, é o comando vir DEPOIS (`nubank excluir`).
# Aparar o sufixo com a mesma lista do prefixo reabriria a segunda forma — por
# isso aqui só entram palavras que não são comando de nada.
#
# Este conjunto é o outro lado do `_PODAVEL_NO_PREFIXO` e obedece à MESMA
# regra: verbo de comando nunca entra em nenhum dos dois. Enquanto isso valer,
# nenhuma leitura de uma mensagem que contenha um verbo de comando consegue se
# livrar dele. Quem MEDE isso é o
# `test_verbo_que_move_dinheiro_nunca_e_podavel`, e ele olha só o
# `_PODAVEL_NO_PREFIXO`: `por` está em "por favor" E no `DEPOSIT_VERBS`, então
# a mesma interseção aqui seria falso positivo. A guarda desta ponta é o
# controle P do catálogo (`tests/_pendencia_credito_helpers.py`).

_CORTESIA_FINAL = {"por", "favor", "pf", "obrigado", "obrigada", "obg",
                   "valeu", "vlw", "pls", "please", "plz"}


def _leituras_da_resposta(alvo: str, max_tokens: int) -> list[str]:
    """Como esta resposta pode ser lida, da leitura MAIS LONGA para a mais curta.

    Três aparos, e o terceiro é o que custou um bug de dinheiro:

    - PREFIXO, e só o que está no `_PODAVEL_NO_PREFIXO` — filler e seleção
      conversacional. Para no PRIMEIRO token de fora, e é essa parada que faz
      da recusa uma propriedade de CONSTRUÇÃO: `somei 50 no nubank` não gera
      `nubank` porque `somei` não é podável, sem ninguém ter enumerado `somei`.
      Só o prefixo, nunca o miolo — um filtro global comeria o `do` do MEIO do
      nome, e `a conta` tem de virar `conta` mesmo com "conta" sendo filler,
      porque o nome do cartão pode SER uma palavra de enfeite.
    - SUFIXO de cortesia: `nubank por favor` é resposta comum e a `main`
      aceitava (por substring).
    - As DUAS leituras, com e sem a cortesia. `pf` é cortesia E é nome de cartão
      real ("PF" de pessoa física: `Nubank PF`, `Itaú PF` existem). Aparando o
      sufixo ANTES de gerar os prefixos, `a do nubank pf` produzia `nubank` e
      NUNCA `nubank pf` — com os dois cartões cadastrados e fatura aberta nos
      dois, isso PAGAVA A FATURA DO CARTÃO ERRADO. Achado pelo Codex no #323.

    A ordem do retorno é o desempate, e por isso é lista e não conjunto: a
    leitura mais LONGA vem primeiro, então o nome mais específico (`nubank pf`)
    ganha do genérico (`nubank`) quando os dois existem.

    `max_tokens` é o maior nome de cartão do usuário, EM TOKENS, e não é
    heurística: o casamento é por IGUALDADE, então leitura com mais tokens que o
    maior nome guardado não pode casar nada — gerá-la era trabalho jogado fora.
    Sem esse corte os dois laços materializam o produto prefixo × sufixo: 2.007
    caracteres viravam 160.801 leituras e 168 MB, e uma mensagem no limite do
    WhatsApp esgotava o worker (P1 do Codex no #323). O corte é só de TAMANHO —
    `i` continua limitado pelo allowlist e `fim` pela cortesia, então o conjunto
    de leituras que PODEM casar é idêntico.

    O que NÃO se faz é voltar ao `re.search` — é ele que fazia
    `excluir cartao nubank` casar `nubank` e pagar R$ 300. Os dois ataques
    seguem fora pelas duas pontas: em `excluir cartao nubank` a primeira palavra
    não é podável e a varredura de prefixo para na hora; em `nubank excluir` o
    último token não é cortesia e a de sufixo também. Como nem
    `_PODAVEL_NO_PREFIXO` nem `_CORTESIA_FINAL` contêm verbo de comando, toda
    leitura de uma mensagem que tenha um continua contendo esse verbo — o que
    tornou o veto de mensagem inteira que existia aqui código morto, medido e
    removido — a suíte de `tests/test_pendencia_credito_*.py` fica inteira
    verde sem ele, e a interseção que garante isso tem teste próprio.
    """
    tokens = alvo.split()
    # `> 1`: resposta que é SÓ cortesia ("obrigado") não pode virar string
    # vazia e casar um cartão de nome vazio.
    sem_cortesia = len(tokens)
    while sem_cortesia > 1 and tokens[sem_cortesia - 1] in _CORTESIA_FINAL:
        sem_cortesia -= 1

    # Até onde o prefixo pode ser podado: para no PRIMEIRO token fora do
    # `_PODAVEL_NO_PREFIXO`. É a diferença entre fechar a classe por construção
    # e fechá-la por enumeração — ver a nota do conjunto.
    podavel = 0
    while podavel < len(tokens) and tokens[podavel] in _PODAVEL_NO_PREFIXO:
        podavel += 1

    leituras: set[str] = set()
    # Todo corte de sufixo entre "nenhuma cortesia aparada" e "toda aparada",
    # não só os dois extremos: a poda é gulosa e em `nubank pf por favor` ela
    # comeria o `pf` junto, deixando sem o `nubank pf`, que é o nome do cartão.
    #
    # `min(podavel, fim - 1)`: o `fim - 1` garante leitura não vazia (a mesma
    # razão do `> 1` acima), e o `podavel` é o allowlist. A leitura também
    # termina SEMPRE no fim (só se poda prefixo, nunca miolo nem cauda) — é isso
    # que mantém `nubank fatura` e `nubank saldo` fora, porque ali o nome não é
    # sufixo da mensagem.
    for fim in range(len(tokens), sem_cortesia - 1, -1):
        for i in range(max(0, fim - max_tokens), min(podavel, fim - 1) + 1):
            leituras.add(" ".join(tokens[i:fim]))
    return sorted(leituras, key=len, reverse=True)


def _card_name_da_resposta(user_id: int, answer: str):
    """Id do cartão quando a MENSAGEM INTEIRA é a resposta — não substring.

    `_find_card_name_in_text` é para COMANDO, e nos oito chamadores dela casar
    dentro da frase é CERTO ("parcelei 500 no nubank"). Numa RESPOSTA é errado,
    e caro: `excluir cartao nubank` respondendo "qual fatura?" casava "nubank"
    e PAGAVA R$ 300.

    Normaliza OS DOIS LADOS, como o `_find_card_name_in_text` já fazia — é o que
    dobra acento e pontuação, e é por isso que ele achava `Itaú`/`C6-Carbon` e a
    versão anterior daqui não (ela normalizava só a resposta e consultava o
    `get_card_id_by_name`, que só faz `lower()`). O que NÃO se reusa dele é o
    `re.search` de substring, que é justamente o defeito.

    Compara por IGUALDADE contra as leituras da resposta, nunca por substring.
    # ponytail: filler fixo; se aparecer variante regional, some nela.
    """
    alvo = normalize_text(answer)
    if not alvo:
        return None
    # Percorre as LEITURAS (mais longa primeiro), não os cartões: a ordem é o
    # desempate entre `Nubank` e `Nubank PF`, e iterar os cartões a perderia.
    por_nome: dict[str, int] = {}
    for card in list_cards(user_id):
        nome = normalize_text(card["name"])
        if nome:
            por_nome.setdefault(nome, card["id"])
    if not por_nome:
        return None
    # O `min` é o que fecha o buraco do maior nome GUARDADO: `credit_cards.name`
    # é `text` e o teto de `validate_card_name` nasceu neste PR, então linha
    # antiga (ou vinda do Open Finance antes da poda) pode ter mil tokens e
    # devolver o produto cartesiano que o corte tinha eliminado — o limite era
    # controlado pelo atacante (P1 do Codex no #323). Validação nova não
    # conserta dado velho; o teto absoluto conserta.
    #
    # O teto SAI do limite de caracteres (§0.7) e por isso não corta nome
    # válido: nome de N caracteres tem no máximo (N+1)//2 tokens. O que ele
    # corta é só nome fora do limite atual — que deixa de casar por resposta de
    # pendência, e o custo prático disso é uma re-pergunta ("qual cartão?") até
    # o usuário renomeá-lo para dentro do teto.
    max_tokens = min(max(len(nome.split()) for nome in por_nome),
                     (MAX_CARD_NAME_LEN + 1) // 2)
    for leitura in _leituras_da_resposta(alvo, max_tokens):
        if leitura in por_nome:
            return por_nome[leitura]
    return None


# Palavras que acompanham um número numa RESPOSTA sem mudar o assunto dela.
#
# Divide-se em DUAS fatias de propósito, porque só uma delas é nossa:
#
# 1. `_FALA_E_MOEDA` — unidade de dinheiro e enchimento de fala. Isto NÃO é
#    nosso: é a mesma coisa que o `MEMORY_STOP_TOKENS` (`utils_text.py`) já
#    enumera, e escrever a segunda lista foi como nasceram quatro divergências
#    medidas — `5000 pila` passava e `5000 pilas` não, `acho que 5000` era
#    recusado com "acho" na lista canônica desde sempre.
#    SELEÇÃO e não reuso literal: a lista canônica tem `dashboard`, `eu` e
#    `meu`, e reusá-la inteira faria `dashboard 5000` virar limite. O
#    `test_fala_e_moeda_nao_divergiu_do_memory_stop_tokens` prova que a seleção
#    continua existindo na origem — renomeie um token lá e ele morre (§0.7).
_FALA_E_MOEDA = MEMORY_STOP_TOKENS & {
    "reais", "real", "centavos", "centavo", "conto", "contos",
    "pila", "pilas", "mango", "mangos",
    "acho", "tipo", "ai", "valor", "mais", "menos", "acredita",
}

# 2. `_UNIDADE_DE_CARTAO` — vocabulário DESTA pergunta (dia de fechamento,
#    vencimento, limite). Não existe em lugar nenhum do repositório, e por isso
#    é escrito aqui. "mil"/"milhão" entram aqui e não no vocabulário de números
#    por extenso porque lá eles são MULTIPLICADOR, tratado à parte no
#    `parse_money`. "r"/"rs" são a borda de "R$ 5.000,00" depois do
#    `normalize_text` (que vira "r 5 000 00").
_UNIDADE_DE_CARTAO = {
    "r", "rs", "limite",
    "mil", "milhao", "milhoes", "bilhao", "bilhoes",
    "dia", "dias", "todo", "toda", "todos", "todas", "cada",
    "mes", "meses", "antes", "depois", "fecha", "fechamento", "fechar",
    "vence", "vencimento", "vencer", "e", "que", "uns", "umas", "por", "volta",
    "cerca", "aproximadamente", "ate",
}

# A rodada 8 tinha aqui uma terceira fatia, `_VERBO_CONVERSACIONAL`
# ("pode", "quero", "colocar"...), para aceitar resposta numérica dita
# conversando. Ela MORREU na rodada 9 e não foi substituída: verbo do português
# é conjunto ABERTO — faltavam `coloque`, `bote`, `ponha`, `deixe` — e a
# segunda via do `_so_numero` (o oráculo `out_of_scope`) cobre a categoria
# inteira sem enumerar nada. Medido: com e sem a lista, resultado IDÊNTICO nas
# 64 strings do corpus (45 legítimas + 19 ataques). Enumerar conjugação era
# garantir uma rodada de revisão por verbo esquecido.
_UNIDADE_DE_RESPOSTA = _FALA_E_MOEDA | _UNIDADE_DE_CARTAO


# `PT_NUM_ALT` (público desde sempre, `utils_text.py`) em vez de uma cópia do
# dicionário: é a MESMA fonte que o `parse_money` usa, e as regexes derivadas
# dele são congeladas no import — o conjunto cru não é exposto de propósito.
_NUM_POR_EXTENSO_RE = re.compile(rf"(?:{PT_NUM_ALT})", re.IGNORECASE)


def _so_numero(text: str) -> bool:
    """A resposta traz um número e NENHUM comando reconhecível?

    NÃO é "tem formato de número". Os parsers deste arquivo (`_parse_day`,
    `parse_money`) fazem `search`, não `fullmatch`: acham o número DENTRO da
    frase, e é assim que "gastei 50 no mercado" respondendo "qual o limite?"
    gravava limite de R$ 50,00. O portão existe para recusar ESSE caso.

    DUAS VIAS, em união, porque cada uma cobre o furo da outra — medido:

    1. TOKENS: sobra alguma palavra que mude o assunto depois de tirar número,
       filler, unidade e verbo de preenchimento? Cobre o vocabulário de moeda e
       de fala ("5 mil", "3 dias antes", "5000 pilas", "5 de cada mes"), que o
       classificador lê como `launches.add` e recusaria.
    2. ORÁCULO: `classify(..., allow_ai=False).intent == "out_of_scope"`, ou
       seja, o classificador NÃO reconheceu comando nenhum. Cobre a conjugação
       que a lista de verbos nunca vai fechar — "coloque 5000", "bote 3000",
       "ponha 5000", "quero que seja dia 10" são todos `out_of_scope/0.00`.
       Verbo do português é conjunto ABERTO, e enumerar era garantir uma
       rodada de revisão por conjugação esquecida.

    POR QUE UNIÃO E NÃO SÓ O ORÁCULO (medido nesta árvore): sozinho, ele
    QUEBRA 10 respostas legítimas que hoje funcionam — `5 mil`, `10 mil`,
    `3 dias`, `3 dias antes`, `5 de cada mes`, `5000 pila`, `5000 pilas`,
    `5000 contos`, `5000 mangos` e `5 mil reais e 50 centavos` classificam
    `launches.add/0.95`, não `out_of_scope`. Trocar tokens POR oráculo seria
    reabrir exatamente o R3-2.

    O CUSTO da união, enumerado (só estes dois no corpus adversarial): as
    frases sem comando reconhecível mas com assunto próprio — "dashboard 5000",
    "quero comprar uma tv de 5000" — passam a ser aceitas e gravam 5000. É
    METADADO (limite, dia de fechamento, dias de aviso), nunca dinheiro: o
    `_so_numero` só guarda `closing_day`, `due_day`, `reminder_days` e
    `credit_limit_ask`. O erro é visível na confirmação e o usuário corrige;
    o erro oposto — recusar resposta legítima — já custou quatro rodadas.

    `allow_ai=False` é requisito, não otimização: mesma razão do
    `abandona_pergunta_de_credito` — com o tier 3 no meio o oráculo volta a ser
    ilimitado e uma alucinação do LLM passa a decidir o portão.
    """
    tokens = normalize_text(text).split()
    if not tokens:
        return False
    e_numero = lambda t: t.isdigit() or _NUM_POR_EXTENSO_RE.fullmatch(t)
    if not any(e_numero(t) for t in tokens):
        return False  # "nao", "sim", "amanha": não há número nenhum a ler
    if all(e_numero(t) or t in _UNIDADE_DE_RESPOSTA or t in _FILLER
           for t in tokens):
        return True
    return classify(text, allow_ai=False).intent == "out_of_scope"


# ---------------------------------------------------------------------------
# RESPOSTA NÃO RECONHECIDA: RE-PERGUNTAR ou ABANDONAR?
#
# A regra é UMA, e está escrita aqui porque ela se aplica a nove portões
# espalhados por este arquivo — deixá-la implícita é como a próxima rodada
# uniformiza os nove e reabre o footgun.
#
#   Manter a pergunta viva ARMA UM GATILHO DESTRUTIVO?
#     SIM  -> `return None`: o `route()` abandona a pendência com aviso.
#     NÃO  -> devolve a re-pergunta: a pendência segue viva.
#
# Por que re-perguntar é seguro no caso NÃO: o `route()` já testou a allowlist
# de comandos (`abandona_pergunta_de_credito`) ANTES de chamar este arquivo.
# Se a mensagem chegou aqui, ela não é comando conhecido — é muito mais provável
# ser uma resposta malformada ("setembro/2026", "a primeira", "09/2026") do que
# um comando, e para essas a re-pergunta é exatamente o que ajuda. Abandonar
# daria "🔕 Cancelei a pergunta anterior" + "não entendi", que é pior.
#
# Por que abandonar é obrigatório no caso SIM: manter viva uma CONFIRMAÇÃO
# destrutiva é precisamente o que o `sim` do turno seguinte vira delete em
# cascata ("excluir cartao nubank" -> "tchau" -> "sim"). Perder a pergunta é
# fail-safe, e é a mesma escolha que o `docs/armadilhas.md` registra.
#
# ABANDONAM (o `sim` seguinte destruiria):
#   `_resolve_delete_card`, `_resolve_set_primary` confirm,
#   step `confirm_delete_existing_card`  -> o `sim` seguinte APAGA cartão;
#   step `set_primary`                   -> o `sim` seguinte TROCA o principal;
#   step `reminder_opt_in`               -> o `sim` seguinte LIGA o lembrete;
# NEM UM NEM OUTRO: o step `duplicate_card_name` é TEXTO LIVRE (o usuário
#   inventa um nome novo de cartão), então "não reconheci" não distingue
#   resposta de comando e o portão não existe. Resposta vazia ali é CANCELAR,
#   igual aos dois irmãos deste arquivo.
# RE-PERGUNTAM (nada a armar; `sim` não é resposta válida em nenhum deles):
#   `_resolve_pay_bill_choice`     -> `sim` não é referência de fatura;
#   `_resolve_set_primary` step `choose` -> `sim` não é nome de cartão;
#   steps `closing_day`, `due_day`, `reminder_days`, `credit_limit_ask`
#                                  -> `sim` não é número.
#
# O que separa os dois grupos é UMA pergunta, e ela é sempre a mesma: o texto
# do PRÓXIMO turno pode ser lido como um "sim" que dispara algo? Nos de cima o
# handler lê sim/não; nos de baixo ele lê número, nome ou referência de fatura,
# e um "sim" solto não é nenhum dos três.
# ---------------------------------------------------------------------------


def _extract_unknown_card_candidate(text: str) -> str | None:
    norm = normalize_text(text)
    patterns = [
        r"\bmeu\s+([a-z0-9]+)\s+(?:vence|fecha)\b",
        r"\bfatura\s+do\s+([a-z0-9]+)\b",
        r"\bfatura\s+de\s+([a-z0-9]+)\b",
        r"\bcartao\s+([a-z0-9]+)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, norm)
        if m:
            candidate = (m.group(1) or "").strip()
            if candidate and candidate not in {"principal", "padrao", "padrão", "cartao", "fatura"}:
                return candidate
    return None


def _get_primary_or_single_card(user_id: int) -> dict | None:
    cards = list_cards(user_id)
    if not cards:
        return None
    current = next((c for c in cards if c.get("is_default")), None)
    if current:
        return current
    if len(cards) == 1:
        return cards[0]
    return None


def _resolve_card_from_context(user_id: int, text: str) -> tuple[dict | None, str | None]:
    cards = list_cards(user_id)
    if not cards:
        return None, "📭 Você ainda não tem cartões cadastrados."

    explicit_name = _find_card_name_in_text(user_id, text)
    if explicit_name:
        card_id = get_card_id_by_name(user_id, explicit_name)
        if not card_id:
            return None, f"❌ Não achei o cartão '{explicit_name}'."
        return get_card_by_id(user_id, card_id), None

    if any(x in normalize_text(text) for x in ("deste cartao", "desse cartao", "cartao atual", "cartao principal", "padrao", "padrão")):
        current = _get_primary_or_single_card(user_id)
        if current:
            return current, None
        return None, "Você tem mais de um cartão. Me diga qual deles você quer consultar. Ex: **fatura nubank**."

    return None, None


def _infer_category_result(user_id: int, desc: str):
    return infer_category(user_id, desc or "", allow_ai=True)


def _infer_category(user_id: int, desc: str) -> str:
    return _infer_category_result(user_id, desc).category


def _is_natural_credit_purchase(text: str) -> bool:
    norm = normalize_text(text)
    if norm.startswith("paguei fatura"):
        return False
    if not re.match(r"^(gastei|paguei|comprei|debitei|gasto)\b", norm):
        return False
    return any(token in norm for token in ("cartao", "credito"))


def _extract_card_reference_for_purchase(user_id: int, text: str) -> tuple[str | None, str | None]:
    explicit_name = _find_card_name_in_text(user_id, text)
    if explicit_name:
        return explicit_name, None

    norm = normalize_text(text)
    if any(x in norm for x in ("cartao principal", "cartao padrao", "cartao padrão", "no credito", "no crédito", "credito", "crédito")):
        return None, None

    m = re.search(r"\bcart[aã]o\s+(.+)$", text, re.IGNORECASE)
    if not m:
        return None, None

    candidate = m.group(1).strip()
    candidate = re.split(r"\b(com|para|pra|em|dia|hoje|ontem)\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0].strip(" -:;,.")
    if not candidate:
        return None, None

    if normalize_text(candidate) in {"principal", "padrao", "padrão"}:
        return None, None

    return candidate, candidate


def _clean_credit_purchase_description(text: str, card_name: str | None) -> str:
    desc = text.strip()
    desc = re.sub(r"^\s*(gastei|paguei|comprei|debitei|gasto)\b", "", desc, flags=re.IGNORECASE).strip()
    desc = re.sub(r"\d+(?:[.,]\d+)?", "", desc, count=1).strip()
    desc = re.sub(r"^\s*reais?\b", "", desc, flags=re.IGNORECASE).strip()
    desc = re.sub(r"\b(?:no|na|em|com)\s+cr[eé]dito\b", "", desc, flags=re.IGNORECASE).strip()
    desc = re.sub(r"\b(?:no|na|em|com)\s+cart[aã]o\s+(?:principal|padr[aã]o)\b", "", desc, flags=re.IGNORECASE).strip()
    if card_name:
        desc = re.sub(
            rf"\b(?:no|na|em|com)\s+cart[aã]o\s+{re.escape(card_name)}\b",
            "",
            desc,
            flags=re.IGNORECASE,
        ).strip()
    # Remove preposição/conector solto que sobra ao tirar "...no crédito"/cartão
    # (ex.: "no crédito com minha namorada" → "com minha namorada" → "minha namorada").
    desc = re.sub(r"^\s*(?:com|no|na|em)\b\s*", "", desc, flags=re.IGNORECASE).strip()
    desc = re.sub(r"\s+", " ", desc).strip(" -:;,.")
    return desc


def add_credit_from_entities(
    user_id: int,
    *,
    valor: float,
    card_name: str | None = None,
    descricao: str | None = None,
    categoria: str | None = None,
    purchased_at=None,
    installments: int | None = None,
) -> str:
    """Registra compra no cartão de crédito a partir de args já estruturados.

    Chamada por:
      - `try_handle_natural_credit_purchase` (linguagem natural: "gastei 50 no cartão")
      - branch `credito X` (formato compacto)
      - tool de IA `add_credit_purchase` (LLM extrai os args)

    Toda lógica compartilhada (resolução de cartão, validação de limite,
    categorização, learn, parcelamento, formato da resposta) vive aqui.
    """
    if valor is None or float(valor) <= 0:
        return "❌ Valor inválido."

    if purchased_at is None:
        purchased_at = now_tz().date()

    card_name_clean = (card_name or "").strip() or None
    if card_name_clean:
        card_id = get_card_id_by_name(user_id, card_name_clean)
        if not card_id:
            return (
                f"❌ Não achei o cartão '{card_name_clean}'. "
                f"Crie com: criar cartao {card_name_clean} fecha 10 vence 17"
            )
    else:
        card_id = get_default_card_id(user_id)
        if not card_id:
            return (
                "❓ Você não tem cartão padrão. Defina com: padrao NOME "
                "(ou crie: criar cartao nubank fecha 10 vence 17)"
            )

    card = get_card_by_id(user_id, card_id)
    card_label = card["name"] if card else (card_name_clean or "cartão")

    limit_error = _validate_credit_limit_before_purchase(user_id, card_id, float(valor))
    if limit_error is not None:
        return limit_error

    desc_clean = (descricao or "").strip() or None
    category_reason = "manual"
    if not categoria:
        inferred = _infer_category_result(user_id, desc_clean or "")
        categoria = inferred.category
        category_reason = inferred.reason
    nota = normalize_text(desc_clean) if desc_clean else "compra no credito"

    n = int(installments) if installments else 1
    if n > 1:
        return _create_installments(
            user_id=user_id,
            card_id=card_id,
            resolved_name=card_label,
            valor=float(valor),
            n=n,
            nota=nota,
            categoria=categoria or "outros",
            purchased_at=purchased_at,
            category_reason=category_reason,
        )

    try:
        tx_id, due, _bill_id = add_credit_purchase(
            user_id=user_id,
            card_id=card_id,
            valor=float(valor),
            categoria=categoria,
            nota=nota,
            purchased_at=purchased_at,
        )
        if desc_clean:
            learn_from_inference(
                user_id, desc_clean, categoria,
                target_hint=desc_clean, reason=category_reason,
            )
        # Pending efêmero: o runtime do WhatsApp lê e anexa um botão "🗑️ Apagar"
        # na confirmação (consome na 1ª renderização). Demais canais ignoram;
        # expira sozinho se não for consumido.
        try:
            set_pending_action(user_id, "delete_credit_purchase", {"tx_id": int(tx_id)})
        except Exception:
            logger.warning(
                "falha ao salvar pending delete_credit_purchase (user %s, tx %s) — botão Apagar não vai aparecer",
                user_id, tx_id, exc_info=True,
            )
        return _format_credit_purchase_success(card_label, float(valor), purchased_at, float(due), int(tx_id))
    except Exception as e:
        return f"❌ Erro registrando compra no crédito: {e}"


def try_handle_natural_credit_purchase(user_id: int, text: str) -> str | None:
    if not _is_natural_credit_purchase(text):
        return None

    dt_evento, text_without_date = extract_date_from_text(text)
    if dt_evento is None:
        dt_evento = now_tz()
    purchased_at = dt_evento.date()
    base_text = (text_without_date or text).strip()

    valor = parse_money(base_text)
    if valor is None:
        return "❌ Não achei o valor da compra no crédito. Ex: `gastei 120 no cartao nubank`"

    card_name_hint, unknown_card_candidate = _extract_card_reference_for_purchase(user_id, base_text)
    if unknown_card_candidate and not get_card_id_by_name(user_id, unknown_card_candidate):
        return f"❌ Não achei o cartão '{unknown_card_candidate}'. Crie com: criar cartao {unknown_card_candidate} fecha 10 vence 17"

    desc = _clean_credit_purchase_description(base_text, card_name_hint or unknown_card_candidate)

    return add_credit_from_entities(
        user_id,
        valor=float(valor),
        card_name=card_name_hint,
        descricao=desc or None,
        purchased_at=purchased_at,
    )


def _extract_credit_transaction_id(text: str) -> int | None:
    norm = normalize_text(text)
    m = re.search(r"\bct\s*#?\s*(\d+)\b", norm)
    if not m:
        m = re.search(r"\bcc\s*#?\s*(\d+)\b", norm)
    if not m:
        m = re.search(r"\b(?:compra|credito|crédito)\s+#?\s*(\d+)\b", norm)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _create_installments(
    user_id: int,
    card_id: int,
    resolved_name: str,
    valor: float,
    n: int,
    nota: str,
    categoria: str,
    purchased_at,
    category_reason: str = "manual",
) -> str:
    """Cria as parcelas no banco e retorna a mensagem de confirmação."""
    from datetime import date as _date
    if isinstance(purchased_at, str):
        purchased_at = _date.fromisoformat(purchased_at)
    try:
        ret = add_credit_purchase_installments(
            user_id=user_id,
            card_id=card_id,
            valor_total=valor,
            categoria=categoria,
            nota=nota,
            purchased_at=purchased_at,
            installments=n,
        )
        result = ret[0] if isinstance(ret, tuple) else ret
        total = float(ret[1]) if isinstance(ret, tuple) and len(ret) >= 2 else valor
        group_id = result.get("group_id")
        learn_from_inference(
            user_id,
            nota,
            categoria,
            target_hint=nota,
            reason=category_reason,
        )
        # Valor por parcela arredondado pra exibição (último ajuste de centavos
        # fica na última parcela; aqui mostramos o nominal pra UX limpa).
        v_parc = valor / n if n else valor
        code = _group_code(group_id)
        return (
            f"✅ **Parcelamento Registrado!**\n\n"
            f"📝 **Descrição:** {nota}\n"
            f"🛍️ **Categoria:** {categoria}\n"
            f"💳 **Valor:** {n}x de {fmt_brl(v_parc)} (total {fmt_brl(total)})\n"
            f"🪪 **Cartão:** {resolved_name}\n"
            f"⚙️ **Código:** {code}\n\n"
            f"Pra apagar: `apagar {code}`"
        )
    except Exception as e:
        return f"❌ Erro ao parcelar no cartão: {e}"


def _extract_installment_group_id(user_id: int, text: str) -> str | None:
    norm = normalize_text(text)
    if not any(x in norm for x in ("grupo", "group", "parcelamento", "parcela")):
        m_short = re.search(r"\bpc([0-9a-f]{8})\b", norm)
        if m_short:
            return resolve_installment_group_id(user_id, f"pc{m_short.group(1)}")
        return None
    m = re.search(
        r"\b(?:grupo|group|parcelamento|parcela)\s+(pc[0-9a-f]{8}|par-[0-9a-f]{8}|[0-9a-f]{8}|[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        norm,
    )
    if not m:
        return None
    return resolve_installment_group_id(user_id, m.group(1))


def _is_credit_delete_command(text: str) -> bool:
    norm = normalize_text(text)
    if not any(x in norm for x in ("desfazer", "apagar", "excluir", "remover", "delete", "deletar")):
        return False
    return any(x in norm for x in ("ct", "cc", "pc", "grupo", "group", "compra", "credito", "crédito", "parcelamento", "parcela"))


def _is_yes(text: str) -> bool:
    return normalize_text(text) in {"sim", "s", "yes", "y", "quero", "claro", "ok", "pode"}


# IMPORTADAS do classificador (§0.7), não copiadas: `NEGATIVAS_EXATAS` é a
# seleção de `confirm.no` do `_EXACT`. A cópia à mão perdia CINCO das dez
# (`nope`, `negativo`, `melhor nao`, `deixa pra la`, `deixa quieto`) — frases
# que o classificador chama de negativa e o portão daqui não reconhecia (Codex,
# #323). `nao`/`agora nao` já vinham de lá; as versões acentuadas saíram porque
# o `normalize_text` tira acento antes da comparação e elas eram inalcançáveis.
#
# `n` e `no` ficam à mão: soltos são ambíguos demais para o `_EXACT`, que é
# global, mas aqui já existe uma pergunta de sim/não na mesa.
_NEGATIVAS_EXATAS = NEGATIVAS_EXATAS | {"n", "no"}


def _is_no(text: str) -> bool:
    """Negativa, em pergunta de sim/não.

    UNIÃO, não substituição: as negativas canônicas do classificador MAIS
    "começa com não". Só os literais deixavam de fora a negativa natural —
    `nao quero`, `não obrigado`,
    `nao precisa`, `nao agora` —, e o efeito não era só "não entendi": o portão
    devolvia `None`, o `route()` abandonava, e `não quero` (que é
    `confirm.no/1.00`) saía como **"Nada a cancelar."**, resposta de outro
    assunto, pulando o resto do cadastro.

    Medido: das 19 positivas do corpus (`sim`, `quero sim`, `pode sim`,
    `claro que sim`, `beleza`...), NENHUMA começa com "não" — a união não
    ambigua nada.

    O lado positivo NÃO ganhou regra equivalente, e é decisão, não esquecimento:
    `_is_yes` também perde `quero sim`/`pode sim`, mas alargar o SIM é alargar o
    gatilho de uma confirmação DESTRUTIVA (`_resolve_delete_card`,
    `confirm_delete_existing_card`). Não reconhecer um "sim" é fail-safe — o
    cartão fica de pé; não reconhecer um "não" é só confuso. As duas pontas não
    correm o mesmo risco, igual ao `_CORTESIA_FINAL`.
    """
    norm = normalize_text(text)
    return norm in _NEGATIVAS_EXATAS or norm.startswith(("nao ", "não "))


def _is_delete(text: str) -> bool:
    return normalize_text(text) in {"excluir", "excluir cartao", "excluir cartão", "deletar", "apagar", "remover", "delete"}


def _prompt_duplicate_card(user_id: int, card_name: str, payload: dict, minutes: int = 20) -> str:
    """
    Salva o pending com step 'duplicate_card_name' e devolve a mensagem de aviso.
    Preserva closing_day / due_day no payload se já foram coletados.
    """
    existing_id = get_card_id_by_name(user_id, card_name)
    existing_card = get_card_by_id(user_id, existing_id) if existing_id else None

    payload["step"] = "duplicate_card_name"
    payload["existing_card_id"] = existing_id
    payload["existing_card_name"] = card_name
    set_pending_action(user_id, "credit_card_setup", payload, minutes=minutes)

    existing_info = ""
    if existing_card:
        existing_info = (
            f"\n📋 Cartão atual: fecha dia {existing_card['closing_day']} / vence dia {existing_card['due_day']}"
        )

    return (
        f"⚠️ Já existe um cartão chamado **{card_name}**.{existing_info}\n\n"
        "• Digite um **novo nome** para criar com outro nome\n"
        "• **excluir** para remover o cartão existente\n"
        "• **cancelar** para desistir"
    )


def _parse_day(text: str) -> int | None:
    norm = normalize_text(text)
    m = re.search(r"\b(\d{1,2})\b", norm)
    if not m:
        return None
    day = int(m.group(1))
    if 1 <= day <= 31:
        return day
    return None


def _parse_card_name_from_create(text: str) -> str | None:
    m = re.search(
        r"(?:criar|cadastrar|registrar|adicionar|incluir)\s+(?:um\s+|novo\s+|meu\s+)?cart[aã]o\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    raw = m.group(1).strip()
    # Remove sufixos "fecha(mento) N" e "vence/vencimento N" e tudo depois
    raw = re.sub(
        r"\s+(?:fecha(?:mento)?|venc(?:e|imento)?)\s+\d{1,2}\b.*$",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    return raw or None


def _parse_inline_days(text: str) -> tuple[int | None, int | None]:
    """
    Extrai closing_day e due_day de um comando inline como:
      'criar cartão Nubank fechamento 01 vencimento 08'
      'criar cartão Nubank fecha 10 vence 17'
    Retorna (closing_day, due_day) ou (None, None) se não encontrado.
    """
    m_close = re.search(
        r"\b(?:fecha(?:mento)?)\s+(\d{1,2})\b",
        text,
        re.IGNORECASE,
    )
    m_due = re.search(
        r"\b(?:venc(?:e|imento)?)\s+(\d{1,2})\b",
        text,
        re.IGNORECASE,
    )
    closing = int(m_close.group(1)) if m_close else None
    due = int(m_due.group(1)) if m_due else None
    # Valida range
    if closing is not None and not (1 <= closing <= 31):
        closing = None
    if due is not None and not (1 <= due <= 31):
        due = None
    return closing, due


def _is_card_create_request(text: str) -> bool:
    norm = normalize_text(text)
    if "cartao" not in norm:
        return False
    return any(re.search(rf"\b{verb}\b", norm) for verb in _CARD_CREATE_VERBS) or "novo cartao" in norm


def _build_credit_contextual_help(text: str) -> str:
    norm = normalize_text(text)

    if _is_card_create_request(text):
        return (
            "💳 Para cadastrar um cartão, eu posso te guiar de duas formas:\n"
            "• `criar cartao Nubank fecha 10 vence 17`\n"
            "• `criar cartao Nubank` e eu pergunto o restante\n\n"
            "Se preferir, também funciona escrever `cadastrar cartao` ou `registrar cartao`."
        )

    if any(expr in norm for expr in ("apagar cartao", "excluir cartao", "remover cartao", "deletar cartao")):
        return (
            "🗑️ Para apagar um cartão, use:\n"
            "• `excluir cartao Nubank`\n\n"
            "Antes de remover, eu vou pedir sua confirmação."
        )

    if any(expr in norm for expr in ("cartao principal", "cartao padrao", "cartao padrão", "principal")):
        return (
            "⭐ Posso te ajudar com o cartão principal assim:\n"
            "• `qual meu cartao principal?`\n"
            "• `mudar cartao principal`\n"
            "• `padrao Nubank`"
        )

    if any(expr in norm for expr in ("vence", "fecha")) and "cartao" in norm:
        return (
            "📅 Para consultar fechamento ou vencimento, tente:\n"
            "• `quando vence o cartao Nubank?`\n"
            "• `quando fecha o cartao Nubank?`\n"
            "• `qual cartao fecha dia 30?`"
        )

    if any(expr in norm for expr in ("pagar", "paguei")) or "fatura" in norm:
        return (
            "🧾 Posso te ajudar com fatura/pagamento assim:\n"
            "• `pagar fatura` — paga a fatura atual do cartão padrão\n"
            "• `pagar Nubank` — paga a fatura do Nubank (lista se houver mais de uma em aberto)\n"
            "• `pagar fatura maio` — paga a fatura de maio\n"
            "• `pagar fatura Nubank 500` — paga R$ 500 da fatura do Nubank\n"
            "• `fatura Nubank` — consulta a fatura\n"
            "• `faturas` — lista todas as faturas em aberto"
        )

    if any(expr in norm for expr in ("parcela", "parcelamento", "parcelar")):
        return (
            "📦 Para parcelamentos, você pode usar:\n"
            "• `parcelar 600 em 3x no cartao Nubank`\n"
            "• `parcelamentos`\n"
            "• `apagar PCAB12CD34`"
        )

    if any(expr in norm for expr in ("compra", "compras", "credito", "cartao")):
        return (
            "Não entendi exatamente o que você quer fazer sobre cartões.\n"
            "💳 Posso te ajudar com cartões de algumas formas:\n"
            "• `criar cartao Nubank fecha 10 vence 17`\n"
            "• `cartoes`\n"
            "• `gastei 150 no cartao Nubank`\n"
            "• `fatura Nubank`\n"
            "• `qual meu cartao principal?`"
        )

    return (
        "Não entendi exatamente o que você quer fazer sobre cartões.\n"
        "Tente uma destas opções:\n"
        "• `criar cartao Nubank`\n"
        "• `cartoes`\n"
        "• `fatura Nubank`\n"
        "• `gastei 150 no cartao Nubank`\n"
        "• `qual meu cartao principal?`"
    )


def contextual_help(text: str) -> str | None:
    norm = normalize_text(text)
    if not any(k in norm for k in ("cartao", "cartoes", "fatura", "credito", "parcela", "parcelamento", "vence", "fecha", "pagar", "paguei")):
        return None
    return _build_credit_contextual_help(text)


def _card_summary(card: dict) -> str:
    reminder_txt = "desativado"
    if card.get("reminders_enabled"):
        reminder_txt = f"{int(card.get('reminders_days_before') or 3)} dia(s) antes"
    principal = "Sim" if card.get("is_default") else "Não"
    limit_txt = fmt_brl(float(card["credit_limit"])) if card.get("credit_limit") else "não definido"
    return (
        f"• Nome: {card['name']}\n"
        f"• Fechamento: dia {card['closing_day']}\n"
        f"• Vencimento: dia {card['due_day']}\n"
        f"• Limite: {limit_txt}\n"
        f"• Cartão principal: {principal}\n"
        f"• Lembrete: {reminder_txt}"
    )


def _extract_card_name_for_delete(text: str) -> str | None:
    m = re.search(r"(?:excluir|apagar|remover|deletar)\s+cart[aã]o\s+(.+)$", text, re.IGNORECASE)
    if not m:
        return None
    return (m.group(1) or "").strip() or None


def _purchase_code(tx_id: int) -> str:
    return f"CC{int(tx_id)}"


def _group_code(group_id) -> str:
    raw = str(group_id or "").replace("-", "").upper()
    return f"PC{raw[:8]}" if raw else "PC?"


def _format_credit_purchase_success(card_label: str, valor: float, purchased_at, due: float, tx_id: int) -> str:
    code = _purchase_code(tx_id)
    return (
        f"✅ **Compra no Crédito Registrada!**\n\n"
        f"💰 **Valor:** {fmt_brl(valor)}\n"
        f"🪪 **Cartão:** {card_label}\n"
        f"📅 **Data:** {fmt_br(purchased_at)}\n"
        f"📌 **Fatura atual:** {fmt_brl(due)}\n"
        f"⚙️ **Código:** {code}\n\n"
        f"Pra apagar: `apagar {code}`"
    )


def _build_credit_limit_block_message(card_name: str, attempted_amount: float, limit_amount: float, used_amount: float) -> str:
    available = max(0.0, limit_amount - used_amount)
    exceeded = max(0.0, attempted_amount - available)
    return (
        f"❌ Compra não registrada no cartão **{card_name}**.\n"
        f"💳 Limite total: {fmt_brl(limit_amount)}\n"
        f"📌 Já usado: {fmt_brl(used_amount)}\n"
        f"🟢 Disponível: {fmt_brl(available)}\n"
        f"🧾 Tentativa de compra: {fmt_brl(attempted_amount)}\n"
        f"⚠️ Excede o limite em {fmt_brl(exceeded)}."
    )


def _validate_credit_limit_before_purchase(user_id: int, card_id: int, purchase_amount: float) -> str | None:
    card = get_card_by_id(user_id, card_id)
    if not card:
        return "❌ Cartão não encontrado."

    limit_amount = card.get("credit_limit")
    if limit_amount is None:
        return None

    used_amount = float(get_card_credit_usage(user_id, card_id))
    limit_float = float(limit_amount)
    if used_amount + float(purchase_amount) > limit_float:
        return _build_credit_limit_block_message(card["name"], float(purchase_amount), limit_float, used_amount)
    return None


def _format_cards_list(user_id: int, cards: list[dict]) -> str:
    lines = ["💳 **Seus cartões cadastrados**", ""]
    for card in cards:
        title_bits = [f"**{card['name']}**"]
        if card.get("is_default"):
            title_bits.append("⭐ principal")

        limit_amount = card.get("credit_limit")
        if limit_amount is None:
            limit_lines = ["💰 Limite: **não definido**"]
        else:
            used_amount = float(get_card_credit_usage(user_id, int(card["id"])))
            limit_float = float(limit_amount)
            available = max(0.0, limit_float - used_amount)
            limit_lines = [
                f"💰 Limite: **{fmt_brl(limit_float)}**",
                f"📌 Em uso: {fmt_brl(used_amount)}",
                f"🟢 Disponível: {fmt_brl(available)}",
            ]

        reminder_txt = "desativado"
        if card.get("reminders_enabled"):
            reminder_txt = f"{int(card.get('reminders_days_before') or 3)} dia(s) antes"

        lines.append(f"💳 {' • '.join(title_bits)}")
        lines.append(f"🗓️ Fechamento: dia **{card['closing_day']}**")
        lines.append(f"📆 Vencimento: dia **{card['due_day']}**")
        lines.extend(limit_lines)
        lines.append(f"🔔 Lembrete: {reminder_txt}")
        lines.append("")

    return "\n".join(lines).strip()


def _instructional_credit_help(text: str) -> str | None:
    norm = normalize_text(text)
    if not any(expr in norm for expr in ("como faco", "como faço", "me ensina", "me explique", "como registrar", "como usar")):
        return None

    if _is_card_create_request(text):
        return (
            "💳 Para cadastrar um cartão, você pode usar:\n"
            "• `criar cartao Nubank fecha 10 vence 17`\n"
            "• `criar cartao Nubank`\n\n"
            "Se mandar só `criar cartao`, eu abro o fluxo guiado e pergunto nome, fechamento e vencimento."
        )

    if any(expr in norm for expr in ("parcelamento", "parcelar", "parcela")):
        # detecta intenção de VER parcelas vs CRIAR parcelas
        view_intent = any(
            expr in norm for expr in ("ver", "visualizar", "listar", "consultar", "checar", "mostra", "mostre")
        )
        if view_intent:
            return (
                "📦 Para ver seus parcelamentos ativos, mande:\n"
                "• `parcelamentos`\n\n"
                "Para ver as parcelas numa fatura específica:\n"
                "• `fatura Nubank`"
            )
        return (
            "💳 Para parcelar uma compra, use:\n"
            "• `parcelar 600 em 3x no cartao Nubank`\n"
            "• `parcelei 300 em 6x no cartao Nubank`\n\n"
            "Para ver os parcelamentos ativos, mande `parcelamentos`."
        )

    if any(expr in norm for expr in ("apagar", "remover", "excluir", "desfazer")):
        return (
            "🗑️ Para apagar algo no crédito:\n"
            "• `apagar CC17` → apaga uma compra no cartão\n"
            "• `apagar PCAB12CD34` → apaga um parcelamento\n\n"
            "Lançamentos comuns continuam sendo apagados com `apagar 17`."
        )

    if any(expr in norm for expr in ("compra", "compras", "cartao de credito", "cartao", "credito")):
        return (
            "💳 Para registrar compras no cartão de crédito, você pode usar:\n"
            "• `credito 150 mercado`\n"
            "• `credito Nubank 150 mercado`\n"
            "• `gastei 150 no cartao Nubank`\n\n"
            "Depois do registro, eu te mostro um código como **CC17** para facilitar apagar depois com `apagar CC17`."
        )

    if "fatura" in norm:
        return (
            "🧾 Para consultar ou pagar fatura:\n"
            "• `fatura Nubank`\n"
            "• `pagar fatura Nubank 1200`\n"
            "• `pagar fatura Nubank com saldo`"
        )

    return None


def _ask_credit_limit_or_finish(user_id: int, payload: dict) -> str:
    """Pergunta sobre limite de crédito antes de finalizar o setup, se ainda não perguntou."""
    if payload.get("credit_limit_asked"):
        return _finish_card_setup(user_id, int(payload["card_id"]), ask_primary=bool(payload.get("ask_primary")))
    payload["credit_limit_asked"] = True
    payload["step"] = "credit_limit_ask"
    set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
    return "Deseja definir um limite de crédito para este cartão? Ex: **5000** ou **não**."


def _finish_card_setup(user_id: int, card_id: int, ask_primary: bool) -> str:
    # Os dois `clear_pending_action` abaixo são INCONDICIONAIS de propósito, e
    # são os únicos do arquivo. Não há pendência lida aqui, e um dos caminhos
    # que chega até esta função (`_ask_credit_limit_or_finish`) reescreve a
    # linha antes de chamar — um compare-and-swap contra o que o chamador leu
    # falharia sempre e prenderia o cadastro de cartão por 20 min.
    card = get_card_by_id(user_id, card_id)
    if not card:
        clear_pending_action(user_id)
        return "❌ Não consegui localizar o cartão recém-criado."

    if ask_primary:
        set_pending_action(
            user_id,
            "credit_card_setup",
            {"step": "set_primary", "card_id": card_id},
            minutes=20,
        )
        return (
            f"✅ Cartão **{card['name']}** registrado com sucesso!\n"
            f"Confira os detalhes:\n{_card_summary(card)}\n\n"
            f"Deseja tornar o **{card['name']}** seu cartão principal? Responda **sim** ou **não**."
        )

    clear_pending_action(user_id)
    return (
        f"✅ Cartão **{card['name']}** registrado com sucesso!\n"
        f"Confira os detalhes:\n{_card_summary(card)}"
    )


def start_card_create_flow(user_id: int, text: str = "") -> str:
    existing_cards = list_cards(user_id)
    inferred_name = _parse_card_name_from_create(text)
    inferred_closing, inferred_due = _parse_inline_days(text) if text else (None, None)

    # Detecta duplicata imediatamente ao inferir o nome
    if inferred_name and card_name_exists(user_id, inferred_name):
        payload = {
            "card_name": inferred_name,
            "existing_count": len(existing_cards),
            "ask_primary": len(existing_cards) > 0,
            "closing_day": inferred_closing,
            "due_day": inferred_due,
        }
        return _prompt_duplicate_card(user_id, inferred_name, payload)

    # Comando completo (nome + fechamento + vencimento) → cria direto sem perguntar
    if inferred_name and inferred_closing and inferred_due:
        try:
            card_id = create_card(
                user_id=user_id,
                name=inferred_name,
                closing_day=inferred_closing,
                due_day=inferred_due,
            )
        except PlanLimitExceeded as exc:
            return exc.message
        first_card = len(existing_cards) == 0
        if first_card:
            set_default_card(user_id, card_id)
        card = get_card_by_id(user_id, card_id)
        payload = {
            "step": "reminder_opt_in",
            "card_name": inferred_name,
            "card_id": card_id,
            "closing_day": inferred_closing,
            "due_day": inferred_due,
            "existing_count": len(existing_cards),
        }
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
        return (
            f"✅ Cartão **{inferred_name}** criado!\n"
            f"{_card_summary(card)}\n\n"
            "Gostaria de receber notificações antes do vencimento da fatura? Responda **sim** ou **não**."
        )

    payload = {
        "step": "name" if not inferred_name else "closing_day",
        "card_name": inferred_name,
        "closing_day": inferred_closing,
        "due_day": inferred_due,
        "existing_count": len(existing_cards),
    }
    set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
    if inferred_name and inferred_closing:
        return f"Perfeito. E qual é o dia de vencimento do cartão **{inferred_name}**?"
    if inferred_name:
        return f"Perfeito. Quando fecha a fatura do cartão **{inferred_name}**?"
    return "Qual cartão deseja registrar?"


def _ask_set_primary_flow(user_id: int, card_name: str | None = None) -> str:
    cards = list_cards(user_id)
    if not cards:
        return "📭 Você ainda não tem cartões cadastrados."

    if card_name:
        card_id = get_card_id_by_name(user_id, card_name)
        if not card_id:
            return f"❌ Não achei o cartão '{card_name}'."
        set_pending_action(
            user_id,
            "credit_card_set_primary",
            {"card_id": card_id},
            minutes=20,
        )
        return f"Deseja tornar o cartão **{card_name}** o seu principal? Responda **sim** ou **não**."

    lines = ["Qual cartão você quer definir como principal?"]
    for c in cards:
        badge = " (atual)" if c.get("is_default") else ""
        lines.append(f"• {c['name']}{badge}")
    set_pending_action(user_id, "credit_card_set_primary", {"step": "choose"}, minutes=20)
    return "\n".join(lines)


def _resolve_set_primary(user_id: int, text: str, pending: dict) -> str | None:
    payload = dict(pending.get("payload") or {})
    answer = (text or "").strip()

    if payload.get("step") == "choose":
        if _is_no(answer):
            consume_pending_action(user_id, pending)
            return "Perfeito. Mantive o cartão principal atual."
        card_id = (get_card_id_by_name(user_id, answer.strip())
                   or _card_name_da_resposta(user_id, answer))
        if not card_id:
            # RE-PERGUNTA (regra acima): o turno seguinte deste step só é lido
            # como NOME de cartão — um `sim` velho vira
            # `get_card_id_by_name("sim")` → None e cai aqui de novo. Nada a
            # armar, então abandonar só custaria o fluxo do usuário.
            return "Não encontrei esse cartão. Me diga o nome exatamente como aparece na lista."
        set_pending_action(user_id, "credit_card_set_primary", {"card_id": card_id}, minutes=20)
        card = get_card_by_id(user_id, card_id)
        return f"Deseja tornar o cartão **{card['name']}** o seu principal? Responda **sim** ou **não**."

    card_id = payload.get("card_id")
    if not card_id:
        consume_pending_action(user_id, pending)
        return None
    card = get_card_by_id(user_id, int(card_id))
    if not card:
        consume_pending_action(user_id, pending)
        return "❌ Não achei esse cartão."

    if _is_yes(answer):
        # Consome ANTES de escrever: depois, o CAS não protegeria nada.
        if not consume_pending_action(user_id, pending):
            return None
        # Devolve a pergunta se a escrita estourar — senão a confirmação some.
        with restore_pending_on_error(user_id, pending, 20):
            set_default_card(user_id, int(card_id))
            card = get_card_by_id(user_id, int(card_id))
        return f"✅ O cartão **{card['name']}** agora é o seu principal.\n{_card_summary(card)}"

    if _is_no(answer):
        consume_pending_action(user_id, pending)
        return "Perfeito. Mantive o cartão principal atual."

    # `None`: sim/não é mundo fechado — os dois conjuntos são literais em
    # `_is_yes`/`_is_no` logo acima; conte com
    #   grep -A1 'def _is_yes\|def _is_no' core/handlers/credit.py
    # Qualquer coisa fora deles não é resposta, e esta pendência sequestrava a
    # conversa.
    return None


def _resolve_delete_card(user_id: int, text: str, pending: dict) -> str | None:
    payload = dict(pending.get("payload") or {})
    card_id = payload.get("card_id")
    if not card_id:
        consume_pending_action(user_id, pending)
        return None

    card = get_card_by_id(user_id, int(card_id))
    card_name = payload.get("card_name") or (card["name"] if card else "esse cartão")

    if _is_yes(text):
        # Porteiro, ANTES do delete: `delete_card` leva faturas e transações
        # junto. Quem perde o CAS não apaga — o "sim" era de outra pergunta.
        if not consume_pending_action(user_id, pending):
            return None
        # Devolve a confirmação se o delete em cascata estourar (FK, timeout):
        # sem isso o usuário perde a pergunta e refaz "excluir cartão X".
        with restore_pending_on_error(user_id, pending, 20):
            deleted = delete_card(user_id, int(card_id))
        if not deleted:
            return f"❌ Não consegui excluir o cartão **{card_name}**."
        return f"✅ Cartão **{card_name}** excluído com sucesso."

    if _is_no(text):
        consume_pending_action(user_id, pending)
        return f"Perfeito. Mantive o cartão **{card_name}**."

    # `None`: idem. É o footgun de 3 turnos — "excluir cartao nubank" → "oi" →
    # "sim" apagava o cartão, e `oi` é out_of_scope/0.00 (nenhuma allowlist de
    # intent o pegaria).
    return None


def resolve_pending(user_id: int, text: str, pending: dict | None = None) -> str | None:
    pending = pending or get_pending_action(user_id)
    if not pending:
        return None

    if pending.get("action_type") == "credit_card_set_primary":
        return _resolve_set_primary(user_id, text, pending)

    if pending.get("action_type") == "credit_delete_card":
        return _resolve_delete_card(user_id, text, pending)

    if pending.get("action_type") == "pay_bill_choice":
        return _resolve_pay_bill_choice(user_id, text, pending)

    if pending.get("action_type") == "installment_pending":
        answer = (text or "").strip()
        if not answer or normalize_text(answer) in ("nao", "cancelar", "cancela"):
            consume_pending_action(user_id, pending)
            return "❌ Parcelamento cancelado."
        payload = dict(pending.get("payload") or {})
        nota = answer
        inferred = _infer_category_result(user_id, nota)
        categoria = inferred.category or payload.get("categoria") or "outros"
        # Porteiro: `_create_installments` cria N lançamentos de dinheiro.
        if not consume_pending_action(user_id, pending):
            return None
        from datetime import date as _date
        purchased_at = _date.fromisoformat(payload["purchased_at"]) if isinstance(payload.get("purchased_at"), str) else payload.get("purchased_at")
        return _create_installments(
            user_id,
            int(payload["card_id"]),
            payload["card_name"],
            float(payload["valor"]),
            int(payload["n"]),
            nota,
            categoria,
            purchased_at,
            inferred.reason,
        )

    if pending.get("action_type") != "credit_card_setup":
        return None

    payload = dict(pending.get("payload") or {})
    step = payload.get("step")
    answer = (text or "").strip()

    if _is_no(answer) and step not in {"reminder_opt_in", "credit_limit_ask", "set_primary", "duplicate_card_name", "confirm_delete_existing_card"}:
        consume_pending_action(user_id, pending)
        return "❌ Cadastro de cartão cancelado."

    # ── Novo step: nome duplicado detectado ──────────────────────────────────
    if step == "duplicate_card_name":
        if _is_no(answer):
            consume_pending_action(user_id, pending)
            return "❌ Cadastro de cartão cancelado."

        if _is_delete(answer):
            # Pede confirmação antes de excluir
            existing_name = payload.get("existing_card_name", "")
            payload["step"] = "confirm_delete_existing_card"
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            return (
                f"⚠️ Tem certeza que deseja **excluir** o cartão **{existing_name}**?\n"
                "Isso irá remover todas as faturas e transações associadas.\n\n"
                "Responda **sim** para confirmar ou **não** para cancelar."
            )

        # Usuário digitou um novo nome
        new_name = answer.strip()
        if not new_name:
            # Vazio = desistiu, igual aos dois irmãos deste arquivo (o
            # `_is_no` deste mesmo step, logo acima, e o do
            # `_resolve_pay_bill_choice`). Antes eram TRÊS comportamentos para o
            # mesmo caso no mesmo arquivo: cancelar, cancelar e abandonar.
            # Inalcançável hoje — `core/handle_incoming.py` devolve [] com texto
            # vazio antes de chegar aqui —, e é por isso que alinhar sai mais
            # barato que manter a terceira leitura.
            consume_pending_action(user_id, pending)
            return "❌ Cadastro de cartão cancelado."

        # Verifica se o novo nome também é duplicado
        if card_name_exists(user_id, new_name):
            payload["existing_card_name"] = new_name
            payload["existing_card_id"] = get_card_id_by_name(user_id, new_name)
            existing_card = get_card_by_id(user_id, payload["existing_card_id"])
            existing_info = ""
            if existing_card:
                existing_info = f"\n📋 fecha dia {existing_card['closing_day']} / vence dia {existing_card['due_day']}"
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            return (
                f"⚠️ Já existe um cartão chamado **{new_name}**.{existing_info}\n\n"
                "Digite outro nome ou **excluir** para remover o existente."
            )

        payload["card_name"] = new_name

        # Se closing_day e due_day já foram coletados (via comando inline), cria direto
        if payload.get("closing_day") and payload.get("due_day"):
            try:
                card_id = create_card(
                    user_id=user_id,
                    name=new_name,
                    closing_day=int(payload["closing_day"]),
                    due_day=int(payload["due_day"]),
                )
            except PlanLimitExceeded as exc:
                consume_pending_action(user_id, pending)
                return exc.message
            first_card = int(payload.get("existing_count") or 0) == 0
            if first_card:
                set_default_card(user_id, card_id)
            payload["card_id"] = card_id
            payload["step"] = "reminder_opt_in"
            payload["ask_primary"] = not first_card
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            card = get_card_by_id(user_id, card_id)
            first_card_txt = "\nComo este é seu primeiro cartão, ele já foi definido como principal." if first_card else ""
            return (
                f"✅ Cartão **{card['name']}** registrado com sucesso! Confira os detalhes:\n"
                f"{_card_summary(card)}"
                f"{first_card_txt}\n\n"
                "Gostaria de receber notificações antes do vencimento da fatura? Responda **sim** ou **não**."
            )

        # Sem dias coletados ainda → continua o fluxo normal
        payload["step"] = "closing_day"
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
        return f"Quando fecha a fatura do cartão **{new_name}**?"

    # ── Novo step: confirmação de exclusão do cartão existente ───────────────
    if step == "confirm_delete_existing_card":
        existing_id = payload.get("existing_card_id")
        existing_name = payload.get("existing_card_name", "")

        if _is_yes(answer) and existing_id:
            # Porteiro, ANTES do delete: apagar um cartão leva faturas e
            # transações junto, e o clear posterior não protegeria nada.
            if not consume_pending_action(user_id, pending):
                return None
            # O que se perde aqui não é só a confirmação: o payload carrega o
            # cadastro inteiro (card_name, closing_day, due_day, ask_primary),
            # e a mensagem de erro abaixo ainda diz "Tente novamente".
            with restore_pending_on_error(user_id, pending, 20):
                deleted = delete_card(user_id, int(existing_id))
            if not deleted:
                return f"❌ Não consegui excluir o cartão **{existing_name}**. Tente novamente."

            # Após excluir, pergunta se quer criar um cartão com o mesmo nome agora
            return (
                f"✅ Cartão **{existing_name}** excluído com sucesso.\n\n"
                f"Se quiser criar um novo cartão com esse nome, use:\n"
                f"**criar cartao {existing_name} fecha X vence Y**"
            )

        if _is_no(answer):
            # Volta para o step de nome duplicado
            payload["step"] = "duplicate_card_name"
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            return (
                f"Tudo bem. O cartão **{existing_name}** foi mantido.\n\n"
                "Digite um **novo nome** para o cartão ou **cancelar** para desistir."
            )

        # `None`: sim/não é mundo fechado. Mesmo footgun destrutivo do
        # `_resolve_delete_card` — este step também apaga cartão em cascata.
        return None

    # ─────────────────────────────────────────────────────────────────────────

    if step == "name":
        name = answer
        if normalize_text(name).startswith("criar cartao") or normalize_text(name).startswith("criar cartão"):
            name = _parse_card_name_from_create(answer) or ""
        name = name.strip()
        if not name:
            return "Qual é o nome do cartão? Ex: **Nubank**"
        # Re-pergunta em vez de deixar o `create_card` levantar dois passos
        # adiante, quando o usuário já tiver respondido fechamento e
        # vencimento. Os outros pontos de criação criam na hora, então ali o
        # erro do banco já é imediato.
        if len(name) > MAX_CARD_NAME_LEN:
            return f"Esse nome é muito longo (máx. **{MAX_CARD_NAME_LEN}** caracteres). Me diga um mais curto. Ex: **Nubank**"

        # Detecta duplicata antes de pedir os dias
        if card_name_exists(user_id, name):
            payload["card_name"] = name
            return _prompt_duplicate_card(user_id, name, payload)

        payload["card_name"] = name
        payload["step"] = "closing_day"
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
        return f"Quando fecha a fatura do cartão **{name}**?"

    if step == "closing_day":
        # Portão de FORMA antes do parser: `_parse_day` faz `search` e acharia o
        # 10 de "gastei 10 no mercado". RE-PERGUNTA (regra acima): perder o
        # cadastro do cartão no meio é chato e não há gatilho a armar.
        if not _so_numero(answer):
            return "Me diga o dia de fechamento com um número entre **1** e **31**. Ex: **dia 1**."
        closing_day = _parse_day(answer)
        if closing_day is None:
            return "Me diga o dia de fechamento com um número entre **1** e **31**. Ex: **dia 1**."
        payload["closing_day"] = closing_day
        payload["step"] = "due_day"
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
        return f"Quando vence a fatura do cartão **{payload['card_name']}**?"

    if step == "due_day":
        if not _so_numero(answer):
            return "Me diga o dia de vencimento com um número entre **1** e **31**. Ex: **dia 8**."
        due_day = _parse_day(answer)
        if due_day is None:
            return "Me diga o dia de vencimento com um número entre **1** e **31**. Ex: **dia 8**."

        payload["due_day"] = due_day
        card_name = payload["card_name"]

        # Detecta duplicata na última etapa (edge case: nome entrado antes de existir outro cartão igual)
        if card_name_exists(user_id, card_name):
            return _prompt_duplicate_card(user_id, card_name, payload)

        try:
            card_id = create_card(
                user_id=user_id,
                name=card_name,
                closing_day=int(payload["closing_day"]),
                due_day=due_day,
            )
        except PlanLimitExceeded as exc:
            consume_pending_action(user_id, pending)
            return exc.message
        payload["card_id"] = card_id
        first_card = int(payload.get("existing_count") or 0) == 0
        if first_card:
            set_default_card(user_id, card_id)

        payload["step"] = "reminder_opt_in"
        payload["ask_primary"] = not first_card
        set_pending_action(user_id, "credit_card_setup", payload, minutes=20)

        card = get_card_by_id(user_id, card_id)
        first_card_txt = "\nComo este é seu primeiro cartão, ele já foi definido como principal." if first_card else ""
        return (
            f"✅ Cartão **{card['name']}** registrado com sucesso! Confira os detalhes:\n"
            f"{_card_summary(card)}"
            f"{first_card_txt}\n\n"
            "Gostaria de receber notificações antes do vencimento da fatura? Responda **sim** ou **não**."
        )

    if step == "reminder_opt_in":
        card_id = int(payload["card_id"])
        # ABANDONA (regra acima): é pergunta de sim/não, e um `sim` velho aqui
        # liga lembrete sozinho. Sem este portão, QUALQUER texto caía no
        # `enabled=False` lá embaixo — "saldo" desligava o lembrete.
        if not _is_yes(answer) and not _is_no(answer):
            return None
        if _is_yes(answer):
            payload["step"] = "reminder_days"
            set_pending_action(user_id, "credit_card_setup", payload, minutes=20)
            return "Quantos dias antes do vencimento você quer ser avisado? Ex: **1**, **3** ou **5**."

        update_card_reminder_settings(user_id, card_id, enabled=False)
        return _ask_credit_limit_or_finish(user_id, payload)

    if step == "reminder_days":
        card_id = int(payload["card_id"])
        if not _so_numero(answer):
            return "Me diga em quantos dias antes devo avisar. Ex: **3**."
        days_before = _parse_day(answer)
        if days_before is None:
            return "Me diga em quantos dias antes devo avisar. Ex: **3**."
        update_card_reminder_settings(user_id, card_id, enabled=True, days_before=days_before)
        return _ask_credit_limit_or_finish(user_id, payload)

    if step == "credit_limit_ask":
        card_id = int(payload["card_id"])
        # Portão de FORMA antes do `parse_money` (que NÃO é tocado): ele faz
        # `search` e "gastei 50 no mercado" gravava limite de R$ 50,00.
        # RE-PERGUNTA (regra acima).
        if not _is_no(answer) and not _so_numero(answer):
            return "Me diga o valor do limite. Ex: **5000** ou responda **não** para pular."
        if _is_no(answer):
            return _finish_card_setup(user_id, card_id, ask_primary=bool(payload.get("ask_primary")))
        limit_val = parse_money(answer)
        if limit_val is None or float(limit_val) <= 0:
            return "Me diga o valor do limite. Ex: **5000** ou responda **não** para pular."
        set_card_limit(user_id, card_id, float(limit_val))
        # O limite já aparece no _card_summary dentro de _finish_card_setup — não precisa prefixar
        return _finish_card_setup(user_id, card_id, ask_primary=bool(payload.get("ask_primary")))

    if step == "set_primary":
        card_id = int(payload["card_id"])
        # ABANDONA (regra acima): sim/não, e um `sim` velho troca o cartão
        # principal sozinho. Sem o portão, qualquer texto caía no "Mantive o
        # principal atual" lá embaixo e consumia a pergunta.
        if not _is_yes(answer) and not _is_no(answer):
            return None
        card = get_card_by_id(user_id, card_id)
        if not card:
            consume_pending_action(user_id, pending)
            return "❌ Não achei esse cartão para definir como principal."
        if _is_yes(answer):
            if not consume_pending_action(user_id, pending):
                return None
            with restore_pending_on_error(user_id, pending, 20):
                set_default_card(user_id, card_id)
                card = get_card_by_id(user_id, card_id)
            return f"✅ Perfeito. O cartão **{card['name']}** agora é o seu principal.\n{_card_summary(card)}"
        consume_pending_action(user_id, pending)
        return f"Perfeito. Mantive o cartão principal atual.\n{_card_summary(card)}"

    # Limpeza de estado: step desconhecido. Ignora o resultado do CAS.
    consume_pending_action(user_id, pending)
    return None


def handle(user_id: int, text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None

    instructional_help = _instructional_credit_help(t)
    if instructional_help is not None:
        return instructional_help

    delete_card_name = _extract_card_name_for_delete(t)
    if delete_card_name:
        card_id = get_card_id_by_name(user_id, delete_card_name)
        if not card_id:
            return f"❌ Não achei o cartão '{delete_card_name}'."
        card = get_card_by_id(user_id, card_id)
        set_pending_action(
            user_id,
            "credit_delete_card",
            {"card_id": card_id, "card_name": card["name"] if card else delete_card_name},
            minutes=20,
        )
        return (
            f"⚠️ Tem certeza que deseja excluir o cartão **{card['name']}**?\n"
            "Isso irá remover também as faturas e transações associadas.\n\n"
            "Responda **sim** para confirmar ou **não** para cancelar."
        )

    if _is_credit_delete_command(t):
        group_id = _extract_installment_group_id(user_id, t)
        if group_id:
            try:
                res = undo_installment_group(user_id, group_id)
                if not res:
                    return "❌ Não achei esse grupo de parcelamento."
                return (
                    f"🗑️ Parcelamento desfeito ({_group_code(res['group_id'])}).\n"
                    f"Removido: {fmt_brl(res['removed_total'])} em {res['removed_count']} itens."
                )
            except Exception as e:
                # `undo_installment_group` não levanta exceção prevista: "não
                # achei" volta como None e já foi tratado acima. Então tudo que
                # cai aqui é inesperado — genérica + log, sem `str(e)` (o texto
                # do psycopg pode trazer valor e descrição da linha).
                _log_falha("undo_installment_group", user_id, e, group_id=group_id)
                return (
                    "❌ Não consegui desfazer o parcelamento agora — deu erro do "
                    "meu lado. Tenta de novo em alguns minutos."
                )

        ct_id = _extract_credit_transaction_id(t)
        if ct_id is not None:
            try:
                res = undo_credit_transaction(user_id, ct_id)
                if not res:
                    return f"❌ Não achei a compra de código CC{ct_id}."
                if res["mode"] == "group":
                    return (
                        f"🗑️ Parcelamento desfeito ({_group_code(res['group_id'])}).\n"
                        f"Removido: {fmt_brl(res['removed_total'])} em {res['removed_count']} itens."
                    )
                return (
                    f"🗑️ Compra no crédito CC{ct_id} apagada.\n"
                    f"Removido: {fmt_brl(res['removed_total'])}."
                )
            except Exception as e:
                # Idem: `undo_credit_transaction` devolve None pra "não achei"
                # (tratado acima) e não levanta exceção prevista.
                _log_falha("undo_credit_transaction", user_id, e, credit_tx_id=ct_id)
                return (
                    f"❌ Não consegui apagar a compra CC{ct_id} agora — deu erro "
                    f"do meu lado. Tenta de novo em alguns minutos."
                )

    natural_credit = try_handle_natural_credit_purchase(user_id, t)
    if natural_credit is not None:
        return natural_credit

    t_low = t.lower().strip()
    t_norm = normalize_text(t)

    if any(x in t_norm for x in ("mudar", "trocar", "definir", "colocar")) and any(x in t_norm for x in ("cartao principal", "cartao padrao", "cartao padrão", "principal")):
        return _ask_set_primary_flow(user_id, _find_card_name_in_text(user_id, t))

    if any(x in t_norm for x in ("fecha dia", "vence dia")) and "cartao" in t_norm and not any(x in t_norm for x in ("criar", "registrar", "novo cartao")):
        cards = list_cards(user_id)
        if not cards:
            return "📭 Você ainda não tem cartões cadastrados."
        target_day = _parse_day(t)
        if target_day is not None:
            if "fecha" in t_norm:
                matches = [c for c in cards if int(c["closing_day"]) == target_day]
                if matches:
                    names = ", ".join(f"**{c['name']}**" for c in matches)
                    return f"💳 Cartão(ões) que fecham dia {target_day}: {names}"
                return f"Não encontrei cartão com fechamento no dia {target_day}."
            if "vence" in t_norm:
                matches = [c for c in cards if int(c["due_day"]) == target_day]
                if matches:
                    names = ", ".join(f"**{c['name']}**" for c in matches)
                    return f"💳 Cartão(ões) que vencem dia {target_day}: {names}"
                return f"Não encontrei cartão com vencimento no dia {target_day}."

    if "cartao principal" in t_norm or "cartao padrao" in t_norm or "cartao padrão" in t_norm:
        if any(x in t_norm for x in ("qual", "quais", "meu", "atual")):
            cards = list_cards(user_id)
            if not cards:
                return "📭 Você ainda não tem cartões cadastrados."
            current = next((c for c in cards if c.get("is_default")), None)
            if current:
                return (
                    f"💳 Seu cartão principal é **{current['name']}**.\n"
                    f"Fechamento: dia {current['closing_day']} | Vencimento: dia {current['due_day']}"
                )
            return "Você tem cartões cadastrados, mas ainda não definiu um principal."

    if "fatura" in t_norm or "faturas" in t_norm:
        if any(x in t_norm for x in ("mostrar", "mostra", "ver", "quais", "minhas", "tenho", "quanto", "valor", "em aberto", "atual")):
            card, error = _resolve_card_from_context(user_id, t)
            if error:
                return error
            if card:
                t_low = f"fatura {card['name']}"
            elif "faturas" in t_norm or "minhas" in t_norm:
                t_low = "faturas"
            else:
                current = _get_primary_or_single_card(user_id)
                if current:
                    t_low = f"fatura {current['name']}"
                else:
                    return "Você tem mais de um cartão. Me diga qual deles quer consultar. Ex: **quanto tenho na fatura do Nubank?**"

    if any(x in t_norm for x in ("vence quando", "fecha quando")):
        card, error = _resolve_card_from_context(user_id, t)
        if error:
            return error
        if card:
            if "vence" in t_norm:
                return f"💳 O cartão **{card['name']}** vence no dia **{card['due_day']}**."
            return f"💳 O cartão **{card['name']}** fecha no dia **{card['closing_day']}**."

        candidate = _extract_unknown_card_candidate(t)
        if candidate:
            return (
                f"Não encontrei um cartão chamado **{candidate}**.\n"
                f"Se quiser, posso te ajudar a cadastrar esse cartão agora. É só me mandar:\n"
                f"**criar cartao {candidate}**"
            )

    if ("cartao" in t_norm or "cartoes" in t_norm) and "fatura" not in t_norm:
        if any(x in t_norm for x in ("quais", "meus", "tenho", "registrado", "registrados", "listar", "mostrar", "mostra", "ver")):
            t_low = "listar cartoes"

    if _is_card_create_request(t):
        m = re.search(
            r"(?:criar|cadastrar|registrar|adicionar|incluir)\s+(?:um\s+|novo\s+|meu\s+)?cart[aã]o\s+(.+?)\s+fecha\s+(\d{1,2})\s+vence\s+(\d{1,2})",
            t,
            re.IGNORECASE,
        )
        if not m:
            return start_card_create_flow(user_id, t)

        name = m.group(1).strip()
        fecha = int(m.group(2))
        vence = int(m.group(3))

        # Bloqueia duplicata antes de criar
        if card_name_exists(user_id, name):
            existing_count = len(list_cards(user_id))
            payload = {
                "card_name": name,
                "closing_day": fecha,
                "due_day": vence,
                "existing_count": existing_count,
                "ask_primary": existing_count > 0,
            }
            return _prompt_duplicate_card(user_id, name, payload)

        try:
            existing_count = len(list_cards(user_id))
            try:
                card_id = create_card(user_id=user_id, name=name, closing_day=fecha, due_day=vence)
            except PlanLimitExceeded as exc:
                return exc.message
            if existing_count == 0:
                set_default_card(user_id, card_id)
            set_pending_action(
                user_id,
                "credit_card_setup",
                {
                    "step": "reminder_opt_in",
                    "card_id": card_id,
                    "ask_primary": existing_count > 0,
                },
                minutes=20,
            )
            card = get_card_by_id(user_id, card_id)
            first_card_txt = "\nComo este é seu primeiro cartão, ele já foi definido como principal." if existing_count == 0 else ""
            return (
                f"✅ Cartão **{name}** registrado com sucesso! Confira os detalhes:\n"
                f"{_card_summary(card)}"
                f"{first_card_txt}\n\n"
                "Gostaria de receber notificações antes do vencimento da fatura? Responda **sim** ou **não**."
            )
        except Exception as e:
            return f"❌ Erro criando cartão: {e}"

    if t_low.startswith("padrao ") or t_low.startswith("padrão "):
        name = re.sub(r"^padr[aã]o\s+", "", t, flags=re.IGNORECASE).strip()
        return _ask_set_primary_flow(user_id, name)

    if t_low in ("cartoes", "cartões", "listar cartoes", "listar cartões"):
        cards = list_cards(user_id)
        if not cards:
            return "📭 Você ainda não tem cartões. Crie com: criar cartao nubank fecha 10 vence 17"
        return _format_cards_list(user_id, cards)

    # Aceita "credito" e "Crédito" (com acento). `.lower()` preserva o acento,
    # então tem que checar ambas variações — ambas têm 7 chars.
    if t_low.startswith("credito") or t_low.startswith("crédito"):
        rest = t[7:].strip()
        if not rest:
            return "Use: credito 120 mercado OU credito nubank 120 mercado"

        dt_evento, rest2 = extract_date_from_text(rest)
        if dt_evento is None:
            dt_evento = now_tz()
        purchased_at = dt_evento.date()

        valor = parse_money(rest2)
        if valor is None:
            return "❌ Não achei o valor. Ex: credito 120 mercado"

        tokens = rest2.split()
        card_name = None
        if tokens and parse_money(tokens[0]) is None:
            card_name = tokens[0]
            rest_desc = " ".join(tokens[1:])
        else:
            rest_desc = rest2

        return add_credit_from_entities(
            user_id,
            valor=float(valor),
            card_name=card_name,
            descricao=rest_desc.strip() or None,
            purchased_at=purchased_at,
        )

    if t_low in ("criar parcelas", "criar parcela"):
        return "Use: `parcelar 300 em 3x no cartao nubank` (ex: `parcelar 120 em 4x no cartao nubank`)"

    # "parcelas" / "ver parcelas" / "listar parcelas" → lista ativos
    _PARCELAS_LIST_TRIGGERS = {
        "parcelas", "ver parcelas", "listar parcelas", "meus parcelamentos",
        "ver parcelamentos", "listar parcelamentos", "parcelamentos ativos",
    }
    if t_low in _PARCELAS_LIST_TRIGGERS or t_low.startswith("parcelamentos"):
        return _list_active_installments(user_id)

    if t_low.startswith("parcelei "):
        t_low = "parcelar " + t_low[len("parcelei "):]

    if t_low.startswith("parcelar"):
        # ── número de parcelas ──────────────────────────────────────────────
        # Aceita variações: "em 3x", "em 3 vezes", "em 3", "3x" — usuário fala
        # de várias formas. Tenta "em N" primeiro (mais específico, evita
        # capturar o valor da compra como número de parcelas), depois "Nx"
        # como fallback pra quem digita "300 3x".
        n = 1
        mx = re.search(r"\bem\s+(\d+)\s*(?:x|vezes?|parcelas?)?\b", t_low)
        if mx is None:
            # sem "em": aceita "3x", "3 vezes", "3 parcelas" (a unidade explícita
            # garante que é a contagem, não o valor da compra).
            mx = re.search(r"(\d+)\s*(?:x|vezes?|parcelas?)\b", t_low)
        if mx:
            try:
                n = int(mx.group(1))
            except Exception:
                n = 1
        else:
            # contagem por extenso: "em doze vezes", "doze parcelas". Exige a
            # unidade (vezes/parcelas/x) — número por extenso solto é ambíguo
            # demais pra assumir que é a contagem.
            smx = (
                re.search(rf"\bem\s+({PT_PHRASE})\s+(?:x|vezes?|parcelas?)\b", t_low, re.IGNORECASE)
                or re.search(rf"\b({PT_PHRASE})\s+(?:x|vezes?|parcelas?)\b", t_low, re.IGNORECASE)
            )
            if smx:
                c = parse_pt_number(smx.group(1))
                if c and c >= 1:
                    n = int(c)

        # ── valor: total financiado OU valor da parcela ─────────────────────
        # Dois jeitos naturais de informar o dinheiro:
        #   (a) TOTAL financiado: "parcelar 958,80 em 12x" → divide por N (padrão).
        #   (b) valor da PARCELA: "parcelei 12x de 79,90"  → total = parcela × N.
        # O "de <valor>" logo após a contagem ("Nx de Y") distingue (b): é o
        # número que a maquininha/app mostra ("12x de R$ 79,90"), com o juros já
        # embutido — assim o user não precisa multiplicar na mão. Sem o "de Y",
        # cai no total (a) e divide, como antes.
        valor = None
        # Contagem e valor podem vir em dígito OU por extenso: "12 parcelas de
        # 100", "12x de 79,90", "doze parcelas de cem", "12 parcelas de mil".
        parc_m = re.search(
            rf"\b(?:\d+|{PT_PHRASE})\s*(?:x|vezes?|parcelas?)\s+de\s+({PT_VALUE})",
            t_low, re.IGNORECASE,
        )
        if parc_m and n > 1:
            parcela_valor = parse_pt_number(parc_m.group(1))
            if parcela_valor is not None:
                valor = round(parcela_valor * n, 2)
        if valor is None:
            valor = parse_money(t_low)
        if valor is None:
            return "Use: parcelar 300 em 3x no cartao nubank"

        # ── data ────────────────────────────────────────────────────────────
        dt_evento, rest2 = extract_date_from_text(t)
        if dt_evento is None:
            dt_evento = now_tz()
        purchased_at = dt_evento.date()

        # ── categoria explícita ─────────────────────────────────────────────
        # "categoria sapato" / "cat sapato" / "categoria: sapato"
        categoria_override: str | None = None
        cat_m = re.search(r"\bcat(?:egoria)?[:\s]+(\S+)", t_low)
        if cat_m:
            categoria_override = cat_m.group(1).strip()

        # ── nome do cartão ───────────────────────────────────────────────────
        # Para evitar capturar palavras que não são nomes de cartão,
        # verificamos token a token e paramos em stop-words.
        _CARD_STOP = {"categoria", "cat", "gasto", "gastei", "em", "de", "com",
                      "para", "comprei", "compra", "parcelei", "parcelar"}
        card_name: str | None = None
        mc = re.search(r"(?:no\s+)?cart[aã]o\s+(.+?)(?:\s+em\s+\d+\s*x\b|$)", t_low)
        if mc:
            raw_tokens = mc.group(1).strip().split()
            card_tokens: list[str] = []
            for tok in raw_tokens:
                if tok in _CARD_STOP:
                    break
                card_tokens.append(tok)
            raw_name = " ".join(card_tokens).strip()
            # "padrao"/"padrão"/"principal" → cartão padrão (card_name = None)
            if raw_name and raw_name not in ("padrao", "padrão", "principal", "default"):
                # Valida contra cartões reais do usuário — evita aceitar lixo
                known_id = get_card_id_by_name(user_id, raw_name)
                if known_id:
                    card_name = raw_name
        # Sem "cartao X" no texto (ex.: "no Nubank", sem a palavra "cartão") ou
        # nome não bateu: procura qualquer nome de cartão real do usuário em
        # qualquer lugar do texto. Roda mesmo sem match de `mc` — antes só
        # rodava DENTRO do `if mc:`, então "parcelar 600 em 3x no Nubank"
        # nunca resolvia o cartão nem removia "no Nubank" da descrição.
        if card_name is None:
            found = _find_card_name_in_text(user_id, t)
            if found:
                card_name = found

        # ── descrição (o que sobrar) ─────────────────────────────────────────
        desc_clean = rest2
        # remove o verbo do comando em qualquer forma ("parcelar"/"parcelei"/
        # "parcele"/"parcelamos") — senão "parcelar 958 em 12x tv" deixava
        # "parcelar" na descrição.
        desc_clean = re.sub(r"\bparcel(?:ar|ei|e|amos|ou)\b", "", desc_clean, flags=re.IGNORECASE)
        # remove o bloco "Nx de Y" inteiro (incl. o "de" e o valor da parcela)
        # ANTES da remoção genérica de números — senão o "de" sobra solto na
        # descrição ("parcelei 12x de 79,90 celular" → "de celular").
        desc_clean = re.sub(
            r"\b\d+\s*(?:x|vezes?|parcelas?)\s+de\s+(?:r\$\s*)?\d[\d.,]*",
            "", desc_clean, flags=re.IGNORECASE,
        )
        # idem pro bloco "Nx de Y" por extenso: "doze parcelas de cem",
        # "12 parcelas de mil" — remove contagem+valor de uma vez.
        desc_clean = re.sub(
            rf"\b(?:\d+|{PT_PHRASE})\s*(?:x|vezes?|parcelas?)\s+de\s+{PT_VALUE}",
            "", desc_clean, flags=re.IGNORECASE,
        )
        desc_clean = re.sub(r"\b\d+[\.,]?\d*\b", "", desc_clean)
        desc_clean = re.sub(r"\b\d+\s*x\b", "", desc_clean, flags=re.IGNORECASE)
        # remove multiplicadores/unidades monetárias que sobraram quando o valor
        # foi escrito por extenso ("mil reais em 10 vezes" → não vira descrição).
        desc_clean = re.sub(
            r"\b(mil|milh[oõ]es|milhao|milh[aã]o|reais|real|r\$|vezes?|parcelas?)\b",
            "", desc_clean, flags=re.IGNORECASE,
        )
        # sobras de números por extenso ("dez", "doze", "cem"...) que faziam
        # parte da contagem/valor. "um"/"uma" ficam de fora pra não perder o
        # artigo em descrições comuns.
        desc_clean = re.sub(
            rf"\b(?:{PT_NUM_ALT_NO_ARTICLE})\b", "", desc_clean, flags=re.IGNORECASE,
        )
        desc_clean = re.sub(r"\bem\b", "", desc_clean, flags=re.IGNORECASE)
        # remove trecho do cartão (incluindo palavras de parada como "padrao")
        desc_clean = re.sub(r"(?:no\s+)?cart[aã]o\s+\S+(?:\s+\S+)*", "", desc_clean, flags=re.IGNORECASE)
        # remove o nome do cartão quando veio sem a palavra "cartão" (ex.: "no
        # Nubank") — sem isso, "parcelar 600 em 3x no Nubank" deixava "no
        # Nubank" como se fosse a descrição da compra, em vez de perguntar o
        # nome (ver resolução de card_name acima).
        if card_name:
            desc_clean = re.sub(
                rf"\b(?:no|na|do|da)?\s*{re.escape(card_name)}\b", "", desc_clean, flags=re.IGNORECASE,
            )
        # remove "categoria X" / "cat X"
        desc_clean = re.sub(r"\bcat(?:egoria)?[:\s]+\S+", "", desc_clean, flags=re.IGNORECASE)
        # remove stop-words soltas que sobraram
        desc_clean = re.sub(r"\b(gasto|gastei|compra|comprei)\b", "", desc_clean, flags=re.IGNORECASE)
        desc_clean = " ".join(desc_clean.split())

        inferred = _infer_category_result(user_id, desc_clean or t)
        categoria_base = inferred.category
        categoria = categoria_override or categoria_base
        category_reason = "explicit" if categoria_override else inferred.reason
        nota = desc_clean.strip() if desc_clean.strip() else ""

        card_id, resolved_name = _pick_card_id(user_id, card_name)
        if not card_id:
            if card_name:
                return f"❌ Não achei o cartão '{card_name}'. Crie com: criar cartao {card_name} fecha 10 vence 17"
            return "❓ Qual cartão?\nEx: `parcelei 500 em 5x no cartao nubank`\nDica: defina um padrão com `padrao nubank`."

        limit_error = _validate_credit_limit_before_purchase(user_id, card_id, float(valor))
        if limit_error is not None:
            return limit_error

        # Se não tem descrição, pergunta antes de criar
        if not desc_clean.strip():
            set_pending_action(
                user_id,
                "installment_pending",
                {
                    "valor": float(valor),
                    "n": n,
                    "card_id": card_id,
                    "card_name": resolved_name,
                    "purchased_at": purchased_at.isoformat(),
                    "categoria": categoria,
                },
                minutes=10,
            )
            return (
                f"💳 {fmt_brl(float(valor))} em {n}x no **{resolved_name}**.\n"
                "Qual é o nome dessa compra? Ex: *TV Samsung*, *iPhone*, *Curso de inglês*"
            )

        return _create_installments(
            user_id, card_id, resolved_name, float(valor), n, nota, categoria,
            purchased_at, category_reason,
        )

    if re.match(r"^(?:pagar|paguei)\b", t_low):
        resp = _handle_pay_bill_command(user_id, t)
        if resp is not None:
            return resp

    if t_low in ("faturas", "listar faturas", "faturas abertas", "listar faturas abertas", "listar fatura", "listar faturas em aberto"):
        try:
            rows = list_open_bills(user_id)
            if not rows:
                return "📭 Nenhuma fatura em aberto."

            months = [
                "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
            ]
            groups = defaultdict(list)
            for r in rows:
                total = float(r["total"] or 0)
                paid = float(r["paid_amount"] or 0)
                due = max(0.0, total - paid)
                if total == 0.0 and paid == 0.0 and due == 0.0:
                    continue
                # Agrupa pelo mês do FECHAMENTO (period_end), que é a convenção
                # usada pelos bancos (Nubank, Itaú etc.): "fatura de maio" = fecha em maio.
                pe = r["period_end"]
                groups[(pe.year, pe.month)].append((r, total, paid, due))

            if not groups:
                return "📭 Nenhuma fatura em aberto (as futuras zeradas foram ocultadas)."

            lines = ["🧾 **Faturas em aberto (por mês):**", ""]
            for (y, m) in sorted(groups.keys()):
                lines.append(f"📅 **{months[m-1]}/{y}:**")
                items = sorted(groups[(y, m)], key=lambda it: (it[0]["card_name"] or "").lower())
                for (r, total, paid, due) in items:
                    lines.append(f"• {r['card_name']}: Total {fmt_brl(total)} | Pago {fmt_brl(paid)} | Em aberto {fmt_brl(due)}")
                lines.append("")
            return "\n".join(lines).strip()
        except Exception as e:
            return f"❌ Erro ao listar faturas: {e}"

    if t_low.startswith("fatura ") or t_low == "fatura":
        card = None
        error = None

        if t_low != "fatura":
            requested_name = t_low.split(" ", 1)[1].strip()
            card_id = get_card_id_by_name(user_id, requested_name)
            if card_id:
                card = get_card_by_id(user_id, card_id)
            else:
                card, error = _resolve_card_from_context(user_id, requested_name)
        else:
            card, error = _resolve_card_from_context(user_id, t)
            if card is None and error is None:
                card = _get_primary_or_single_card(user_id)
                if card is None:
                    error = "Você tem mais de um cartão. Me diga qual deles quer consultar. Ex: **fatura nubank**."

        if error:
            return error
        if not card:
            return "❓ Não consegui identificar qual cartão você quer consultar."

        try:
            res = get_open_bill_summary(user_id, int(card["id"]), as_of=today_tz())
            if not res:
                return f"📭 Nenhuma fatura aberta para {card['name']}."

            bill, items = res
            total = float(bill["total"] or 0)
            paid = float(bill.get("paid_amount", 0) or 0)
            due = max(0.0, total - paid)
            lines = [
                f"💳 Fatura atual ({card['name']}) {fmt_br(bill['period_start'])} → {fmt_br(bill['period_end'])}",
                f"Total: {fmt_brl(total)} | Pago: {fmt_brl(paid)} | Em aberto: {fmt_brl(due)}",
            ]
            # mostra uso do limite se definido
            card_with_limit = get_card_by_id(user_id, int(card["id"]))
            if card_with_limit and card_with_limit.get("credit_limit"):
                lim = float(card_with_limit["credit_limit"])
                avail = max(0.0, lim - total)
                pct = round((total / lim) * 100) if lim > 0 else 0
                lines.append(f"Limite: {fmt_brl(lim)} | Disponível: {fmt_brl(avail)} ({100 - pct}%)")
            lines.append("")

            # Busca resumo dos grupos de parcelamento presentes nesta fatura
            # em uma única query (sem N+1)
            group_ids_nesta_fatura = [
                str(it["group_id"])
                for it in items
                if it.get("group_id") and it.get("installments_total", 0) > 1
            ]
            group_summaries = get_installment_group_summaries(user_id, group_ids_nesta_fatura)

            # Separa parcelamentos, compras simples e estornos/descontos
            installment_items = [
                it for it in items
                if it.get("group_id") and it.get("installments_total", 0) > 1
                and not it.get("is_refund")
            ]
            simple_items = [
                it for it in items
                if not (it.get("group_id") and it.get("installments_total", 0) > 1)
                and not it.get("is_refund")
            ]
            refund_items = [it for it in items if it.get("is_refund")]

            # ── Parcelamentos ─────────────────────────────────────────────────
            # Exibe um bloco por group_id (evita repetir se várias parcelas do
            # mesmo grupo caírem na mesma fatura, o que é raro mas possível)
            seen_groups: set[str] = set()
            if installment_items:
                lines.append("📦 **Parcelamentos nesta fatura:**")
                for it in installment_items:
                    gid = str(it["group_id"])
                    if gid in seen_groups:
                        continue
                    seen_groups.add(gid)

                    inst_no = it.get("installment_no") or "?"
                    inst_total = it.get("installments_total") or "?"
                    nota = (it.get("nota") or "").strip()
                    valor_parcela = float(it["valor"])

                    summ = group_summaries.get(gid, {})
                    parcelas_restantes_db = summ.get("parcelas_restantes", 0)
                    valor_restante_db = summ.get("valor_restante", 0.0)

                    # ── Contagem de parcelas restantes ────────────────────────
                    # O memo ("04/10") é sempre confiável para o NÚMERO, mesmo
                    # quando o usuário só importou uma fatura e não tem histórico.
                    # O DB só conta o que já foi importado — pode subestimar.
                    inst_no_int = inst_no if isinstance(inst_no, int) else 0
                    inst_total_int = inst_total if isinstance(inst_total, int) else 0
                    proximas_pelo_memo = max(0, inst_total_int - inst_no_int)

                    # ── Valor restante ────────────────────────────────────────
                    # Usa o DB quando ele tem dados para TODAS as parcelas futuras
                    # (histórico completo). Caso contrário, estima pelo valor desta.
                    parcelas_futuras_db = max(0, parcelas_restantes_db - 1)
                    if parcelas_futuras_db >= proximas_pelo_memo and parcelas_restantes_db > 0:
                        # DB completo — usa os valores reais
                        valor_restante_final = max(0.0, valor_restante_db - valor_parcela)
                        estimado = False
                    else:
                        # Histórico parcial — estima pelo valor desta parcela
                        valor_restante_final = proximas_pelo_memo * valor_parcela
                        estimado = proximas_pelo_memo > 0

                    proximas = proximas_pelo_memo
                    valor_suf = " (estimado)" if estimado else ""

                    restante_txt = ""
                    if proximas > 0:
                        restante_txt = (
                            f"\n     ↳ Ainda faltam **{proximas}** parcela(s) = "
                            f"{fmt_brl(valor_restante_final)}{valor_suf}"
                        )
                    else:
                        restante_txt = "\n     ↳ **Última parcela** desta compra"

                    lines.append(
                        f"  📌 {nota}\n"
                        f"     Parcela **{inst_no}/{inst_total}** — {fmt_brl(valor_parcela)}"
                        f"{restante_txt}"
                    )
                lines.append("")

            # ── Compras simples ───────────────────────────────────────────────
            display_simple = simple_items[:10]
            if display_simple:
                lines.append("🧾 **Outras compras:**")
                for it in display_simple:
                    lines.append(
                        f"  • {fmt_brl(float(it['valor']))} | {it['categoria'] or 'outros'}"
                        f" | {fmt_br(it['purchased_at'])} | {it['nota'] or ''}"
                    )

            # ── Estornos e descontos ──────────────────────────────────────────
            if refund_items:
                lines.append("")
                lines.append("↩️ **Estornos / Descontos:**")
                for it in refund_items[:5]:
                    lines.append(
                        f"  • -{fmt_brl(float(it['valor']))} | {it['categoria'] or 'outros'}"
                        f" | {fmt_br(it['purchased_at'])} | {it['nota'] or ''}"
                    )

            total_hidden = (len(installment_items) - len(seen_groups)) + max(0, len(simple_items) - 10)
            if total_hidden > 0:
                lines.append(f"\n… e mais {total_hidden} lançamento(s).")

            return "\n".join(lines)
        except Exception as e:
            return f"❌ Erro ao buscar fatura: {e}"

    # ── Limite de crédito ─────────────────────────────────────────────────────
    # "definir limite nubank 5000" / "limite do nubank 5000" / "limite 3000"
    _limit_set_match = re.match(
        r"^(?:definir|setar|colocar|mudar|alterar)\s+limite"
        r"(?:\s+(?:do|de|no|da)\s+)?"
        r"(?P<card>[a-zA-ZÀ-ú0-9 ]+?)?\s+"
        r"(?P<val>[\d,.]+)$",
        t_low.strip(),
    )
    if not _limit_set_match:
        # "limite [cartão] [valor]" sem prefixo de ação
        _limit_set_match = re.match(
            r"^limite\s+(?:(?:do|de|no|da)\s+)?(?P<card>[a-zA-ZÀ-ú0-9 ]+?)?\s*(?P<val>[\d,.]+)$",
            t_low.strip(),
        )
    if _limit_set_match:
        raw_val = _limit_set_match.group("val") or ""
        raw_card = (_limit_set_match.group("card") or "").strip()
        amount = parse_money(raw_val)
        if amount is None or float(amount) <= 0:
            return "❌ Valor inválido. Ex: *definir limite nubank 5000*"

        card_name_hint = raw_card if raw_card else _find_card_name_in_text(user_id, t)
        card_id, resolved_name = _pick_card_id(user_id, card_name_hint)
        if not card_id:
            return "❓ Não encontrei o cartão. Verifique o nome com: *cartões*"

        ok = set_card_limit(user_id, card_id, float(amount))
        if not ok:
            return "❌ Não consegui atualizar o limite."
        return f"✅ Limite do **{resolved_name}** definido em {fmt_brl(float(amount))}."

    # "ver limite [cartão]" / "qual limite do nubank"
    _limit_view_match = re.search(r"\blimite\b", t_norm)
    if _limit_view_match and not any(x in t_norm for x in ("definir", "setar", "colocar", "mudar", "alterar")):
        card_name_hint = _find_card_name_in_text(user_id, t)
        card_id, resolved_name = _pick_card_id(user_id, card_name_hint)
        if not card_id:
            cards = list_cards(user_id)
            if not cards:
                return "📭 Você ainda não tem cartões cadastrados."
            lines = ["💳 **Limites dos cartões:**"]
            for c in cards:
                if c.get("credit_limit"):
                    used = float(get_card_credit_usage(user_id, int(c["id"])))
                    lim = f"{fmt_brl(float(c['credit_limit']))} | disponível {fmt_brl(max(0.0, float(c['credit_limit']) - used))}"
                else:
                    lim = "não definido"
                badge = " ⭐" if c.get("is_default") else ""
                lines.append(f"• {c['name']}{badge}: {lim}")
            return "\n".join(lines)

        card = get_card_by_id(user_id, card_id)
        if not card:
            return "❓ Cartão não encontrado."
        lim = card.get("credit_limit")
        if lim is None:
            return f"💳 **{resolved_name}** não tem limite definido.\nDefina com: *definir limite {resolved_name} 5000*"

        # busca uso atual (fatura aberta)
        used = float(get_card_credit_usage(user_id, card_id))
        lim_f = float(lim)
        avail = max(0.0, lim_f - used)
        pct = round((used / lim_f) * 100) if lim_f > 0 else 0
        bar_filled = round(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        return (
            f"💳 **{resolved_name}** — Limite de crédito\n"
            f"Limite total:  {fmt_brl(lim_f)}\n"
            f"Usado:         {fmt_brl(used)} ({pct}%)\n"
            f"Disponível:    {fmt_brl(avail)}\n"
            f"[{bar}]"
        )

    # ── Pagar fatura com saldo da conta ───────────────────────────────────────
    if re.search(r"pagar\s+fatura\s+com\s+saldo|pagar\s+com\s+saldo|usar\s+saldo\s+para\s+pagar", t_norm):
        card_name_hint = _find_card_name_in_text(user_id, t)
        card_id, resolved_name = _pick_card_id(user_id, card_name_hint)
        if not card_id:
            return "❓ Você não tem cartão padrão. Informe o nome do cartão: *pagar fatura nubank com saldo*"

        amount_match = re.search(r"([\d,.]+)", t)
        amount = float(parse_money(amount_match.group(1))) if amount_match and parse_money(amount_match.group(1)) else None

        try:
            bill_id = get_current_open_bill_id(user_id, card_id, today_tz())
            if not bill_id:
                return "📭 Nenhuma fatura aberta para pagar."

            res = pay_bill_amount(user_id, card_id, resolved_name, amount, bill_id=bill_id)
            if isinstance(res, dict) and res.get("error") == "amount_too_high":
                return (
                    f"❌ Valor maior que o em aberto ({fmt_brl(res['due'])}).\n"
                    f"Use: *pagar fatura {resolved_name} com saldo {fmt_brl(res['due'])}*"
                )
            if isinstance(res, dict) and res.get("error") == "invalid_amount":
                return "❌ Valor inválido."
            if not res:
                return "📭 Nada para pagar."
            return (
                f"✅ Pagamento da fatura **{resolved_name}** realizado!\n"
                f"Valor pago: {fmt_brl(res['paid'])}\n"
                f"Saldo da conta: {fmt_brl(res['new_balance'])}"
            )
        except Exception as e:
            return f"❌ Erro ao pagar fatura: {e}"

    if t_low in ("parcelamentos", "listar parcelamentos"):
        return _list_active_installments(user_id)

    return _build_credit_contextual_help(t)


def _list_active_installments(user_id: int) -> str:
    rows = list_installment_groups(user_id, limit=15)
    if not rows:
        return "📭 Você não tem parcelamentos registrados."

    lines = ["📦 **Parcelamentos ativos:**"]
    for r in rows:
        n_total = int(r.get("n_total") or r.get("n_registered") or 0)
        n_pending = int(r.get("n_pending") or 0)
        if n_pending == 0:
            continue
        n_paid = n_total - n_pending
        total = float(r.get("total") or 0)
        pending = float(r.get("total_pending") or 0)
        nota = (r.get("nota") or "").strip()
        desc = f" — {nota}" if nota else ""
        progress = f"{n_paid}/{n_total} pagas"
        group_code = _group_code(r.get("group_id"))
        lines.append(
            f"• {r.get('card_name', '?')}{desc}\n"
            f"  💰 Total: {fmt_brl(total)} | Restante: {fmt_brl(pending)} ({progress})\n"
            f"  🔢 Código: `{group_code}`\n"
            f"  🗑️ Apagar: `apagar {group_code}`"
        )

    if len(lines) == 1:
        return "✅ Você não tem parcelamentos em aberto."
    return "\n".join(lines)
