# Arquitetura de IA para Bot Financeiro Multicanal

> Proposta técnica completa — do zero, pensada para produção.
> Baseada na análise do código atual (`ai_router.py` + `handle_incoming.py`).

---

## 1. Diagnóstico do Código Atual

### O que existe hoje

```
Mensagem recebida
    → handle_incoming.py
        → if t_low == "saldo" ...         (regras fixas hardcoded)
        → elif t_low.startswith(...) ...   (mais regras)
        → handle_quick_entry(...)          (parser regex)
        → handle_ai_message(...)           (GPT — só WhatsApp, só como fallback)
```

### Problemas identificados

1. **A IA é um fallback de último recurso**, não o núcleo de classificação.
2. **Não há score de confiança** — o sistema não sabe se entendeu ou está chutando.
3. **`handle_incoming` é uma cadeia gigante de `if/elif`** — difícil de manter e expandir.
4. **O fallback de IA só funciona no WhatsApp** (linha 391: `if msg.platform == "whatsapp"`).
5. **Não há separação entre classificar intenção e executar ação**.
6. **A formatação de resposta está misturada com lógica de negócio** em `ai_router.py`.
7. **Não existe saída estruturada da IA** — ela escolhe funções direto, sem camada intermediária.

---

## 2. Arquitetura Proposta: Visão Geral

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CAMADA DE CANAL                             │
│   WhatsApp Adapter │ Discord Adapter │ Telegram Adapter (futuro)    │
│   (normaliza input, formata output para cada canal)                 │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ IncomingMessage (estrutura unificada)
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    CAMADA DE INTERPRETAÇÃO                          │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │  IntentClassifier (Híbrido: Regras → IA → Fallback)        │   │
│   │                                                             │   │
│   │  Tier 1: Exact match  (custo zero, velocidade máxima)      │   │
│   │  Tier 2: Alias/regex  (custo zero, cobre variações)        │   │
│   │  Tier 3: AI classify  (LLM — só quando tiers 1/2 falham)   │   │
│   │                                                             │   │
│   │  Saída → IntentResult { intent, confidence, entities,      │   │
│   │                          needs_clarification, question }   │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ IntentResult
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   CAMADA DE DECISÃO (Router)                        │
│                                                                     │
│   confidence >= 0.85 → executa direto                               │
│   confidence 0.60–0.84 → pede confirmação (se ação destrutiva)      │
│   needs_clarification → faz 1 pergunta                              │
│   out_of_scope → resposta padrão                                    │
│   confidence < 0.60 → "Não entendi, pode reformular?"               │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ Intent confirmado
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  CAMADA DE NEGÓCIO (Handlers)                       │
│                                                                     │
│   balance_handler │ launch_handler │ pocket_handler │ ...           │
│   Cada handler recebe entidades extraídas e chama o serviço certo   │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ Dados reais do sistema
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  CAMADA DE DADOS (já existe)                        │
│                                                                     │
│   db.py — get_balance, list_launches, add_launch, etc.              │
└─────────────────────────────┬───────────────────────────────────────┘
                              │ Resultado
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 CAMADA DE RESPOSTA (ResponseFormatter)              │
│                                                                     │
│   Formata o resultado para o canal certo (bold, emojis, limites)    │
│   Nunca inventa texto — só formata dados reais                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Saída Estruturada da IA (IntentResult)

O classificador de IA deve retornar **sempre este JSON**, sem texto livre:

```json
{
  "intent": "balance.check",
  "confidence": 0.97,
  "entities": {},
  "needs_clarification": false,
  "clarification_question": null
}
```

```json
{
  "intent": "launches.add",
  "confidence": 0.91,
  "entities": {
    "tipo": "despesa",
    "valor": 45.90,
    "alvo": "iFood",
    "categoria": "alimentação"
  },
  "needs_clarification": false,
  "clarification_question": null
}
```

```json
{
  "intent": "pockets.deposit",
  "confidence": 0.78,
  "entities": {
    "pocket_name": "viagem",
    "amount": 200
  },
  "needs_clarification": false,
  "clarification_question": null
}
```

