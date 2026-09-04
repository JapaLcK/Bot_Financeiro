# core/intent_router.py
"""
Recebe um IntentResult + mensagem original e decide o que fazer:
  - confiança alta   → executa direto
  - ação destrutiva  → cria pending e pede confirmação
  - needs_clarif     → salva estado e faz pergunta
  - confiança baixa  → pede reformulação
  - out_of_scope     → resposta padrão
  - confirm.yes/no   → tenta resolver pending
"""
from __future__ import annotations

import logging
import re
import unicodedata

import db
from core.intent_classifier import IntentResult, classify
from core.types import IncomingMessage
from utils_text import (PT_VALUE, _SEM_CONTEUDO, _TRACOS, contains_word,
                        limpa_pontuacao_final, marcador_de_tudo, normalize_text,
                        valor_perigoso)

# handlers
from core.handlers import (
    balance    as h_balance,
    credit     as h_credit,
    launches   as h_launches,
    pockets    as h_pockets,
    investments as h_investments,
    report     as h_report,
    help_handler as h_help,
    categories as h_categories,
    dashboard  as h_dashboard,
    account    as h_account,
    pending    as h_pending,
    greeting   as h_greeting,
    recurring  as h_recurring,
    bills      as h_bills,
)

logger = logging.getLogger(__name__)

# Limiar de confiança para executar sem pedir confirmação
CONFIDENCE_EXECUTE = 0.85

# Intents que exigem confirmação antes de executar (destrutivos)
DESTRUCTIVE_INTENTS = {
    "launches.delete",
    "launches.delete_bulk",
    "pockets.delete",
    "investments.delete",
}

# Intents que modificam dados — confiança moderada (<0.85) pede confirmação.
WRITE_INTENTS = {
    "launches.add", "pockets.create", "pockets.deposit", "pockets.withdraw",
    "investments.deposit", "investments.withdraw",
    "funds.withdraw",
    "categories.create", "categories.delete",
    "recurring.add",
}

# action_types de pending_actions que representam uma confirmação destrutiva
# armada (esperando "sim"/"não"). Diferente de DESTRUCTIVE_INTENTS (nomes de
# intent): aqui são os action_type gravados por propose_delete no DB. Usado
# pelo guard anti-órfão em route().
DESTRUCTIVE_PENDING_TYPES = {
    "delete_launch",
    "delete_launch_bulk",
    "delete_pocket",
    "delete_investment",
}

OUT_OF_SCOPE_MSG = (
    "Só consigo ajudar com finanças pessoais: "
    "saldo, lançamentos, cartões, caixinhas e investimentos.\n"
    "Digite *ajuda* para ver o que posso fazer."
)

NOT_UNDERSTOOD_MSG = (
    "Não entendi bem o que você quis fazer. 🤔\n"
    "Tenta assim:\n"
    "• *gastei 50 no mercado* — registrar um gasto\n"
    "• *saldo* — ver quanto você tem\n"
    "• *recebi 1000 de salário* — registrar uma receita\n\n"
    "Ou digite *ajuda* pra ver tudo que eu faço."
)


def _contextual_help_message(text: str, platform: str) -> str:
    return h_help.infer_contextual_fallback(text, platform)


def _should_redirect_launches_list_to_help(text: str) -> bool:
    norm = normalize_text(text)
    if not any(term in norm for term in ("gasto", "gastos", "despesa", "despesas", "lancamento", "lancamentos", "historico", "extrato")):
        return False

    allowed_patterns = (
        r"^(gastos?|despesas?|lancamentos?|historico|extrato)$",
        r"^(meus|minhas)\s+(gastos?|despesas?|lancamentos?)$",
        r"^(ver|mostrar|mostra|listar)\s+(meus\s+)?(gastos?|despesas?|lancamentos?|extrato)(\s+recentes?)?$",
        r"^(quais|qual)\s+(sao|foram|e|foi)?\s*(meus|os|minhas|as)?\s*(gastos?|despesas?|lancamentos?|ultimos?)$",
        r"^(o\s+que|quanto)\s+(gastei|gastos?|despesas?|lancamentos?)$",
        r"^(gastos?|despesas?)\s+(recentes?|ultimos?|da\s+semana|do\s+mes)$",
        r".*\b(hoje|ontem)\b.*",
    )
    if any(re.fullmatch(pattern, norm) for pattern in allowed_patterns):
        return False

    first = norm.split()[0] if norm.split() else ""
    return first in {"gasto", "gastos", "despesa", "despesas", "lancamento", "lancamentos", "historico", "extrato"}


# ---------------------------------------------------------------------------
# Passo 1 da pergunta de valor. As QUATRO portas são numeradas aqui, e só
# aqui — qualquer comentário sobre "a porta N" no repositório se refere a esta
# lista (o passo 1 em si vale para as portas 2, 3 e 4; ver a nota no fim):
#
#   porta 1  conta variável, por texto  core/handlers/bills.py::resolve_bill_amount
#   porta 2  QUALQUER `clarification`  este arquivo, `_clarification_abandonada`
#            no `route()` — não só a de `launches.add`: a de `recurring.add`, a
#            de `funds.add_ask` e as genéricas da IA passam pela mesma escotilha.
#            O filtro não é o intent da pergunta, é o `_ja_tem_o_valor` — mais
#            o veto de catálogo do #185, que só vale para as perguntas de NOME.
#            É a única das quatro que tem uma SEGUNDA via de abandono, a do
#            #281: intent de ESCRITA cuja mensagem inteira não é o valor
#            pedido ("gastei 50 no mercado" respondendo "Qual o valor?"). Ela
#            é só daqui — as portas 3 e 4 seguem lendo o `ABANDONA` pelo
#            `abandona_pergunta_de_valor`, que não mudou.
#   porta 3  fila do multi-lançamento  core/handlers/launches.py::resolve_multi_launch_value
#   porta 4  botão "✅ Já paguei"      adapters/whatsapp/wa_runtime.py (roda ANTES
#                                      do handle_incoming, por isso importa daqui)
#
# A porta 1 é a única que NÃO consulta o `abandona_pergunta_de_valor`: o portão
# de forma dela (`_VALOR_RE.fullmatch`) já é mais estrito. Ver
# `core/handlers/bills.py::resolve_bill_amount`.
#
# MUNDO FECHADO, de propósito. A versão anterior era o contrário — abandonava a
# pergunta para qualquer intent fora de uma lista de permitidos — e isso apagou
# a fila inteira de um multi-lançamento quando o usuário respondeu "132 no
# cartao" (`credit.handle` 0.95). Numa blacklist sobre um oráculo ilimitado,
# todo intent novo e toda alucinação do LLM nascem destruindo pendência; numa
# whitelist, nascem inertes. O default seguro é NÃO abandonar: a pergunta
# continua de pé, e o dado do usuário nunca some por engano.
#
# Os seis abaixo foram medidos um a um com `classify(..., allow_ai=False)`:
#   balance.check        "saldo"                 1.0
#   launches.list        "extrato", "meus gastos"  1.0
#   launches.delete      "apagar 42"             0.95
#   launches.spend_query "quanto gastei em 132", "quanto gastei em maio"  0.95
#   pockets.list         "caixinhas"             1.0
#   report.monthly       "resumo do mes"         1.0
#
# `credit.handle` ficou de FORA **deste conjunto** por medição, não por
# esquecimento: "fatura" é 1.0 e "132 no cartao" é 0.95 — mesmo intent, e o
# segundo é uma resposta legítima ("foram 132, no cartão"). O único corte por
# confiança que os separa (== 1.0) também derrubaria "fatura do cartao"
# (0.95), então não é corte limpo.
#
# ELE SEGUE FORA DO `ABANDONA` — e desde o #281 está no `ESCRITA` logo abaixo,
# por decisão do dono. O que mudou não foi a medição, foi a peça disponível: o
# `_STARTS_WITH_VALUE_RE` (parsers.py:176) separa limpo o que nenhum corte de
# confiança separava. Medido com `allow_ai=False`:
#   "fatura"        sv=0, e não é só-o-valor  -> EXPLÍCITO: abandona a porta 2
#   "132 no cartao" sv=1                      -> AMBÍGUO: NÃO abandona (PR A)
# A resposta legítima continua protegida, e o comando de cartão deixa de ficar
# preso. A via nova é SÓ da porta 2: as portas 3 e 4 consultam o
# `abandona_pergunta_de_valor`, que lê este conjunto e não mudou.
#
# O PREÇO de deixá-lo fora daqui, MEDIDO nas quatro portas com "fatura",
# "fatura do cartao", "meus cartoes" e "cartao nubank" — não é "o usuário
# repete o comando", e é diferente em cada porta:
#
#   porta 1  custo ZERO: o `_VALOR_RE` não casa, a pergunta é abandonada e o
#            comando de cartão roda normalmente.
#   porta 3  custo ZERO: o `_extract_valor` não acha valor, mesmo efeito.
#   porta 2  ERA um LAÇO INDEFINIDO **medido com a IA desligada**: a pergunta
#            voltava ("Quanto foi no *luz*?") e a pendência era RE-ARMADA a
#            cada turno (medido: `expires_at` cresce em cada resposta), então
#            nunca expirava. FECHADO pelo #281: "fatura" entra pela via
#            EXPLÍCITO do `ESCRITA` e abandona.
#   porta 4  laço, mas com saída: a pergunta volta ("Não peguei o valor") e a
#            pendência NÃO é reescrita (`expires_at` não muda), então o laço
#            morre nos 10 min contados da pergunta original.
#
# Nas portas 3 e 4 o preço continua de pé, e é o que se quis proteger: quem
# responde "132 no cartao" a uma pergunta de valor não perde o lançamento (a
# fila não é apagada).
ABANDONA = {
    "balance.check", "launches.list", "launches.delete",
    "launches.spend_query", "pockets.list", "report.monthly",
}

