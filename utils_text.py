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
    (["mercado", "supermercado", "mercadinho", "hortifruti", "padaria"], "alimentação"),
    (["psicologo", "psicologa", "terapia", "terapeuta", "psiquiatra"], "saúde"),
    (["petshop", "pet shop", "racao", "veterinario", "vet", "banho", "tosa"], "pets"),
    (["ifood", "restaurante", "lanchonete"], "alimentação"),
    (["livro", "livros", "ebook", "curso", "cursos", "aula", "aulas", "material", "apostila", "faculdade", "escola"], "educação"),
    (["uber", "99", "taxi", "metro", "onibus", "gasolina", "combustivel"], "transporte"),
    (["academia", "remedio", "farmacia", "dentista", "consulta"], "saúde"),
    (["netflix", "spotify", "youtube", "prime video", "disney"], "assinaturas"),
]

STOPWORDS_PT = {
    "gastei","paguei","comprei","debitei","recebi","ganhei","salario","reembolso",
    "no","na","nos","nas","em","de","do","da","dos","das","pra","para","por",
    "um","uma","uns","umas","com","ao","aos","as","os","o","a"
}

def extract_keyword_for_memory(text_norm: str) -> str:
    # 1) se bater em alguma keyword das regras locais, salva essa keyword
    for keywords, _cat in LOCAL_RULES:
        for kw in keywords:
            kw_norm = normalize_text(kw)
            if kw_norm and (contains_word(text_norm, kw_norm) or kw_norm in text_norm):
                return kw_norm

    # 2) fallback: pega o último “token útil”
    tokens = [t for t in text_norm.split() if t and t not in STOPWORDS_PT and len(t) >= 3]
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

    # se rate veio como fração (0.01 = 1%), converte pra %
    display = rate * 100 if rate <= 1 else rate

    # formatação limpa (sem 1.0000)
    if abs(display - round(display)) < 1e-12:
        pct = str(int(round(display)))
    else:
        pct = f"{display:.6f}".rstrip("0").rstrip(".")

    return f"{pct}% {period}"

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
    "alimentação": ["ifood", "uber eats", "rappi", "restaurante", "lanche", "pizza", "hamburguer", "café", "padaria"],
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