```json
{
  "intent": "launches.add",
  "confidence": 0.60,
  "entities": {
    "tipo": "despesa",
    "valor": 50
  },
  "needs_clarification": true,
  "clarification_question": "Para qual destino foi essa despesa de R$ 50?"
}
```

```json
{
  "intent": "out_of_scope",
  "confidence": 0.95,
  "entities": {},
  "needs_clarification": false,
  "clarification_question": null
}
```

### Regras rígidas para o JSON

- `intent` é sempre um dos valores do catálogo fixo (ver seção 10).
- `confidence` é float entre 0.0 e 1.0.
- `entities` contém apenas campos relevantes para o intent.
- Se `needs_clarification` é `true`, `clarification_question` é obrigatório.
- A IA **nunca** responde com texto livre — apenas este JSON.

---

## 4. Classificador Híbrido (3 tiers)

### Tier 1 — Exact match (custo zero)

```python
EXACT_INTENTS = {
    "saldo":               "balance.check",
    "saldo conta":         "balance.check",
    "conta":               "balance.check",
    "saldo geral":         "balance.check",
    "lancamentos":         "launches.list",
    "lançamentos":         "launches.list",
    "caixinhas":           "pockets.list",
    "investimentos":       "investments.list",
    "categorias":          "categories.list",
    "dashboard":           "dashboard.open",
    "ajuda":               "help",
    "help":                "help",
    "tutorial":            "help.tutorial",
    "relatorio":           "report.daily",
    "relatório":           "report.daily",
    "resumo":              "report.daily",
}
```

### Tier 2 — Aliases e regex (custo zero)

```python
ALIAS_PATTERNS = [
    (r"^(quanto tenho|quanto tem na conta|meu saldo)$",  "balance.check"),
    (r"^(ver|listar|mostrar) (caixinhas?|cofrinhos?)$",  "pockets.list"),
    (r"^(ver|listar|mostrar) investimentos?$",           "investments.list"),
    (r"^(ver|listar|mostrar) lancamentos?$",             "launches.list"),
    (r"^criar caixinha (.+)$",                           "pockets.create"),
    (r"^criar investimento (.+)$",                       "investments.create"),
    (r"^apagar lancamento #?(\d+)$",                     "launches.delete"),
    (r"^link(\s+\d{6})?$",                               "account.link"),
    (r"^vincular\s+\d{6}$",                              "account.vincular"),
]
```

### Tier 3 — Classificação por IA (só quando tiers 1/2 falham)

Chamada a GPT com o **prompt de sistema classificador** (ver seção 12).
Retorna o JSON estruturado (IntentResult).

### Fluxo completo do classificador

```python
def classify(text: str, user_id: int) -> IntentResult:
    normalized = normalize(text)

    # Tier 1: exact match
    if normalized in EXACT_INTENTS:
        return IntentResult(
            intent=EXACT_INTENTS[normalized],
            confidence=1.0,
            entities={},
            needs_clarification=False
        )

    # Tier 2: alias/regex
    result = match_alias_patterns(normalized)
    if result:
        return result  # confidence = 0.95

    # Tier 3: IA
    return classify_with_ai(text, user_id)
```

---

## 5. Prompt de Sistema Ideal para o Classificador de IA

