import re

def parse_interest(text: str):
    raw = text.lower()

    # bloqueia explicitamente "1," ou "1." (com espaços depois também)
    if re.search(r'\d+\s*[.,]\s*(?:%|\b)', raw):
        # exemplos que caem aqui: "1,", "1.", "1, %", "1. %"
        # mas "1,0" NÃO cai porque tem dígito após a vírgula
        if not re.search(r'\d+\s*[.,]\s*\d+', raw):
            return None

    # pega número: 1 / 1.1 / 1,1 / 0,03 etc
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*%?', raw)
    if not m:
        return None

    taxa_pct = float(m.group(1).replace(",", "."))
    taxa = taxa_pct / 100.0

    # período
    if re.search(r'\b(ao|a|por)\s*dia\b|/dia', raw):
        period = "daily"
    elif re.search(r'\b(ao|a|por)\s*m[eê]s\b|/mes|/mês', raw):
        period = "monthly"
    elif re.search(r'\b(ao|a|por)\s*ano\b|/ano', raw):
        period = "yearly"
    else:
        return None

    return taxa, period
