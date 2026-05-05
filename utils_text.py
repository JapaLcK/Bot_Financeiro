# utils_text.py
"""
Helpers de texto: normalização, parse de valores, regras locais e utilitários.
"""

import re
import unicodedata
from decimal import Decimal

def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))  # remove acentos
    text = re.sub(r"[^a-z0-9\s]", " ", text)  # tira pontuação
    text = re.sub(r"\s+", " ", text).strip()
    return text

def contains_word(text: str, word: str) -> bool:
    # bate palavra inteira quando possível (evita falsos positivos)
    return re.search(rf"\b{re.escape(word)}\b", text) is not None

# Regras locais (baratas) — já cobrindo mercado/psicologo/petshop
LOCAL_RULES = [
    # ─── Movimentações internas têm prioridade ────────────────────────────────
    # Sem isso, frases como "mercado bitcoin" ou "fundo multimercado" caem em
    # alimentação porque "mercado" bate primeiro.
    #
    # Aportes de investimento — movimentação interna (não é despesa real)
    (["aporte", "aportei", "aportar", "aplicacao", "aplicação", "apliquei", "investi", "investir",
      "compra de acoes", "compra de ações", "compra de acao", "compra de ação",
      "acoes", "ações", "fii", "fiis", "cdb", "rdb", "lci", "lca", "cra", "cri",
      "tesouro direto", "tesouro selic", "tesouro ipca", "tesouro pre", "tesouro pré",
      "tesouro pos", "tesouro pós", "tesouro", "selic", "ipca", "etf",
      "previdencia", "previdência", "previdencia privada", "previdência privada",
      "pgbl", "vgbl", "fia", "fim", "multimercado", "fundo multimercado", "coe",
      "debenture", "debênture",
      "dolar", "dólar", "euro", "ouro",
      "xp investimentos", "nuinvest", "btg", "btg pactual", "btg invest", "rico investimentos",
      "clear corretora", "ágora", "agora corretora", "modal mais", "warren",
      "binance", "mercado bitcoin", "mercado pago crypto", "coinbase",
      ], "investimento_aporte"),
    # Cripto — mantida separada porque o card "Aportes" usa essa categoria também
    (["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "cripto", "criptomoeda", "criptomoedas",
      "doge", "dogecoin", "shiba", "shib", "ada", "cardano", "usdt", "tether",
      "xrp", "ripple", "bnb", "polygon", "matic", "avax", "avalanche"], "criptomoedas"),
    # Genérico "investimento" — ainda interno, captura quem digita "investi" sem produto
    (["investimento", "investimentos"], "investimentos"),
    # Resgates de investimento — movimentação interna (não é receita real)
    (["resgate", "retirada de investimento", "retirei do investimento"], "investimento_resgate"),
    # Rendimentos — receita real (lucro/juros/dividendos)
    (["rendimento", "rendimentos", "juros", "dividendo", "dividendos", "lucro investimento"], "rendimentos"),

    # ─── Despesas reais ───────────────────────────────────────────────────────
    (["mercado", "supermercado", "mercadinho", "hortifruti", "padaria", "cafe", "café", "cafeteria"], "alimentação"),
    (["aluguel", "condominio", "condomínio", "luz", "energia", "conta de luz", "agua", "água", "conta de agua",
      "conta de água", "gas", "gás", "internet", "wifi"], "moradia"),
    (["psicologo", "psicologa", "terapia", "terapeuta", "psiquiatra"], "saúde"),
    (["petshop", "pet shop", "racao", "veterinario", "vet", "banho", "tosa"], "pets"),
    (["ifood", "restaurante", "lanchonete"], "alimentação"),
    (["livro", "livros", "ebook", "curso", "cursos", "aula", "aulas", "material", "apostila", "faculdade", "escola"], "educação"),
    (["uber", "99", "taxi", "metro", "onibus", "gasolina", "combustivel"], "transporte"),
    (["academia", "remedio", "farmacia", "dentista", "consulta"], "saúde"),
    (["netflix", "spotify", "youtube", "prime video", "disney"], "assinaturas"),
]

# Keywords que devem ser matchadas como palavra inteira (sem substring), para
# evitar que "acoes" bata em "transações", "investi" em "investigar", etc.
# A lista é normalizada (sem acento, lowercase) e checada antes da heurística
# de substring em infer_category.
EXACT_WORD_KEYWORDS = {
    "acoes", "acao", "acoesp",
    "investi", "investir", "investimento", "investimentos",
    "aporte", "aportei", "aportar",
    "ouro", "euro", "dolar",
    "btg", "xp",
}

