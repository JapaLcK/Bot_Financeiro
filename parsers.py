import re
from utils_text import normalize_text, is_internal_category, parse_money
from utils_date import extract_date_from_text
from core.services.category_service import infer_category, learn_from_explicit_category


# ---------------------------------------------------------------------------
# Conversão de números por extenso (português) → float
# Cobre saídas comuns do Whisper: "mil e trezentos", "trinta e cinco"
# ---------------------------------------------------------------------------

_PT_HUNDREDS = {
    "cem": 100, "cento": 100,
    "duzentos": 200, "duzentas": 200,
    "trezentos": 300, "trezentas": 300,
    "quatrocentos": 400, "quatrocentas": 400,
    "quinhentos": 500, "quinhentas": 500,
    "seiscentos": 600, "seiscentas": 600,
    "setecentos": 700, "setecentas": 700,
    "oitocentos": 800, "oitocentas": 800,
    "novecentos": 900, "novecentas": 900,
}
_PT_TENS = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50,
    "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90,
}
_PT_ONES = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2,
    "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11,
    "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14,
    "quinze": 15, "dezesseis": 16, "dezessete": 17,
    "dezoito": 18, "dezenove": 19,
}
_PT_ALL = {**_PT_HUNDREDS, **_PT_TENS, **_PT_ONES}


def _words_to_number(text: str) -> float | None:
    """
    Tenta converter sequência de palavras numéricas em português para float.
    Ex: "mil e trezentos" → 1300.0, "trinta e cinco" → 35.0
    Retorna None se não reconhecer nada.
    """
    import unicodedata
    def _norm(s: str) -> str:
        s = s.lower().strip()
        return "".join(
            c for c in unicodedata.normalize("NFKD", s)
            if not unicodedata.combining(c)
        )

    tokens = re.split(r"[\s,]+", _norm(text))
    tokens = [t for t in tokens if t and t not in ("e", "de", "com")]

    if not tokens:
        return None
    if not any(t in _PT_ALL or t == "mil" for t in tokens):
        return None

    total = 0.0
    current = 0.0
    found_any = False

    for tok in tokens:
        if tok == "mil":
            current = current if current > 0 else 1
            total += current * 1000
            current = 0.0
            found_any = True
        elif tok in _PT_HUNDREDS:
            current += _PT_HUNDREDS[tok]
            found_any = True
        elif tok in _PT_TENS:
            current += _PT_TENS[tok]
            found_any = True
        elif tok in _PT_ONES:
            current += _PT_ONES[tok]
            found_any = True

    total += current
    return total if found_any and total > 0 else None


def _extract_valor(text: str) -> float | None:
    """
    Extrai o valor monetário de um texto, suportando:
      - Números: "30", "30,50", "R$ 30,50", "2.000" (=2000), "10.000" (=10000)
      - Multiplicador: "10 mil" (=10000), "2,5 mil", "3 milhões"
      - "X reais e Y centavos": "30 reais e 50 centavos" → 30.50
      - Números por extenso: "mil e trezentos" → 1300
    """
    # 1. "X reais e Y centavos" (saída comum do Whisper) — antes do parse_money,
    #    que só pegaria a parte inteira e ignoraria os centavos.
    m = re.search(
        r"(\d+)\s+reais?\s+e\s+(\d+)\s+centavos?",
        text, re.IGNORECASE
    )
    if m:
        return float(m.group(1)) + float(m.group(2)) / 100

    # 2. Qualquer número em dígitos → delega pro parse_money, fonte ÚNICA que
    #    trata milhar/decimal BR ("2.000"=2000, "10.000"=10000, "30,50"=30.5) e
    #    o multiplicador "mil"/"milhão". Antes, a lógica local aqui pegava só o
    #    "2" de "2.000" (bug do áudio "adicione 2.000 de saldo" → R$ 2,00).
    val = parse_money(text)
    if val is not None:
        return val

    # 3. Número por extenso ("dez mil", "trezentos") — parse_money não cobre.
    return _words_to_number(text)


