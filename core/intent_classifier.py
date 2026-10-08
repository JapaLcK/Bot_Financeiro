# core/intent_classifier.py
"""
Classificador de intenção híbrido — 3 tiers:
  Tier 1: exact match     (custo zero, instantâneo)
  Tier 2: regex/alias     (custo zero, cobre variações)
  Tier 3: IA (GPT)        (só quando os dois acima falham)

Retorna sempre um IntentResult com:
  intent, confidence, entities, needs_clarification, clarification_question
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from difflib import get_close_matches
from dataclasses import dataclass, field
from typing import Any
from utils_text import PT_NUM_ALT_NO_ARTICLE, parse_money

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Estrutura de saída
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    intent: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    needs_clarification: bool = False
    clarification_question: str | None = None


# ---------------------------------------------------------------------------
# Tier 1 — Exact match (normalizado, sem acento)
# ---------------------------------------------------------------------------

_EXACT: dict[str, str] = {
    # saudações
    "oi":                       "greeting",
    "oie":                      "greeting",
    "oii":                      "greeting",
    "oiii":                     "greeting",
    "ola":                      "greeting",
    "alo":                      "greeting",
    "hello":                    "greeting",
    "hey":                      "greeting",
    "bom dia":                  "greeting",
    "boa tarde":                "greeting",
    "boa noite":                "greeting",
    # saldo
    "saldo":                    "balance.check",
    "saldo conta":              "balance.check",
    "saldo da conta":           "balance.check",
    "conta":                    "balance.check",
    "saldo geral":              "balance.check",
    "saldo atual":              "balance.check",
    "meu saldo":                "balance.check",
    "qual o saldo":             "balance.check",
    "qual meu saldo":           "balance.check",
    "quanto tenho":             "balance.check",
    "quanto tem":               "balance.check",
    "quanto eu tenho":          "balance.check",
    "quanto de saldo":          "balance.check",
    "quanto sobrou":            "balance.check",
    "quanto me resta":          "balance.check",
    "quanto resta":             "balance.check",
    "ta quanto":                "balance.check",
    # lançamentos / gastos / despesas
    "meus lancamentos":         "launches.list",
    "meu historico":            "launches.list",
    "minhas caixinhas":         "pockets.list",
    "meus investimentos":       "investments.list",
    "lancamentos":              "launches.list",
    "lancamento":               "launches.list",
    "listar lancamentos":       "launches.list",
    "ultimos lancamentos":      "launches.list",
    "historico":                "launches.list",
    # gastos — aliases naturais
    "gastos":                   "launches.list",
    "meus gastos":              "launches.list",
    "ver gastos":               "launches.list",
    "quais gastos":             "launches.list",
    "listar gastos":            "launches.list",
    "ultimos gastos":           "launches.list",
    "meus ultimos gastos":      "launches.list",
    "despesas":                 "launches.list",
    "minhas despesas":          "launches.list",
    "ver despesas":             "launches.list",
    "listar despesas":          "launches.list",
    "ultimas despesas":         "launches.list",
    "minhas ultimas despesas":  "launches.list",
    "extrato":                  "launches.list",
    "ver extrato":              "launches.list",
    "meu extrato":              "launches.list",
    # caixinhas
    "caixinhas":                "pockets.list",
    "caixinha":                 "pockets.list",
    "listar caixinhas":         "pockets.list",
    "saldo caixinhas":          "pockets.list",
    "ver caixinhas":            "pockets.list",
    # investimentos
    "investimentos":            "investments.list",
    "investimento":             "investments.list",
    "listar investimentos":     "investments.list",
    "saldo investimentos":      "investments.list",
    "ver investimentos":        "investments.list",
    # relatório
    "relatorio":                "report.daily",
    "relatorio diario":         "report.daily",
    "report":                   "report.daily",
    "report diario":            "report.daily",
    "resumo":                   "report.daily",
    "resumo diario":            "report.daily",
    # resumo semanal
    "resumo semanal":           "report.weekly",
    "relatorio semanal":        "report.weekly",
    "report semanal":           "report.weekly",
    "resumo da semana":         "report.weekly",
    "resumo semana":            "report.weekly",
    "gastos da semana":         "report.weekly",
    # resumo mensal
    "resumo mensal":            "report.monthly",
    "relatorio mensal":         "report.monthly",
    "report mensal":            "report.monthly",
    "resumo do mes":            "report.monthly",
    "resumo mes":               "report.monthly",
    "gastos do mes":            "report.monthly",
    # toggle report
    "ligar report diario":      "report.enable",
    "ativar report diario":     "report.enable",
    "voltar report diario":     "report.enable",
    "desligar report diario":   "report.disable",
    "desativar report diario":  "report.disable",
    "parar report diario":      "report.disable",
    # liga/desliga resumo semanal
    "ligar resumo semanal":     "report.weekly_enable",
    "ativar resumo semanal":    "report.weekly_enable",
    "voltar resumo semanal":    "report.weekly_enable",
    "desligar resumo semanal":  "report.weekly_disable",
    "desativar resumo semanal": "report.weekly_disable",
    "parar resumo semanal":     "report.weekly_disable",
    # liga/desliga resumo mensal
    "ligar resumo mensal":      "report.monthly_enable",
    "ativar resumo mensal":     "report.monthly_enable",
    "voltar resumo mensal":     "report.monthly_enable",
    "desligar resumo mensal":   "report.monthly_disable",
    "desativar resumo mensal":  "report.monthly_disable",
    "parar resumo mensal":      "report.monthly_disable",
    # emails de engajamento
    "reativar emails":          "emails.resubscribe",
    "receber emails":           "emails.resubscribe",
    "voltar emails":            "emails.resubscribe",
    "ativar emails":            "emails.resubscribe",
    "quero emails":             "emails.resubscribe",
    "parar emails":             "emails.unsubscribe",
    "cancelar emails":          "emails.unsubscribe",
    "desativar emails":         "emails.unsubscribe",
    "nao quero emails":         "emails.unsubscribe",
    # categorias
    "categorias":               "categories.list",
    "categoria":                "categories.list",
    "listar categorias":        "categories.list",
    "regras":                   "categories.list",
    "regras de categoria":      "categories.list",
    "regras de categorias":     "categories.list",
    "listar regras":            "categories.list",
    "ver regras":               "categories.list",
    # dashboard
    "dashboard":                "dashboard.open",
    "ver dashboard":            "dashboard.open",
    "abrir dashboard":          "dashboard.open",
    "painel":                   "dashboard.open",
    "ver painel":               "dashboard.open",
    # cartões / crédito
    "cartoes":                  "credit.handle",
    "cartoes de credito":       "credit.handle",
    "meus cartoes":             "credit.handle",
    "listar cartoes":           "credit.handle",
    "cadastrar cartao":         "credit.handle",
    "registrar cartao":         "credit.handle",
    "adicionar cartao":         "credit.handle",
    "incluir cartao":           "credit.handle",
    "novo cartao":              "credit.handle",
    "quais sao meus cartoes":   "credit.handle",
    "qual meu cartao principal":"credit.handle",
    "meu cartao principal":     "credit.handle",
    "fatura":                   "credit.handle",
    "faturas":                  "credit.handle",
    "listar faturas":           "credit.handle",
    "minhas faturas":           "credit.handle",
    "parcelamentos":            "credit.handle",
    "listar parcelamentos":     "credit.handle",
    "parcelas":                 "credit.handle",
    "ver parcelas":             "credit.handle",
    "listar parcelas":          "credit.handle",
    "meus parcelamentos":       "credit.handle",
    "ver parcelamentos":        "credit.handle",
    "parcelamentos ativos":     "credit.handle",
    # limite de crédito
    "limite":                   "credit.handle",
    "meu limite":               "credit.handle",
    "ver limite":               "credit.handle",
    "qual limite":              "credit.handle",
    "definir limite":           "credit.handle",
    "pagar fatura com saldo":   "credit.handle",
    "pagar com saldo":          "credit.handle",
    "usar saldo para pagar":    "credit.handle",
    # CDI
    "ver cdi":                  "cdi.check",
    "cdi":                      "cdi.check",
    "taxa cdi":                 "cdi.check",
    "qual cdi":                 "cdi.check",
    "qual a cdi":               "cdi.check",
    "qual e o cdi":             "cdi.check",
    "cdi hoje":                 "cdi.check",
    "cdi atual":                "cdi.check",
    # ajuda
    "ajuda":                    "help",
    "help":                     "help",
    "tutorial":                 "help.tutorial",
    # confirmações — sinais fortes de sim/não. Como o _EXACT é global (casa
    # mesmo sem ação pendente), evitamos aqui acks ambíguos ("ok", "beleza",
    # "certo") que a pessoa manda solto sem estar confirmando nada; esses ficam
    # só no confirmations.py da IA, que só é consultado quando há pending.
    "sim":                      "confirm.yes",
    "s":                        "confirm.yes",
    "confirmar":                "confirm.yes",
    "confirmo":                 "confirm.yes",
    "confirmado":               "confirm.yes",
    "isso":                     "confirm.yes",
    "isso mesmo":               "confirm.yes",
    "isso ai":                  "confirm.yes",
    "e isso":                   "confirm.yes",
    "aham":                     "confirm.yes",
    "uhum":                     "confirm.yes",
    "exato":                    "confirm.yes",
    "exatamente":               "confirm.yes",
    "com certeza":              "confirm.yes",
    "pode sim":                 "confirm.yes",
    "pode mandar":              "confirm.yes",
    "manda bala":               "confirm.yes",
    "positivo":                 "confirm.yes",
    "nao":                      "confirm.no",
    "nope":                     "confirm.no",
    "cancelar":                 "confirm.no",
    "cancela":                  "confirm.no",
    "negativo":                 "confirm.no",
    "melhor nao":               "confirm.no",
    "agora nao":                "confirm.no",
    "deixa pra la":             "confirm.no",
    "deixa quieto":             "confirm.no",
    "nao quero":                "confirm.no",
    # desfazer
    "desfazer":                 "launches.undo",
}

# As negativas canônicas, DERIVADAS do `_EXACT` — é a fonte, não uma segunda
# lista (§0.7). O `_is_no` de `core/handlers/credit.py` importa daqui: copiada à
# mão ela já divergia em 5 das 10 (`nope`, `negativo`, `melhor nao`,
# `deixa pra la`, `deixa quieto`), e numa pergunta de sim/não isso não era só
# "não entendi" — o portão devolvia `None`, o `route()` abandonava, e a pessoa
# via **"Nada a cancelar."** no lugar do ramo negativo (Codex, #323).
NEGATIVAS_EXATAS = frozenset(k for k, v in _EXACT.items() if v == "confirm.no")

# ---------------------------------------------------------------------------
# Vocabulário de VERBO DE LANÇAMENTO — fonte única (§0.7)
# ---------------------------------------------------------------------------
# As regexes do Tier 2 abaixo são MONTADAS a partir destas tuplas, e a união é
# lida de fora por `tests/test_pendencia_credito_vocabulario.py`, que exige
# interseção VAZIA com o `_PODAVEL_NO_PREFIXO` de `core/handlers/credit.py`:
# verbo que move dinheiro não pode ser podado do começo de uma resposta, senão
# `debitei 50 no cartao Nubank` respondendo "qual fatura?" PAGA a fatura
# (Codex, #323). Reescrever a lista de lá à mão repete a deriva que causou isso;
# derivá-la daqui a torna impossível.
#
# São QUATRO tuplas e não uma porque as regexes já usavam vocabulários
# DIFERENTES, e uniformizar mudaria ROTEAMENTO: só os cinco de `_VERBOS_CARTAO`
# mandam para `credit.handle` (`mandei 50 no cartao` é `launches.add` hoje, e
# continua sendo), e `gastando` só existe na forma solta, nunca na datada. As
# quatro regexes montadas abaixo são idênticas, caractere a caractere, às que
# estavam escritas à mão: medido no #323 comparando os 83 padrões de
# `_ALIAS_PATTERNS` antes e depois, mais 80 frases pelo `classify` — 0 divergência.
#
# ATENÇÃO: isto NÃO é o vocabulário de comando inteiro do bot. Verbo de
# caixinha/investimento/saldo (`somei`, `depositei`, `investi`, `saquei`…) mora
# em OUTRAS regexes e segue fora do veto — medido, `somei 50 no nubank` ainda
# paga a fatura. Ver o relato do #323 antes de assumir que a classe está fechada.
_VERBOS_CARTAO = ("gastei", "paguei", "comprei", "debitei", "gasto")
_VERBOS_ENVIO = ("mandei", "enviei", "pixei", "torrei", "queimei")
_VERBOS_ENTRADA = ("recebi", "ganhei", "entrou", "caiu", "pingou", "pinguei",
                   "embolsei")
_VERBOS_SAIDA = _VERBOS_CARTAO + _VERBOS_ENVIO
_VERBOS_SAIDA_SOLTA = _VERBOS_SAIDA + ("gastando",)

# A UNIÃO — o que o veto do crédito lê. Verbo novo em QUALQUER uma das tuplas
# entra no veto sem ninguém precisar lembrar de mexer no outro arquivo.
VERBOS_DE_LANCAMENTO = frozenset(_VERBOS_SAIDA_SOLTA + _VERBOS_ENTRADA)
# `_VERBOS_LANC_RE` (pergunta comparativa, mais abaixo) estende esta união com
# "gastou" e "gastamos", só para aquela heurística.

# ---------------------------------------------------------------------------
# Tier 2 — Regex / alias (normalizado)
# ---------------------------------------------------------------------------

_ALIAS_PATTERNS: list[tuple[str, str]] = [
    # saudações — captura qualquer variação antes de chegar na IA
    (r"^(oi+e*|ol[aá]+|al[oô]+|hello+|hey+|e ?ai+|opa+|eai+)\b",
     "greeting"),
    (r"^bom\s+dia\b",  "greeting"),
    (r"^boa\s+tarde\b", "greeting"),
    (r"^boa\s+noite\b", "greeting"),

    # saldo
    (r"^(quanto tenho na conta|quanto tem na conta|meu saldo|qual meu saldo|ver saldo"
     r"|me fala (o )?meu saldo|me fala o saldo|me diz (o )?saldo|qual (e )?meu saldo|ver meu saldo"
     r"|quero saber (o )?saldo|quanto (tem|tenho) na (minha )?conta)$",
     "balance.check"),
    # adicionar/somar dinheiro AO saldo → lançamento de receita ("adicione 10
    # mil de saldo"). Vem antes de pockets.deposit ("adicionei \d") pra não cair
    # em caixinha; o "saldo" desambigua de "adicionar cartão/caixinha".
    (r"^(adicionar|adicione|adiciona|adicionei|adicionou|somar|soma|some|somei)\b.*\bsaldo\b",
     "launches.add"),
    # "adicione 300 na caixinha viagem" → depósito na caixinha (verbos já no
    # DEPOSIT_VERBS). O destino explícito ("caixinha") desambigua.
    (r"^(adicionar|adicione|adiciona|adicionei|adicionou|somar|soma|some|somei)\b.*\bcaixinha\b",
     "pockets.deposit"),
    # "adicione 500 no investimento X" → aporte no investimento.
    (r"^(adicionar|adicione|adiciona|adicionei|adicionou|somar|soma|some|somei)\b.*\binvestimento\b",
     "investments.deposit"),
    # "adicione 10 mil" SEM destino → pergunta onde (saldo/caixinha/investimento).
    # Exclui cartão/categoria (fluxos próprios) e exige um valor. saldo/caixinha/
    # investimento já foram capturados pelas regras acima (first-match vence).
    (r"^(?!.*\b(cartao|categoria)\b)(adicionar|adicione|adiciona|adicionei|adicionou|somar|soma|some|somei)\s+(r\$\s*)?\d",
     "funds.add_ask"),

    # lançamentos — com data (hoje/ontem)
    (r"\b(lancamentos?|gastos?|despesas?|receitas?|historico|extrato)\b.*(hoje|ontem)",
     "launches.list"),
    (r"\b(hoje|ontem)\b.*(lancamentos?|gastos?|despesas?|receitas?)",
     "launches.list"),
    # "quanto gastei [na categoria X] [período]" → soma do gasto (não listagem).
    # Ex: "quanto gastei na categoria outros esta semana", "quanto gasto em
    # julho", "quanto gastei ontem". A listagem fica com os padrões
    # "ver/mostrar/listar gastos" logo abaixo.
    (r"^(quanto|algum|tive|tivemos?|houve)\s.*(gastei|gastou|gasto|gasta|despesa|despesas)\b",
     "launches.spend_query"),
    # lançamentos / gastos — perguntas naturais sem data
    (r"^(ver|mostrar|mostra|listar)\s+(meus\s+)?(lancamentos?|gastos?|despesas?|extrato)(\s+recentes?)?$",
     "launches.list"),
    (r"^(quais|qual)\s+(sao|foram|e|foi)?\s*(meus|os|minhas|as)?\s*(gastos?|despesas?|lancamentos?|ultimos?)\b",
     "launches.list"),
    (r"^(me\s+)?(mostra|mostre|mostrar|ve|ver|lista|liste)\s+(meus\s+|os\s+|minhas\s+|as\s+)?(gastos?|despesas?|lancamentos?|extrato|historico)\b",
     "launches.list"),
    (r"^(o\s+que|quanto)\s+(gastei|gastos?|despesas?|lancamentos?)\b",
     "launches.list"),
    (r"^(gastos?|despesas?)(?:\s+(recentes?|ultimos?|da\s+semana|do\s+mes))?$",
     "launches.list"),
    # delete de um único lançamento
    (r"^apagar\s+(?:id\s+)?(lancamento\s+)?#?(\d+)$",
     "launches.delete"),
    (r"^excluir\s+(?:id\s+)?(lancamento\s+)?#?(\d+)$",
     "launches.delete"),
    (r"^deletar\s+(?:id\s+)?(lancamento\s+)?#?(\d+)$",
     "launches.delete"),
    (r"^apagar\s+(?:id\s+)?#?(\d+)$",
     "launches.delete"),
    # delete de múltiplos lançamentos: "apagar id 757, 756" ou "apagar #757 e #756"
    (r"^(?:apagar|excluir|deletar)\s+(?:id[s]?\s+)?(?:#?\d+[\s,e]+)+#?\d+$",
     "launches.delete_bulk"),

    # desfazer / apagar compras no crédito
    (r"^(desfazer|apagar|excluir|remover|deletar|delete)\b.*(?:\bcc\s*\d+\b|\bpc[0-9a-f]{8}\b|\bct\s*#?\s*\d+\b|\bgrupo\b|\bgroup\b|\bcompra\b|\bcredito\b|\bcr[eé]dito\b|\bparcelamento\b|\bparcela\b)",
     "credit.handle"),

    # compra no crédito em linguagem natural
    (r"^(" + "|".join(_VERBOS_CARTAO) + r")\b.*\b(cartao|credito)\b",
     "credit.handle"),

    # pagamento de fatura (precede launches.add para não capturar "paguei" como gasto)
    (r"^(?:pagar|paguei)\s+(?:a\s+)?fatura\b",
     "credit.handle"),

    # despesa / receita — detecta padrão sem chamar IA
    (r"^(" + "|".join(_VERBOS_SAIDA_SOLTA) + r")\b",
     "launches.add"),
    (r"^(" + "|".join(_VERBOS_ENTRADA) + r")\b",
     "launches.add"),
    (r"^(hoje|ontem|\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?|dia\s+\d{1,2}(?:[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?)?)\b.*\b("
     + "|".join(_VERBOS_SAIDA + _VERBOS_ENTRADA) + r")\b",
     "launches.add"),

    # cartões / crédito
    (r"^(cartoes|cartoes de credito|listar cartoes|meus cartoes|quais cartoes|quais sao meus cartoes|criar cartao|padrao\b|credito\b|parcelar\b|parcelei\b|fatura\b|faturas\b|pagar\b|paguei\b|parcelamentos\b|parcelas\b|minhas faturas|me mostra minhas faturas|qual meu cartao principal|meu cartao principal|trocar cartao principal|mudar cartao principal|definir limite|limite cartao|limite do cartao)",
     "credit.handle"),
    (r"^(quero\s+)?(cadastrar|registrar|adicionar|incluir|criar)\s+(um\s+|novo\s+|meu\s+)?cartao\b",
     "credit.handle"),
    (r"^(quero|preciso|gostaria\s+de|me\s+ajuda\s+a|me\s+ajude\s+a)\s+.*\b(cadastrar|registrar|adicionar|incluir|criar)\b.*\bcartao\b",
     "credit.handle"),
    # parcelas / parcelamentos — ver/listar variações
    (r"^(ver|listar|mostrar|me mostra|quero ver|quais|meus)\s+(parcelas?|parcelamentos?)\b",
     "credit.handle"),
    # limite de crédito — variações naturais
    (r"\blimite\s+(de\s+credito|do\s+cartao|do\s+\w+|disponivel)\b",
     "credit.handle"),
    (r"^(definir|setar|colocar|mudar|alterar)\s+limite\b",
     "credit.handle"),
    (r"\bpagar\s+(fatura|o\s+cartao)\s+com\s+saldo\b",
     "credit.handle"),
    (r"\busar\s+saldo\s+para\s+pagar\b",
     "credit.handle"),
    (r"\bquanto\s+(tenho\s+de\s+|ainda\s+)?limite\b",
     "credit.handle"),
    (r"^(quero|preciso|gostaria de)\s+.*\b(cartao|cartoes|fatura|faturas|credito|parcelamento)\b",
     "credit.handle"),
    (r"^(me\s+mostra|mostrar|ver|quero ver|quais|qual)\s+.*\b(cartao|cartoes|fatura|faturas|credito|parcelamento)\b",
     "credit.handle"),
    (r"^(meu|minha|este|esse)\s+.*\b(vence|fecha)\s+quando\b",
     "credit.handle"),
    (r"^(quanto|qual)\s+.*\b(fatura|credito)\b.*\b(nubank|visa|mastercard|cartao|cartoes)\b",
     "credit.handle"),
    (r"^(trocar|mudar|definir|colocar)\s+.*\b(cartao principal|principal)\b",
     "credit.handle"),
    (r"\b(cartoes|cartao|fatura|faturas|parcelamentos|credito)\b",
     "credit.handle"),

    # caixinhas
    (r"^(ver|mostrar|listar)\s+(minhas\s+)?caixinhas?$",
     "pockets.list"),
    # criar: infinitivo/imperativo/presente ("criar/cria/crie/nova/abrir caixinha X")
    (r"^(criar|cria|crie|nova|novo|abrir|abre|abra)\s+caixinha\s+(.+)$",
     "pockets.create"),
    # excluir: infinitivo/imperativo ("excluir/deletar/apagar/remover caixinha X")
    (r"^(excluir|exclua|deletar|delete|apagar|apaga|apague|remover|remove|remova)\s+caixinha\s+(.+)$",
     "pockets.delete"),
    (r"^(coloquei|adicionei|depositei|transferi|pus|botei)\s+\d",
     "pockets.deposit"),
    # depósito no imperativo/infinitivo COM destino explícito "caixinha"
    # ("deposita/depositar/guarda/guardar/poupa/junta ... caixinha X"). Os verbos
    # no passado ("depositei", "coloquei") já são pegos pela regra acima; aqui
    # cobrem-se as formas anunciadas no catálogo ("deposita 200 na caixinha X").
    (r"^(deposita|deposite|depositar|guarda|guarde|guardar|guardei|poupa|poupe|poupar|poupei|junta|junte|juntar|juntei|coloca|coloque|colocar|bota|bote|botar)\b.*\bcaixinha\b",
     "pockets.deposit"),
    (r"^(retirei|saquei|tirei)\s+\d.*\bcaixinha\b",
     "pockets.withdraw"),
    # saque no imperativo/infinitivo COM destino explícito "caixinha"
    # ("saca/sacar/tira/tirar/retira/retirar ... caixinha X"). Anunciado no
    # catálogo como "saca 50 da caixinha viagem".
    (r"^(saca|saque|sacar|tira|tire|tirar|retira|retire|retirar|resgata|resgate|resgatar)\b.*\bcaixinha\b",
     "pockets.withdraw"),
    # sacar tudo / esvaziar / zerar a caixinha (sem valor) → saque total que zera o saldo
    (r"\b(saca|sacar|saque|saquei|retira|retirar|retirei|tira|tirar|tirei|resgata|resgatar)\b.*\btudo\b.*\bcaixinha\b",
     "pockets.withdraw"),
    (r"\b(esvaziar|esvazia|esvaziei|zerar|zera|zerei)\b.*\bcaixinha\b",
     "pockets.withdraw"),

    # investimentos
    (r"^(ver|mostrar|listar)\s+(meus\s+)?investimentos?$",
     "investments.list"),
    (r"^criar\s+investimento\s+(.+)$",
     "investments.create"),
    (r"^excluir\s+investimento\s+(.+)$",
     "investments.delete"),
    (r"^(apliquei|aportei|investi)\s+\d",
     "investments.deposit"),
    (r"^(resgatei|saquei do investimento|retirei do investimento)\b",
     "investments.withdraw"),
    (r"^(retirei|saquei|tirei|resgatei)\s+\d",
     "funds.withdraw"),
    # resgatar tudo / esvaziar / zerar investimento (sem valor) → resgate total que zera
    (r"\b(resgata|resgatar|saca|sacar|saque|retira|retirar|tira|tirar)\b.*\btudo\b.*\binvestimento",
     "funds.withdraw"),
    (r"\b(esvaziar|esvazia|esvaziei|zerar|zera|zerei)\b.*\binvestimento",
     "funds.withdraw"),

    # CDI — formas interrogativas naturais ("quanto está o cdi", "como anda o
    # cdi"). Só casa pergunta explícita sobre a taxa; não colide com criação de
    # investimento ("cria/aporta ... % do cdi"), que começa com outro verbo.
    (r"^(quanto|como|qual)\b.*\b(esta|está|ta|anda|ficou|foi|e|é)\b.*\bcdi\b",
     "cdi.check"),
    (r"^(quanto|como)\s+(esta|está|ta|anda)\s+o\s+cdi\b",
     "cdi.check"),

    # categorias
    (r"^(regras|regras de categoria|regras de categorias|listar regras|ver regras)$",
     "categories.list"),
    (r"^aprender\s+.+\s+como\s+.+$",
     "categories.create"),
    (r"^criar\s+categoria\s+",
     "categories.create"),
    (r"^(remove|remover|apagar|excluir|deletar)\s+regra\s+",
     "categories.delete"),
    (r"^remover\s+destinatario\s+",
     "categories.delete"),
    (r"^linkar\s+",
     "categories.create"),

    # relatório diário com horário
    # cobre: "ligar report diario 20h", "ativar report diario as 8h30", "report diario 21h"
    (r"\b(ligar|ativar|voltar|habilitar|configurar|report|relatorio)\b.*\b(report|relatorio)\b.*\b\d{1,2}h\b",
     "report.set_hour"),
    (r"\b(ligar|ativar|voltar|habilitar|configurar)\b.*\b(report|relatorio)\b.*\b\d{1,2}[:\s]\d{2}\b",
     "report.set_hour"),
    (r"\b(report|relatorio)\b.*\b(diario|daily)\b.*\b\d{1,2}h\b",
     "report.set_hour"),

    # liga/desliga do semanal — precisa vir ANTES do alias de report.weekly,
    # senão "desligar resumo semanal" cairia em report.weekly.
    (r"\b(desligar|desliga|desligue|desativar|desativa|desative|parar|cancelar|cancela)\b.*\b(resumo|relatorio|report)\b.*\b(semanal|semana)\b",
     "report.weekly_disable"),
    (r"\b(ligar|liga|ligue|ativar|ativa|ative|habilitar|habilita|voltar)\b.*\b(resumo|relatorio|report)\b.*\b(semanal|semana)\b",
     "report.weekly_enable"),
    # "receber" e "para de mandar" só ligam/desligam na frase exata abaixo, com
    # cortesia opcional. Qualquer outra forma (dúvida, correção, complemento,
    # plural, pergunta) cai na consulta ou na IA: lista aberta de variações não
    # fecha — ver a rodada de revisão de 2026-10-08 em tests/test_intent_alias_resumo_liga_desliga.py.
    (r"^(?:(?:por favor|pfv|obrigad[oa]) )?(?:eu )?(?:nao (?:quero|desejo)(?: mais)? receber|para de mandar) o resumo semanal(?: por favor| pfv| obrigad[oa])?$",
     "report.weekly_disable"),
    (r"^(?:(?:por favor|pfv|obrigad[oa]) )?(?:eu )?(?:quero|desejo) receber o resumo semanal(?: todo domingo)?(?: por favor| pfv| obrigad[oa])?$",
     "report.weekly_enable"),
    (r"^(?:(?:por favor|pfv|obrigad[oa]) )?(?:eu )?(?:nao (?:quero|desejo)(?: mais)? receber|para de mandar) o resumo mensal(?: por favor| pfv| obrigad[oa])?$",
     "report.monthly_disable"),
    (r"^(?:(?:por favor|pfv|obrigad[oa]) )?(?:eu )?(?:quero|desejo) receber o resumo mensal(?: todo mes)?(?: por favor| pfv| obrigad[oa])?$",
     "report.monthly_enable"),
    # liga/desliga do mensal — idem, antes de report.monthly.
    (r"\b(desligar|desliga|desligue|desativar|desativa|desative|parar|cancelar|cancela)\b.*\b(resumo|relatorio|report)\b.*\b(mensal|mes)\b",
     "report.monthly_disable"),
    (r"\b(ligar|liga|ligue|ativar|ativa|ative|habilitar|habilita|voltar)\b.*\b(resumo|relatorio|report)\b.*\b(mensal|mes)\b",
     "report.monthly_enable"),

    # resumo semanal: "resumo da semana", "relatorio semanal", "gastos da semana"
    (r"\b(resumo|relatorio|report|gastos?)\b.*\b(semanal|semana)\b",
     "report.weekly"),
    # resumo mensal: "resumo do mes", "relatorio mensal", "gastos do mes"
    (r"\b(resumo|relatorio|report|gastos?)\b.*\b(mensal|mes)\b",
     "report.monthly"),

    # vinculação de contas
    (r"^link(\s+\d{6})?$",
     "account.link"),
    (r"^vincular\s+\d{6}$",
     "account.vincular"),

    # ajuda com seção
    (r"^(ajuda|help)\s+\w+",
     "help"),

    # valor primeiro, sem palavra-chave: "77,90 mercado", "50 uber" → despesa.
    # Receita SEMPRE exige "recebi"/"receita"/"ganhei" (capturado acima). Vem por
    # último pra não roubar intents com keyword de domínio (cartão, caixinha,
    # investimento). Exige descrição após o valor pra não pegar número solto
    # (ex: resposta "50" a uma pergunta). Em `norm`, "77,90" já virou "77 90".
    (r"^(?:r\s+)?\d+(?:\s+\d+)*\s+[a-z]",
     "launches.add"),

    # confirmação textual
    (r"^(sim|s|confirmo|confirmar|pode|vai)$",
     "confirm.yes"),
    (r"^(nao|n|cancela|cancelar|nope|negativo)$",
     "confirm.no"),
]


# ---------------------------------------------------------------------------
# Helpers de normalização
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _extract_id_from_text(text_norm: str) -> int | None:
    """Extrai o primeiro número inteiro do texto normalizado."""
    m = re.search(r"\b(\d+)\b", text_norm)
    return int(m.group(1)) if m else None


def _extract_amount_from_text(original: str) -> float | None:
    try:
        return parse_money(original)
    except Exception:
        return None


def _extract_source_target_name(original: str) -> str | None:
    text = (original or "").strip()
    if not text:
        return None

    patterns = (
        r"\b(?:da|do|de)\s+(?:caixinha|investimento)\s+(.+)$",
        r"\b(?:da|do|de)\s+(.+)$",
    )
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        name = (m.group(1) or "").strip(" .,!?:;")
        if name:
            return name
    return None


_DOMAIN_HINT_KEYWORDS = (
    "cartao", "cartoes", "fatura", "credito", "parcela", "parcelamento",
    "caixinha", "caixinhas",
    "investimento", "investimentos", "aporte", "resgate", "cdb", "tesouro", "cdi",
    "categoria", "categorias", "regra", "regras", "linkar",
    "dashboard", "painel",
    "report", "relatorio",
    "ofx", "extrato", "importar",
    "saldo", "gasto", "gastos", "despesa", "despesas", "recebi", "receita", "lancamento", "lancamentos",
)


def _contains_domain_hint(norm: str) -> bool:
    tokens = [tok for tok in norm.split() if tok]
    if any(keyword in norm for keyword in _DOMAIN_HINT_KEYWORDS):
        return True

    for token in tokens:
        if get_close_matches(token, _DOMAIN_HINT_KEYWORDS, n=1, cutoff=0.84):
            return True

    return False


# Marcadores de RECORRÊNCIA (texto normalizado, sem acento). Quando presentes
# junto de um valor, a mensagem é sobre criar gasto/receita recorrente — que só
# a IA (Tier 3) sabe classificar em recurring.add. Sem este desvio, o Tier 2
# pega "300 em aluguel todo mes" como launches.add (avulso) e o domain-hint
# devolve out_of_scope antes da IA ver.
_RECURRENCE_MARKERS = (
    "recorrente", "gasto fixo", "gastos fixos", "despesa fixa", "conta fixa",
    "receita fixa", "renda fixa", "receita recorrente", "salario fixo",
    "todo mes", "todos os meses", "todo ano", "todos os anos",
    "mensal", "mensais", "mensalmente", "anual", "anuais", "anualmente",
    "por mes", "por ano", "todo dia", "uma vez por ano", "uma vez por mes",
    "uma vez ao ano", "uma vez ao mes", "1x por ano", "1x por mes",
)


def _has_recurrence_marker(norm: str) -> bool:
    """True se a mensagem tem marcador de recorrência E um número (valor).
    Exclui pedidos de ORÇAMENTO ("orçamento mensal de X") — esses são budget,
    não recorrente, e seguem o fluxo normal (IA conversacional)."""
    if not any(c.isdigit() for c in norm):
        return False
    if "orcamento" in norm:
        return False
    return any(mk in norm for mk in _RECURRENCE_MARKERS)


# Marcadores de CRIAÇÃO de conta a pagar / boleto — recorrente manual, que a
# IA classifica como recurring.add (com pagamento='manual'). Não confundir com
# PAGAMENTO ("paguei o boleto" → launches.add, tratado no router).
_BILL_CREATE_MARKERS = (
    "boleto", "boletos", "conta a pagar", "contas a pagar", "conta pra pagar",
    "conta para pagar", "me lembra", "me lembre", "lembrete", "lembra de pagar",
    "lembrar de pagar",
)


def _has_bill_marker(norm: str) -> bool:
    """True se a mensagem parece CRIAR uma conta a pagar (marcador de boleto/
    lembrete + um valor), e não é um pagamento nem um orçamento."""
    if not any(c.isdigit() for c in norm):
        return False
    if "orcamento" in norm:
        return False
    if re.match(r"^(ja\s+)?(paguei|quitei)\b", norm):
        return False
    return any(mk in norm for mk in _BILL_CREATE_MARKERS)


# Marcadores de BOLETO / AGENDA / PRAZO — tudo isso vive na IA conversacional
# (tools get_bills_to_pay, add_boleto, check_cashflow, mark_bill_paid), NÃO no
# route() determinístico. Sem este desvio, "como tô de boletos no dia 17" era
# classificado como launches.list (por causa do "dia 17") e o bot pedia o mês.
_BOLETO_DOMAIN_MARKERS = (
    "boleto", "boletos", "conta a pagar", "contas a pagar", "conta pra pagar",
    "contas pra pagar", "conta para pagar", "contas para pagar",
)
_PRAZO_MARKERS = (
    "to tranquilo", "tranquilo nesse prazo", "tranquilo ate", "aguento esse prazo",
    "aguento o prazo", "aguento pagar", "da pra pegar", "consigo pagar ate",
    "como to de boleto", "como to de conta", "como estou de boleto",
)


def _is_boleto_ai_query(norm: str) -> bool:
    """True se a mensagem é sobre boletos/contas a pagar/prazo e deve ir pra IA
    (não pro route determinístico). Exclui pagamento ("paguei", tratado no
    launches.add) e criação RECORRENTE ("boleto todo mês" → recurring.add)."""
    if re.match(r"^(ja\s+)?(paguei|quitei)\b", norm):
        return False
    if any(mk in norm for mk in _RECURRENCE_MARKERS):
        return False  # boleto recorrente ("todo mês") segue pro recurring.add
    return (any(mk in norm for mk in _BOLETO_DOMAIN_MARKERS)
            or any(mk in norm for mk in _PRAZO_MARKERS))


# Pergunta COMPARATIVA que começa com verbo de lançamento ("gastei mais esse mês
# que no passado?", "gastei mais em 2025 ou 2026?") vai pra IA (compare_periods),
# não pro launches.add — que perguntava o valor ou gravava o ano como R$ 2.025.
# Só vale com mais/menos/muito/demais SEM quantidade logo depois ("gastei mais 30"
# e "gastei mais ou menos 50" seguem lançamento) E com "?" ou marcador. Antes do
# verbo, pula um prefixo de lista FECHADA ("oi", "eu", "sera que").
# ponytail: heurística. Limites medidos em 2026-09-30 (remeça antes de reusar):
#   GRAVAM dinheiro: "gastei mais 2025?" (R$ 2.025, um ano só); "gastei mais no
#   ano de 2025" e "gastei mais de 2025 pra ca" (R$ 2.025: o marcador de ano não
#   tem "de", decisão do dono); "gastei mais de 2024 do que 2025" (R$ 2.024);
#   "gastei+ em 2025 ou 2026?" (R$ 2.025); "gastei uns 200 a mais que no mes
#   passado?" e "gastei duzentos a mais que no mes passado?" (R$ 200); "gastei
#   mais nos ultimos tres meses" (R$ 3); "gastei 200 a mais do mes passado?" (sem
#   "que") e "gastei 200 a mais em relacao ao mes passado?" (R$ 200, mesmo com
#   "?"); e, por decisão do dono, sem "?": "N a mais que", "acima de" e
#   percentual ("gastei mais 30% esse mês" grava R$ 30); e, no `add()`, #693
#   (o split não corta em "." nem ";"): com 2+ lançamentos e a pergunta colada
#   por "." ("gastei 30 no uber e gastei no bar. gastei mais em 2025 ou 2026?"
#   grava R$ 30 e R$ 2.025; "paguei a luz. gastei mais em 2025 ou 2026? e paguei
#   50 no mercado" grava R$ 2.025 e R$ 50, porque o split corta no "e" e o 1º
#   pedaço não começa com o verbo da pergunta). Conserto possível, fora deste PR:
#   no laço do multi do `add()` (texto e áudio), `sem_perguntas_comparativas(part)`
#   no lugar de `is_comparative_question(part)`. Com UM lançamento o `add()` já lê
#   só o pedaço. E #699: verbo acentuado ("gastei no uber. Gastéi mais em 2025 ou
#   2026?") grava R$ 2.025: a contagem de verbos diverge, `sem_perguntas` devolve
#   ("", [texto]) e o `add()` cai no fluxo antigo com o texto inteiro.
#   Contexto fora da lista antes do verbo ("ontem gastei muito no bar 80?", Codex
#   P2 do #739): o `classify` o manda ao `launches.add` e o `add()` recusa pelo "?"
#   ("Não consegui identificar o valor" + o aviso). Medido em 2026-10-02 pela
#   conversa: sem "ontem"/"hoje" a frase já não grava na main (vai à IA/ajuda,
#   regra "? basta" do #570); com eles a main gravava R$ 80 por acidente. "ontem
#   gastei 80 no bar?", "ontem paguei 120 de luz?" e "ontem recebi 500 do freela?"
#   gravam igual na main e aqui. Coerente com a regra do dono, só a mensagem
#   destoa. Pôr "ontem/hoje" em `_PREFIXO_RE` alinharia o `classify`, mas é
#   decisão de produto. Idem com o "?" em outra cláusula ("ontem gastei muito no
#   bar 80 e qual meu saldo?", Codex P2 do #739): o "?" vale para todo verbo
#   antes dele, e sem o "ontem" a frase já não grava na main. Limitar o "?" à
#   cláusula mudaria a regra "? basta" também para a versão sem prefixo.
#   SÓ NÃO VAI PRA IA: prefixo fora da lista ("tipo gastei mais em 2025?") escapa
#   do `classify`, mas as portas pegam pela varredura e a conversa nova não grava.
# "gastou"/"gastamos" só aqui (não estão em `VERBOS_DE_LANCAMENTO`). Que "gastamos
# 50 no mercado" não grave é lacuna preexistente do Tier 2, não decisão.
_VERBOS_LANC_RE = "|".join(sorted(VERBOS_DE_LANCAMENTO | {"gastou", "gastamos"},
                                  key=len, reverse=True))
_ANO = r"(?:19|20)\d\d"
# "mais 2025 ou 2026": ano seguido de conector e outro ano não é quantidade.
_QTD = (rf"(?:(?:uns|umas)\s+)?(?:(?!{_ANO}\s+(?:ou|x|vs|e|q|que|do que)\s+(?:em\s+)?{_ANO}\b)\d"
        rf"|r\s|(?:{PT_NUM_ALT_NO_ARTICLE})\b)")   # "r\s" = "R$" depois do _normalize
# Sem "^": as regexes rodam com `.match(norm, pos)` a partir de cada verbo.
_COMPARATIVO_RE = re.compile(
    rf"(?:{_VERBOS_LANC_RE})\s+(?:(?:bem|mto|mt)\s+)?(?:mais|menos|muito|demais)\b"
    rf"(?!\s+{_QTD})"                  # "gastei mais (uns) 30", "paguei menos 10", "mais trinta"
    rf"(?!\s+ou\s+menos\s+{_QTD})"     # "gastei mais ou menos (uns) 50 no mercado"
)
# Só com "?". Sem, informam o valor: "paguei 20 a mais que o normal na luz",
# "paguei acima do normal 200 na luz", "gastei 30% a mais no uber".
_SO_COM_PERGUNTA_RE = re.compile(
    rf"(?:{_VERBOS_LANC_RE})\s+(?:(?:r\s+)?[\d\s]+(?:reais\s+)?a\s+(?:mais|menos)\s+(?:do\s+)?(?:que|q)\b"
    rf"|porcento\s+(?:a\s+)?(?:mais|menos)\b"
    rf"|(?:acima|abaixo)\b(?!\s+{_QTD}))"
)
# "que" só COLADO ao comparativo ("mais que", "menos do que"; "q" é o mesmo "que"
# abreviado): o "que" relativo ("mais um pix de 50 que o joao mandou") é
# lançamento. O "ou" de "mais ou menos" é aproximação, não alternativa. Ano só
# depois de preposição, e sem "de": "gastei mais de 2000 no carro" é lançamento,
# igual a "mais de 100".
_MARCADOR_COMPARACAO_RE = re.compile(
    r"\b(?:(?:mais|menos) (?:do )?(?:que|q)|(?<!\bmais )ou|passado|passada|anterior|normal|comum|antes"
    rf"|comparad[oa]s?|(?:em|no|desde|ate) {_ANO}|ultim[oa]s \d+ (?:dias|semanas|meses|anos))\b"
)
_PREFIXO_RE = re.compile(
    r"^(?:(?:oi|ola|opa|eai|e ai|piggy|eu|sera que|sera q|sera|sabe se|me diz se|me fala se)\s+)*")
_VERBO_LANC_RE = re.compile(rf"\b(?:{_VERBOS_LANC_RE})\b", re.IGNORECASE)


def _sem_simbolo(text: str) -> str:
    """O _normalize apaga "%", "+" e "-": "mais 30%" não é quantidade, "+ ou -" é
    "mais ou menos", e "+" solto é "mais"."""
    # Quem garante a linearidade é o `(?<!\d)`: a regex só tenta do início de cada
    # número (sem ele, "1"*20000 + " x%" é O(n²)). O `if "%"` só poupa o trabalho.
    t = re.sub(r"(?<!\d)\d+(?:[.,]\d+)?\s*%", "porcento", text) if "%" in text else text
    t = re.sub(r"\+\s*ou\s*-", "mais ou menos", t)
    return re.sub(r"(?<!\S)\+(?=\s)", "mais", t)


def _comparativa_em(norm: str, pos: int, tem_pergunta: bool, tem_marcador: bool) -> bool:
    """Pergunta comparativa começando no verbo em `norm[pos]`."""
    if tem_pergunta and _SO_COM_PERGUNTA_RE.match(norm, pos):
        return True
    return (tem_pergunta or tem_marcador) and bool(_COMPARATIVO_RE.match(norm, pos))


def is_comparative_question(text: str) -> bool:
    """True se a mensagem é pergunta comparativa com verbo de lançamento."""
    raw = _sem_simbolo(text or "")
    norm = _PREFIXO_RE.sub("", _normalize(raw))
    return _comparativa_em(norm, 0, "?" in raw, bool(_MARCADOR_COMPARACAO_RE.search(norm)))


def _sem_conector_final(cabeca: str) -> str:
    """Tira de "paguei a luz e " / "gastei 30 no uber. " o que liga ao pedaço cortado.
    Sem regex ancorada no fim: sobre uma corrida de espaços ela seria O(n²)."""
    cabeca = cabeca.rstrip(" \t\n,.;")
    resto, _, ultima = cabeca.rpartition(" ")
    return resto.rstrip(" \t\n,.;") if ultima.lower() in ("e", "tb", "mas") else cabeca


def _inicio_da_pergunta(text: str) -> int | None:
    """Índice em `text` do verbo onde começa a 1ª pergunta comparativa, ou None.
    Linear: normaliza uma vez e vale para cada verbo o "?" e o marcador que vêm
    DEPOIS dele. O verbo do texto original é o de mesma ordem no normalizado."""
    raw = _sem_simbolo(text)
    norm = _normalize(raw)
    ultima_pergunta = raw.rfind("?")
    # O "?" vira espaço no _normalize, então o texto antes dele normaliza para um prefixo de `norm`.
    corte = len(_normalize(raw[:ultima_pergunta])) if ultima_pergunta >= 0 else -1
    ultimo_marcador = max((m.start() for m in _MARCADOR_COMPARACAO_RE.finditer(norm)), default=-1)
    verbos = [m.start() for m in _VERBO_LANC_RE.finditer(norm)]
    for k, pos in enumerate(verbos):
        if _comparativa_em(norm, pos, pos < corte, pos <= ultimo_marcador):
            brutos = [m.start() for m in _VERBO_LANC_RE.finditer(text)]
            # Verbo que o `_normalize` achou e o original não (ex.: largura total): sem
            # como cortar com segurança, a mensagem inteira é pergunta.
            return brutos[k] if len(brutos) == len(verbos) else 0
    return None


def sem_perguntas_comparativas(text: str) -> tuple[str, list[str]]:
    """(texto sem os pedaços comparativos, pedaços tirados). Sem pergunta, o
    texto volta intacto; só pergunta, volta "". Quem lê o valor da mensagem
    inteira (conta, compra no crédito, fatura) lê deste texto: o 2025 de "paguei
    a luz e gastei mais em 2025 ou 2026?" é da pergunta (#568).

    A pergunta começa no 1º verbo de lançamento que a abre (`_inicio_da_pergunta`,
    vale ".", ";", "tb" e fala sem conector): o que vem ANTES é o pedaço legítimo
    e o que vem DEPOIS sai junto. Pedaço do split sem verbo próprio ("... e mais
    que o normal no bar?") herda o do anterior e só o split o acha.

    Quem lê o valor do texto passa por aqui: o `add()` com UM pedaço legítimo (D4
    revista), a conta, o crédito e a fatura. Com 2+ lançamentos o `add()` ainda
    separa só pelo split (#693), e verbo acentuado devolve ("", [texto]) e cai no
    fluxo antigo (#699): os dois no `ponytail:` acima.

    Única detecção das portas e dos guardas da conta: `contains_comparative_question`
    é `bool(puladas)` (§0.7). O `is_comparative_question` segue decidindo no
    `classify`, nos laços do multi e no fluxo cartão da forma de pagamento."""
    from parsers import split_financial_transactions  # local: parsers importa daqui
    t = text or ""
    pos = _inicio_da_pergunta(t)
    if pos is not None:
        cabeca = t[:pos]
        if not _PREFIXO_RE.sub("", _normalize(cabeca) + " "):  # "oi gastei mais...": só prefixo antes
            return "", [t]
        return _sem_conector_final(cabeca), [t[pos:]]
    partes = split_financial_transactions(t)
    puladas = [p for p in partes if is_comparative_question(p)]
    if puladas:
        return " e ".join(p for p in partes if p not in puladas), puladas
    # Último recurso: o "?" ANTES do verbo ("oi? gastei mais 30%") só `is_comparative_question` aceita.
    return ("", [t]) if is_comparative_question(t) else (text, [])


def contains_comparative_question(text: str) -> bool:
    """True se `sem_perguntas_comparativas` tira algo: a pergunta no texto inteiro,
    em algum pedaço do split OU a partir de algum verbo. As portas da pergunta de
    valor usam esta: em "paguei a luz e gastei mais em 2025 ou 2026?" o texto
    inteiro não é comparativo e o valor extraído é 2025."""
    return bool(sem_perguntas_comparativas(text)[1])


# ---------------------------------------------------------------------------
# Tier 1 — busca exata
# ---------------------------------------------------------------------------

def _try_exact(norm: str) -> IntentResult | None:
    intent = _EXACT.get(norm)
    if intent:
        return IntentResult(intent=intent, confidence=1.0)
    return None


# ---------------------------------------------------------------------------
# Tier 2 — regex
# ---------------------------------------------------------------------------

def _extract_date_entity(norm: str) -> str | None:
    """Extrai 'hoje', 'ontem' ou data do texto normalizado."""
    if re.search(r"\bhoje\b", norm):
        return "hoje"
    if re.search(r"\bontem\b", norm):
        return "ontem"
    m = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})(?:[\/\-](\d{2,4}))?\b", norm)
    if m:
        return m.group(0)
    # "dia 4", "dia 03"
    m = re.search(r"\bdia\s+(\d{1,2})\b", norm)
    if m:
        return f"dia {m.group(1)}"
    return None


# Toggles de resumo (ligar/desligar semanal e mensal). Pergunta com '?' nunca liga
# nem desliga: _normalize tira o '?', então o texto original é que decide.
_RESUMO_TOGGLES = frozenset({
    "report.weekly_enable", "report.weekly_disable",
    "report.monthly_enable", "report.monthly_disable",
})


def _try_alias(norm: str, original: str) -> IntentResult | None:
    for pattern, intent in _ALIAS_PATTERNS:
        # Pergunta sobre ligar/desligar não é pedido: _normalize tira o '?', então
        # quem decide é o texto original.
        if "?" in original and intent in _RESUMO_TOGGLES:
            continue
        if re.search(pattern, norm):
            entities: dict[str, Any] = {}

            # extrai data para consultas de lançamentos
            if intent == "launches.list":
                date_ent = _extract_date_entity(norm)
                if date_ent:
                    entities["date_filter"] = date_ent

            # extrai ID para deletes
            elif intent == "launches.delete":
                launch_id = _extract_id_from_text(norm)
                if launch_id:
                    entities["launch_id"] = launch_id

            elif intent == "launches.delete_bulk":
                ids = [int(x) for x in re.findall(r"\d+", norm)]
                if ids:
                    entities["launch_ids"] = ids

            elif intent == "pockets.create":
                m = re.search(r"^(?:criar|cria|crie|nova|novo|abrir|abre|abra)\s+caixinha\s+(.+)$", norm)
                if m:
                    entities["name"] = m.group(1).strip()

            elif intent == "pockets.delete":
                m = re.search(r"^(?:excluir|exclua|deletar|delete|apagar|apaga|apague|remover|remove|remova)\s+caixinha\s+(.+)$", norm)
                if m:
                    entities["pocket_name"] = m.group(1).strip()

            # Sem este ramo o Tier 2 reconhecia o aporte com 0.95 e devolvia
            # `entities={}` — o handler caía em "em qual investimento?" mesmo com o
            # nome escrito na frase, e a confiança alta impedia a IA (Tier 3) de ver
            # a mensagem. Medido: nenhum aporte determinístico completava.
            elif intent == "investments.deposit":
                from utils_text import parse_investment_deposit_natural
                _amt, _nome = parse_investment_deposit_natural(original)
                if _amt is not None:
                    entities["amount"] = _amt
                if _nome:
                    entities["investment_name"] = _nome

            elif intent == "investments.create":
                m = re.search(r"^criar\s+investimento\s+(.+)$", norm)
                if m:
                    entities["raw_name"] = m.group(1).strip()

            elif intent == "investments.delete":
                m = re.search(r"^(?:excluir|deletar)\s+investimento\s+(.+)$", norm)
                if m:
                    entities["investment_name"] = m.group(1).strip()

            elif intent == "funds.withdraw":
                amount = _extract_amount_from_text(original)
                target_name = _extract_source_target_name(original)
                if amount:
                    entities["amount"] = amount
                if target_name:
                    entities["target_name"] = target_name
                if "caixinha" in norm:
                    entities["target_kind"] = "pocket"
                elif "investimento" in norm:
                    entities["target_kind"] = "investment"

            elif intent == "report.set_hour":
                # tenta "20h", "20h30", "20:30", "8 30" etc.
                mh = re.search(r"\b(\d{1,2})h(\d{2})?\b", norm)
                if not mh:
                    mh = re.search(r"\b(\d{1,2})[:\s](\d{2})\b", norm)
                if mh:
                    entities["hour"]   = int(mh.group(1))
                    entities["minute"] = int(mh.group(2)) if mh.group(2) else 0

            elif intent == "account.link":
                m = re.search(r"link\s+(\d{6})", norm)
                if m:
                    entities["code"] = m.group(1)

            elif intent == "account.vincular":
                m = re.search(r"vincular\s+(\d{6})", norm)
                if m:
                    entities["code"] = m.group(1)

            return IntentResult(intent=intent, confidence=0.95, entities=entities)

    return None


# ---------------------------------------------------------------------------
# Tier 3 — IA (GPT com temperatura 0, saída JSON forçada)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Você é um classificador de intenções para um bot financeiro pessoal.

REGRAS ABSOLUTAS:
1. Retorne SOMENTE o JSON. Nenhum texto antes ou depois.
2. Nunca invente saldos, nomes ou valores.
3. Use apenas intents do catálogo abaixo.
4. Se não souber com segurança, use "out_of_scope".
5. Se faltar informação essencial para executar, ative needs_clarification.
6. confidence deve refletir sua certeza real.
7. NÃO confunda recorrente com lançamento avulso: "gastei 50 no mercado" = launches.add (uma vez); "gasto fixo de 100 todo dia 10" / "salário todo dia 5" = recurring.add (todo mês). Em recurring.add, se o usuário NÃO disser DO QUE é (nome/descrição), ative needs_clarification perguntando do que é o gasto/receita.
8. Só o pedido de LIGAR/DESLIGAR o envio AUTOMÁTICO e recorrente do resumo (semanal ou mensal) é report.*_enable/_disable, mesmo com dia ou "todo mês": "quero receber o resumo mensal todo dia 1" = report.monthly_enable, NUNCA recurring.add. Pedido PONTUAL de um resumo com data ("resumo mensal de setembro de 2026", "quero o resumo de setembro") é CONSULTA: report.monthly ou report.weekly, nunca enable/disable. Pergunta sobre o recebimento ("quando vou receber o resumo mensal?", "como faço para receber o resumo semanal?") NÃO é pedido de ligar/desligar: é consulta (report.weekly / report.monthly) ou out_of_scope.

CATÁLOGO DE INTENTS:
- balance.check        → usuário quer saber o saldo da conta
- launches.list        → quer listar lançamentos/histórico (entities: limit?, date_filter? ex: "hoje","ontem","2026-04-03")
- launches.spend_query → quer saber QUANTO gastou (um total, não a lista) num período e/ou categoria. Ex: "quanto gastei na categoria outros esta semana", "quanto gasto em julho", "quanto gastei ontem". O handler re-lê o texto pra extrair período e categoria — não precisa de entities.
- launches.add         → quer registrar receita ou despesa
- launches.delete      → quer apagar um lançamento (entities: launch_id)
- launches.undo        → quer desfazer o último lançamento
- recurring.add        → quer CADASTRAR um gasto/receita RECORRENTE (fixo, todo mês OU todo ano). Sinais: "recorrente", "todo dia N", "todo mês", "mensal(mente)", "gasto fixo", "assinatura", "salário todo dia 5"; ANUAL: "todo ano", "por ano", "anual(mente)", "1x por ano", "todo ano em <mês>". TAMBÉM cobre CONTA A PAGAR / BOLETO (o usuário paga na mão, não é débito automático): sinais "boleto", "conta a pagar", "me lembra de pagar", "lembrete" → nesses casos entities.pagamento="manual". entities: tipo("despesa"|"receita"), valor, dia(1-31, dia do vencimento/recebimento), nome(do que é, se disser), categoria, inicio("10/09" — se disser), frequencia("mensal"|"anual", default "mensal"), mes(1-12, só se anual — o mês do vencimento; ex: "todo ano em setembro" → mes=9), pagamento("manual" só se for boleto/conta a pagar/lembrete; senão omita), valor_variavel(true só se o usuário disser que o valor muda todo mês — água/luz/gás, "valor varia", "não é fixo")
- credit.handle        → quer criar/listar/consultar cartão, fatura, crédito ou parcelamento
- pockets.list         → quer listar caixinhas
- pockets.create       → quer criar caixinha (entities: name)
- pockets.deposit      → quer depositar em caixinha (entities: pocket_name, amount)
- pockets.withdraw     → quer sacar de caixinha (entities: pocket_name, amount)
- pockets.delete       → quer apagar caixinha (entities: pocket_name)
- investments.list     → quer listar investimentos
- investments.create   → quer abrir o dashboard para criar investimento (entities: raw_name)
- investments.deposit  → quer aportar em investimento (entities: investment_name, amount)
- investments.withdraw → quer resgatar investimento (entities: investment_name, amount)
- investments.delete   → quer apagar investimento (entities: investment_name)
- categories.list      → quer ver categorias
- categories.create    → quer criar regra de categoria (entities: keyword, category_name)
- categories.delete    → quer remover regra (entities: keyword)
- report.daily         → quer o resumo/relatório do dia
- report.weekly        → quer o resumo/relatório da semana (gastos da semana)
- report.monthly       → quer o resumo/relatório do mês (gastos do mês)
- report.weekly_enable → quer ativar o resumo semanal automático
- report.weekly_disable→ quer desativar o resumo semanal automático
- report.monthly_enable→ quer ativar o resumo mensal automático
- report.monthly_disable→ quer desativar o resumo mensal automático
- report.enable        → quer ativar relatório diário (sem especificar horário)
- report.set_hour      → quer ativar relatório diário com horário específico (entities: hour, minute)
- report.disable       → quer desativar relatório diário
- dashboard.open       → quer acessar o dashboard
- account.link         → quer vincular plataformas (entities: code?)
- account.vincular     → quer vincular conta web (entities: code)
- help                 → quer ajuda
- confirm.yes          → confirmando uma ação pendente
- confirm.no           → cancelando uma ação pendente
- out_of_scope         → pedido fora do escopo financeiro

PARA launches.add, extraia as entities:
  tipo: "despesa" ou "receita"
  valor: número
  alvo: estabelecimento/destinatário (se mencionado)
  categoria: alimentação|mercado|transporte|saúde|moradia|lazer|educação|assinaturas|pets|compras online|beleza|outros
  (mercado = compra de supermercado/limpeza/higiene; alimentação = comer fora, delivery, padaria)

FORMATO OBRIGATÓRIO (JSON puro, sem markdown):
{"intent":"<intent>","confidence":<0.0-1.0>,"entities":{...},"needs_clarification":<true|false>,"clarification_question":<"pergunta" ou null>}

EXEMPLOS:
"qual meu saldo?" → {"intent":"balance.check","confidence":0.99,"entities":{},"needs_clarification":false,"clarification_question":null}
"gastei 50 no mercado" → {"intent":"launches.add","confidence":0.97,"entities":{"tipo":"despesa","valor":50,"alvo":"mercado","categoria":"mercado"},"needs_clarification":false,"clarification_question":null}
"deposita 200 na caixinha viagem" → {"intent":"pockets.deposit","confidence":0.97,"entities":{"pocket_name":"viagem","amount":200},"needs_clarification":false,"clarification_question":null}
"quais cartoes tenho registrado?" → {"intent":"credit.handle","confidence":0.96,"entities":{},"needs_clarification":false,"clarification_question":null}
"quais sao meus cartoes?" → {"intent":"credit.handle","confidence":0.96,"entities":{},"needs_clarification":false,"clarification_question":null}
"quero cadastrar um cartao" → {"intent":"credit.handle","confidence":0.95,"entities":{},"needs_clarification":false,"clarification_question":null}
"me mostra minhas faturas" → {"intent":"credit.handle","confidence":0.96,"entities":{},"needs_clarification":false,"clarification_question":null}
"quanto tenho na fatura do nubank?" → {"intent":"credit.handle","confidence":0.95,"entities":{},"needs_clarification":false,"clarification_question":null}
"meu nubank vence quando?" → {"intent":"credit.handle","confidence":0.95,"entities":{},"needs_clarification":false,"clarification_question":null}
"meu visa vence quando?" → {"intent":"credit.handle","confidence":0.94,"entities":{},"needs_clarification":false,"clarification_question":null}
"qual meu cartao principal?" → {"intent":"credit.handle","confidence":0.95,"entities":{},"needs_clarification":false,"clarification_question":null}
"quero mudar meu cartao principal" → {"intent":"credit.handle","confidence":0.94,"entities":{},"needs_clarification":false,"clarification_question":null}
"me recomenda uma ação da bolsa" → {"intent":"out_of_scope","confidence":0.98,"entities":{},"needs_clarification":false,"clarification_question":null}
"gastei cinquenta" → {"intent":"launches.add","confidence":0.72,"entities":{"tipo":"despesa","valor":50},"needs_clarification":true,"clarification_question":"Em que você gastou R$ 50?"}
"gasto recorrente de 100 reais todo dia 10 comecando a partir do dia 10/09" → {"intent":"recurring.add","confidence":0.95,"entities":{"tipo":"despesa","valor":100,"dia":10,"inicio":"10/09"},"needs_clarification":true,"clarification_question":"Do que é esse gasto fixo? (ex: aluguel, academia)"}
"aluguel de 1500 todo dia 5" → {"intent":"recurring.add","confidence":0.96,"entities":{"tipo":"despesa","valor":1500,"dia":5,"nome":"aluguel","categoria":"moradia"},"needs_clarification":false,"clarification_question":null}
"netflix 44,90 todo mes dia 9" → {"intent":"recurring.add","confidence":0.95,"entities":{"tipo":"despesa","valor":44.90,"dia":9,"nome":"netflix","categoria":"assinaturas"},"needs_clarification":false,"clarification_question":null}
"recebo meu salario de 3000 todo dia 5" → {"intent":"recurring.add","confidence":0.96,"entities":{"tipo":"receita","valor":3000,"dia":5,"nome":"salário","categoria":"salário"},"needs_clarification":false,"clarification_question":null}
"dominio do site 60 reais todo ano em setembro dia 15" → {"intent":"recurring.add","confidence":0.95,"entities":{"tipo":"despesa","valor":60,"dia":15,"nome":"domínio","categoria":"assinaturas","frequencia":"anual","mes":9}}
"ipva de 1200 uma vez por ano em janeiro" → {"intent":"recurring.add","confidence":0.94,"entities":{"tipo":"despesa","valor":1200,"dia":1,"nome":"IPVA","categoria":"transporte","frequencia":"anual","mes":1}}
"recebo 5000 de bonus todo ano em dezembro dia 20" → {"intent":"recurring.add","confidence":0.95,"entities":{"tipo":"receita","valor":5000,"dia":20,"nome":"bônus","categoria":"bônus","frequencia":"anual","mes":12}}
"boleto da luz de 150 todo mes dia 10" → {"intent":"recurring.add","confidence":0.95,"entities":{"tipo":"despesa","valor":150,"dia":10,"nome":"luz","categoria":"moradia","pagamento":"manual"}}
"me lembra de pagar o condominio dia 5, 800 reais" → {"intent":"recurring.add","confidence":0.93,"entities":{"tipo":"despesa","valor":800,"dia":5,"nome":"condomínio","categoria":"moradia","pagamento":"manual"}}
"conta de agua todo mes dia 10, uns 80 mas varia" → {"intent":"recurring.add","confidence":0.92,"entities":{"tipo":"despesa","valor":80,"dia":10,"nome":"água","categoria":"moradia","pagamento":"manual","valor_variavel":true}}
"quanto gastei hoje?" → {"intent":"launches.list","confidence":0.96,"entities":{"date_filter":"hoje"},"needs_clarification":false,"clarification_question":null}
"tive algum gasto ontem?" → {"intent":"launches.list","confidence":0.95,"entities":{"date_filter":"ontem"},"needs_clarification":false,"clarification_question":null}
"gastos do dia 4" → {"intent":"launches.list","confidence":0.90,"entities":{"date_filter":"dia 4"},"needs_clarification":true,"clarification_question":"Você gostaria de ver os gastos do dia 4 de qual mês?"}
"""


def _classify_llm_call(user_content: str, user_id: int | None) -> IntentResult:
    """Núcleo da classificação via IA (Tier 3): aplica gate pago + rate limit e
    chama o LLM com `_SYSTEM_PROMPT`. `user_content` é a mensagem de usuário
    (pode ser o texto cru OU um bloco de contexto de esclarecimento)."""
    from core.ai_rate_limiter import is_allowed

    # A classificação também produz registros básicos, inclusive recorrentes.
    # Essencial+ no v2; o gate preserva Pro no modo legado.
    if user_id is not None:
        try:
            from core.services.plan_service import plan_gate_ok
            if not plan_gate_ok(int(user_id), "generic"):
                return IntentResult(intent="out_of_scope", confidence=0.0)
        except Exception:
            logger.warning(
                "gate pago do tier-3 falhou pro user %s — classificação indisponível",
                user_id, exc_info=True,
            )
            return IntentResult(intent="out_of_scope", confidence=0.0)

    if user_id is not None and not is_allowed(user_id):
        print(f"[intent_classifier] rate limit atingido para user_id={user_id}")
        return IntentResult(intent="out_of_scope", confidence=0.0)

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return IntentResult(intent="out_of_scope", confidence=0.0)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )

        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)

        return IntentResult(
            intent=data.get("intent", "out_of_scope"),
            confidence=float(data.get("confidence", 0.0)),
            entities=data.get("entities") or {},
            needs_clarification=bool(data.get("needs_clarification", False)),
            clarification_question=data.get("clarification_question"),
        )

    except Exception as e:
        print(f"[intent_classifier] AI error: {e}")
        return IntentResult(intent="out_of_scope", confidence=0.0)


def _classify_with_ai(text: str, user_id: int | None = None) -> IntentResult:
    result = _classify_llm_call(text, user_id)
    # Pergunta nunca liga/desliga resumo, nem quando a IA devolve o toggle.
    if "?" in text and result.intent in _RESUMO_TOGGLES:
        return IntentResult(intent="out_of_scope", confidence=0.0)
    return result


def classify_with_context(
    orig_text: str,
    question: str,
    answer: str,
    user_id: int | None = None,
) -> IntentResult:
    """Reclassifica a resposta de um esclarecimento COM contexto.

    Junta [mensagem original + pergunta que o bot fez + resposta do usuário] e
    pede ao LLM a intenção COMPLETA já com as entities preenchidas — em vez de
    tentar remendar a resposta crua com regras. Funciona pra qualquer intent.
    Degrada como `_classify_with_ai` (out_of_scope 0.0) se IA indisponível.
    """
    content = (
        "CONTEXTO DE ESCLARECIMENTO — o bot fez uma pergunta e o usuário respondeu. "
        "Junte as três partes numa ÚNICA intenção completa e executável.\n\n"
        f'Mensagem original do usuário: "{orig_text}"\n'
        f'Pergunta que o bot fez: "{question}"\n'
        f'Resposta do usuário agora: "{answer}"\n\n'
        "A resposta preenche a informação que faltava. Retorne a intent final com TODAS as "
        "entities preenchidas e needs_clarification=false — a não ser que a resposta seja "
        "incompreensível ou o usuário tenha mudado de assunto (aí classifique a nova mensagem "
        "normalmente). Se o usuário claramente desistiu/cancelou, use out_of_scope."
    )
    return _classify_llm_call(content, user_id)


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def classify(text: str, user_id: int | None = None, *, allow_ai: bool = True) -> IntentResult:
    """
    Classifica a intenção do texto em 3 tiers.
    Retorna IntentResult com intent, confidence, entities, etc.

    user_id: quando fornecido, aplica rate limiting no Tier 3 (chamada à IA).
    allow_ai=False: só os tiers 1 e 2 (determinísticos). No lugar do tier 3 sai
        o mesmo `out_of_scope 0.0` com que ele já degrada hoje quando não há
        `OPENAI_API_KEY` e quando o usuário não é Pro. Tem um único chamador em
        produção — o `abandona_pergunta_de_valor`
        (`core/intent_router.py`), o passo 1 das portas 2, 3 e 4 da pergunta de
        valor, que precisa saber se a resposta é OUTRO comando: com o tier 3 no
        meio, uma alucinação do LLM viraria abandono da pergunta e a fila do
        usuário seria apagada.

        Com o default `True` os DOIS `if not allow_ai` abaixo são inalcançáveis,
        então nenhum chamador existente muda de comportamento. (O
        `_is_boleto_ai_query` e o `is_comparative_question` continuam vindo
        antes dos dois: "132 no boleto" e "gastei mais em 2025 ou 2026?" saem
        `out_of_scope 0.4` mesmo com `allow_ai=False`.)
    """
    norm = _normalize(text)

    # Boletos / contas a pagar / prazo → IA conversacional (é lá que estão as
    # tools). Retorna out_of_scope pra cair no fallback de IA (handle_incoming),
    # em vez de o determinístico tratar como launches.list por causa da data.
    if _is_boleto_ai_query(norm):
        return IntentResult(intent="out_of_scope", confidence=0.4)

    # Pergunta comparativa ("gastei mais esse mês que no passado?") → IA. Antes
    # da recorrência: "recebi mais que 5 mil todo mes?" não é recurring.add.
    if is_comparative_question(text):
        return IntentResult(intent="out_of_scope", confidence=0.4)

    # Recorrência (valor + "todo mes"/"mensal"/"recorrente"/"gasto fixo"/"anual"…)
    # vai DIRETO pra IA (Tier 3), que classifica recurring.add. Precede os atalhos
    # de Tier 1/2 e o domain-hint, que senão mandariam pra launches.add ou
    # out_of_scope (→ IA conversacional criava orçamento por engano).
    if _has_recurrence_marker(norm) or _has_bill_marker(norm):
        if not allow_ai:
            return IntentResult(intent="out_of_scope", confidence=0.0)
        return _classify_with_ai(text, user_id=user_id)

    # Tier 1
    result = _try_exact(norm)
    if result and not ("?" in text and result.intent in _RESUMO_TOGGLES):
        return result

    # Tier 2
    result = _try_alias(norm, text)
    if result:
        return result

    if _contains_domain_hint(norm):
        return IntentResult(intent="out_of_scope", confidence=0.4)

    # Tier 3
    if not allow_ai:
        return IntentResult(intent="out_of_scope", confidence=0.0)
    return _classify_with_ai(text, user_id=user_id)
