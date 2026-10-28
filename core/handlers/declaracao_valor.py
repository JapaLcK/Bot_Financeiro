"""core/handlers/declaracao_valor.py — a declaração "paga pelo banco" só grava se o VALOR foi lido de forma inequívoca
(ADR 0006, PR-B; tabela forma x canal em `.time-dev/pr-b/enumeracao-valor.md`).

O valor da declaração sem par é travado (DP-4: só apaga e relança), então ler só uma parte ("2 reais e 50" gravando R$ 2 e
R$ 50) custa caro. Critério estrutural, em vez de uma regex por frase: depois de tirar a forma dita e a data, a frase é
recusada sem gravar quando (1) o splitter a parte e algum pedaço tem valor SEM descrição; (2) a descrição que sobra do
parse começa com número ou "e/com/mais" + número ("e 50 no mercado"); (3) há dois numerais vizinhos que o português não
compõe num valor só ("dois cinquenta", "vinte reais e cinquenta", "dois e meio", "mil e duzentos"); (4) há abreviação (k, mi,
centavos). A data sai antes de tudo (dois "dia 5 e 30 na padaria" não são ambiguidade). Um ponto de entrada: `recusa(texto)`.
Devolve o texto T10/T11 (str) ou None; `declaracao_banco.recusa_valor` o embrulha em `NaoGravou`.
"""
from __future__ import annotations

import re
import unicodedata

from core.handlers import declaracao_banco_textos as tx
from core.handlers import forma_pagamento as fp
from utils_date import _MONTHS_PT
from utils_text import PT_NUM_ALT, _PT_NUM_WORDS, normalize_text

_LIMITE = 1000  # caracteres analisados: as regex abaixo são quadráticas em texto patológico, e despesa não tem 1000

# Valor abreviado ("2,2k", "dois k", "2kk", "1,5 mi", "50 centavos", "cinquenta centavos", "25 cents"): o parser lê 2,2 / 2 / 50
# (e "trinta reais e cinquenta centavos" vira R$ 80). Recusa também o que parece abreviação mas não é ("anel 18k",
# "99 centavos"): recusar não custa nada, gravar errado custa apagar e relançar.
_ABREVIADO_RE = re.compile(r"(?<![\w.,])\d[\d.,]*\s*(?:kk?|mi)\b|(?<!\w)(?:\w+\s+)?kk?(?!\w)"
                           r"|(?:\S+\s+)?\bcent(?:avos?|s)?\b", re.IGNORECASE)

# Valor solto no fim ("50 no mercado e 30"): o splitter o parte em " e N" e o pedaço não tem descrição.
_SOLTO_FIM_RE = re.compile(
    r"(?<![\w.,])\d[\d.,]*[^\d.,]*?\be\s+(?:mais\s+)?\d[\d.,]*(?:\s*(?:reais|real))?\s*[.,;!?]*$", re.IGNORECASE)

# Composição de numerais (tabela completa em `.time-dev/pr-b/enumeracao-valor.md` e no ADR 0006). Dois numerais vizinhos
# são UM valor só quando o português os compõe: dezena + "e" + unidade ("vinte e cinco"), centena + "e" + (unidade, 11-19
# ou dezena) ("duzentos e cinquenta", "cento e vinte e cinco"), multiplicador ("dois mil", "cem mil") e "N mil e <resto>"
# quando o parser lê o resto (prefixo por extenso + resto por extenso). Todo o resto é dois valores ou um valor lido pela
# metade: moeda no meio ("vinte reais e cinquenta"), "e meio", unidade/11-19/"cem" + "e" + qualquer coisa ("dois e
# cinquenta", "quinze e cinquenta"), "mil" sozinho + resto, dígito + "e" + número.
_EXTENSO = {unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode(): v for w, v in _PT_NUM_WORDS.items()}
_MOEDA = frozenset("real reais conto contos pila pilas pau paus mango mangos r".split())
_NUMERAIS = frozenset({"dig", "un", "teen", "dez", "cento", "cen", "cem", "mil"})