```
Você é um classificador de intenções para um bot financeiro pessoal.

FUNÇÃO: Analisar a mensagem do usuário e retornar APENAS um JSON estruturado.

REGRAS ABSOLUTAS:
1. Retorne SOMENTE o JSON. Nenhum texto antes ou depois.
2. Nunca invente dados, saldos, nomes ou valores.
3. Use apenas intents do catálogo abaixo.
4. Se não souber com segurança, use "out_of_scope" ou "unknown".
5. Se faltar informação essencial, ative needs_clarification.
6. Confidence deve refletir sua certeza real (não force 1.0).

CATÁLOGO DE INTENTS:
- balance.check        → usuário quer saber o saldo da conta
- launches.list        → quer listar lançamentos/histórico
- launches.add         → quer registrar receita ou despesa (entities: tipo, valor, alvo, categoria)
- launches.delete      → quer apagar um lançamento (entities: launch_id)
- pockets.list         → quer listar caixinhas
- pockets.create       → quer criar caixinha (entities: name)
- pockets.deposit      → quer depositar em caixinha (entities: pocket_name, amount)
- pockets.withdraw     → quer sacar de caixinha (entities: pocket_name, amount)
- pockets.delete       → quer apagar caixinha (entities: pocket_name)
- investments.list     → quer listar investimentos
- investments.create   → quer criar investimento (entities: name, rate, period)
- investments.deposit  → quer aportar em investimento (entities: investment_name, amount)
- investments.withdraw → quer resgatar investimento (entities: investment_name, amount)
- categories.list      → quer ver categorias
- categories.create    → quer criar categoria (entities: category_name, keyword)
- report.daily         → quer o resumo/relatório do dia
- report.enable        → quer ativar relatório diário
- report.disable       → quer desativar relatório diário
- dashboard.open       → quer acessar o dashboard
- account.link         → quer vincular plataformas (entities: code?)
- help                 → quer ajuda ou lista de comandos
- out_of_scope         → pedido fora do escopo do bot financeiro
- unknown              → ambiguidade real, sem clareza suficiente

FORMATO DE SAÍDA OBRIGATÓRIO:
{
  "intent": "<intent do catálogo>",
  "confidence": <0.0 a 1.0>,
  "entities": { ... campos relevantes para o intent ... },
  "needs_clarification": <true|false>,
  "clarification_question": "<pergunta ou null>"
}

EXEMPLOS:
"qual meu saldo?" → {"intent":"balance.check","confidence":0.99,"entities":{},"needs_clarification":false,"clarification_question":null}
"gastei 50 no mercado" → {"intent":"launches.add","confidence":0.97,"entities":{"tipo":"despesa","valor":50,"alvo":"mercado","categoria":"alimentação"},"needs_clarification":false,"clarification_question":null}
"deposita 200 na caixinha viagem" → {"intent":"pockets.deposit","confidence":0.97,"entities":{"pocket_name":"viagem","amount":200},"needs_clarification":false,"clarification_question":null}
"me ajuda com minha vida" → {"intent":"out_of_scope","confidence":0.98,"entities":{},"needs_clarification":false,"clarification_question":null}
"gastei cinquenta" → {"intent":"launches.add","confidence":0.70,"entities":{"tipo":"despesa","valor":50},"needs_clarification":true,"clarification_question":"Em que você gastou R$ 50?"}
```

### Por que esse prompt minimiza alucinação

- Obriga saída JSON estrita (sem texto livre).
- Usa `temperature=0` na chamada da API.
- Catálogo fechado de intents — a IA não pode inventar novos.
- Confiança abaixo de 0.85 → o sistema não age, pede confirmação.
- `out_of_scope` explícito — a IA tem uma saída honesta quando não sabe.

---

## 6. Catálogo Completo de Intenções (bot financeiro)

| Intent | Descrição | Entidades-chave |
|---|---|---|
| `balance.check` | Consultar saldo | — |
| `launches.list` | Listar lançamentos recentes | `limit` (opcional) |
| `launches.add` | Registrar receita/despesa | `tipo`, `valor`, `alvo`, `categoria` |
| `launches.delete` | Apagar lançamento | `launch_id` |
| `pockets.list` | Listar caixinhas | — |
| `pockets.create` | Criar caixinha | `name` |
| `pockets.deposit` | Depositar em caixinha | `pocket_name`, `amount` |
| `pockets.withdraw` | Sacar de caixinha | `pocket_name`, `amount` |
| `pockets.delete` | Apagar caixinha | `pocket_name` |
| `investments.list` | Listar investimentos | — |
| `investments.create` | Criar investimento | `name`, `rate`, `period` |
| `investments.deposit` | Aportar em investimento | `investment_name`, `amount` |
| `investments.withdraw` | Resgatar investimento | `investment_name`, `amount` |
| `investments.accrue` | Atualizar rendimentos | — |
| `categories.list` | Listar categorias | — |
| `categories.create` | Criar regra de categoria | `category_name`, `keyword` |
| `categories.delete` | Remover regra | `keyword` |
| `report.daily` | Relatório do dia | — |
| `report.enable` | Ativar relatório diário | — |
| `report.disable` | Desativar relatório diário | — |
| `dashboard.open` | Abrir dashboard web | — |
| `account.link` | Vincular plataformas | `code` (opcional) |
| `account.vincular` | Vincular conta web | `code` |
| `help` | Ajuda geral | `section` (opcional) |
| `out_of_scope` | Fora do escopo | — |
| `unknown` | Ambíguo / incompreensível | — |

