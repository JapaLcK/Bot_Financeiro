import re

ASSET_TYPES = {
    "cdb": "CDB",
    "lci": "LCI",
    "lca": "LCA",
    "debenture": "Debênture",
    "debênture": "Debênture",
    "cri": "CRI",
    "cra": "CRA",
    "etf renda fixa": "ETF Renda Fixa",
    "etf rf": "ETF Renda Fixa",
    "tesouro selic": "Tesouro Selic",
    "tesouro ipca": "Tesouro IPCA+",
    "tesouro ipca+": "Tesouro IPCA+",
    "tesouro prefixado": "Tesouro Prefixado",
}

TAX_EXEMPT_ASSET_TYPES = {"LCI", "LCA", "CRI", "CRA"}


def tax_profile_for_asset(asset_type: str | None) -> str:
    asset = asset_type or "CDB"
    if asset in TAX_EXEMPT_ASSET_TYPES:
        return "exempt_ir_iof"
    if asset == "ETF Renda Fixa":
        return "etf_rf_15"
    return "regressive_ir_iof"


def detect_asset_type(text: str) -> str:
    raw = (text or "").lower()
    for needle, label in sorted(ASSET_TYPES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(needle)}\b", raw):
            return label
    return "CDB"


def _parse_money_number(raw: str) -> float | None:
    value = raw.strip().replace(" ", "")
    if "," in value and "." in value:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif "," in value:
        value = value.replace(".", "").replace(",", ".")
    elif "." in value:
        parts = value.split(".")
        if len(parts[-1]) == 3:
            value = value.replace(".", "")
    try:
        return float(value)
    except ValueError:
        return None


def parse_initial_amount(text: str) -> float | None:
    raw = text or ""
    pattern = (
        r"(?:valor(?:\s+investido)?|aporte\s+inicial|com\s+aporte(?:\s+de)?|"
        r"investido|aplicado|apliquei|investi)\s*(?:r\$)?\s*(\d[\d.,\s]*)"
    )
    m = re.search(pattern, raw, flags=re.I)
    if not m:
        return None
    value = _parse_money_number(m.group(1))
    return value if value and value > 0 else None


def strip_initial_amount(text: str) -> str:
    pattern = (
        r"(?:valor(?:\s+investido)?|aporte\s+inicial|com\s+aporte(?:\s+de)?|"
        r"investido|aplicado|apliquei|investi)\s*(?:r\$)?\s*\d[\d.,\s]*"
    )
    return re.sub(pattern, "", text or "", flags=re.I).strip(" -–—")


def parse_interest(text: str):
    """
    Extrai (taxa, period) de um texto de criação de investimento.

    Formatos suportados:
      - "1% ao mês"  / "1% a mês"  / "1%/mês"   → (0.01, "monthly")
      - "0,03% ao dia" / "0.03%/dia"              → (0.0003, "daily")
      - "12% ao ano" / "12%/ano"                  → (0.12, "yearly")
      - "110% CDI"  / "110% do CDI"               → (1.10, "cdi")
        (multiplier: 100% CDI = 1.0, 110% CDI = 1.10)

    Retorna None se não conseguir identificar taxa e período.
    """
    raw = text.lower()

    # ── CDI: detecta antes do fallback percentual ──────────────────────────
    # Padrão: <número>% [do] cdi  (ex: "110% CDI", "110% do CDI", "110 % CDI")
    m_cdi = re.search(
        r'(\d+(?:[.,]\d+)?)\s*%\s*(?:do\s+)?cdi\b',
        raw,
        flags=re.IGNORECASE,
    )
    if m_cdi:
        try:
            pct = float(m_cdi.group(1).replace(",", "."))
        except ValueError:
            return None
        if pct <= 0:
            return None
        # multiplicador: 110% CDI → 1.10  (armazenado como rate bruto)
        return pct / 100.0, "cdi"

    # ── Bloqueia "1," ou "1." sem dígito seguinte (ambíguo) ───────────────
    if re.search(r'\d+\s*[.,]\s*(?:%|\b)', raw):
        if not re.search(r'\d+\s*[.,]\s*\d+', raw):
            return None

    # ── Extrai número percentual ───────────────────────────────────────────
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*%', raw)
    if not m:
        return None

    taxa_pct = float(m.group(1).replace(",", "."))
    taxa = taxa_pct / 100.0

    # ── Período ───────────────────────────────────────────────────────────
    if re.search(r'\b(ao|a|por)\s*dia\b|/dia', raw):
        period = "daily"
    elif re.search(r'\b(ao|a|por)\s*m[eê]s\b|/mes|/mês', raw):
        period = "monthly"
    elif re.search(r'\b(ao|a|por)\s*ano\b|/ano', raw):
        period = "yearly"
    else:
        return None

    return taxa, period


def parse_investment_spec(text: str) -> dict | None:
    """
    Extrai uma especificação normalizada de investimento.

    Indexadores suportados:
      - 110% CDI            -> period="cdi", rate=1.10
      - CDI + 2,5% a.a.     -> period="cdi_spread", rate=0.025
      - IPCA + 7,43% a.a.   -> period="ipca_spread", rate=0.0743
      - SELIC + 0,07% a.a.  -> period="selic_spread", rate=0.0007
      - 13,59% a.a.         -> period="yearly", rate=0.1359
    """
    raw = text or ""
    low = raw.lower()
    asset_type = detect_asset_type(raw)

    patterns = [
        ("cdi", "pct_cdi", r"(\d+(?:[.,]\d+)?)\s*%\s*(?:do\s+)?cdi\b", lambda x: round(float(x) / 100.0, 12)),
        ("cdi_spread", "cdi_spread", r"\bcdi\s*\+\s*(\d+(?:[.,]\d+)?)\s*%?\s*(?:a\.?a\.?|ao\s+ano)?", lambda x: round(float(x) / 100.0, 12)),
        ("ipca_spread", "ipca_spread", r"\bipca\+?\s*\+\s*(\d+(?:[.,]\d+)?)\s*%?\s*(?:a\.?a\.?|ao\s+ano)?", lambda x: round(float(x) / 100.0, 12)),
        ("selic_spread", "selic_spread", r"\bselic\s*\+\s*(\d+(?:[.,]\d+)?)\s*%?\s*(?:a\.?a\.?|ao\s+ano)?", lambda x: round(float(x) / 100.0, 12)),
        ("yearly", "fixed", r"(\d+(?:[.,]\d+)?)\s*%\s*(?:a\.?a\.?|ao\s+ano|ano)\b", lambda x: round(float(x) / 100.0, 12)),
    ]

    for period, indexer, pattern, convert in patterns:
        m = re.search(pattern, low, flags=re.I)
        if not m:
            continue

        try:
            rate = convert(m.group(1).replace(",", "."))
        except ValueError:
            return None
        if rate <= 0 and period != "selic_spread":
            return None

        name = strip_initial_amount(raw[:m.start()] + raw[m.end():])
        if not name:
            name = raw.strip()

        return {
            "name": name,
            "rate": rate,
            "period": period,
            "indexer": indexer,
            "asset_type": asset_type,
            "tax_profile": tax_profile_for_asset(asset_type),
        }

    parsed = parse_interest(raw)
    if not parsed:
        return None

    rate, period = parsed
    name = strip_initial_amount(re.sub(r"\s*\d+[.,]?\d*\s*%.*$", "", raw, flags=re.IGNORECASE).strip())
    return {
        "name": name or raw.strip(),
        "rate": rate,
        "period": period,
        "indexer": "fixed" if period == "yearly" else period,
        "asset_type": asset_type,
        "tax_profile": tax_profile_for_asset(asset_type),
    }