def _classe(tok: str) -> str | None:
    if tok[0].isdigit():
        return "dig"
    if tok in ("meio", "meia", "mil", "e", "com", "mais"):
        return {"meio": "meio", "meia": "meio", "mil": "mil"}.get(tok, "conj")
    if tok in _MOEDA:
        return "moeda"
    v = _EXTENSO.get(tok)
    if v is None:
        return None
    return "un" if v < 10 else "teen" if v < 20 else "dez" if v < 100 else "cem" if tok == "cem" else "cento" if tok == "cento" else "cen"


def _compoe(a: str, b: str, moeda: bool, conj: str | None, antes: str | None) -> bool:
    """`a [moeda] [conj] b` é um valor só (True) ou dois valores / um valor lido pela metade (False)?"""
    if b == "mil":
        return not moeda and conj is None
    if moeda:
        return False
    if conj in ("com", "mais"):  # "30 com 2 amigos" é descrição; "2 com 50 no mercado" a regra da descrição que sobra pega
        return True
    if conj is None:  # vizinhos sem "e": "dois cinquenta", "mil duzentos", "50 + 30" ("12,90" é UM token, a vírgula fica dentro)
        return (a == "dig") != (b == "dig")
    if a == "mil":  # "dois mil e quinhentos" o parser lê; "mil e duzentos" e "2 mil e quinhentos" ele lê só o "mil"
        return (antes == "dig" and b == "dig") or (antes in ("un", "teen", "dez", "cento", "cen", "cem") and b in ("un", "teen", "dez", "cen", "cento"))
    if a == "dez":
        return b == "un"
    if a in ("cento", "cen"):
        return b in ("un", "teen", "dez")
    return False


def _numeral_ambiguo(texto: str) -> str | None:
    """O trecho com dois numerais vizinhos que não compõem um valor só, ou None."""
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    toks = [(m.group(), m.start(), m.end()) for m in re.finditer(r"\d[\d.,]*|[a-z]+", t)]
    for i, (w, ini, _) in enumerate(toks):
        a = _classe(w)
        if a not in _NUMERAIS:
            continue
        j, moeda, conj = i + 1, False, None
        while j < len(toks) and _classe(toks[j][0]) == "moeda":
            moeda, j = True, j + 1
        if j < len(toks) and _classe(toks[j][0]) == "conj":
            conj, j = toks[j][0], j + 1
        if j >= len(toks):
            continue
        b = _classe(toks[j][0])
        if b not in _NUMERAIS and b != "meio":
            continue
        antes = _classe(toks[i - 1][0]) if i else None
        if not _compoe(a, b, moeda, conj, antes):
            return t[ini:toks[j][2]]
    return None


# Moeda coloquial que sobra como "descrição" ("gastei 2 contos" -> "contos"): não é descrição.
_MOEDA_RE = re.compile(r"^(?:\s*(?:reais|real|contos?|pilas?|paus|mangos?|r\$))+", re.IGNORECASE)
# A descrição que sobra do parse começa com outro número: dígito ("50 no mercado") ou "e/com/mais" + número ("e 50 de
# pão", "e duzentos de aluguel", "com meio"). Extenso SEM conjunção fica de fora de propósito: o parser deixa "vinte e
# cinco no mercado" inteiro na descrição (o valor 25 já foi lido); "dois cinquenta" é pego por `_numeral_ambiguo`.
_PREP = r"(?:no|na|nos|nas|em|num|numa|pra|para|pro|ao|e|com|mais|reais?)"
_ALVO_NUMERO_RE = re.compile(
    rf"^(?:(?:e|com|mais)\s+(?:\d[\d.,]*|meio|meia|(?:{PT_NUM_ALT})(?:\s+(?:e\s+)?(?:{PT_NUM_ALT}))*)(?=\s*$|\s+(?:de|do|da|dos|das|{_PREP})\b)"
    rf"|\d[\d.,]*(?=\s*$|\s+{_PREP}\b))", re.IGNORECASE)  # "25 de março" (rua) não é segundo valor