---

## 7. Exemplos de Frases → Intents

| Frase do usuário | Intent | Confidence esperada |
|---|---|---|
| "qual meu saldo?" | `balance.check` | 0.99 |
| "quanto tenho na conta?" | `balance.check` | 0.98 |
| "me fala meu saldo" | `balance.check` | 0.97 |
| "gastei 45 no iFood" | `launches.add` | 0.97 |
| "recebi 3000 de salário" | `launches.add` | 0.96 |
| "paguei 120 de luz" | `launches.add` | 0.95 |
| "gastei cinquenta" | `launches.add` | 0.70 (needs_clarification) |
| "meus lançamentos" | `launches.list` | 1.00 (exact) |
| "últimos 5 lançamentos" | `launches.list` | 0.93 |
| "apaga o lançamento 42" | `launches.delete` | 0.95 |
| "minhas caixinhas" | `pockets.list` | 1.00 (exact) |
| "cria caixinha viagem" | `pockets.create` | 0.97 |
| "deposita 200 na caixinha viagem" | `pockets.deposit` | 0.97 |
| "tira 100 da caixinha emergência" | `pockets.withdraw` | 0.94 |
| "meus investimentos" | `investments.list` | 1.00 (exact) |
| "quero investir 500 no tesouro" | `investments.deposit` | 0.88 |
| "resumo do dia" | `report.daily` | 0.98 |
| "abre meu dashboard" | `dashboard.open` | 0.97 |
| "me recomenda uma ação" | `out_of_scope` | 0.95 |
| "oi tudo bem?" | `out_of_scope` | 0.97 |
| "faz uma planilha do excel" | `out_of_scope` | 0.99 |

---

## 8. Fluxo de Decisão (Router)

```
                    IntentResult
                         │
         ┌───────────────┼───────────────────┐
         │               │                   │
   out_of_scope      unknown          intent válido
         │               │                   │
   "Isso está fora   "Pode reformular?"      │
    do que faço."                            │
                                  ┌──────────┴───────────┐
                                  │                      │
                         confidence >= 0.85      confidence < 0.85
                                  │                      │
                         needs_clarification?   "Não entendi bem.
                          │             │        Pode reformular?"
                         true          false
                          │             │
                    faz 1 pergunta   intent destrutivo?
                                      │         │
                                     sim        não
                                      │         │
                              pede confirmação  executa direto
                                (sim/não)
```

### Threshold de confiança

| Faixa | Ação |
|---|---|
| >= 0.90 | Executa direto (exceto se destrutivo) |
| 0.75–0.89 | Executa (não destrutivo) / pede confirmação (destrutivo) |
| 0.60–0.74 | Pede confirmação mesmo para ações simples |
| < 0.60 | "Não entendi. Pode reformular?" |

---

## 9. Fluxo de Ambiguidades

Quando `needs_clarification = true`, o sistema:

1. Salva o estado parcial: `pending_intent = { intent, entities_so_far }`.
2. Envia `clarification_question` ao usuário.
3. Na próxima mensagem, retoma o contexto:
   - Preenche a entidade faltante com a resposta.
   - Executa o intent completo.

### Regra de ouro: máximo 1 pergunta por interação.

Se ainda faltar informação após a resposta, o sistema executa com defaults razoáveis ou usa `"outros"` como categoria.

---

## 10. Fluxo para Mensagens Fora do Escopo

