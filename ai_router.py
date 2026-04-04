# ai_router.py
import os
import json
from openai import OpenAI
import re
import unicodedata
import db
import hashlib
from db import list_category_rules
from core.services.category_service import infer_category
from utils_text import is_internal_category

# aliases para não precisar mudar o resto do arquivo
ensure_user = db.ensure_user
get_balance = db.get_balance
list_launches = db.list_launches
list_pockets = db.list_pockets
list_investments = db.list_investments
add_launch_and_update_balance = db.add_launch_and_update_balance
pocket_deposit_from_account = db.pocket_deposit_from_account
pocket_withdraw_to_account = db.pocket_withdraw_to_account
create_pocket = db.create_pocket
delete_pocket = db.delete_pocket
create_investment = db.create_investment
delete_investment = db.delete_investment
investment_deposit_from_account = db.investment_deposit_from_account
investment_withdraw_to_account = db.investment_withdraw_to_account
accrue_all_investments = db.accrue_all_investments
delete_launch_and_rollback = db.delete_launch_and_rollback
set_pending_action = db.set_pending_action


client = None

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _get_openai_client():
    global client
    if client is not None:
        return client

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None

    client = OpenAI(api_key=api_key)
    return client

# Tools que a IA pode chamar
TOOLS_NESTED = [
    {
        "type": "function",
        "function": {
            "name": "get_balance",
            "description": "Retorna o saldo atual da conta principal do usuário.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_launches",
            "description": "Lista lançamentos recentes do usuário (últimos N).",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pockets",
            "description": "Lista todas as caixinhas e seus saldos.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_investments",
            "description": "Lista investimentos e seus saldos.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },

    # ações NÃO destrutivas
   {
    "type": "function",
        "function": {
            "name": "add_launch",
            "description": "Cria um lançamento (receita/despesa) e atualiza o saldo da conta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "description": "ex: despesa, receita"},
                    "valor": {"type": "number"},
                    "alvo": {"type": "string", "description": "Destinatário/estabelecimento (ex: Uber, iFood, Spotify)."},
                    "nota": {"type": "string", "description": "Descrição/observação do lançamento."},
                    "categoria": {"type": "string", "description": "Categoria do lançamento (ex: lazer, alimentação, transporte, rifa)."},
                },
                "required": ["tipo", "valor"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_pocket",
            "description": "Cria uma caixinha.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pocket_deposit",
            "description": "Transfere valor da conta para uma caixinha existente.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pocket_name": {"type": "string"},
                    "amount": {"type": "number"},
                    "nota": {"type": "string"},
                },
                "required": ["pocket_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pocket_withdraw",
            "description": "Transfere valor de uma caixinha para a conta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pocket_name": {"type": "string"},
                    "amount": {"type": "number"},
                    "nota": {"type": "string"},
                },
                "required": ["pocket_name", "amount"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "create_investment",
            "description": "Cria um investimento com taxa e período.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "rate": {"type": "number"},
                    "period": {"type": "string", "enum": ["daily", "monthly", "yearly"]},
                },
                "required": ["name", "rate", "period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "investment_deposit",
            "description": "Transfere valor da conta para um investimento.",
            "parameters": {
                "type": "object",
                "properties": {
                    "investment_name": {"type": "string"},
                    "amount": {"type": "number"},
                    "nota": {"type": "string"},
                },
                "required": ["investment_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "investment_withdraw",
            "description": "Resgata valor de um investimento para a conta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "investment_name": {"type": "string"},
                    "amount": {"type": "number"},
                    "nota": {"type": "string"},
                },
                "required": ["investment_name", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accrue_all_investments",
            "description": "Atualiza rendimentos de todos os investimentos do usuário.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },

    # destrutivas (pedem confirmação) -> a IA NÃO apaga direto, ela só cria uma pendência
    {
        "type": "function",
        "function": {
            "name": "propose_delete_launch",
            "description": "Pede confirmação para apagar um lançamento (destrutivo).",
            "parameters": {
                "type": "object",
                "properties": {
                    "launch_id": {"type": "integer"},
                },
                "required": ["launch_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_delete_pocket",
            "description": "Pede confirmação para deletar uma caixinha (destrutivo).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pocket_name": {"type": "string"},
                },
                "required": ["pocket_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_delete_investment",
            "description": "Pede confirmação para deletar um investimento (destrutivo).",
            "parameters": {
                "type": "object",
                "properties": {
                    "investment_name": {"type": "string"},
                },
                "required": ["investment_name"],
            },
        },
    },
]

# Responses API (na prática) está exigindo tools com "name" no nível de cima.
# Então a gente converte do formato nested -> flat automaticamente.
TOOLS = []
for t in TOOLS_NESTED:
    if t.get("type") == "function" and "function" in t:
        fn = t["function"]
        TOOLS.append({
            "type": "function",
            "name": fn["name"],
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    else:
        TOOLS.append(t)


INSTRUCTIONS = """
Você é um assistente financeiro para WhatsApp e Discord.
Regras:
- Sempre use ferramentas (tools) para consultar valores reais. Nunca invente números.
- Se a solicitação for destrutiva (apagar/deletar/excluir), NÃO execute direto.
  Em vez disso, chame a tool propose_* correspondente para criar uma confirmação.
- Se faltar informação, faça UMA pergunta curta e objetiva.
- Responda sempre em português do Brasil.
- Não invente funcionalidades. Se o usuário pedir algo que não existe (ex: exportar sheets), diga claramente que ainda não está implementado.
- Só ofereça ações que existam nas tools (get_balance, list_launches, list_pockets, list_investments, add_launch, etc.).
"""

# categorias permitidas (canon)
ALLOWED_CATEGORIES = [
    "alimentação",
    "transporte",
    "saúde",
    "moradia",
    "lazer",
    "educação",
    "assinaturas",
    "pets",
    "compras online",
    "beleza",
    "outros",
]

def _normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))  # remove acentos
    text = re.sub(r"[^a-z0-9\s]", " ", text)  # remove pontuação
    text = re.sub(r"\s+", " ", text).strip()
    return text

# mapeia variações comuns -> categoria canônica
_CATEGORY_ALIASES = {
    "alimentacao": "alimentação",
    "saude": "saúde",
    "educacao": "educação",
    "compra online": "compras online",
    "compras": "compras online",
    "online": "compras online",
    "pet": "pets",
    "petshop": "pets",
}

def _normalize_category(cat: str) -> str:
    norm = _normalize_text(cat)
    # tenta mapear variações
    mapped = _CATEGORY_ALIASES.get(norm, norm)

    # se o GPT respondeu com mais de uma palavra (ex: "compras online"), mantém como está
    # e faz check com allowed também normalizado
    allowed_norm_map = {_normalize_text(c): c for c in ALLOWED_CATEGORIES}
    return allowed_norm_map.get(_normalize_text(mapped), "outros")

def classify_category_with_gpt(descricao: str) -> str:
    descricao = (descricao or "").strip()
    if not descricao:
        return "outros"

    prompt = (
        "Você é um classificador de categorias financeiras.\n"
        "Responda com UMA única categoria exatamente dentre as opções abaixo.\n"
        "Não explique. Não use pontuação. Não escreva mais nada.\n"
        "Categorias: " + ", ".join(ALLOWED_CATEGORIES) + "\n\n"
        "Exemplos:\n"
        "petshop, ração, veterinário -> pets\n"
        "psicólogo, terapia, remédio, dentista -> saúde\n"
        "aluguel, condomínio, luz, internet -> moradia\n"
        "uber, 99, gasolina, ônibus, metrô -> transporte\n"
        "mercado, ifood, restaurante, padaria -> alimentação\n"
        "compra online, online, amazon, shopee -> compras online\n"
        "livros, curso, aulas, video aulas -> educação\n"
        "spotify, youtube, netflix -> assinaturas\n\n"
        f"Texto: {descricao}\n"
        "Resposta:"
    )

    # se não tiver chave, não tenta IA
    if not os.getenv("OPENAI_API_KEY"):
        return "outros"

    current_client = _get_openai_client()
    if current_client is None:
        return "outros"

    try:
        resp = current_client.responses.create(
            model=MODEL,
            input=prompt,
            temperature=0,
        )

        cat_raw = (resp.output_text or "").strip()
        return _normalize_category(cat_raw)

    except Exception as e:
        print("GPT category error:", e)
        return "outros"



def _extract_tool_calls(resp):
    # Extrai tool calls de forma defensiva (respostas podem variar por SDK)
    calls = []
    output = getattr(resp, "output", None) or []
    for item in output:
        t = getattr(item, "type", None)
        if t in ("function_call", "tool_call"):
            name = getattr(item, "name", None) or getattr(getattr(item, "function", None), "name", None)
            args = getattr(item, "arguments", None) or getattr(getattr(item, "function", None), "arguments", None)
            calls.append((name, args))
    return calls

def _internal_user_id(raw_user_id: int | str) -> int:
    """
    Converte um user_id potencialmente enorme (ex: WhatsApp) em um int seguro (32-bit-ish),
    estável entre execuções, para não estourar colunas INTEGER no banco.
    """
    s = str(raw_user_id)
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    n = int.from_bytes(digest[:8], "big")  # grande
    # comprime pra faixa segura de int32 positivo
    return int(n % 2_000_000_000) + 1

def handle_ai_message(user_id: int | str, text: str) -> str | None:
    # se não tiver chave, não tenta IA
    if not os.getenv("OPENAI_API_KEY"):
        return None

    current_client = _get_openai_client()
    if current_client is None:
        return None

    uid = _internal_user_id(user_id)
    db.ensure_user(uid)


    resp = current_client.responses.create(
        model=MODEL,
        instructions=INSTRUCTIONS,
        input=text,
        tools=TOOLS,
    )

    # Se o modelo respondeu direto:
    direct = getattr(resp, "output_text", None)
    calls = _extract_tool_calls(resp)

    if not calls:
        return direct or None

    # Executa UMA tool call por vez (simples e confiável).
    # Se você quiser multi-step depois, a gente evolui.
    name, args_json = calls[0]
    args = json.loads(args_json) if isinstance(args_json, str) else (args_json or {})

    # Map tool -> função do seu sistema
    if name == "get_balance":
        bal = float(get_balance(uid))
        return f"Seu saldo atual é **R$ {bal:,.2f}**".replace(",", "X").replace(".", ",").replace("X", ".")
    if name == "list_launches":
        rows = list_launches(uid, limit=int(args.get("limit", 10)))
        if not rows:
            return "Você ainda não tem lançamentos."
        lines = [f"#{r['id']} • {r['tipo']} • {r['valor']} • {r.get('nota') or ''}" for r in rows]
        return "**Lançamentos recentes:**\n" + "\n".join(lines)

    if name == "list_pockets":
        rows = list_pockets(uid)
        if not rows:
            return "Você ainda não tem caixinhas."
        lines = [f"• {r['name']}: {r['balance']}" for r in rows]
        return "**Caixinhas:**\n" + "\n".join(lines)

    if name == "list_investments":
        rows = list_investments(uid)
        if not rows:
            return "Você ainda não tem investimentos."
        lines = [f"• {r['name']}: {r['balance']} (rate={r['rate']} {r['period']})" for r in rows]
        return "**Investimentos:**\n" + "\n".join(lines)

    if name == "add_launch":
        alvo = args.get("alvo")
        nota = args.get("nota", text)

        # 1) Se a IA passou categoria, respeita
        categoria = (args.get("categoria") or "").strip()
        rules = list_category_rules(uid)

        # 2) Se não veio categoria, tenta inferir por regras (alvo > nota > texto)
        if not categoria:
            # tenta inferir pelo memo mais informativo
            memo = (nota or "").strip() or (alvo or "").strip() or text
            categoria = infer_category(memo, rules)

        launch_id, new_balance = add_launch_and_update_balance(
            user_id=uid,
            tipo=args.get("tipo", "despesa"),
            valor=float(args["valor"]),
            alvo=args.get("alvo"),
            nota=args.get("nota", text),
            categoria=categoria,
            criado_em=None,
            is_internal_movement=is_internal_category(categoria),
        )
        return f"✅ Lançamento criado **#{launch_id}** ({categoria}). Saldo agora: **{new_balance}**"

    if name == "create_pocket":
        pocket_id, canon = create_pocket(uid, args["name"])
        return f"✅ Caixinha criada: **{canon}** (id {pocket_id})"

    if name == "pocket_deposit":
        launch_id, new_acc, new_pocket, canon_name = pocket_deposit_from_account(
            uid, args["pocket_name"], float(args["amount"]), args.get("nota", text)
        )
        return f"✅ Depósito na caixinha **{canon_name}**. ID **#{launch_id}**."

    if name == "pocket_withdraw":
        launch_id, new_acc, new_pocket, canon_name = pocket_withdraw_to_account(
            uid, args["pocket_name"], float(args["amount"]), args.get("nota", text)
        )
        return f"✅ Saque da caixinha **{canon_name}**. ID **#{launch_id}**."

    if name == "create_investment":
        inv_id, canon = create_investment(uid, args["name"], float(args["rate"]), args["period"])
        return f"✅ Investimento criado: **{canon}** (id {inv_id})"

    if name == "investment_deposit":
        launch_id, new_acc, new_inv, canon = investment_deposit_from_account(
            uid, args["investment_name"], float(args["amount"]), args.get("nota", text)
        )
        return f"✅ Aporte em **{canon}**. ID **#{launch_id}**."

    if name == "investment_withdraw":
        launch_id, new_acc, new_inv, canon = investment_withdraw_to_account(
            uid, args["investment_name"], float(args["amount"]), args.get("nota", text)
        )
        return f"✅ Resgate de **{canon}**. ID **#{launch_id}**."

    if name == "accrue_all_investments":
        updated = accrue_all_investments(uid)
        return f"📈 Rendimentos atualizados em {updated} investimento(s)."

    # confirmação (destrutivo)
    if name == "propose_delete_launch":
        set_pending_action(uid, "delete_launch", {"launch_id": int(args["launch_id"])})
        return f"⚠️ Isso vai apagar o lançamento **#{args['launch_id']}** e desfazer os efeitos. Confirma? Responda **sim** ou **não**."
    if name == "propose_delete_pocket":
        set_pending_action(uid, "delete_pocket", {"pocket_name": args["pocket_name"]})
        return f"⚠️ Isso vai deletar a caixinha **{args['pocket_name']}**. Confirma? Responda **sim** ou **não**."
    if name == "propose_delete_investment":
        set_pending_action(uid, "delete_investment", {"investment_name": args["investment_name"]})
        return f"⚠️ Isso vai deletar o investimento **{args['investment_name']}**. Confirma? Responda **sim** ou **não**."

    return direct or "Não consegui processar isso."
