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
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


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
    # saldo
    "saldo":                    "balance.check",
    "saldo conta":              "balance.check",
    "saldo da conta":           "balance.check",
    "conta":                    "balance.check",
    "saldo geral":              "balance.check",
    "quanto tenho":             "balance.check",
    "quanto tem":               "balance.check",
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
    # toggle report
    "ligar report diario":      "report.enable",
    "ativar report diario":     "report.enable",
    "voltar report diario":     "report.enable",
    "desligar report diario":   "report.disable",
    "desativar report diario":  "report.disable",
    "parar report diario":      "report.disable",
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
    "quais sao meus cartoes":   "credit.handle",
    "qual meu cartao principal":"credit.handle",
    "meu cartao principal":     "credit.handle",
    "fatura":                   "credit.handle",
    "faturas":                  "credit.handle",
    "listar faturas":           "credit.handle",
    "minhas faturas":           "credit.handle",
    "parcelamentos":            "credit.handle",
    "listar parcelamentos":     "credit.handle",
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
    # confirmações
    "sim":                      "confirm.yes",
    "s":                        "confirm.yes",
    "confirmar":                "confirm.yes",
    "nao":                      "confirm.no",
    "nope":                     "confirm.no",
    "cancelar":                 "confirm.no",
    # desfazer
    "desfazer":                 "launches.undo",
}

# ---------------------------------------------------------------------------
# Tier 2 — Regex / alias (normalizado)
# ---------------------------------------------------------------------------