```python
OUT_OF_SCOPE_RESPONSE = (
    "Só consigo ajudar com finanças pessoais: "
    "saldo, lançamentos, caixinhas e investimentos. "
    "Digite *ajuda* para ver o que posso fazer."
)
```

- **Nunca** tenta responder sobre temas fora do escopo.
- **Nunca** inventa uma resposta.
- Oferece o caminho de volta (`ajuda`).

---

## 11. Estratégia Anti-Alucinação

### Na camada de classificação

- `temperature=0` na chamada da API.
- Schema JSON obrigatório (via `response_format` ou `output_schema` da OpenAI).
- Catálogo fechado de intents — sem invenção de novos valores.
- Confiança abaixo do threshold → não executa.

### Na camada de resposta

- O texto de resposta é sempre **template + dados reais**.
- A IA **nunca** gera o texto final — apenas classifica a intenção.
- Exemplo:

```python
# BOM — dados reais + template
return f"Seu saldo atual é {fmt_brl(balance)}."

# RUIM — deixar a IA gerar o texto
return gpt("Diga o saldo para o usuário")  # NUNCA fazer isso
```

### Separação clara de responsabilidades

| Componente | Responsabilidade | Pode inventar? |
|---|---|---|
| IntentClassifier | Classificar intenção | ❌ Não |
| Handlers | Buscar dados reais | ❌ Não |
| ResponseFormatter | Formatar template | ❌ Não |
| IA (GPT) | **Apenas classificar** | ❌ Não |

---

## 12. Padronização Multicanal

### Canal como adaptador

Cada canal tem seu próprio adaptador. Todos normalizam para a mesma `IncomingMessage` e recebem a mesma `OutgoingMessage`.

```python
@dataclass
class OutgoingMessage:
    text: str
    parse_mode: Literal["markdown", "plain"] = "markdown"
    # (futuramente: buttons, images, etc.)
```

### ResponseFormatter por canal

```python
class ResponseFormatter:
    def format(self, text: str, platform: str) -> str:
        if platform == "whatsapp":
            # WhatsApp: *bold* (não **)
            return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
        elif platform == "discord":
            # Discord: **bold** (já é o padrão)
            return text
        elif platform == "telegram":
            # Telegram: *bold* ou <b>bold</b> dependendo do parse mode
            return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
        return text
```

### Regras de formato por canal

| Recurso | WhatsApp | Discord | Telegram |
|---|---|---|---|
| Bold | `*texto*` | `**texto**` | `*texto*` |
| Code | `` `code` `` | `` `code` `` | `` `code` `` |
| Limite de chars | ~1000 | ~2000 | ~4096 |
| Botões inline | ❌ | ✅ | ✅ |
| Embeds ricos | ❌ | ✅ | ❌ |

---

## 13. Implementação em Node.js (referência)

> Estrutura de referência para quem quiser replicar a arquitetura em Node.js.
> Seu código atual é Python — a mesma lógica se aplica diretamente.

### Estrutura de arquivos

```
src/
  core/
    types.ts              # IncomingMessage, OutgoingMessage, IntentResult
    intent_classifier.ts  # Classificador híbrido (3 tiers)
    intent_router.ts      # Roteador por intent
    response_formatter.ts # Formata resposta por canal
  handlers/
    balance.ts
    launches.ts
    pockets.ts
    investments.ts
    report.ts
    help.ts
    out_of_scope.ts
  channels/
    whatsapp_adapter.ts
    discord_adapter.ts
  data/
    db.ts                 # Acesso ao banco (igual ao db.py atual)
```

### types.ts

```typescript
export interface IntentResult {
  intent: string;
  confidence: number;
  entities: Record<string, any>;
  needs_clarification: boolean;
  clarification_question: string | null;
}

export interface IncomingMessage {
  platform: "whatsapp" | "discord" | "telegram";
  user_id: string;
  text: string;
  external_id?: string;
  attachments?: Attachment[];
}

export interface OutgoingMessage {
  text: string;
  parse_mode?: "markdown" | "plain";
}
```

### intent_classifier.ts

