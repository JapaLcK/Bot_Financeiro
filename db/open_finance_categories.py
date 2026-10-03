"""Categoria da Pluggy (inglês) -> categoria do PigBank (issue #149).

Mapa aprovado pelo dono. Vale para o que vai para `launches.categoria` e
`credit_transactions.categoria`; o espelho `open_finance_transactions.category`
continua cru (a classificação interna/fatura/aplicação lê o inglês).
"""
from .categories import ensure_user_category

# Categoria PigBank -> rótulos da Pluggy (description, em minúsculas).
_GRUPOS: dict[str, tuple[str, ...]] = {
    "mercado": ("groceries",),
    "alimentação": ("eating out", "food delivery", "food and drinks"),
    "transporte": (
        "taxi and ride-hailing", "gas stations", "automotive", "parking",
        "tolls and in vehicle payment", "vehicle maintenance", "transportation", "bicycle",
    ),
    "saúde": (
        "pharmacy", "healthcare", "wellness and fitness", "gyms and fitness centers",
        "hospital clinics and labs", "dentist",
    ),
    "moradia": (
        "housing", "electricity", "water", "rent", "gas", "utilities", "internet",
        "telecommunications", "mobile",
    ),
    "assinaturas": ("digital services", "music streaming", "tv"),
    "educação": ("education", "school", "university", "bookstore"),
    "pets": ("pet supplies and vet",),
    "compras online": ("online shopping",),
    "lazer": (
        "leisure", "cinema, theater and concerts", "tickets", "sports practice",
        "travel", "airport and airlines", "accomodation",
    ),
    "rendimentos": ("cashback",),
    "transferencia_interna": (
        "same person transfer", "same person transfer - cash", "transfer - internal",
        "transfer - cash",
    ),
    "pagamento_fatura": ("credit card payment",),
    "investimento_aporte": ("automatic investment", "investments", "mutual funds"),
    "outros": ("mileage programs",),
    # Categorias novas: nascem no catálogo do cliente (`garantir_no_catalogo`).
    "compras": (  # loja física; "online shopping" é outra
        "shopping", "clothing", "houseware", "electronics", "office supplies",
        "sports goods", "kids and toys",
    ),
    "transferências": ("transfers", "transfer - pix", "third party transfers"),
    "renda": ("entrepreneurial activities", "non-recurring income"),
    "serviços": ("services",),
    "apostas": ("gambling",),
    "taxas e juros": (
        "tax on financial operations", "interests charged",
        "late payment and overdraft costs", "credit card fees", "bank fees",
        "wire transfer fees and atm fees",
    ),
    "empréstimos": ("financing", "loans and financing", "loans"),
}

PLUGGY_PARA_PIGBANK: dict[str, str] = {r: cat for cat, rs in _GRUPOS.items() for r in rs}


def categoria_pigbank(rotulo: str | None) -> str | None:
    """Categoria do PigBank para o rótulo da Pluggy; None se não há par. Sem par
    exato, tenta a parte antes de " - " ("Same person transfer - PIX"), como o
    prefixo de `classify_open_finance_launch`."""
    k = (rotulo or "").strip().lower()
    return PLUGGY_PARA_PIGBANK.get(k) or PLUGGY_PARA_PIGBANK.get(k.split(" - ")[0])


def garantir_no_catalogo(user_id: int, nomes) -> None:
    """Cria em `user_categories` as categorias novas usadas pelo import. Fora de
    conexão aberta: `ensure_user_category` abre a sua e é best-effort."""
    for n in sorted(set(nomes)):
        ensure_user_category(user_id, n, exigir_plano=False)