# Categorias que representam movimentações internas (não entram em receita/despesa do dashboard)
INTERNAL_MOVEMENT_CATEGORIES = {
    "investimento_aporte",
    "investimento_resgate",
    "transferencia_interna",
    "pagamento_fatura",
    "ajuste_saldo",
}

CATEGORY_LABELS = {
    "alimentacao": "alimentação",
    "transporte": "transporte",
    "saude": "saúde",
    "moradia": "moradia",
    "lazer": "lazer",
    "educacao": "educação",
    "assinaturas": "assinaturas",
    "pets": "pets",
    "compras online": "compras online",
    "beleza": "beleza",
    "outros": "outros",
    "investimento_aporte": "investimento_aporte",
    "investimento_resgate": "investimento_resgate",
    "transferencia_interna": "transferencia_interna",
    "pagamento_fatura": "pagamento_fatura",
    "ajuste_saldo": "ajuste_saldo",
    "rendimentos": "rendimentos",
    "investimentos": "investimentos",
    "criptomoedas": "criptomoedas",
}

INVESTMENT_CATEGORY_HINTS = {
    "investimento", "investimentos",
    "criptomoeda", "criptomoedas", "cripto",
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol",
    "acao", "acoes", "fii", "fiis", "etf", "etfs",
    "tesouro", "cdb", "rdb", "lci", "lca",
}

def is_internal_category(categoria: str | None) -> bool:
    """Retorna True se a categoria indica movimentação interna."""
    if not categoria:
        return False
    norm = normalize_text(categoria)
    if norm in INTERNAL_MOVEMENT_CATEGORIES:
        return True
    if norm == "rendimentos":
        return False

    return any(contains_word(norm, hint) for hint in INVESTMENT_CATEGORY_HINTS)


def canonicalize_category_label(category: str | None) -> str:
    norm = normalize_text(category or "")
    if not norm:
        return ""
    return CATEGORY_LABELS.get(norm, norm)

STOPWORDS_PT = {
    "gastei","paguei","comprei","debitei","recebi","ganhei","salario","reembolso",
    "no","na","nos","nas","em","de","do","da","dos","das","pra","para","por",
    "um","uma","uns","umas","com","ao","aos","as","os","o","a"
}

MEMORY_NOISE_TOKENS = {
    "pix", "pagamento", "pag", "pgto", "transferencia", "transfer", "ted", "doc",
    "debito", "credito", "compra", "pagto", "recebimento", "receita", "despesa",
    "saldo", "ajuste", "tarifa", "taxa", "estorno", "deb", "cred", "cp", "comp",
    "ltda", "me", "epp", "sa", "eireli", "banco", "bank", "loja",
}


def is_useful_memory_keyword(keyword: str | None) -> bool:
    kw = normalize_text(keyword or "")
    if not kw or len(kw) < 3:
        return False
    if len(kw) > 48:
        return False
    if not re.search(r"[a-z]", kw):
        return False

    tokens = [tok for tok in kw.split() if tok]
    if not tokens:
        return False
    if len(tokens) > 6:
        return False
    if all(tok in STOPWORDS_PT or tok in MEMORY_NOISE_TOKENS for tok in tokens):
        return False
    return True


def extract_memory_candidates(text: str | None, limit: int = 3) -> list[str]:
    norm = normalize_text(text or "")
    if not norm:
        return []

    raw_tokens = [tok for tok in norm.split() if tok]
    filtered = [
        tok for tok in raw_tokens
        if len(tok) >= 2
        and not tok.isdigit()
        and tok not in STOPWORDS_PT
        and tok not in MEMORY_NOISE_TOKENS
    ]

    candidates: list[str] = []

    if filtered:
        phrase = " ".join(filtered[:4])
        if is_useful_memory_keyword(phrase):
            candidates.append(phrase)

        first_two = " ".join(filtered[:2])
        if is_useful_memory_keyword(first_two):
            candidates.append(first_two)

    keyword = extract_keyword_for_memory(norm)
    if is_useful_memory_keyword(keyword):
        candidates.append(normalize_text(keyword))

    deduped: list[str] = []
    for candidate in candidates:
        c = normalize_text(candidate)
        if c and c not in deduped:
            deduped.append(c)
        if len(deduped) >= limit:
            break
    return deduped