# Intents de ESCRITA. Mundo fechado como o `ABANDONA` acima, pela mesma razão
# (blacklist sobre oráculo ilimitado faz todo intent novo nascer destrutivo),
# e com o mesmo rigor: cada entrada abaixo veio de uma medição, e a string que
# a produziu está do lado. `classify(..., allow_ai=False)`:
#   launches.add         "gastei 50 no mercado", "paguei 120 de luz"    0.95
#   pockets.deposit      "guardei 100 na caixinha viagem"               0.95
#   pockets.withdraw     "retirei 100 da caixinha viagem"               0.95
#   investments.deposit  "aportei 200 no CDB"                           0.95
#   investments.withdraw "resgatei 200 do CDB"                          0.95
#   funds.withdraw       "saquei 200", "tirei 200 do tesouro direto"    0.95
#   pockets.create       "criar caixinha viagem"                        0.95
#   pockets.delete       "apagar caixinha viagem"                       0.95
#   credit.handle        "fatura" 1.0, "132 no cartao" 0.95  (ver acima)
#
# SÓ a porta 2 lê este conjunto (`_clarification_abandonada`). O
# `abandona_pergunta_de_valor` — compartilhado com as portas 3 e 4 — continua
# lendo apenas o `ABANDONA`, de propósito: mexer nele moveria três portas de
# uma vez.
#
# E ele NUNCA decide sozinho. O portão de admissão é o `_so_o_valor` logo
# abaixo: "100 reais" também classifica `launches.add` 0.95, e é a resposta
# que o bot pediu. Sem esse portão, nenhuma pergunta do bot funcionaria mais.
ESCRITA = {
    "launches.add", "pockets.deposit", "pockets.withdraw",
    "investments.deposit", "investments.withdraw", "funds.withdraw",
    "pockets.create", "pockets.delete", "credit.handle",
}


def abandona_pergunta_de_valor(text: str) -> bool:
    """True quando a resposta a uma pergunta de valor é OUTRO comando.

    `allow_ai=False`: só os tiers 1 e 2, que são determinísticos e não fazem
    rede. Duas razões, as duas medidas:

    - a porta 4 roda ANTES do `handle_incoming`, e "132" (a resposta mais comum
      daquela pergunta) cai no tier 3 — consultar com IA custaria uma chamada
      de LLM por conta paga;
    - com o tier 3 no meio, o oráculo volta a ser ilimitado. Sem ele, "132",
      "cinquenta" e "132 todo mes" caem em `out_of_scope 0.0` e "132 no boleto"
      em `out_of_scope 0.4` (tier 2) — nenhum deles está no `ABANDONA`, então
      nunca abandonam nada, que é exatamente o comportamento da `main`.
    """
    return classify((text or "").strip(), allow_ai=False).intent in ABANDONA


# PORTÃO DE ADMISSÃO da via de escrita da porta 2 (#281). "A resposta tem de
# consumir a mensagem INTEIRA" é uma pergunta sobre a mensagem, não sobre o
# parser: o contrato do `_funde_a_resposta` ("leia número e alvo desta
# resposta") está certo — ele só estava errado quando a mensagem não era uma
# resposta. Por isso o portão mora aqui e o `_funde_a_resposta` não muda.
#
# Tudo aqui é peça já existente e compartilhada — nada foi inventado:
#
#   `_SEM_CONTEUDO`  (utils_text) o prefixo que NÃO carrega conteúdo. É o
#                    `_ENCHIMENTO` ("foi", "uns", "acho que") MAIS os 12
#                    verbos de lançamento MAIS "r$"/"rs". A lista já existe
#                    com exatamente este significado: é ela que decide, no
#                    `_sinal_negativo`, se o que vem antes de um traço é
#                    conteúdo.
#   `PT_VALUE`       (utils_text) dígitos ou número por extenso, encadeado.
#   `_UNIDADE`       (porta 1) "reais", "pila", "conto"…
#   `_NUMERO_AMBIGUO_RE` (porta 1) só dígitos/separadores/espaço, mas
#                    malformado: "132 50", "1.23.456", ",50", "1"×400.
#
# O VERBO É PREFIXO SEM CONTEÚDO, e isto é o oposto de um detalhe. "paguei
# 132" não nomeia alvo nenhum, então não compete com a pergunta pendente — ele
# É a resposta, com um verbo na frente. Tratá-lo como comando novo tem preço
# MEDIDO, porque o filtro de dano (`valor_perigoso`) mora no
# `_resolve_clarification` e o abandono passa por fora dele:
#   "paguei 132 50"           -> R$ 13.250,00 registrados (o bug que o filtro existe para matar)
#   "paguei 132,50. foi isso" -> R$ 13.250,00
#   "paguei -10"              -> R$ 10,00 POSITIVOS
#   "paguei " + "1"*400       -> "erro interno" (Infinity no JSON da pendência)
# Com o verbo como prefixo, os quatro voltam ao `valor_perigoso` — que é o
# comportamento de HOJE e recusa os quatro. `gastei 50 no mercado` segue
# EXPLÍCITO: sobra "no mercado", e sobra é alvo.
#
# O SINAL e o `_NUMERO_AMBIGUO_RE` estão aqui pela mesma razão, não por
# completude: sem eles "paguei -10" e "paguei ,50" escapam do filtro.
#
# A unidade é obrigatória em cada iteração externa: sem isso o `PT_VALUE`
# aninhado num segundo `*` dá backtracking catastrófico (medido: 40 grupos de
# "2 mil e " não terminaram em 120s; com esta forma, 0,09 ms).
#
# "centavos" entra além do `_UNIDADE` da porta 1 porque aqui a mensagem é uma
# frase falada inteira ("30 reais e 50 centavos"), e lá é só o número.
_SEM_CONTEUDO_ALT = "|".join(
    sorted((re.escape(w) for w in _SEM_CONTEUDO), key=len, reverse=True))
_SO_O_VALOR_RE = re.compile(
    rf"(?:(?:{_SEM_CONTEUDO_ALT})\s+)*(?:-\s*)?"
    rf"(?:{PT_VALUE}"
    rf"(?:\s+(?:e\s+)?(?:{h_bills._UNIDADE}|centavos?)(?:\s+(?:e\s+)?{PT_VALUE})?)*"
    rf"|{h_bills._NUMERO_AMBIGUO_RE.pattern})",
    re.I)


def _so_o_valor(text: str) -> bool:
    """A mensagem INTEIRA é o valor que o bot pediu ("100 reais", "2 mil").

    True aqui = a mensagem responde a pergunta, mesmo que o classificador a
    tenha lido como comando de escrita ("100 reais" é `launches.add` 0.95).
    É o controle positivo do #281 virado código: sem ele a via de escrita
    abandonaria a pergunta para toda resposta falada com "reais"/"mil", e
    nenhuma pergunta do bot funcionaria mais.

    NÃO usa `normalize_text`: ele apaga `$` e `,`, e aí "R$ 100" e
    "R$ 1.200,00" deixam de consumir a mensagem — medido. A dobra abaixo é a
    da porta 1 (`lower` + NFKD sem combining + `_TRACOS` +
    `limpa_pontuacao_final`), que preserva os dois.

    TETO medido, e é a razão de "100 reais mesmo" cair em AMBÍGUO: o
    `_ENCHIMENTO_PALAVRAS` só vale ANTES do número. Crescer aquela lista para
    acomodar o enchimento depois do número move o `_sinal_negativo` e o
    `_VALOR_RE` da porta 1 junto (utils_text.py:947), e o preço registrado lá
    é "foi - 10" voltar a pagar R$ 10,00 positivo. Um turno a mais é barato;
    aquilo não é.
    """
    dobrado = unicodedata.normalize(
        "NFKD", (text or "").strip().lower().translate(_TRACOS))
    dobrado = "".join(c for c in dobrado if not unicodedata.combining(c))
    return bool(_SO_O_VALOR_RE.fullmatch(limpa_pontuacao_final(dobrado)))