```typescript
import OpenAI from "openai";

const EXACT_INTENTS: Record<string, string> = {
  "saldo":          "balance.check",
  "lancamentos":    "launches.list",
  "lançamentos":    "launches.list",
  "caixinhas":      "pockets.list",
  "investimentos":  "investments.list",
  "categorias":     "categories.list",
  "dashboard":      "dashboard.open",
  "ajuda":          "help",
  "relatorio":      "report.daily",
  "relatório":      "report.daily",
  "resumo":         "report.daily",
};

const CLASSIFIER_SYSTEM_PROMPT = `
Você é um classificador de intenções para um bot financeiro.
Retorne SOMENTE JSON. Sem texto antes ou depois.
Catálogo de intents: balance.check, launches.list, launches.add,
launches.delete, pockets.list, pockets.create, pockets.deposit,
pockets.withdraw, pockets.delete, investments.list, investments.create,
investments.deposit, investments.withdraw, categories.list, categories.create,
report.daily, report.enable, report.disable, dashboard.open,
account.link, help, out_of_scope, unknown.

Formato obrigatório:
{
  "intent": "<intent>",
  "confidence": <0.0-1.0>,
  "entities": {},
  "needs_clarification": false,
  "clarification_question": null
}
`.trim();

function normalize(text: string): string {
  return text.toLowerCase().trim()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ").trim();
}

export async function classifyIntent(
  text: string,
  client: OpenAI
): Promise<IntentResult> {
  const norm = normalize(text);

  // Tier 1: exact match
  if (EXACT_INTENTS[norm]) {
    return {
      intent: EXACT_INTENTS[norm],
      confidence: 1.0,
      entities: {},
      needs_clarification: false,
      clarification_question: null,
    };
  }

  // Tier 2: alias patterns
  const aliasResult = matchAliasPatterns(norm, text);
  if (aliasResult) return aliasResult;

  // Tier 3: AI classification
  return await classifyWithAI(text, client);
}

async function classifyWithAI(
  text: string,
  client: OpenAI
): Promise<IntentResult> {
  try {
    const response = await client.chat.completions.create({
      model: "gpt-4o-mini",
      temperature: 0,
      response_format: { type: "json_object" },
      messages: [
        { role: "system", content: CLASSIFIER_SYSTEM_PROMPT },
        { role: "user", content: text },
      ],
    });

    const raw = response.choices[0].message.content ?? "{}";
    const parsed = JSON.parse(raw);

    return {
      intent: parsed.intent ?? "unknown",
      confidence: parsed.confidence ?? 0,
      entities: parsed.entities ?? {},
      needs_clarification: parsed.needs_clarification ?? false,
      clarification_question: parsed.clarification_question ?? null,
    };
  } catch (err) {
    console.error("AI classify error:", err);
    return {
      intent: "unknown",
      confidence: 0,
      entities: {},
      needs_clarification: false,
      clarification_question: null,
    };
  }
}
```

### intent_router.ts