def extract_keyword_for_memory(text_norm: str) -> str:
    # 1) se bater em alguma keyword das regras locais, salva essa keyword
    for keywords, _cat in LOCAL_RULES:
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if kw_norm:
                if len(kw_norm) <= 3:
                    ok = contains_word(text_norm, kw_norm)
                else:
                    ok = contains_word(text_norm, kw_norm) or (kw_norm in text_norm)

                if ok:
                    return kw_norm

    # 2) fallback: pega o último "token útil" (exclui números e stopwords)
    tokens = [
        t for t in text_norm.split()
        if t
        and t not in STOPWORDS_PT
        and t not in MEMORY_NOISE_TOKENS
        and len(t) >= 3
        and not t.replace(",", "").replace(".", "").isdigit()
    ]
    if not tokens:
        return ""
    return tokens[-1]

def fmt_brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_rate(rate, period: str | None) -> str:
    if rate is None or not period:
        return ""

    # rate pode vir como Decimal do Postgres
    if isinstance(rate, Decimal):
        rate = float(rate)
    else:
        rate = float(rate)

    # CDI é armazenado como multiplicador:
    # 1.16 => 116% CDI, 1.0 => 100% CDI
    if period == "cdi":
        display = rate * 100
    else:
        # Taxas comuns são armazenadas como fração:
        # 0.14 => 14%
        display = rate * 100 if rate <= 1 else rate

    # formatação limpa (sem 1.0000)
    if abs(display - round(display)) < 1e-12:
        pct = str(int(round(display)))
    else:
        pct = f"{display:.6f}".rstrip("0").rstrip(".")
    pct = pct.replace(".", ",")

    if period == "cdi":
        return f"{pct}% CDI"
    if period == "cdi_spread":
        return f"CDI + {pct}% a.a."
    if period == "ipca_spread":
        return f"IPCA + {pct}% a.a."
    if period == "selic_spread":
        return f"SELIC + {pct}% a.a."

    # Na listagem, mostrar só a taxa evita redundância como "14% anual".
    return f"{pct}%"

DEPOSIT_VERBS = [
    "transferi", "coloquei", "adicionei", "depositei", "pus", "botei",
    "mandei", "joguei", "colocar", "adicionar", "depositar", "por", "botar"
]

def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def parse_money(text: str) -> float | None:
    # pega o primeiro número contínuo (com possíveis separadores)
    m = re.search(r'(\d[\d.,\s]*)', text)
    if not m:
        return None

    raw = m.group(1).strip().replace(" ", "")

    # normaliza milhares/decimais
    # se tiver vírgula E ponto, decide o decimal pelo último
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            # BR: 1.000,50
            raw = raw.replace(".", "").replace(",", ".")
        else:
            # US: 1,000.50
            raw = raw.replace(",", "")
    elif "," in raw:
        # BR: 1000,50 ou 1.000,50
        raw = raw.replace(".", "").replace(",", ".")
    elif "." in raw:
        # pode ser milhar (1.000) ou decimal (1000.50)
        parts = raw.split(".")
        if len(parts[-1]) == 3:   # milhar
            raw = raw.replace(".", "")
        # senão, é decimal (deixa como está)

    try:
        return float(raw)
    except ValueError:
        return None
    
# foca a IA para responder questoes so do bot e nao geral
def should_use_ai(text: str) -> bool:
    t = text.lower().strip()

    # 1) Não chama IA pra coisas muito curtas / aleatórias
    if len(t) < 4:
        return False

    # 2) Palavras-chave financeiras
    keywords = [
        "saldo", "lanc", "lanç", "recebi", "receita", "gastei", "despesa",
        "caixinha", "caixinhas", "invest", "investimento", "aporte", "resgate",
        "fatura", "cartao", "cartão", "parcel", "metas", "limite", "gastos",
        "extrato", "conta", "rendeu", "rendendo", "cdb", "tesouro", "cdi"
    ]

    if any(k in t for k in keywords):
        return True

    # 3) Se começa com comandos conhecidos
    commands = [
        "saldo", "listar lancamentos", "listar lançamentos", "desfazer",
        "criar caixinha", "listar caixinhas", "saldo caixinhas",
        "criar investimento", "saldo investimentos"
    ]

    if any(t.startswith(c) for c in commands):
        return True

    return False