_DIA_RE = re.compile(r"\bdia\s+\d{1,2}\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE)  # "dia 5", "10:30"


def _sem_datas(texto: str) -> str:
    """Tira "dia 5", "05/10", "hoje", "25 de março"…: data não é valor (e o "e" entre duas datas não parte nada)."""
    from utils_date import extract_date_from_text
    for _ in range(4):
        novo = _DIA_RE.sub("", (extract_date_from_text(texto)[1] or texto)).strip()
        if novo == texto:
            break
        texto = novo
    return texto


def _por_pedaco(partes: list[str]) -> str | None:
    from core.intent_classifier import is_comparative_question
    from parsers import _extract_target_after_amount, _extract_valor
    partes = [p for p in partes if not is_comparative_question(p)]
    for i, p in enumerate(partes):
        bruto = fp.limpar(p, banco=True)
        limpo = _sem_datas(bruto)
        if not ((_extract_valor(limpo) or 0) > 0):
            if len(partes) > 1 and (_extract_valor(bruto) or 0) > 0:  # o splitter cortou dentro de uma data ("20 e 21 de outubro")
                return tx.valor_ambiguo(bruto)
            continue  # sem valor: a fila de valores pergunta
        alvo = _MOEDA_RE.sub("", _extract_target_after_amount(limpo)).strip()
        if len(partes) > 1 and not alvo:  # o outro lado do "e": "gastei 2 reais" + "gastei 50 no mercado"
            a = min(i, len(partes) - 2)
            ini, fim = (fp.limpar(x, banco=True) for x in partes[a:a + 2])
            verbo = ini.split()[0].lower() + " " if ini else ""
            return tx.valor_ambiguo(f"{ini} e {fim[len(verbo):] if verbo and fim.lower().startswith(verbo) else fim}")
        if _ALVO_NUMERO_RE.match(normalize_text(alvo)) or (len(partes) > 1 and normalize_text(alvo) in _MONTHS_PT):
            return tx.valor_ambiguo(bruto)  # (ou o splitter cortou "21 de outubro": o pedaço é só um mês)
    return None


def recusa(texto: str) -> str | None:
    """T10 (abreviação) ou T11 (valor que pode ser um só ou dois) se o valor não foi lido de forma inequívoca."""
    from parsers import split_financial_transactions
    texto = (texto or "")[:_LIMITE]
    if m := _ABREVIADO_RE.search(texto):
        return tx.valor_abreviado(m.group(0))
    limpo = fp.limpar(texto, banco=True)  # sem a forma dita: "e 30 pelo banco" termina em "e 30"
    sem = _sem_datas(limpo)
    if trecho := _numeral_ambiguo(sem) or (m.group(0) if (m := _SOLTO_FIM_RE.search(sem)) else None):
        return tx.valor_ambiguo(trecho)
    return _por_pedaco(split_financial_transactions(limpo))  # os pedaços que o `add` vai de fato ler


def reunir(partes: list[str]) -> str:
    """O áudio já partido, de volta numa frase (sem repetir o verbo de cada pedaço), para `recusa` ver o "e" entre os pedaços."""
    verbo = partes[0].split()[0].lower() + " " if partes and partes[0].split() else ""
    return " e ".join(p[len(verbo):] if i and verbo and p.lower().startswith(verbo) else p for i, p in enumerate(partes))


def tem_cartao(partes: list[str]) -> bool:
    """Algum pedaço é no cartão de CRÉDITO ("cartão de débito" não): cartão nunca declara, a mensagem toda vai para o T8."""
    return any(fp._no_cartao(p) for p in partes)