```typescript
import { classifyIntent } from "./intent_classifier";
import * as handlers from "../handlers";

const CONFIDENCE_THRESHOLD = 0.85;
const DESTRUCTIVE_INTENTS = new Set([
  "launches.delete",
  "pockets.delete",
  "investments.delete",
]);

export async function routeMessage(
  msg: IncomingMessage,
  client: OpenAI
): Promise<OutgoingMessage> {
  const result = await classifyIntent(msg.text, client);

  // Fora do escopo
  if (result.intent === "out_of_scope") {
    return handlers.outOfScope();
  }

  // Não entendeu
  if (result.intent === "unknown" || result.confidence < 0.60) {
    return { text: "Não entendi. Pode reformular?" };
  }

  // Precisa de esclarecimento
  if (result.needs_clarification && result.clarification_question) {
    await savePendingIntent(msg.user_id, result);
    return { text: result.clarification_question };
  }

  // Confiança baixa
  if (result.confidence < CONFIDENCE_THRESHOLD) {
    return {
      text: `Entendi como *${intentLabel(result.intent)}*. Confirma? (sim/não)`
    };
  }

  // Ação destrutiva — pede confirmação
  if (DESTRUCTIVE_INTENTS.has(result.intent)) {
    await savePendingIntent(msg.user_id, result);
    return handlers.proposeDestructive(result.intent, result.entities);
  }

  // Executa direto
  return await executeIntent(result, msg.user_id);
}

async function executeIntent(
  result: IntentResult,
  userId: string
): Promise<OutgoingMessage> {
  const { intent, entities } = result;

  switch (intent) {
    case "balance.check":
      return handlers.balance.check(userId);
    case "launches.list":
      return handlers.launches.list(userId, entities.limit ?? 10);
    case "launches.add":
      return handlers.launches.add(userId, entities);
    case "pockets.list":
      return handlers.pockets.list(userId);
    case "pockets.create":
      return handlers.pockets.create(userId, entities.name);
    case "pockets.deposit":
      return handlers.pockets.deposit(userId, entities);
    case "pockets.withdraw":
      return handlers.pockets.withdraw(userId, entities);
    case "investments.list":
      return handlers.investments.list(userId);
    case "report.daily":
      return handlers.report.daily(userId);
    case "help":
      return handlers.help(entities.section);
    default:
      return { text: "Função não implementada ainda." };
  }
}
```

### handlers/balance.ts

```typescript
import { getBalance } from "../data/db";
import { fmtBRL } from "../utils/format";

export async function check(userId: string): Promise<OutgoingMessage> {
  const balance = await getBalance(userId);
  // Dados reais — nunca texto inventado pela IA
  return { text: `🏦 Saldo atual: ${fmtBRL(balance)}` };
}
```

---

## 14. Fluxo Completo de Ponta a Ponta

### Exemplo: "quanto tenho na conta?"

```
1. ENTRADA (WhatsApp)
   └── webhook recebe: { from: "5511...", body: "quanto tenho na conta?" }
   └── whatsapp_adapter.normalize() →
       IncomingMessage { platform: "whatsapp", user_id: "5511...", text: "quanto tenho na conta?" }

2. CLASSIFICAÇÃO
   └── normalize("quanto tenho na conta?") → "quanto tenho na conta"
   └── Tier 1 (exact): não encontrado
   └── Tier 2 (alias): regex "^(quanto tenho|quanto tem na conta)$" → MATCH
   └── IntentResult {
         intent: "balance.check",
         confidence: 0.95,
         entities: {},
         needs_clarification: false
       }

3. DECISÃO (Router)
   └── intent != out_of_scope ✓
   └── confidence 0.95 >= 0.85 ✓
   └── needs_clarification: false ✓
   └── not destructive ✓
   └── → executeIntent("balance.check", userId)

4. HANDLER + DADOS REAIS
   └── balance_handler.check(userId)
   └── db.get_balance(userId) → 1250.00
   └── result = { balance: 1250.00 }

5. FORMATAÇÃO
   └── template: "🏦 Saldo atual: {balance}"
   └── response_formatter.format(text, "whatsapp")
   └── → "🏦 Saldo atual: R$ 1.250,00"

6. RESPOSTA
   └── whatsapp_adapter.send(from, "🏦 Saldo atual: R$ 1.250,00")
```

### Exemplo: "gastei cinquenta"

```
1. ENTRADA: "gastei cinquenta"

2. CLASSIFICAÇÃO
   └── Tier 1: não encontrado
   └── Tier 2: não encontrado
   └── Tier 3 (IA):
       → IntentResult {
           intent: "launches.add",
           confidence: 0.70,
           entities: { tipo: "despesa", valor: 50 },
           needs_clarification: true,
           clarification_question: "Em que você gastou R$ 50?"
         }

3. DECISÃO
   └── needs_clarification: true
   └── → salva pending_intent no banco
   └── → retorna clarification_question

4. RESPOSTA
   └── "Em que você gastou R$ 50?"

--- próxima mensagem do usuário: "no mercado" ---

5. RETOMADA
   └── carregar pending_intent → { intent: "launches.add", entities: { tipo: "despesa", valor: 50 } }
   └── completar entities: { ..., alvo: "mercado", categoria: "alimentação" }
   └── executeIntent("launches.add", userId, entities_completas)

6. EXECUÇÃO
   └── db.add_launch_and_update_balance(...)
   └── result = { launch_id: 123, new_balance: 1200.00 }

7. RESPOSTA
   └── "💸 Despesa registrada: R$ 50,00 • mercado • alimentação\n🏦 Saldo: R$ 1.200,00"
```