# Categorias por palavras-chave (bem simples e eficaz)
CATEGORY_KEYWORDS = {
    "alimentação": ["ifood", "uber eats", "rappi", "restaurante", "lanche", "pizza", "hamburguer", "cafe", "café", "cafeteria", "padaria"],
    "mercado": ["mercado", "supermercado", "carrefour", "whole foods", "walmart", "target", "costco"],
    "transporte": ["uber", "lyft", "99", "metro", "trem", "ônibus", "gasolina", "combustível", "posto", "estacionamento", "parking"],
    "moradia": ["aluguel", "rent", "condomínio", "luz", "energia", "água", "internet", "wifi", "gás"],
    "saúde": ["farmácia", "remédio", "medicina", "consulta", "dentista", "hospital"],
    "assinaturas": ["netflix", "spotify", "prime", "amazon prime", "hbo", "disney", "icloud", "google one"],
    "compras": ["amazon", "shopee", "aliexpress", "loja", "compra", "roupa", "tenis", "sapato"],
    "lazer": ["cinema", "show", "bar", "balada", "viagem", "hotel", "airbnb"],
    "educação": ["curso", "udemy", "coursera", "livro", "faculdade", "mensalidade"],
    "outros": []
}

def guess_category(text: str) -> str:
    t = text.lower()
    for cat, words in CATEGORY_KEYWORDS.items():
        for w in words:
            if w in t:
                return cat
    return "outros"

def parse_note_after_amount(text: str, amount: float) -> str:
    """
    Pega uma "descrição" simples depois do valor.
    Ex: 'gastei 35 no ifood' -> 'ifood'
    """
    t = re.sub(r"\s+", " ", text.strip())
    # remove o valor (primeira ocorrência de número)
    t2 = re.sub(r"\d+[.,]?\d*", "", t, count=1).strip(" -–—:;")
    # remove palavras comuns
    t2 = re.sub(r"\b(gastei|gasto|paguei|pagar|recebi|ganhei|pix|no|na|em|pra|para|de|do|da)\b", "", t2, flags=re.I).strip()
    return t2.strip()[:60]  # limita

def parse_expense_income_natural(text: str):
    """
    Retorna dict com:
      kind: 'expense'|'income'
      amount: float
      note: str
      category: str
    ou None se não reconhecer.
    """
    raw = re.sub(r"\s+", " ", text.lower()).strip()
    amount = parse_money(raw)
    if amount is None:
        return None

    expense_verbs = ["gastei", "paguei", "comprei", "debitei", "cartão", "cartao"]
    income_verbs  = ["recebi", "ganhei", "caiu", "salário", "salario", "pix recebido", "reembolso"]

    is_expense = any(v in raw for v in expense_verbs)
    is_income  = any(v in raw for v in income_verbs)

    # se não tiver verbo claro, não assume
    if not (is_expense or is_income):
        return None

    kind = "expense" if is_expense and not is_income else "income" if is_income and not is_expense else None
    if kind is None:
        return None

    note = parse_note_after_amount(text, amount)
    category = guess_category(text)

    return {"kind": kind, "amount": amount, "note": note, "category": category}

def parse_pocket_deposit_natural(text: str):
    """
    Retorna (amount: float, pocket_name: str) ou (None, None)
    Entende frases como:
      - coloquei 300 na emergência
      - adicionei 50 na caixinha viagem
      - depositei 1200 em emergencia
      - transferi 200 pra caixinha emergencia
    """
    raw = normalize_spaces(text.lower())

    # precisa ter algum verbo de depósito
    if not any(v in raw for v in DEPOSIT_VERBS):
        return None, None

    amount = parse_money(raw)
    if amount is None:
        return None, None

    # tenta extrair o nome depois de "caixinha ..."
    if "caixinha" in raw:
        pocket = raw.split("caixinha", 1)[1].strip()
        pocket = re.sub(r"^(da|do|na|no|pra|para|em)\s+", "", pocket).strip()
        pocket = re.sub(r"\b(hoje|ontem)\b.*$", "", pocket).strip()
        if pocket:
            return amount, pocket

    # se não tem a palavra caixinha, tenta padrões "na/em/pra <nome>"
    m = re.search(r"\b(na|no|em|pra|para)\s+([a-z0-9_\-áàâãéèêíìîóòôõúùûç ]+)", raw)
    if m:
        pocket = m.group(2).strip()
        pocket = re.sub(r"\b(hoje|ontem)\b.*$", "", pocket).strip()
        # corta se tiver outras palavras típicas depois
        pocket = re.split(r"\b(saldo|investimento|apliquei|aplicar)\b", pocket)[0].strip()
        if pocket:
            return amount, pocket

    return None, None