def _extract_explicit_category(raw_text: str) -> tuple[str, str | None]:
    t = (raw_text or "").strip()
    if not t:
        return t, None

    # formato: #alimentacao
    m = re.search(r"(?:^|\s)#([a-zA-ZÀ-ÿ0-9_\-]+)\b", t)
    if m:
        cat = m.group(1)
        t2 = (t[: m.start()] + t[m.end() :]).strip()
        return t2, cat

    # formato: cat=alimentacao
    m = re.search(r"(?:^|\s)cat=([a-zA-ZÀ-ÿ0-9_\-]+)\b", t)
    if m:
        cat = m.group(1)
        t2 = (t[: m.start()] + t[m.end() :]).strip()
        return t2, cat

    # formato: "categoria alimentacao" ou "categoria: alimentacao"
    m = re.search(r"\bcategor(?:ia)?[:\s]+([a-zA-ZÀ-ÿ0-9_\-]+)\b", t, re.IGNORECASE)
    if m:
        cat = m.group(1)
        t2 = (t[: m.start()] + t[m.end() :]).strip()
        return t2, cat

    return t, None


def _extract_target_after_amount(text_base: str) -> str:
    t = (text_base or "").strip()
    if not t:
        return ""

    t = re.sub(r"^\s*(gastei|gasto|paguei|pagar|comprei|debitei|mandei|enviei|pixei|recebi|receita|ganhei|entrou|caiu|pingou|pinguei|embolsei|adicionar|adicione|adiciona|adicionei|adicionou|somar|soma|some|somei)\b", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^\s*r\$\s*", "", t, flags=re.IGNORECASE).strip()
    # Consome o valor inteiro, incl. separador de milhar + decimal ("1.234,56").
    t = re.sub(r"^\s*\d[\d.,]*", "", t, count=1).strip()
    # multiplicador "mil"/"milhão" que sobrou depois do dígito ("10 mil" → "mil")
    t = re.sub(r"^\s*(mil|milh[oõ]es|milhao|milhão)\b", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^\s*reais?\b", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"^\s*(de|do|da|dos|das|no|na|nos|nas|em|pra|para|ao|aos|a)\b", "", t, flags=re.IGNORECASE).strip()
    t = re.sub(r"\s+", " ", t).strip(" -:;,.")
    # Valor por extenso ("gastei cinquenta") não é consumido pelo strip de
    # dígito acima — sem isso, a palavra do número vazava como se fosse a
    # descrição da compra ("cinquenta" virava alvo, categoria "outros", e o
    # bot nunca perguntava "em que você gastou?"). `_words_to_number` sozinho
    # não serve pra essa checagem — ele ignora token desconhecido em vez de
    # abortar ("cinquenta mercado" também vira 50.0), então é preciso
    # confirmar que TODO token restante é número por extenso, não só que
    # ALGUM é.
    if t:
        toks = [tok for tok in re.split(r"\s+", t) if tok]
        if toks and all(
            tok.lower() in ("e", "de", "com", "reais", "real", "r$") or _words_to_number(tok) is not None
            for tok in toks
        ):
            return ""
    return t


# Mensagem que começa com o valor, com ou sem "R$": "77,90 mercado", "50 uber",
# "R$ 1.234,56 aluguel". Sem palavra-chave, isso é despesa por padrão (atalho).
_STARTS_WITH_VALUE_RE = re.compile(r"^\s*(?:r\$\s*)?\d", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Separação de múltiplos lançamentos numa única mensagem
# ---------------------------------------------------------------------------

# Verbos que iniciam um lançamento financeiro.
_FINANCIAL_VERBS = (
    r"gastei|paguei|comprei|debitei|mandei|enviei|pix|gasto|"
    r"recebi|ganhei|receita|entrou|caiu|pingou|pinguei|embolsei"
)

# Conectores de soma que podem introduzir um segundo lançamento. Ordenados do
# mais longo pro mais curto (a alternância do regex é ordenada), senão "e"
# casaria antes de "e mais". O segundo lançamento pode começar com um verbo
# ("e gastei 100") OU direto com um valor ("e mais 800", "mais R$ 30") — nesse
# caso é implícito e herda o verbo/tipo do lançamento anterior. A vírgula
# ("50 uber, 30 café") é a mesma coisa numa lista sem repetir o conector.
_SPLIT_TX_RE = re.compile(
    # ",\s+" (não "\s*,\s*"): a vírgula decimal brasileira ("77,90") nunca tem
    # espaço depois — só a vírgula de lista tem ("uber, 30 café"). Sem exigir
    # o espaço, "77,90 mercado" virava dois lançamentos ("77" e "90 mercado").
    r"(?:,\s+|\s+(?:e\s+mais|mais\s+também|e\s+também|mas\s+também|além\s+disso|também|mais|e)\s+)"
    rf"(?={_FINANCIAL_VERBS}|r\$|\d)",
    re.IGNORECASE,
)
_LEAD_VERB_RE = re.compile(rf"^\s*({_FINANCIAL_VERBS})\b", re.IGNORECASE)

# Item final de uma lista SEM valor ("... 30 café e o aluguel") — o "e" acima
# exige um valor/verbo logo depois pra não separar frases comuns ("fui ao
# banco e ao mercado"), então "e o aluguel" nunca vira um split ali. Só entra
# em jogo DEPOIS que _SPLIT_TX_RE já achou ≥2 partes (lista confirmada) e só
# no ÚLTIMO pedaço — evita separar uma frase solta que só por acaso tem um "e".
_TRAILING_VALUELESS_RE = re.compile(r"^(?P<head>.+?)\s+e\s+(?P<tail>[^\d]+)$", re.IGNORECASE)


def split_financial_transactions(text: str) -> list[str]:
    """
    Detecta múltiplos lançamentos numa única mensagem (texto ou áudio) e os
    separa.
    Ex: "recebi 600 da mãe e gastei 100 no mercado"
        → ["recebi 600 da mãe", "gastei 100 no mercado"]
        "gastei 500 no ifood e mais 800 no mercado"
        → ["gastei 500 no ifood", "gastei 800 no mercado"]

    O segundo lançamento pode vir com verbo explícito ("... e gastei 100 ...")
    ou implícito, só somando um valor ("... e mais 800 ..."). No caso implícito
    o segmento herda o verbo do lançamento anterior — o usuário quase sempre
    soma mexidas do mesmo tipo (despesa com despesa, receita com receita).

    Retorna lista com um único item se não detectar múltiplos lançamentos.
    """
    parts = _SPLIT_TX_RE.split(text)
    cleaned = [p.strip() for p in parts if p.strip()]
    if len(cleaned) <= 1:
        return [text]

    m = _TRAILING_VALUELESS_RE.match(cleaned[-1])
    if m and _extract_valor(m.group("tail")) is None and _extract_valor(m.group("head")) is not None:
        cleaned[-1] = m.group("head").strip()
        cleaned.append(m.group("tail").strip())

    # Herança de verbo: segmentos que começam só com valor ("800 no mercado")
    # ganham o verbo do último lançamento com verbo explícito.
    result: list[str] = []
    last_verb: str | None = None
    for seg in cleaned:
        m = _LEAD_VERB_RE.match(seg)
        if m:
            last_verb = m.group(1)
            result.append(seg)
        elif last_verb:
            result.append(f"{last_verb} {seg}")
        else:
            result.append(seg)
    return result


_RECEITA_VERBS = {"recebi", "receita", "ganhei"}


def describe_valueless_launch(text: str) -> tuple[str, str] | None:
    """
    Para um trecho que parece um lançamento mas está SEM valor ("paguei o
    aluguel", "e o mercado"), retorna (tipo, descrição_limpa) — usado pra
    perguntar o valor ao usuário em vez de dropar o lançamento.

    Retorna None se o trecho já tem valor, não tem verbo financeiro
    reconhecível, ou sobra sem descrição (ex: só "paguei").
    """
    if _extract_valor(text) is not None:
        return None
    m = _LEAD_VERB_RE.match(text)
    if not m:
        return None
    verb = m.group(1).lower()
    tipo = "receita" if verb in _RECEITA_VERBS else "despesa"
    # remove verbo + preposição inicial; depois tira artigos que sobrarem
    desc = _extract_target_after_amount(text)
    desc = re.sub(r"^\s*(o|a|os|as|um|uma|uns|umas)\b", "", desc, flags=re.IGNORECASE).strip()
    if not desc:
        return None
    return tipo, desc


# Verbos que marcam dinheiro que ENTROU, no formato de prefixo usado pelo
# startswith abaixo (com o espaço). Módulo-level porque os handlers de consulta
# ("o que caiu em rendimentos") precisam do MESMO conjunto — lista duplicada lá
# foi o que fez "caiu"/"pingou"/"embolsei" caírem no caminho expense-only.
#
# ACOPLAMENTO EM MÃO ÚNICA — leia antes de acrescentar verbo aqui:
# `_PEDE_RECEITA_RE` (core/handlers/launches.py) é derivado desta tupla, então
# tudo que entra aqui passa a marcar CONSULTA como pergunta de receita. Só verbo
# de RECEITA entra. Verbo de LANÇAMENTO que não é pergunta de receita
# ("respingou 200 do freela") mudaria em silêncio o tipo detectado nas consultas;
# se precisar de um desses, ponha na lista do startswith, não nesta tupla.
RECEITA_START_VERBS = (
    "recebi ", "receita ", "ganhei ", "entrou ", "caiu ", "pingou ", "pinguei ", "embolsei ",
)


def parse_receita_despesa_natural(user_id: int, raw_text: str) -> dict | None:
    text_clean = (raw_text or "").strip()
    if not text_clean:
        return None

    # categoria explícita (se houver)
    text_for_parse, explicit_cat = _extract_explicit_category(text_clean)

    # extrai data do texto
    dt_evento, text_without_date = extract_date_from_text(text_for_parse)

    # usa o texto sem a data para tipo/categoria/valor
    text_base = text_without_date.strip() if text_without_date else text_for_parse.strip()
    raw_norm = normalize_text(text_base)

    # tipo — receita SEMPRE exige palavra-chave explícita ("recebi"/"receita"/
    # "ganhei"/"entrou"/"caiu"/"pingou"/"pinguei"/"embolsei" — o mesmo conjunto
    # de verbos que core/intent_classifier.py já roteia pra launches.add como
    # receita). Sem palavra-chave, uma mensagem que começa com o valor
    # ("77,90 mercado") é despesa por padrão: é o atalho mais usado pra lançar
    # gasto, então o user não precisa escrever "gastei" toda vez.
    tipo = None
    if raw_norm.startswith(("gastei ", "gasto ", "paguei ", "pagar ", "comprei ", "debitei ", "mandei ", "enviei ", "pixei ")):
        tipo = "despesa"
    elif raw_norm.startswith(RECEITA_START_VERBS):
        tipo = "receita"
    elif "saldo" in raw_norm and raw_norm.startswith((
        "adicionar ", "adicione ", "adiciona ", "adicionei ", "adicionou ",
        "somar ", "soma ", "some ", "somei ",
    )):
        # "adicione 10 mil de saldo" / "somar 500 ao saldo" → adiciona dinheiro
        # ao saldo, tratado como receita. "adicionar" sozinho é ambíguo (cartão,
        # caixinha, categoria), então só vale quando a frase fala de "saldo".
        tipo = "receita"
    elif _STARTS_WITH_VALUE_RE.match(text_base):
        tipo = "despesa"
    else:
        return None

    # valor
    valor = _extract_valor(text_base)
    if valor is None or valor <= 0:
        return None

    # Passa o texto NÃO normalizado pra infer_category preservar maiúsculas
    # (a detecção de ticker BR exige uppercase: PETR4, VALE3, MXRF11...).
    res = infer_category(user_id=user_id, text_base=text_base, explicit_category=explicit_cat)
    categoria = res.category

    # aprendizado automático (somente se explícita)
    if explicit_cat:
        inferred_no_explicit = infer_category(
            user_id=user_id,
            text_base=text_base,
            explicit_category=None
        ).category
        learn_from_explicit_category(
            user_id=user_id,
            text_base=raw_norm,
            chosen_category=explicit_cat,
            inferred_category=inferred_no_explicit,
            source="manual",
            launch_id=None,
        )

    alvo = _extract_target_after_amount(text_base)

    return {
        "tipo": tipo,
        "valor": valor,
        "categoria": categoria,
        "category_reason": res.reason,
        "alvo": alvo,
        "nota": text_base.strip(),
        "criado_em": dt_evento,
        "is_internal_movement": is_internal_category(categoria),
    }
