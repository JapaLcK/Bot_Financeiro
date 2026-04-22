import re


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