_ALIAS_PATTERNS: list[tuple[str, str]] = [
    # saldo
    (r"^(quanto tenho na conta|quanto tem na conta|meu saldo|qual meu saldo|ver saldo"
     r"|me fala (o )?meu saldo|me fala o saldo|me diz (o )?saldo|qual (e )?meu saldo|ver meu saldo"
     r"|quero saber (o )?saldo|quanto (tem|tenho) na (minha )?conta)$",
     "balance.check"),

    # lançamentos — com data (hoje/ontem)
    (r"\b(lancamentos?|gastos?|despesas?|receitas?|historico|extrato)\b.*(hoje|ontem)",
     "launches.list"),
    (r"\b(hoje|ontem)\b.*(lancamentos?|gastos?|despesas?|receitas?)",
     "launches.list"),
    (r"^(quanto|algum|tive|tivemos?|houve)\s.*(gastei|gastou|gasto|gasta|despesa|despesas|lancamentos?)\b",
     "launches.list"),
    # lançamentos / gastos — perguntas naturais sem data
    (r"^(ver|mostrar|mostra|listar)\s+(meus\s+)?(lancamentos?|gastos?|despesas?|extrato)(\s+recentes?)?$",
     "launches.list"),
    (r"^(quais|qual)\s+(sao|foram|e|foi)?\s*(meus|os|minhas|as)?\s*(gastos?|despesas?|lancamentos?|ultimos?)\b",
     "launches.list"),
    (r"^(me\s+)?(mostra|mostre|mostrar|ve|ver|lista|liste)\s+(meus\s+|os\s+|minhas\s+|as\s+)?(gastos?|despesas?|lancamentos?|extrato|historico)\b",
     "launches.list"),
    (r"^(o\s+que|quanto)\s+(gastei|gastos?|despesas?|lancamentos?)\b",
     "launches.list"),
    (r"^(gastos?|despesas?)\s+(recentes?|ultimos?|da\s+semana|do\s+mes)?\b",
     "launches.list"),
    (r"^apagar\s+(?:id\s+)?(lancamento\s+)?#?(\d+)$",
     "launches.delete"),
    (r"^excluir\s+(?:id\s+)?(lancamento\s+)?#?(\d+)$",
     "launches.delete"),
    (r"^deletar\s+(?:id\s+)?(lancamento\s+)?#?(\d+)$",
     "launches.delete"),
    (r"^apagar\s+(?:id\s+)?#?(\d+)$",
     "launches.delete"),

    # desfazer / apagar compras no crédito
    (r"^(desfazer|apagar|excluir|remover|deletar|delete)\b.*(?:\bcc\s*\d+\b|\bpc[0-9a-f]{8}\b|\bct\s*#?\s*\d+\b|\bgrupo\b|\bgroup\b|\bcompra\b|\bcredito\b|\bcr[eé]dito\b|\bparcelamento\b|\bparcela\b)",
     "credit.handle"),

    # compra no crédito em linguagem natural
    (r"^(gastei|paguei|comprei|debitei|gasto)\b.*\b(cartao|credito)\b",
     "credit.handle"),

    # despesa / receita — detecta padrão sem chamar IA
    (r"^(gastei|paguei|comprei|debitei|gasto|mandei|enviei|pixei)\b",
     "launches.add"),
    (r"^(recebi|ganhei|entrou|caiu)\b",
     "launches.add"),
    (r"^(hoje|ontem|\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?|dia\s+\d{1,2}(?:[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?)?)\b.*\b(gastei|paguei|comprei|debitei|gasto|mandei|enviei|pixei|recebi|ganhei|entrou|caiu)\b",
     "launches.add"),

    # cartões / crédito
    (r"^(cartoes|cartoes de credito|listar cartoes|meus cartoes|quais cartoes|quais sao meus cartoes|criar cartao|padrao\b|credito\b|parcelar\b|parcelei\b|fatura\b|faturas\b|pagar fatura\b|paguei fatura\b|parcelamentos\b|minhas faturas|me mostra minhas faturas|qual meu cartao principal|meu cartao principal|trocar cartao principal|mudar cartao principal|definir limite|limite cartao|limite do cartao|pagar fatura com saldo)",
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
    (r"^criar\s+caixinha\s+(.+)$",
     "pockets.create"),
    (r"^excluir\s+caixinha\s+(.+)$",
     "pockets.delete"),
    (r"^deletar\s+caixinha\s+(.+)$",
     "pockets.delete"),
    (r"^(coloquei|adicionei|depositei|transferi|pus|botei)\s+\d",
     "pockets.deposit"),
    (r"^(retirei|saquei|tirei)\s+\d",
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

    # categorias
    (r"^criar\s+categoria\s+",
     "categories.create"),
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

    # vinculação de contas
    (r"^link(\s+\d{6})?$",
     "account.link"),
    (r"^vincular\s+\d{6}$",
     "account.vincular"),

    # ajuda com seção
    (r"^(ajuda|help)\s+\w+",
     "help"),

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


def _try_alias(norm: str, original: str) -> IntentResult | None:
    for pattern, intent in _ALIAS_PATTERNS:
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

            elif intent == "pockets.create":
                m = re.search(r"^criar\s+caixinha\s+(.+)$", norm)
                if m:
                    entities["name"] = m.group(1).strip()

            elif intent == "pockets.delete":
                m = re.search(r"^(?:excluir|deletar)\s+caixinha\s+(.+)$", norm)
                if m:
                    entities["pocket_name"] = m.group(1).strip()

            elif intent == "investments.create":
                m = re.search(r"^criar\s+investimento\s+(.+)$", norm)
                if m:
                    entities["raw_name"] = m.group(1).strip()

            elif intent == "investments.delete":
                m = re.search(r"^(?:excluir|deletar)\s+investimento\s+(.+)$", norm)
                if m:
                    entities["investment_name"] = m.group(1).strip()

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

CATÁLOGO DE INTENTS:
- balance.check        → usuário quer saber o saldo da conta
- launches.list        → quer listar lançamentos/histórico (entities: limit?, date_filter? ex: "hoje","ontem","2026-04-03")
- launches.add         → quer registrar receita ou despesa
- launches.delete      → quer apagar um lançamento (entities: launch_id)
- launches.undo        → quer desfazer o último lançamento
- credit.handle        → quer criar/listar/consultar cartão, fatura, crédito ou parcelamento
- pockets.list         → quer listar caixinhas
- pockets.create       → quer criar caixinha (entities: name)
- pockets.deposit      → quer depositar em caixinha (entities: pocket_name, amount)
- pockets.withdraw     → quer sacar de caixinha (entities: pocket_name, amount)
- pockets.delete       → quer apagar caixinha (entities: pocket_name)
- investments.list     → quer listar investimentos
- investments.create   → quer criar investimento (entities: raw_name)
- investments.deposit  → quer aportar em investimento (entities: investment_name, amount)
- investments.withdraw → quer resgatar investimento (entities: investment_name, amount)
- investments.delete   → quer apagar investimento (entities: investment_name)
- categories.list      → quer ver categorias
- categories.create    → quer criar regra de categoria (entities: keyword, category_name)
- categories.delete    → quer remover regra (entities: keyword)
- report.daily         → quer o resumo/relatório do dia
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
  categoria: alimentação|transporte|saúde|moradia|lazer|educação|assinaturas|pets|compras online|beleza|outros

FORMATO OBRIGATÓRIO (JSON puro, sem markdown):
{"intent":"<intent>","confidence":<0.0-1.0>,"entities":{...},"needs_clarification":<true|false>,"clarification_question":<"pergunta" ou null>}

EXEMPLOS:
"qual meu saldo?" → {"intent":"balance.check","confidence":0.99,"entities":{},"needs_clarification":false,"clarification_question":null}
"gastei 50 no mercado" → {"intent":"launches.add","confidence":0.97,"entities":{"tipo":"despesa","valor":50,"alvo":"mercado","categoria":"alimentação"},"needs_clarification":false,"clarification_question":null}
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
"quanto gastei hoje?" → {"intent":"launches.list","confidence":0.96,"entities":{"date_filter":"hoje"},"needs_clarification":false,"clarification_question":null}
"tive algum gasto ontem?" → {"intent":"launches.list","confidence":0.95,"entities":{"date_filter":"ontem"},"needs_clarification":false,"clarification_question":null}
"gastos do dia 4" → {"intent":"launches.list","confidence":0.90,"entities":{"date_filter":"dia 4"},"needs_clarification":true,"clarification_question":"Você gostaria de ver os gastos do dia 4 de qual mês?"}
"""


def _classify_with_ai(text: str) -> IntentResult:
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
                {"role": "user", "content": text},
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


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def classify(text: str) -> IntentResult:
    """
    Classifica a intenção do texto em 3 tiers.
    Retorna IntentResult com intent, confidence, entities, etc.
    """
    norm = _normalize(text)

    # Tier 1
    result = _try_exact(norm)
    if result:
        return result

    # Tier 2
    result = _try_alias(norm, text)
    if result:
        return result

    # Tier 3
    return _classify_with_ai(text)