def route(result: IntentResult, msg: IncomingMessage, *,
          ignora_pendencias: bool = False) -> str:
    """
    Ponto de entrada único do roteador.
    Retorna o texto de resposta (ainda não formatado por plataforma).

    `ignora_pendencias=True`: roteia a mensagem como comando novo SEM olhar a
    linha de `pending_actions`. Um chamador só — a porta 4
    (`adapters/whatsapp/wa_runtime.py`), quando o CAS de abandono dela falha:
    aí a linha já é de OUTRA tarefa, com uma pergunta que o usuário acabou de
    ver na tela, e deixar as guardas abaixo rodarem faria o comando velho
    abandoná-la (o `resolve_bill_amount` a apaga; o `resolve_multi_launch_value`
    apaga a fila inteira). Ver o mesmo tratamento na porta 2, logo abaixo.
    """
    user_id  = int(msg.user_id)
    text     = (msg.text or "").strip()
    platform = msg.platform
    external_id = getattr(msg, "external_id", None) or ""

    intent     = result.intent
    confidence = result.confidence
    entities   = result.entities or {}

    inferred_help = h_help.infer_help_from_text(text, platform)
    if inferred_help is not None:
        norm = normalize_text(text)
        if (
            "Não entendi exatamente" in inferred_help
            and any(term in norm for term in ("investimento", "investimentos", "aporte", "resgate", "cdb", "tesouro", "cdi"))
        ):
            return h_investments.list_investments(
                user_id,
                "Não entendi exatamente o pedido de investimentos. Aqui está sua carteira:",
            )
        return inferred_help

    # -----------------------------------------------------------------------
    # 0. Esclarecimento pendente — tem prioridade máxima
    #    Se o bot fez uma pergunta e está esperando resposta, usa esta mensagem
    #    para completar a intent original em vez de classificar do zero.
    # -----------------------------------------------------------------------
    clarif = None if ignora_pendencias else h_pending.get_pending_clarification(user_id)
    if clarif:
        # `"resolve"` | `"abandona"` — ver `_clarification_abandonada`.
        if _clarification_abandonada(clarif, text, user_id) == "abandona":
            # Porta 2. Mesma escotilha de escape do `investment_pick` e do
            # `funding_source_choice` logo abaixo: outro comando claro abandona
            # a pergunta em vez de ser engolido por ela ("Quanto foi no *luz*?"
            # + "saldo" virava "Quanto foi no *luz saldo*?").
            #
            # CONDICIONAL, não `clear_pending_action`: entre o
            # `get_pending_clarification` acima e agora, outra tarefa pode ter
            # posto uma pergunta NOVA na linha (é uma linha por usuário) — que
            # já apareceu na tela. Apagar por cima a deixaria órfã.
            #
            # E o CAS que falha vale para o TURNO INTEIRO, não só para o
            # DELETE: a linha agora é da pergunta nova, e sem esta linha o
            # `get_pending_action` abaixo a recarregava e o comando velho
            # ("saldo") ia parar no `resolve_bill_amount` dela — deixando
            # órfã exatamente a pergunta que o CAS protegeu.
            ignora_pendencias = not db.consume_pending_action(user_id, clarif)
        else:
            return _resolve_clarification(clarif, text, user_id, platform, external_id)

    pending = None if ignora_pendencias else db.get_pending_action(user_id)

    # Resposta à pergunta de valor de uma conta variável. Precisa acontecer
    # antes do roteamento por intent: um número sozinho costuma ser classificado
    # como out_of_scope e não carregaria qual conta o bot acabou de perguntar.
    if pending and pending.get("action_type") == "bill_amount_expected":
        # Sem o passo 1 aqui de propósito: o portão de forma da porta 1
        # (`_VALOR_RE.fullmatch`) já é mais estrito que o
        # `abandona_pergunta_de_valor` — medido, nenhuma das 44.289 strings que
        # ele casa cai no `ABANDONA`. Ver `resolve_bill_amount`.
        resp = h_bills.resolve_bill_amount(user_id, text, pending)
        if resp is not None:
            return resp
        pending = None  # outro comando abandona a pergunta e roteia normalmente

    # Pergunta de valor pendente de um lançamento múltiplo ("... e paguei o
    # aluguel" sem número → "quanto foi o aluguel?"). A resposta com valor
    # registra o item; sem valor, abandona e segue o roteamento normal.
    if pending and pending.get("action_type") == "multi_launch_values":
        resp = h_launches.resolve_multi_launch_value(
            user_id, text, pending, platform,
            outro_comando=abandona_pergunta_de_valor(text))
        if resp is not None:
            return resp
        pending = None  # abandonado → mensagem roteia como comando novo
    if pending and pending.get("action_type") in {"credit_card_setup", "credit_card_set_primary", "credit_delete_card", "installment_pending", "pay_bill_choice"}:
        resp = h_credit.resolve_pending(user_id, text, pending)
        if resp is not None:
            return resp

    # Pergunta "de onde sai o dinheiro?" (funding_source_choice). Nas etapas de
    # escolha qualquer texto poderia virar resposta, então vale a mesma regra do
    # guard anti-órfão logo abaixo: outro comando claro abandona a pergunta e
    # limpa a pendência. confirm.yes/no seguem para o fluxo (podem cancelar).
    # Resposta à pergunta "em qual investimento?" — mesma escotilha de escape do
    # guard anti-órfão: outro comando claro abandona a pergunta.
    if pending and pending.get("action_type") == "investment_pick":
        if (confidence >= 0.55 and intent != "out_of_scope"
                and intent not in ("confirm.yes", "confirm.no")
                and not intent.startswith("investments.")):
            # Abandono: condicional para não apagar uma pendência que outra
            # tarefa acabou de armar. Perder = não havia nada nosso lá.
            db.consume_pending_action(user_id, pending)
            pending = None
        else:
            resp = h_investments.resolve_investment_pick(user_id, text, pending)
            if resp is not None:
                return resp

    if pending and pending.get("action_type") == "funding_source_choice":
        abandonou = (
            confidence >= 0.55
            and intent != "out_of_scope"
            and intent not in ("confirm.yes", "confirm.no")
        )
        if abandonou:
            db.consume_pending_action(user_id, pending)
            pending = None
        else:
            resp = h_investments.resolve_funding_choice(user_id, text, pending)
            if resp is not None:
                return resp

    # -----------------------------------------------------------------------
    # 0c. Guard anti-órfão: uma confirmação destrutiva armada (delete_launch/
    #     pocket/investment esperando "sim"/"não") só vale enquanto o user está
    #     respondendo a confirmação. Se, em vez de confirmar, ele manda OUTRO
    #     comando claro (saldo, listar, novo lançamento, outro apagar...), ele
    #     abandonou a confirmação — limpa o pending pra que um "sim" posterior
    #     não dispare a exclusão antiga. (Footgun: "apagar #285" → "saldo" →
    #     "sim" apagava #285 sem querer.) confirm.yes/no resolvem normalmente;
    #     out_of_scope e baixa confiança seguem sem mexer no pending (podem ser
    #     continuação da conversa que ainda retoma ou cancela a confirmação).
    # -----------------------------------------------------------------------
    if (pending
            and pending.get("action_type") in DESTRUCTIVE_PENDING_TYPES
            and intent not in ("confirm.yes", "confirm.no")
            and intent != "out_of_scope"
            and confidence >= 0.55):
        db.consume_pending_action(user_id, pending)
        pending = None

    # -----------------------------------------------------------------------
    # 1. Confirmações (sim / não) para ações destrutivas
    # -----------------------------------------------------------------------
    if intent == "confirm.yes":
        resp = h_pending.resolve_delete(user_id, confirmed=True)
        return resp if resp is not None else NOT_UNDERSTOOD_MSG

    if intent == "confirm.no":
        resp = h_pending.resolve_delete(user_id, confirmed=False)
        return resp if resp is not None else "Nada a cancelar."

    # -----------------------------------------------------------------------
    # 2. Fora do escopo
    # -----------------------------------------------------------------------
    if intent == "out_of_scope":
        return _contextual_help_message(text, platform)

    # -----------------------------------------------------------------------
    # 3. Confiança muito baixa
    # -----------------------------------------------------------------------
    if confidence < 0.55:
        return _contextual_help_message(text, platform)

    # -----------------------------------------------------------------------
    # 4. Precisa de esclarecimento
    # -----------------------------------------------------------------------
    if result.needs_clarification and result.clarification_question:
        # salva intent + entities parciais para retomar quando o usuário responder
        db.set_pending_action(
            user_id,
            "clarification",
            {
                "intent":    intent,
                "entities":  entities,
                "question":  result.clarification_question,
                "orig_text": text,
            },
        )
        return result.clarification_question

    # -----------------------------------------------------------------------
    # 5-7. Destrutivo → confirma; write com confiança moderada → confirma;
    #      senão executa direto. (Mesma lógica reusada pelo esclarecimento.)
    # -----------------------------------------------------------------------
    return _dispatch_actionable(intent, user_id, text, entities, confidence, platform, external_id)