### Exemplo: "quero excluir o lançamento 42"

```
1. CLASSIFICAÇÃO
   └── IntentResult { intent: "launches.delete", confidence: 0.95, entities: { launch_id: 42 } }

2. DECISÃO
   └── intent é destrutivo ✓
   └── → salva pending_action { action: "delete_launch", data: { launch_id: 42 } }
   └── → retorna confirmação

3. RESPOSTA
   └── "⚠️ Isso vai apagar o lançamento #42 e desfazer seus efeitos. Confirma? (sim/não)"

--- usuário responde: "sim" ---

4. EXECUÇÃO
   └── db.delete_launch_and_rollback(42)
   └── "✅ Lançamento #42 apagado."
```

### Exemplo: "me recomenda uma ação da bolsa"

```
1. CLASSIFICAÇÃO (Tier 3 - IA)
   └── IntentResult { intent: "out_of_scope", confidence: 0.98 }

2. DECISÃO
   └── intent == out_of_scope
   └── → retorna resposta padrão (sem chamar banco, sem inventar)

3. RESPOSTA
   └── "Só consigo ajudar com finanças pessoais: saldo, lançamentos, caixinhas e investimentos. Digite *ajuda* para ver o que posso fazer."
```

---

## 15. Implementação no Código Python Atual

### O que mudar no `handle_incoming.py`

Substituir a cadeia de `if/elif` por:

```python
# NOVO handle_incoming.py — simplificado
def handle_incoming(msg: IncomingMessage) -> List[OutgoingMessage]:
    # 1. Classificar intenção (híbrido)
    intent_result = intent_classifier.classify(msg.text, msg.user_id)

    # 2. Checar pending state (confirmações pendentes)
    pending = db.get_pending_action(msg.user_id)
    if pending:
        response = pending_handler.resolve(msg, pending, intent_result)
        return [OutgoingMessage(text=response_formatter.format(response, msg.platform))]

    # 3. Rotear pelo intent
    raw_response = intent_router.route(intent_result, msg)

    # 4. Formatar para o canal
    formatted = response_formatter.format(raw_response, msg.platform)

    return [OutgoingMessage(text=formatted)]
```

### O que mudar no `ai_router.py`

- Remover o uso da IA para **executar** funções diretamente.
- A IA passa a ser usada **apenas** para classificar a intenção (retorno JSON).
- A execução fica nos handlers Python.

### Novos arquivos a criar

```
core/
  intent_classifier.py   # Substitui a lógica de if/elif
  intent_router.py       # Mapeia intent → handler
  response_formatter.py  # Formata por canal (já existe parcialmente)
  pending_handler.py     # Gerencia confirmações e clarificações
handlers/
  balance.py
  launches.py
  pockets.py
  investments.py
  report.py
  help.py
  out_of_scope.py
```

---

## Resumo Executivo

| Situação atual | Situação proposta |
|---|---|
| IA é fallback de último recurso | IA é o núcleo de classificação |
| Sem score de confiança | Confiança numérica em todas as decisões |
| if/elif para 30+ comandos | Catálogo de intents + roteador |
| IA só funciona no WhatsApp | Arquitetura multicanal uniforme |
| Resposta inventada pelo GPT | GPT só classifica — dados vêm do banco |
| Formatação misturada com lógica | ResponseFormatter separado por canal |
| Sem tratamento de ambiguidade | Fluxo estruturado de clarificação |
| Alucinação possível nas respostas | Alucinação bloqueada por design |

> A mudança principal: a IA para de **responder** e passa a **classificar**.
> Quem responde é o seu sistema, com dados reais.