def _dispatch_actionable(
    intent: str, user_id: int, text: str, entities: dict,
    confidence: float, platform: str, external_id: str,
) -> str:
    """Etapa final do roteamento pra uma intent já classificada e acionável:
    destrutivo → confirma; write com confiança <0.85 → confirma; senão executa."""
    if intent in DESTRUCTIVE_INTENTS:
        return _handle_destructive(intent, user_id, entities, text)

    if intent in WRITE_INTENTS and confidence < CONFIDENCE_EXECUTE:
        label = _intent_label(intent)
        return f"Entendi como *{label}*. Confirma? Responda **sim** ou **não**."

    return _execute(intent, user_id, text, entities, platform, external_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _handle_destructive(intent: str, user_id: int, entities: dict, text: str) -> str:
    if intent == "launches.delete":
        launch_id = entities.get("launch_id")
        if not launch_id:
            return "Qual o ID do lançamento para apagar? Ex: *apagar #42*"
        # O usuário digita user_seq (#1, #2...). Resolve pro id interno.
        user_seq = int(launch_id)
        internal_id = db.resolve_user_seq_to_id(user_id, user_seq)
        if internal_id is None:
            return f"Não encontrei o lançamento **#{user_seq}**. Use *listar lançamentos* pra ver os IDs."
        return h_launches.propose_delete(user_id, int(internal_id))

    if intent == "launches.delete_bulk":
        seqs = entities.get("launch_ids") or []
        if not seqs:
            return "Informe os IDs a apagar. Ex: *apagar id 757, 756*"
        # Mapeia user_seq → id interno; reporta os que não existem.
        resolved: list[int] = []
        display_map: dict[str, int] = {}
        missing: list[int] = []
        for seq in seqs:
            seq_int = int(seq)
            internal = db.resolve_user_seq_to_id(user_id, seq_int)
            if internal is None:
                missing.append(seq_int)
            else:
                resolved.append(int(internal))
                display_map[str(int(internal))] = seq_int
        if not resolved:
            return f"Nenhum desses lançamentos existe: {', '.join(f'#{s}' for s in missing)}"
        ids_fmt = ", ".join(f"**#{display_map[str(i)]}**" for i in resolved)
        db.set_pending_action(
            user_id,
            "delete_launch_bulk",
            {"launch_ids": resolved, "display_ids": display_map},
        )
        warn = ""
        if missing:
            warn = f"\n(ignorando {', '.join(f'#{s}' for s in missing)} — não encontrei)"
        return (
            f"⚠️ Isso vai apagar os lançamentos {ids_fmt} e desfazer seus efeitos no saldo.{warn}\n"
            "Confirma? Responda **sim** ou **não**."
        )

    if intent == "pockets.delete":
        pocket_name = entities.get("pocket_name")
        if not pocket_name:
            return "Qual caixinha quer deletar? Ex: *excluir caixinha viagem*"
        return h_pockets.propose_delete(user_id, pocket_name)

    if intent == "investments.delete":
        investment_name = entities.get("investment_name")
        if not investment_name:
            return "Qual investimento quer deletar? Ex: *excluir investimento CDB Nubank*"
        return h_investments.propose_delete(user_id, investment_name)

    return NOT_UNDERSTOOD_MSG


def _ask_add_destination(user_id: int, text: str) -> str:
    """
    "adicione 10 mil" sem destino → pergunta ONDE (saldo/caixinha/investimento) e
    arma uma clarification. A resposta é combinada com o texto original e
    reclassificada (ver `_resolve_clarification`), roteando pro destino certo.
    """
    from parsers import _extract_valor
    from utils_text import fmt_brl

    valor = _extract_valor(text)
    if not valor or valor <= 0:
        return ("Quanto e onde você quer adicionar? Ex: *adicione 500 no saldo* "
                "ou *adicione 500 na caixinha viagem*")

    pergunta = (
        f"Onde você quer adicionar {fmt_brl(valor)}?\n"
        "Responda: *saldo*, *caixinha NOME* ou *investimento NOME*."
    )
    db.set_pending_action(user_id, "clarification", {
        "intent":    "funds.add_ask",
        "entities":  {"valor": valor},
        "question":  pergunta,
        "orig_text": text,
    })
    return pergunta


def _execute(intent: str, user_id: int, text: str, entities: dict, platform: str, external_id: str) -> str:

    # --- adicionar dinheiro sem destino → pergunta onde ---
    if intent == "funds.add_ask":
        return _ask_add_destination(user_id, text)

    # --- saudações ---
    if intent == "greeting":
        resp = h_greeting.handle_greeting(text, user_id=user_id)
        return resp if resp is not None else "👋 Oi! Como posso te ajudar?"

    # --- saldo ---
    if intent == "balance.check":
        return h_balance.check(user_id)

    # --- lançamentos ---
    if intent == "launches.list":
        if _should_redirect_launches_list_to_help(text):
            return _contextual_help_message(text, platform)
        limit = int(entities.get("limit", 10))
        return h_launches.list_launches(user_id, limit=limit, entities=entities, original_text=text)

    # --- "quanto gastei [na categoria X] [período]" → total gasto ---
    if intent == "launches.spend_query":
        return h_launches.spend_query(user_id, text, entities=entities)

    if intent == "launches.add":
        # "paguei a luz" pode quitar uma CONTA A PAGAR pendente (boleto) em vez
        # de criar um lançamento avulso. Só intercepta se casar uma conta
        # pendente; senão segue o fluxo normal de despesa.
        paid = h_bills.try_pay_from_text(user_id, text)
        if paid is not None:
            return paid
        return h_launches.add(user_id, text, entities, platform=platform)

    if intent == "launches.undo":
        return h_launches.undo(user_id)

    # --- recorrentes (gastos fixos / rendas fixas) ---
    if intent == "recurring.add":
        return h_recurring.add(user_id, text, entities)

    # --- cartões / crédito ---
    if intent == "credit.handle":
        resp = h_credit.handle(user_id, text)
        return resp if resp is not None else _contextual_help_message(text, platform)

    # --- caixinhas ---
    if intent == "pockets.list":
        return h_pockets.list_pockets(user_id)

    if intent == "pockets.create":
        name = entities.get("name") or ""
        return h_pockets.create(user_id, name)

    if intent == "pockets.deposit":
        return h_pockets.deposit(user_id, text, entities)

    if intent == "pockets.withdraw":
        return h_pockets.withdraw(user_id, text, entities)

    if intent == "funds.withdraw":
        return _execute_generic_withdraw(user_id, text, entities)

    # --- investimentos ---
    if intent == "investments.list":
        return h_investments.list_investments(user_id)

    if intent == "investments.create":
        raw_name = entities.get("raw_name") or ""
        return h_investments.create(user_id, raw_name, text)

    if intent == "investments.deposit":
        return h_investments.deposit(user_id, text, entities)

    if intent == "investments.withdraw":
        return h_investments.withdraw(user_id, text, entities)

    # --- categorias ---
    if intent == "categories.list":
        return h_categories.list_categories(user_id)

    if intent == "categories.create":
        return h_categories.create(user_id, text)

    if intent == "categories.delete":
        return h_categories.delete(user_id, text)

    # --- relatório ---
    if intent == "report.daily":
        return h_report.daily(user_id)

    if intent == "report.weekly":
        return h_report.weekly(user_id)

    if intent == "report.monthly":
        return h_report.monthly(user_id)

    if intent == "report.weekly_enable":
        return h_report.enable_weekly(user_id)

    if intent == "report.weekly_disable":
        return h_report.disable_weekly(user_id)

    if intent == "report.monthly_enable":
        return h_report.enable_monthly(user_id)

    if intent == "report.monthly_disable":
        return h_report.disable_monthly(user_id)

    if intent == "report.enable":
        return h_report.enable(user_id)

    if intent == "report.set_hour":
        hour   = int(entities.get("hour",   9))
        minute = int(entities.get("minute", 0))
        return h_report.set_hour(user_id, hour, minute)

    if intent == "report.disable":
        return h_report.disable(user_id)

    # --- emails de engajamento ---
    if intent == "emails.resubscribe":
        import db as _db
        _db.set_engagement_opt_out(user_id, False)
        return "✅ Pronto! Você voltará a receber as dicas e insights do Piggy por email."

    if intent == "emails.unsubscribe":
        import db as _db
        _db.set_engagement_opt_out(user_id, True)
        return "👍 Ok! Você não vai mais receber os emails de dicas do Piggy.\nSeus emails de segurança (código de verificação etc.) continuam normais.\nQuer voltar a receber? É só mandar *reativar emails*."

    # --- dashboard ---
    if intent == "dashboard.open":
        return h_dashboard.open_dashboard(user_id)

    # --- ajuda ---
    if intent == "help":
        parts = text.split(maxsplit=1)
        section_arg = parts[1] if len(parts) > 1 else None
        if section_arg:
            return h_help.help_section(section_arg, platform)
        return h_help.help_general(platform)

    if intent == "help.tutorial":
        return h_help.tutorial(platform)

    # --- CDI ---
    if intent == "cdi.check":
        return h_investments.check_cdi()

    # --- vinculação ---
    if intent == "account.link":
        code = entities.get("code")
        return h_account.link(platform, external_id, code)

    if intent == "account.vincular":
        code = entities.get("code", "")
        return h_account.vincular(platform, external_id, code)

    # fallback final
    return OUT_OF_SCOPE_MSG


def _ja_tem_o_valor(entities: dict | None) -> bool:
    """A `clarification` pendente já traz o valor, então ela pede a DESCRIÇÃO.

    Fonte única dos dois lugares que precisam da distinção (a escotilha de
    abandono e o ramo `launches.add` do `_resolve_clarification`) — se elas
    divergirem, uma pergunta de descrição vira pergunta de valor e o dinheiro já
    digitado some.

    SEMPRE sobre o payload como foi GRAVADO — as entities da pendência que o
    usuário viu na tela —, nunca sobre um dicionário já enriquecido por outra
    fonte. Os dois chamadores obedecem: `_clarification_abandonada` lê
    `clarif["payload"]["entities"]`, e o `_resolve_clarification` decide no topo
    da função (`pedia_o_valor`), antes do merge das entities da IA. Foi por
    consultar o dicionário pós-merge que a mesma pergunta era classificada de
    dois jeitos no mesmo turno, e o ramo da descrição pulava o filtro de dano —
    dinheiro errado gravado sem confirmação. O detalhe está no `pedia_o_valor`.

    São 5 produtores de `clarification`, e 4 deles foram lidos um a um:

      grava `entities["valor"]`  →  pede DESCRIÇÃO/destino, nunca abandona
        `core/handlers/launches.py:981`  "Em que você gastou R$ 50?"
        `_ask_add_destination` (este arquivo)  "Onde você quer adicionar…?"
      NÃO grava  →  pede VALOR, pode abandonar
        `core/handlers/launches.py:1001`  "Quanto foi no *luz*?"
        `core/handlers/recurring.py:97`   "Qual o valor desse recorrente?"

    O 5º é o esclarecimento genérico da IA (`result.needs_clarification`, no
    `route()`): as `entities` vêm do LLM e NÃO são mensuráveis aqui. Ele cai no
    ramo fail-safe do `try` abaixo quando o campo vier torto, e no `> 0` quando
    vier limpo.

    (`launches.py:959` NÃO é produtor de `clarification` — é o
    `multi_launch_values`, a porta 3.)

    Não olha o `orig_text`: uma versão anterior somava
    `_extract_valor(orig_text)` e classificava "gasto fixo aluguel todo dia 10"
    como "já tem o valor" por causa do *dia* 10 — a mesma pergunta se comportava
    de dois jeitos conforme o texto original ter ou não um dígito, e nenhum
    teste prendia a cláusula.
    """
    try:
        return float((entities or {}).get("valor") or 0) > 0
    except (AttributeError, TypeError, ValueError):
        return True


def _o_reroteamento_le_o_mesmo_valor(text: str) -> bool:
    """Guarda de entrega da via EXPLÍCITO: só larga a pergunta se o roteamento
    normal for ler o MESMO número que esta porta lê.

    As quatro portas limpam a pontuação de prosa (`limpa_pontuacao_final`); o
    roteamento normal NÃO — o furo é do `parse_money` e tem issue própria (ver
    `_cola_separador_decimal`). Enquanto ele existir, entregar a mensagem ao
    roteamento normal pode MUDAR o valor. Medido, e é a diferença entre
    R$ 132,50 e R$ 13.250,00:

        "paguei 132,50. foi isso"  porta: 132.5   roteamento normal: 13250.0
        "1.234,56, foi isso"       porta: 1234.56 roteamento normal: None

    Nas outras 14 formas EXPLÍCITO medidas (`gastei 50 no mercado`,
    `paguei 120 de luz`, `guardei 100 na caixinha viagem`, `aportei 200 no
    CDB`, `fatura`…) os dois leem o mesmo, então a guarda não custa nada.

    Estritamente CONSERVADORA: ela só faz a porta abandonar MENOS. O caso que
    ela segura continua exatamente no comportamento de hoje — o resolver, com
    o filtro de dano.
    """
    from utils_text import parse_money

    bruto = (text or "").strip()
    return parse_money(bruto) == parse_money(limpa_pontuacao_final(bruto))


def _clarification_abandonada(clarif: dict, text: str, user_id: int) -> str:
    """Decide o que a mensagem é: `"resolve"` a pergunta, ou a `"abandona"`.

    Porta 2 do passo 1 (`abandona_pergunta_de_valor`). Quem decide é o INTENT,
    não a forma do texto: "gastei 132" e "apagar 42" têm os dois um número.

    Duas vias levam a `"abandona"`, e as duas leem a MESMA classificação:

      ABANDONA   os 6 intents de LEITURA ("saldo", "extrato"), como sempre,
                 mais o veto de catálogo do #185 lá embaixo.
      ESCRITA    comando de escrita EXPLÍCITO — verbo próprio e a mensagem
                 inteira não é o valor pedido ("gastei 50 no mercado"). #281.

    A via `ESCRITA` tem dois portões, nesta ordem:

      1. `_so_o_valor` — "100 reais" classifica `launches.add` 0.95 e É a
         resposta que o bot pediu. Ele resolve, nunca abandona.
      2. `_STARTS_WITH_VALUE_RE` (parsers.py:176) — sobra o ATALHO sem verbo
         ("50 no mercado", "120 de luz", "100 na caixinha viagem"), que é
         genuinamente ambíguo: pode ser o gasto ou pode ser o valor com o
         nome junto. Hoje ele cai em `"resolve"`, que é o comportamento da
         `main`. A pergunta de desempate é o PR B da #281 — quando ela vier,
         é aqui que nasce o terceiro valor de retorno, sem tocar no resto.

    Não é `bool` de propósito: o PR B acrescenta uma via, e um `bool` obrigaria
    o chamador a adivinhar qual dos dois "False" ele recebeu.

    UMA chamada de `classify` por turno. O `abandona_pergunta_de_valor` faria
    a segunda com a mesma entrada e o mesmo `allow_ai=False` — a linha dele
    está inlinada abaixo, e o docstring dele continua sendo a explicação de
    por que a IA fica fora.

    Vale para TODA `clarification`, não só a de `launches.add`: roda no
    `route()`, antes de o `_resolve_clarification` olhar o `intent` do payload,
    então a de `recurring.add`, a de `funds.add_ask` e as genéricas da IA
    passam por aqui também. Os filtros por payload são dois: o `_ja_tem_o_valor`
    e o veto de catálogo lá embaixo (#185).

    Antes disso, uma condição que nada tem a ver com o classificador: **a
    pergunta ainda não pode ter o valor.** Toda `clarification` que pede a
    DESCRIÇÃO já gravou `entities["valor"]`; as que pedem o VALOR não têm nada
    lá. Abandonar uma pergunta de descrição descartaria em silêncio o dinheiro
    que o usuário JÁ digitou ("gastei 50" + "extrato" perderia os R$ 50), então
    ela nunca abandona.

    O que NÃO é comando claro segue para o `_resolve_clarification`, que aceita
    o valor, recusa o valor perigoso ou re-pergunta — a pergunta continua viva
    em todos os três casos.
    """
    from parsers import _STARTS_WITH_VALUE_RE

    payload = clarif.get("payload")
    if not isinstance(payload, dict):
        # Nenhum produtor grava payload não-dict hoje; sem esta linha um dado
        # torto viraria AttributeError → "erro interno" em loop, com a
        # pendência nunca sendo limpa.
        return "resolve"
    if _ja_tem_o_valor(payload.get("entities")):
        return "resolve"

    intent_da_resposta = classify((text or "").strip(), allow_ai=False).intent

    # Via ESCRITA (#281), ANTES do `ABANDONA`: os dois conjuntos são disjuntos
    # (o `test_281_escrita_e_abandona_sao_disjuntos` prende isso), então a
    # ordem não muda resultado — ela está aqui porque o veto de catálogo
    # abaixo é sobre o `ABANDONA`, e misturar os dois blocos confunde.
    if intent_da_resposta in ESCRITA and not _so_o_valor(text):
        if _STARTS_WITH_VALUE_RE.match((text or "").strip()):
            return "resolve"   # AMBÍGUO — desempate é o PR B da #281
        if not _o_reroteamento_le_o_mesmo_valor(text):
            return "resolve"
        return "abandona"      # EXPLÍCITO — verbo próprio, comando novo

    if intent_da_resposta not in ABANDONA:
        return "resolve"

    # VETO DE CATÁLOGO (#185). Última palavra, e só no turno raro em que o
    # classificador já disse "abandona": a pergunta pendente pede um NOME
    # (`falta` é a chave de nome daquela intent) e a resposta é, literalmente,
    # uma caixinha/investimento DESTE usuário. Aí "saldo" não é o comando de
    # saldo — é o nome que a pergunta pediu, e abandonar descartaria o valor
    # que ele já digitou ("saquei 200" → "de qual caixinha?" → "saldo").
    #
    # DEPOIS do classificador, não antes: o determinístico decide o caso comum
    # sozinho, e o catálogo custa I/O (`_alvos_existentes` chama
    # `accrue_all_investments`, que ESCREVE accruals). Nesta ordem ele só roda
    # quando a resposta já é um dos 6 intents do `ABANDONA` e a pergunta viva
    # é de nome.
    #
    # A via `ESCRITA` do #281 foi posta ACIMA deste bloco pelo mesmo motivo, e
    # é o que mantém a frase anterior verdadeira: se ela caísse aqui, o turno
    # raro do veto viraria o turno comum ("gastei 50 no mercado" é
    # `launches.add`), e cada mensagem sequestrada pagaria as 6 chamadas de
    # catálogo — inclusive a que escreve. Medido no #280: 6 chamadas no turno
    # do veto contra 2 no turno que abandona.
    #
    # `_ja_tem_o_valor` fica INTOCADO de propósito. A one-liner da issue
    # (ler `amount` além de `valor` ali) tem regressão medida: `saquei 200` +
    # `saldo` SEM caixinha chamada "saldo" deixa de abandonar, cai no resolver,
    # e `_funde_a_resposta` devolve `target_name="saldo"` — o bot responde
    # "Não encontrei *saldo* nem em caixinhas nem em investimentos" e o comando
    # nunca roda. Hoje o usuário ao menos vê o saldo. E mexer no
    # `_ja_tem_o_valor` moveria o outro chamador (`pedia_o_valor`, :1040), que
    # é o filtro de dano do #140.
    #
    # ── OS TRÊS TETOS, medidos. Esta é a ÚNICA cópia do texto: o comentário de
    # `tests/test_perguntas_guardam_contexto.py` e o corpo do PR #280 apontam
    # para cá em vez de repetir (§0.7 — a primeira redação errada teve de ser
    # corrigida em três lugares).
    #
    # TETO 1, aceito pelo dono, e é o exemplo do título da issue: `saquei 200` +
    # `extrato` SEM caixinha chamada "extrato" continua abandonando, e os
    # R$ 200 se perdem. Fechar isso exigiria rodar o outro comando E re-armar a
    # pergunta no mesmo turno — padrão de UX novo, decisão adiada.
    #
    # TETO 2 (invertido), também aceito, e MAIOR que "executa o saque": quem tem
    # caixinha chamada exatamente `saldo`/`extrato`/`caixinhas` e quer o COMANDO
    # enquanto uma pergunta de nome está de pé executa o comando PENDENTE no
    # tamanho que o `orig_text` mandar — inclusive ESVAZIAMENTO INTEGRAL, quantia
    # ilimitada, porque o handler lê `marcador_de_tudo(orig_text)` quando as
    # entities não trazem `want_all` (`core/handlers/pockets.py:215-216`).
    # Medido, caixinha `saldo` com R$ 300:
    #   "esvaziar caixinha" -> "Qual caixinha?"   (falta=pocket_name, entities={})
    #   "saldo"             -> "Caixinha *saldo* esvaziada: -R$ 300,00"  (300 -> 0)
    #                          sem o veto: mostra a Conta Corrente, caixinha em 300
    # E a mensagem que dispara isso é `classify("saldo")` -> `balance.check` com
    # confiança 1.0. Troca consciente — o lado do dinheiro ganha. Prende este
    # teto o `test_185_teto2_comando_pendente_esvazia_a_caixinha`.
    #
    # TETO 3, declarado depois da revisão do #280: o veto também dispara quando
    # NÃO HÁ VALOR A PERDER, e aí a justificativa acima não se aplica.
    # `tirar da caixinha` grava `entities={}` — o `falta="pocket_name"` vem ANTES
    # da checagem de valor (`core/handlers/pockets.py:230-232`). Medido:
    #   "tirar da caixinha" -> "Qual caixinha?"
    #   "saldo"             -> "Qual o valor?"  (em vez do saldo)
    # Custa UM turno: na volta o `falta` é `amount`, o veto não pega e o comando
    # roda. Prende este teto o `test_185_teto3_veto_dispara_sem_valor_a_perder`.
    falta = payload.get("falta")
    intent = payload.get("intent")
    # `isinstance(intent, str)` pelo mesmo motivo do guard de payload não-dict lá
    # em cima: `_CHAVE_DO_NOME.get([])` é `TypeError: unhashable type`, que vira
    # "erro interno" com a pendência NUNCA consumida (o `consume_pending_action`
    # só acontece depois, `core/handle_incoming.py:785`) — o loop que este
    # guard existe para evitar. Nenhum produtor grava não-str hoje.
    if falta and isinstance(intent, str) and falta == _CHAVE_DO_NOME.get(intent):
        resposta = limpa_pontuacao_final((text or "").strip())
        if _eh_nome_do_catalogo(resposta, _alvos_existentes(user_id, intent)):
            return "resolve"
    return "abandona"


_SO_NUMERO_RE = re.compile(r"[\d.,\s]+")
_ESPACO_NO_SEPARADOR_RE = re.compile(r"(?<=\d)\s*([.,])\s*(?=\d)")


def _cola_separador_decimal(resposta: str) -> str:
    """"132, 50" vira "132,50" antes de o texto seguir para o `launches.add`.

    A porta 2 é a ÚNICA das quatro que re-serializa o valor aceito de volta para
    TEXTO — as portas 1, 3 e 4 registram por entities/`mark_bill_paid` e nunca
    passam pelo `split_financial_transactions`. É por isso que só ela precisa
    disto, e é por isso que o problema pertence a este conserto.

    O `valor_perigoso` já decidiu, uma linha acima, que aquela vírgula é
    separador DECIMAL — `_espaco_ambiguo` tem exceção explícita para a forma
    (utils_text.py). O texto entregue adiante tem de dizer a mesma coisa. Sem
    isto, medido: `split_financial_transactions("gastei 132, 50 a luz")` devolve
    `['gastei 132', 'gastei 50 a luz']` (`,\\s+` seguido de dígito, parsers.py;
    o pedaço sem verbo herda o anterior) e um valor vira DOIS lançamentos. Com
    a colagem, `"gastei 132,50 a luz"` volta a ser uma parte só.

    Só quando a resposta é NUMÉRICA por inteiro. O recorte é o mesmo predicado
    da porta 1 (`_NUMERO_AMBIGUO_RE`, core/handlers/bills.py): sem letras não há
    palavra do usuário para danificar, e o comentário logo abaixo do chamador
    registra que a palavra digitada é o que categoriza. Resposta com letra
    ("mercado, 20") sai intocada, como hoje.

    Inócuo para o dinheiro: `parse_money` já apaga espaço dentro do bloco
    numérico, então o valor lido é idêntico antes e depois — o que muda é só o
    ponto onde o splitter corta.

    Fora do alcance, medido: "1, 2, 3" nunca chega aqui, porque
    `_extract_valor` devolve None e o chamador re-pergunta antes.
    """
    if not _SO_NUMERO_RE.fullmatch(resposta):
        return resposta
    return _ESPACO_NO_SEPARADOR_RE.sub(r"\1", resposta)


# As intents cujos handlers PERGUNTAM por uma entidade que falta e guardam o
# contexto via `h_pending.pergunta_guardando_contexto` (#136). Fonte única: quem
# entra aqui tem de gravar `falta` no payload, e quem grava `falta` tem de estar
# aqui. O `tests/test_perguntas_guardam_contexto.py` compara as duas pontas.
# "da caixinha viagem" e "caixinha viagem" são a MESMA caixinha que "viagem" —
# o usuário repete o substantivo da pergunta. Mesma limpeza do
# `_pocket_name_from_text` (core/handlers/pockets.py), sem exigir a palavra
# "caixinha" no texto, porque aqui a resposta curta ("viagem") é o caso comum.
#
# NÃO tira "reserva": "reserva de emergência" é nome legítimo de caixinha, e o
# exemplo da própria pergunta do saque genérico usa esse nome.
_PREP_RE = re.compile(r"^(?:d[aeo]|n[ao]|para|pra|em)\s+", re.I)
# Mesma jogada do `_pocket_name_from_text` (core/handlers/pockets.py): o nome é
# o que vem DEPOIS do substantivo. Generalizado para não EXIGIR o substantivo,
# porque aqui a resposta curta ("viagem") é o caso comum.
_SUBST_ALVO_RE = re.compile(r"(?:caixinha|investimento)\s+(.+)$", re.I)


def _alvos_existentes(user_id: int, intent: str) -> list[str]:
    """Nomes de caixinha/investimento do usuário, conforme o que a intent move."""
    nomes: list[str] = []
    try:
        if intent in ("pockets.deposit", "pockets.withdraw", "funds.withdraw"):
            nomes += [p.get("name") or "" for p in (db.list_pockets(user_id) or [])]
        if intent in ("investments.deposit", "investments.withdraw", "funds.withdraw"):
            nomes += [i.get("name") or "" for i in (db.accrue_all_investments(user_id) or [])]
    except Exception:
        logger.exception("falha ao listar alvos do usuario %s", user_id)
    return [n for n in nomes if n]


def _eh_nome_do_catalogo(resposta: str, existentes: list[str] | None = None) -> bool:
    """A resposta INTEIRA já é um alvo do usuário? Fonte única do desempate."""
    alvo = normalize_text((resposta or "").strip())
    return bool(alvo) and any(normalize_text(n) == alvo for n in (existentes or []))


def _nome_do_alvo(resposta: str, existentes: list[str] | None = None) -> str:
    """O nome do alvo dentro da resposta do usuário.

    "caixinha" no MEIO da string é ambíguo: em "retirei 100 da caixinha viagem"
    é prefixo sintático e o nome é "viagem"; em "minha caixinha viagem" — nome
    literal de uma caixinha criada pelo dashboard — faz parte do nome. Recortar
    sempre respondia "Caixinha *viagem* não encontrada" (medido); não recortar
    nunca fazia o comando completo funcionar (medido). Apontado pelo Codex no
    #184, as duas pontas.

    Quem desempata é o CATÁLOGO do usuário, que é definitivo: se a resposta
    inteira já é um alvo dele, ela é o nome e não se toca. Só quando não é é que
    o recorte vale.
    """
    t = resposta.strip()
    if _eh_nome_do_catalogo(t, existentes):
        return t
    achou = _SUBST_ALVO_RE.search(t)
    if achou:
        t = achou.group(1)
    recortado = _PREP_RE.sub("", t).strip()
    if recortado and _eh_nome_do_catalogo(recortado, existentes):
        return recortado

    # Nem a resposta inteira nem o recorte batem. Última tentativa, e ainda pelo
    # CATÁLOGO: um nome do usuário aparecendo DENTRO da resposta. É o que salva
    # "tira 100 da viagem" — sem o substantivo "caixinha" o recorte acima não
    # tem onde cortar, e o nome inteiro virava "tira 100 da viagem".
    #
    # Só quando UM nome casa: com dois, escolher seria adivinhar, e o handler
    # dizendo "não encontrada" é melhor que mover dinheiro no alvo errado.
    # Palavra inteira, senão a caixinha "ana" casaria em "banana" — quem faz
    # isso é o `contains_word` do `utils_text`, não uma regex repetida aqui (§0.7).
    alvo_norm = normalize_text(resposta)
    dentro = [n for n in (existentes or []) if contains_word(alvo_norm, normalize_text(n))]
    if len(dentro) > 1:
        # "viagem" e "minha viagem" casam os dois numa MENÇÃO só ("tira 100 da
        # minha viagem"): ANINHADOS, o mais específico ganha. O teste é UMA
        # MENÇÃO, não "um nome cabe no outro": em "tira 100 da viagem e 50 da
        # viagem japao" um cabe no outro do mesmo jeito, e são DUAS menções —
        # escolher ali seria adivinhar, e o handler dizendo "não encontrada" é
        # melhor que mover dinheiro no alvo errado.
        #
        # Quem separa os dois casos é a REMOÇÃO (mesmo idioma do `_pede_tudo`):
        # tira a ocorrência do maior e vê se algum candidato ainda casa no resto.
        # Se casa, sobrou outra menção. Disjuntos ("ana" e "bruno") caem aqui
        # também, e continuam recusados.
        maior = max(dentro, key=lambda n: len(normalize_text(n)))
        resto = re.sub(rf"\b{re.escape(normalize_text(maior))}\b", " ", alvo_norm, count=1)
        if not any(contains_word(resto, normalize_text(n)) for n in dentro):
            dentro = [maior]
    # TETO deixado ABERTO de propósito: com catálogo ["viagem", "minha caixinha
    # viagem"], "tira 100 da minha caixinha viagem" ainda devolve "viagem",
    # porque o `_SUBST_ALVO_RE` recorta e o catálogo confirma ANTES de chegar
    # aqui. Fechar isso mexeria na precedência documentada acima. Está em #260,
    # com repro e plano de teste.
    #
    # SEGUNDO teto, deste desempate: menções SOBREPOSTAS. Com ["casa nova",
    # "nova moto"], "tira da casa nova moto" devolve "casa nova" — a remoção do
    # maior deixa " moto", que não casa "nova moto", e o código aceita. Exige
    # duas caixinhas compartilhando um token e uma frase que as emenda; é raro,
    # mas é dinheiro num alvo adivinhado, que é a classe que este bloco fecha.
    if len(dentro) == 1:
        return dentro[0]
    return recortado or resposta.strip()


def _pede_tudo(resposta: str, existentes: list[str] | None = None) -> bool:
    """O marcador de TUDO conta só se sobreviver à remoção do nome do catálogo.

    Nome de caixinha é string arbitrária: "zerar dívida" é um nome legítimo, e
    "tira 100 da zerar dívida" pedia 100 — não esvaziar.
    """
    if not marcador_de_tudo(resposta):
        return False
    alvo = _nome_do_alvo(resposta, existentes)
    # ESSENCIAL: sem esta guarda, "esvaziar" sozinho se removeria de si mesmo
    # (o `_nome_do_alvo` devolve a própria resposta) e o marcador sumiria.
    if not _eh_nome_do_catalogo(alvo, existentes):
        return True
    resto = re.sub(rf"\b{re.escape(normalize_text(alvo))}\b", " ", normalize_text(resposta))
    return marcador_de_tudo(resto)


_INTENTS_PERGUNTA_DE_HANDLER: frozenset[str] = frozenset({
    "pockets.deposit",
    "pockets.withdraw",
    "investments.deposit",
    "investments.withdraw",
    "funds.withdraw",
})

# Chave do NOME por intent. O valor é sempre `amount`; a quantidade "tudo" é
# `want_all`. Fonte única — antes cada sítio repetia a sua.
_CHAVE_DO_NOME: dict[str, str] = {
    "pockets.deposit":      "pocket_name",
    "pockets.withdraw":     "pocket_name",
    "investments.deposit":  "investment_name",
    "investments.withdraw": "investment_name",
    "funds.withdraw":       "target_name",
}


def _funde_a_resposta(
    intent: str, ents: dict, resposta: str, existentes: list[str],
) -> tuple[dict, str | None]:
    """Lê a resposta INTEIRA e funde nos slots. Pura: sem I/O, sem `db`.

    Este é o ponto ÚNICO de leitura da resposta a uma pergunta de handler. Antes
    eram dois sub-ramos escolhidos por `falta`, e foi isso que produziu QUATRO
    rodadas de revisão no #184: como seletor de LEITURA, cada ramo aprendia um
    pedaço diferente do mundo, e a rodada seguinte achava o pedaço que faltava no
    outro. `falta` agora só decide o que RE-PERGUNTAR (quem faz isso é o
    chamador); o que se LÊ da resposta não depende dele.

    Dois slots, não três:

        target    — o nome do alvo
        quantity  — QUANTIA(x) | TUDO | ausente

    `amount` e `want_all` são duas formas de preencher o MESMO conceito (quanto
    movimentar), então são EXCLUDENTES. Modelá-los como campos independentes com
    união permite `amount=100 AND want_all=True`, e aí quem decide é a
    precedência do handler — que ninguém escolheu. O caso que prova:

        esvaziar caixinha   -> "Qual caixinha?"   (quantity = TUDO)
        tira 100 da viagem  -> a caixinha seria ESVAZIADA, não debitada em 100

    Portões (cada slot vence o guardado só se passar no seu):

      target    o CATÁLOGO confirma. Sem ele, "retirei 100 do salário"
                sobrescreveria a caixinha certa por uma que não existe.
      quantity  o CATÁLOGO guarda o slot INTEIRO, não só o ramo do
                `_extract_valor`: a caixinha "meta 2028" não vira saque de
                R$ 2.028 (Codex P1, #184, rodada 3) e a caixinha "zerar dívida"
                não vira "esvaziar" (`_pede_tudo`). No ramo do número vale
                ainda o `valor_perigoso`.

    Devolve `(ents, recusa)`. `recusa` é o motivo do `valor_perigoso`, e quem
    monta a mensagem e re-arma a pergunta é o chamador, que tem `db` e `payload`.
    """
    from parsers import _extract_valor

    ents = dict(ents)
    resposta = (resposta or "").strip()

    # ── quantity ────────────────────────────────────────────────────────────
    # Ordem: TUDO primeiro, porque "esvaziar" não tem número e o `_extract_valor`
    # devolveria None — o que antes virava "Não entendi o valor" em laço.
    eh_nome = _eh_nome_do_catalogo(resposta, existentes)
    # TETO conhecido: caixinha chamada `tudo`, respondida com `tudo`, é lida
    # como NOME — a mesma precedência já aceita em `meta 2028`.
    if _pede_tudo(resposta, existentes):
        ents["want_all"] = True
        ents.pop("amount", None)          # excludente: TUDO substitui a quantia
    elif not eh_nome:
        valor = _extract_valor(resposta)
        perigo = valor_perigoso(resposta, valor)
        if perigo:
            # NÃO é `return` seco: o nome desta mesma resposta ainda vale. Antes
            # a recusa re-armava o payload ORIGINAL e o alvo novo se perdia.
            ents = _funde_o_nome(intent, ents, resposta, existentes, eh_nome)
            return ents, perigo
        if valor is not None:
            ents["amount"] = valor
            # `False` EXPLÍCITO, não `pop`: a PRESENÇA da chave é o sinal de que
            # o resolver decidiu a quantidade. Removendo-a, o handler caía no
            # fallback de texto e re-derivava "tudo" do `orig_text` — medido:
            # "esvaziar caixinha" + "tira 100 da viagem" ESVAZIAVA a caixinha,
            # que é precisamente o dano que a exclusividade existe para impedir.
            ents["want_all"] = False
    # `eh_nome` sem marcador: a resposta é só o nome. A quantidade guardada fica.

    # ── target ──────────────────────────────────────────────────────────────
    return _funde_o_nome(intent, ents, resposta, existentes, eh_nome), None


def _funde_o_nome(
    intent: str, ents: dict, resposta: str, existentes: list[str], eh_nome: bool,
) -> dict:
    """O alvo mais recente vence o guardado — se o catálogo o confirmar.

    Sem o portão do catálogo, "explícito" viraria sinônimo de "existente" e
    `retirei 100 do salário` sobrescreveria a caixinha certa. Quando não há alvo
    guardado, entra o best-effort do `_nome_do_alvo` mesmo sem confirmação —
    é o que permite ao handler responder "*X* não encontrada" em vez de
    perguntar de novo em laço.
    """
    chave = _CHAVE_DO_NOME.get(intent)
    if not chave:
        return ents
    ents = dict(ents)
    if eh_nome:
        ents[chave] = resposta
    else:
        candidato = _nome_do_alvo(resposta, existentes)
        if _eh_nome_do_catalogo(candidato, existentes) or not ents.get(chave):
            ents[chave] = candidato
    return ents


def _resolve_clarification(clarif: dict, user_response: str, user_id: int, platform: str, external_id: str) -> str:
    """
    O bot tinha feito uma pergunta e está esperando a resposta do usuário.
    Combina a resposta com as entidades originais e re-executa a intent.
    """
    from utils_date import extract_date_from_text

    payload          = clarif.get("payload", {})
    original_intent  = payload.get("intent", "launches.list")
    original_entities = dict(payload.get("entities") or {})
    orig_text        = payload.get("orig_text", "")

    # A pergunta que o usuário VIU pedia o valor ou a descrição? Decidido AQUI,
    # sobre o payload como foi GRAVADO, e não lá embaixo sobre o
    # `original_entities` — que a essa altura já foi reescrito com as entities
    # que a IA devolveu (:780). O prompt manda o classificador extrair `valor`
    # (core/intent_classifier.py:832-834), então numa conta Pro ele quase sempre
    # preenche: `_ja_tem_o_valor` virava True, o fluxo entrava no ramo "a
    # resposta é a DESCRIÇÃO" e pulava o filtro de dano do #140 inteiro. Medido
    # em produção: "paguei a luz" + "-10" gravava R$ 10,00, "132 50" gravava
    # R$ 13.250,00, e "fatura" virava descrição do gasto ("mercado - fatura").
    #
    # Isto RESTAURA a fonte única em vez de quebrá-la: o outro consumidor,
    # `_clarification_abandonada` (:721), já lia `clarif["payload"]["entities"]`
    # cru. Os dois discordavam sobre a mesma pergunta, no mesmo turno.
    pedia_o_valor = not _ja_tem_o_valor(original_entities)

    # Consome ANTES de executar, e condicionado ao que foi lido: a intent
    # original é re-executada abaixo (pode registrar/gastar). Se a linha já não
    # é esta, outra tarefa a substituiu — o texto do usuário responde a OUTRA
    # pergunta, e re-executar a antiga duplicaria a ação.
    if not db.consume_pending_action(user_id, clarif):
        return NOT_UNDERSTOOD_MSG

    # se o usuário negou / cancelou explicitamente
    resp_norm = user_response.strip().lower()
    if resp_norm in ("nao", "não", "n", "cancelar", "cancela"):
        return "❌ Cancelado."

    # "adicione 10 mil" → perguntamos ONDE. A resposta traz o destino (saldo /
    # caixinha X / investimento Y). Combina com o texto original e reclassifica:
    # as regras de alias já roteiam "adicionar ... saldo/caixinha/investimento"
    # pro handler certo, preservando o valor.
    if original_intent == "funds.add_ask":
        from core.intent_classifier import classify
        combined = f"{orig_text} {user_response}".strip()
        res2 = classify(combined, user_id=user_id)
        if res2.intent in ("launches.add", "pockets.deposit", "investments.deposit"):
            return _execute(res2.intent, user_id, combined, res2.entities or {}, platform, external_id)
        # destino não reconhecido → re-arma a pergunta pra não perder o valor
        db.set_pending_action(user_id, "clarification", payload)
        return ("Não entendi o destino. Responda: *saldo*, *caixinha NOME* ou "
                "*investimento NOME* (ou *cancelar*).")

    # ── Perguntas feitas por HANDLER (#136) ──────────────────────────────────
    # Caixinha, investimento e saque genérico. O payload diz QUAL entidade foi
    # pedida (`falta`), gravado por quem perguntou — não inferido aqui: cada
    # handler checa numa ordem diferente e deduzir daria a resposta errada em
    # metade dos casos.
    #
    # ANTES da reclassificação por IA, de propósito. Já sabemos o que
    # perguntamos e o que fazer com a resposta; deixar o LLM redecidir
    # reintroduziria exatamente o sequestro que esta issue fecha — e só para o
    # usuário PRO, que é quem paga. O caminho determinístico é o certo aqui.
    #
    # A escotilha de abandono NÃO é responsabilidade deste bloco: outro comando
    # claro já foi desviado pelo `_clarification_abandonada` lá no `route()`,
    # antes de chegarmos aqui.
    falta = payload.get("falta")
    if falta and original_intent in _INTENTS_PERGUNTA_DE_HANDLER:
        resposta_h = limpa_pontuacao_final(user_response.strip())
        # Catálogo só quando a resposta pode conter um nome. Resposta feita só de
        # dígitos e pontuação não esconde nome nenhum, e pular a consulta evita
        # o `accrue_all_investments` (que ESCREVE accruals) no caso mais comum.
        # Teto aceito: caixinha chamada "2028" respondida a "Qual o valor?" é
        # lida como valor.
        existentes = ([] if _SO_NUMERO_RE.fullmatch(resposta_h)
                      else _alvos_existentes(user_id, original_intent))
        ents, recusa = _funde_a_resposta(
            original_intent, original_entities, resposta_h, existentes)

        # A quantidade ainda falta? Pode ser recusa do filtro de dano, ou uma
        # resposta que só trouxe o nome. Nos dois casos a pergunta VOLTA viva —
        # mesmo contrato das 4 portas do #140 — mas agora com as entities
        # ATUALIZADAS: o alvo que o usuário acabou de dizer não se perde.
        sem_quantidade = not ents.get("want_all") and not ents.get("amount")
        if recusa or (falta == "amount" and sem_quantidade):
            # `create_pending_action_if_absent` e não `set_pending_action`: o
            # `consume_pending_action` no topo desta função já apagou a linha, e
            # entre lá e aqui outra tarefa pode ter posto uma pergunta NOVA —
            # que o usuário já viu. O upsert a atropelaria.
            db.create_pending_action_if_absent(
                user_id, "clarification", {**payload, "entities": ents})
            texto = ("O valor precisa ser maior que zero." if recusa == "nao_positivo"
                     else "Não entendi o valor. Manda só o número, por exemplo: *132,50*")
            return f"{texto}\n\n{payload.get('question') or 'Qual o valor?'}"

        # Que texto vira a NOTA do lançamento (`db/pockets.py:497`, `:659` — o
        # `text` é passado como `nota`; o `alvo` já é o nome canônico resolvido).
        #
        # Regra: o `orig_text`, EXCETO quando o usuário redirecionou o alvo na
        # resposta. Aí ele mentiria — "tirar da caixinha carro" gravado num
        # lançamento que debitou `viagem` —, e extrato, auditoria, suporte e a
        # IA lendo o histórico depois herdam a mentira.
        #
        # A divergência é medida comparando as entities ANTES e DEPOIS, não
        # re-parseando texto: exato e sem custo. E é seguro passar a resposta
        # nesse caso porque o alvo novo veio DELA e já passou pelo portão do
        # catálogo — o parse do handler, se rodar, chega ao mesmo nome. Quando o
        # portão RECUSOU o alvo da resposta não há divergência, então o
        # `orig_text` fica, e o parse não tem como reintroduzir o alvo recusado.
        # `antes` VAZIO conta igual: "esvaziar caixinha" (sem alvo guardado) +
        # "tira 100 da viagem" gravava a nota "esvaziar caixinha" num saque de
        # R$ 100 — mesma mentira, sem alvo velho para citar. Com uma condição a
        # mais: o texto também é a fonte de PARSE do handler, e a quantidade
        # "tudo" mora só no `orig_text` (as entities não a carregam quando a
        # pergunta foi "Qual caixinha?"). Trocar o texto por uma resposta que só
        # traz o NOME — "esvaziar caixinha" + "viagem" — perdia o esvaziar
        # (medido: virava "Qual o valor?"). Com `antes` preenchido a quantidade
        # já veio nas entities guardadas, então a condição só vale para o ramo
        # novo.
        chave = _CHAVE_DO_NOME.get(original_intent)
        antes = (original_entities or {}).get(chave) if chave else None
        depois = ents.get(chave) if chave else None
        qtd_nova = (ents.get("amount") != original_entities.get("amount")
                    or ents.get("want_all") != original_entities.get("want_all"))
        redirecionou = bool(depois
                            and normalize_text(antes or "") != normalize_text(depois)
                            and (antes or qtd_nova))
        texto = resposta_h if redirecionou else orig_text
        return _execute(original_intent, user_id, texto, ents, platform, external_id)

    # Reclassifica COM contexto (mensagem original + pergunta que o bot fez +
    # resposta). Se a IA montar a intenção completa, despacha por ela — resolve
    # pra QUALQUER intent (caixinha, cartão, investimento…), não só launches.add.
    # Se a IA não resolver (out_of_scope / baixa confiança / IA indisponível),
    # cai no fluxo legado abaixo, que segue inalterado.
    question = payload.get("question", "")
    try:
        from core.intent_classifier import classify_with_context
        res = classify_with_context(orig_text, question, user_response, user_id=user_id)
    except Exception:
        res = None
    if (res is not None
            and res.confidence >= 0.55
            and not res.needs_clarification
            and res.intent not in ("out_of_scope", "confirm.yes", "confirm.no")):
        merged_entities = {**original_entities, **(res.entities or {})}
        if res.intent == "launches.add":
            # cai na construção de texto limpo do bloco legado de launches.add,
            # agora com as entities enriquecidas pela IA.
            original_intent = "launches.add"
            original_entities = merged_entities
        elif res.intent == "launches.list":
            # datas ("março", "dia 4") ficam com a extração legada, que resolve o
            # ano relativo à data de HOJE (a IA não sabe a data atual e chuta o ano).
            pass
        else:
            # Recorrente cujo esclarecimento era "do que é?": se a IA não trouxe
            # o nome, a própria resposta do usuário É o nome (ex: "aluguel").
            if res.intent == "recurring.add" and not merged_entities.get("nome"):
                merged_entities["nome"] = user_response.strip()
            combined = f"{orig_text} {user_response}".strip()
            return _dispatch_actionable(
                res.intent, user_id, combined, merged_entities,
                res.confidence, platform, external_id,
            )

    # Fallback quando a IA-com-contexto não resolveu (rate limit / API / baixa
    # confiança): mantém o contexto de recorrente. Resposta numérica = o VALOR
    # que faltava; resposta textual = o NOME. Assim não perde nem vira avulso.
    if original_intent == "recurring.add":
        from parsers import _extract_valor
        ents = dict(original_entities)
        v = _extract_valor(user_response)
        has_valor = bool(ents.get("valor")) and float(ents.get("valor") or 0) > 0
        if v is not None and not has_valor:
            ents["valor"] = v
        elif not ents.get("nome"):
            ents["nome"] = user_response.strip()
        return _dispatch_actionable(
            "recurring.add", user_id, f"{orig_text} {user_response}".strip(),
            ents, 0.9, platform, external_id,
        )

    # launches.add: o bot tinha feito uma pergunta pra completar o lançamento —
    # ou faltava o VALOR ("Qual foi o valor?") ou faltava a DESCRIÇÃO ("Em que
    # você gastou?"). Junta a resposta do usuário com o texto original e
    # reexecuta pelo fluxo normal de add, reaproveitando parser de valor/data,
    # categorização e aprendizado.
    if original_intent == "launches.add":
        from parsers import _extract_valor
        tipo = (original_entities.get("tipo") or "despesa").lower()
        verbo = "recebi" if tipo == "receita" else "gastei"
        resposta = limpa_pontuacao_final(user_response.strip())
        if not pedia_o_valor:
            # já tínhamos o valor → a resposta é a descrição/alvo que faltava.
            # Testado ANTES de parsear a resposta como valor: "gastei 50" +
            # "mercado 20" tem um número na resposta e virava R$ 2.050,00.
            #
            # O " - " não é enfeite. O `parse_money` cola dois grupos de
            # dígitos separados por espaço, então o combinado "gastei 50 0"
            # (resposta "0" a uma pergunta de DESCRIÇÃO) virava R$ 500,00 e
            # "gastei 50 20 no mercado" virava R$ 5.020,00. Medido: com o
            # traço, os dois voltam a R$ 50,00 — o traço não está em
            # `[\d.,\s]` e o recorte para no primeiro número.
            combined = f"{orig_text} - {resposta}".strip()
        else:
            valor = _extract_valor(resposta)
            perigo = valor_perigoso(resposta, valor)
            if perigo:
                # Porta 2. Recusa MANTÉM a pergunta viva: re-arma o mesmo
                # payload (o `orig_text` não cresce) e repete a pergunta
                # guardada. Descartar a pendência jogaria o usuário no fallback
                # genérico e o valor já digitado sumiria.
                #
                # CONDICIONAL, como os quatro CAS das outras portas. O
                # `consume_pending_action` no topo desta função já apagou a
                # linha, então o primitivo certo é o "insere só se não houver" e
                # NÃO o `advance_pending_action` (o CAS não acharia old_payload
                # nenhum e não gravaria nada — a pergunta morreria no caso
                # normal). `set_pending_action` é upsert incondicional: entre o
                # consumo e aqui, outra tarefa pode ter posto uma pergunta NOVA
                # na linha (é uma por usuário) — que já apareceu na tela — e o
                # upsert a atropelava. Medido com a corrida injetada.
                db.create_pending_action_if_absent(
                    user_id, "clarification", payload)
                recusa = ("O valor precisa ser maior que zero."
                          if perigo == "nao_positivo"
                          else "Não entendi o valor. Manda só o número, por exemplo: *132,50*")
                return f"{recusa}\n\n{payload.get('question') or 'Qual foi o valor? Tente: *150*'}"
            if valor is None:
                # ainda sem valor reconhecível — refaz a pergunta e mantém o
                # pending pra não perder a descrição original.
                db.set_pending_action(user_id, "clarification", payload)
                return payload.get("question") or "Qual foi o valor? Tente: *150*"
            # resposta trouxe o VALOR que faltava → descrição vem do texto
            # original (sem o verbo, pra não duplicar "gastei 150 gastei ...").
            #
            # O texto do USUÁRIO, não `fmt_brl(valor)`: medido, "paguei a luz" +
            # "132 no mercado" grava categoria *mercado* com o texto cru e
            # *moradia* com o valor re-renderizado — a palavra que a pessoa
            # digitou é o que categoriza. Só a pontuação final sai
            # (`limpa_pontuacao_final`), senão "132,50." vira R$ 13.250,00.
            desc = re.sub(
                r"^\s*(gastei|gasto|paguei|pagar|comprei|debitei|mandei|enviei|pixei|recebi|receita|ganhei)\b",
                "", orig_text, flags=re.IGNORECASE,
            ).strip()
            combined = f"{verbo} {_cola_separador_decimal(resposta)} {desc}".strip()
        return _execute("launches.add", user_id, combined, original_entities, platform, external_id)

    # demais intents (ex: launches.list "dia 4 de qual mês?") → extrai data
    dt, _ = extract_date_from_text(user_response)
    if not dt:
        # tenta extrair do texto original (ex: "quanto gastei dia 4")
        dt, _ = extract_date_from_text(orig_text)

    if dt:
        original_entities["date_filter"] = dt.date().isoformat()

    # re-executa a intent original com as entidades completas
    return _execute(original_intent, user_id, orig_text or user_response, original_entities, platform, external_id)


def _intent_label(intent: str) -> str:
    labels = {
        "launches.add":         "registrar lançamento",
        "pockets.create":       "criar caixinha",
        "pockets.deposit":      "depositar em caixinha",
        "pockets.withdraw":     "retirar de caixinha",
        "funds.withdraw":       "retirar de caixinha ou investimento",
        "investments.create":   "abrir investimentos",
        "investments.deposit":  "aportar em investimento",
        "investments.withdraw": "resgatar investimento",
        "categories.create":    "criar regra de categoria",
        "categories.delete":    "remover regra de categoria",
        "recurring.add":        "cadastrar recorrente (gasto/receita fixa)",
    }
    return labels.get(intent, intent)


def _execute_generic_withdraw(user_id: int, text: str, entities: dict) -> str:
    amount = entities.get("amount")
    target_name = (entities.get("target_name") or "").strip()
    target_kind = entities.get("target_kind")
    # A quantidade "tudo" tem de sobreviver às QUATRO reconstruções de dict
    # abaixo — elas montam as entities do zero e descartavam o marcador, então
    # "esvaziar" por esta porta perguntava o valor para sempre. Irmão dos dois
    # handlers de saque; fecha junto (§2). A PRESENÇA da chave manda (idioma de
    # core/handlers/pockets.py:215): com `or`, o `want_all=False` que o resolver
    # grava DE PROPÓSITO era religado pelo texto original — "esvaziar caixinha"
    # seguido de "tira 100 da viagem" esvaziava a caixinha.
    want_all = (bool(entities["want_all"]) if "want_all" in (entities or {})
                else marcador_de_tudo(text))
    quantia = {"amount": amount, "want_all": want_all}

    if target_kind == "pocket":
        return h_pockets.withdraw(user_id, text, {"pocket_name": target_name, **quantia})
    if target_kind == "investment":
        return h_investments.withdraw(user_id, text, {"investment_name": target_name, **quantia})

    if not want_all and (not amount or float(amount) <= 0):
        return h_pending.pergunta_guardando_contexto(
            user_id, "funds.withdraw", entities,
            "Qual o valor? Tente: *saquei 200 da reserva de emergência*", text, falta="amount")

    if not target_name:
        return h_pending.pergunta_guardando_contexto(
            user_id, "funds.withdraw", {**(entities or {}), **quantia},
            "Você quer retirar de qual caixinha ou investimento?", text, falta="target_name")

    norm_target = normalize_text(target_name)

    pockets = db.list_pockets(user_id) or []
    investments = db.accrue_all_investments(user_id) or []

    pocket_matches = [p for p in pockets if normalize_text(p.get("name") or "") == norm_target]
    investment_matches = [i for i in investments if normalize_text(i.get("name") or "") == norm_target]

    if len(pocket_matches) == 1 and not investment_matches:
        return h_pockets.withdraw(user_id, text, {"pocket_name": pocket_matches[0]["name"], **quantia})

    if len(investment_matches) == 1 and not pocket_matches:
        return h_investments.withdraw(user_id, text, {"investment_name": investment_matches[0]["name"], **quantia})

    if pocket_matches and investment_matches:
        return (
            f"Encontrei esse nome tanto em caixinha quanto em investimento: **{target_name}**.\n"
            f"Você quer retirar da *caixinha* ou do *investimento*?"
        )

    return (
        f"Não encontrei **{target_name}** nem em caixinhas nem em investimentos.\n"
        f"Use *listar caixinhas* ou *listar investimentos* para ver os nomes disponíveis."
    )
