"""core/handlers/declaracao_valor.py — leitor ESTRITO do valor da declaração "paga pelo banco" (ADR 0006, seção
"Leitura do valor da declaração (DP-10)", issue #1310).

O valor da declaração sem par é travado (DP-4: só apaga e relança). A declaração grava UM gasto por mensagem (E1), e só
quando este leitor acha UM número solto (o valor; E3: data, hora, medida e número colado a letras não contam), exato
(E2), e o parser da Carteira lê O MESMO número; senão recusa sem gravar (T10 a T12, T14 a T17). Um ponto de entrada:
`recusa(texto, valor=None)` (`valor` = o que o chamador grava quando não é o parser: fila de valores, fallback do
classificador). `declaracao_banco.recusa_valor` o embrulha em `NaoGravou`. A ordem dos passos está no ADR.
"""
from __future__ import annotations

import re
import unicodedata

from core.handlers import declaracao_banco_textos as tx
from core.handlers import forma_pagamento as fp
from utils_date import _MONTHS_PT
from utils_text import PT_NUM_ALT_NO_ARTICLE, _PT_NUM_WORDS, agrupamento_de_milhar_ok

_LIMITE = 1000  # P0: despesa não tem 1000 caracteres; acima disso recusa (T15) em vez de analisar só uma parte

# Valor abreviado ("2,2k", "dois k", "2kk", "1,5 mi", "50 centavos", "cinquenta centavos", "25 cents"): o parser lê 2,2 / 2 / 50
# (e "trinta reais e cinquenta centavos" vira R$ 80). Recusa também o que parece abreviação mas não é ("anel 18k",
# "99 centavos"): recusar não custa nada, gravar errado custa apagar e relançar.
_ABREVIADO_RE = re.compile(r"(?<![\w.,])\d[\d.,]*\s*(?:kk?|mi)\b|(?<!\w)(?:\w+\s+)?kk?(?!\w)"
                           r"|(?:\S+\s+)?\bcent(?:avos?|s)?\b", re.IGNORECASE)

# P3 parcelado (D5: compra dividida; o extrato mostra a parcela, não o total). Pagar "a parcela"/"o carnê" é UM débito
# (o exemplo do T14), e "2 vezes por semana"/"fui 2x" é frequência; "parcela 1/3" recusa porque o parser leria a data 01/03.
_PARCELADO_RE = re.compile(
    rf"\bparcel(?:ad[oa]s?|ei|ando|ar|o)\b|\ba\s+prazo\b|\bparcela\s+\d+\s*/\s*\d+"
    rf"|\bem\s+(?:\d+|{PT_NUM_ALT_NO_ARTICLE})\s*(?:x|vezes|parcelas?)\b"
    rf"|\b(?:\d+|{PT_NUM_ALT_NO_ARTICLE})\s*(?:x|vezes)\s+de\b|\b(?:\d+|{PT_NUM_ALT_NO_ARTICLE})\s+parcelas\b"
    rf"|\bcrediario\b|\bdividid[oa]\s+em\b|\bdividi\s+em\b")
# "3x", "10 x", "3vezes", "3vzs" junto do valor (rodadas 4 e 5): compra dividida ou conta, nos dois casos o total não sai
# de uma vez. "3x4" (foto) é palavra; "1x" é à vista; "fui 2x no mercado e gastei…" fica no outro pedaço.
_NX_RE = re.compile(r"\b(?:[2-9]|\d{2,})(?:\s?x|vezes|vzs)\b(?!\s*(?:\d|por\b))")  # "2x por semana" é frequência
# E2: valor aproximado (rodada 4: em qualquer posição). `livre`: aproxima o valor onde estiver ("50 no mercado mais ou
# menos"), salvo se estiver ligado a OUTRO número ("por volta de 22h"). `colado`: só conta antes do próprio valor ("uns 50";
# "uns lanches", "até hoje", "quase 2 horas" não). Lista de vocabulário: o que ficar de fora grava (risco no ADR).
_APROX_RE = re.compile(
    r"(?P<epouco>\be\s+(?:pouc[oa]s?|tant[oa]s?)\b)"
    r"|(?P<livre>\bmais ou menos\b|\baprox(?:imadamente|imado)?\b\.?|\bpor ai\b|\bem torno de\b|\bna faixa de\b|\bla pel[oa]s?\b|\bpor volta de\b|\bcerca de\b|~|≈|\+\s*/?\s*-)"
    r"|(?P<colado>\b(?:mais|menos) (?:de|que|do que)\b|\b(?:acima|abaixo|perto) d[eoa]s?\b|\bquase\b|\bate\b|\buns\b"
    r"|\bumas\b|\bno (?:maximo|minimo)\b|\bpelo menos\b|\bbeirando\b|\btipo\b|\bcoisa de\b|\balgo como\b|\buma base de\b)")
_VEZES_RE = re.compile(r"\bx\d")  # "R$ 30 no bar x3": colado a letra o "3" seria palavra, mas é conta

# Composição de numerais (tabela no ADR 0006). Dois numerais vizinhos são UM valor só quando o português os compõe:
# dezena + "e" + unidade ("vinte e cinco"), centena + "e" + (unidade, 11-19 ou dezena) ("duzentos e cinquenta", "cento e
# vinte e cinco"), multiplicador ("dois mil", "cem mil") e "N mil e <resto>" quando o parser lê o resto (prefixo por
# extenso + resto por extenso). Todo o resto é dois valores ou um valor lido pela metade: moeda no meio ("vinte reais e
# cinquenta"), "e meio", unidade/11-19/"cem" + "e" + qualquer coisa ("dois e cinquenta"), "mil" sozinho + resto, dígito + "e" + número.
_EXTENSO = {unicodedata.normalize("NFKD", w).encode("ascii", "ignore").decode(): v for w, v in _PT_NUM_WORDS.items()}
_MOEDA = frozenset("real reais conto contos pila pilas pau paus mango mangos r".split())
_NUMERAIS = frozenset({"dig", "un", "teen", "dez", "cento", "cen", "cem", "mil"})


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


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
    if conj in ("com", "mais"):  # "30 com 2 amigos" é descrição: quem decide é a contagem (P7)
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
    t = _norm(texto)
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
# P5: data e hora saem da CONTAGEM ("dia 5", "10:30", "25 de março (de 2026)", "às 10"). Só tiram número da contagem: o
# valor continua conferido contra o que o parser lê (P11), então tirar demais não grava errado.
_MESES = "|".join([m for m in _MONTHS_PT if len(m) > 3] + ["março"])
_DIA_RE = re.compile(rf"\bdia\s+\d{{1,2}}\b|\b\d{{1,2}}:\d{{2}}\b|\b\d{{1,2}}\s+de\s+(?:{_MESES})(?:\s+de\s+\d{{4}})?\b"
                     rf"|\b[aà]s\s+\d{{1,2}}(?::\d{{2}}|h\d{{0,2}})?\b", re.IGNORECASE)


def _sem_datas(texto: str) -> str:
    """Tira "dia 5", "05/10", "hoje", "25 de março"…: data não é valor (e o "e" entre duas datas não parte nada)."""
    from utils_date import extract_date_from_text
    for _ in range(4):
        novo = _DIA_RE.sub("", (extract_date_from_text(texto)[1] or texto)).strip()
        if novo == texto:
            break
        texto = novo
    return texto


# P6: tokens da contagem (texto normalizado). O separador inicial fica no número (",50" é malformado, não 50).
_TOKEN_RE = re.compile(r"[.,]?\d(?:[\d.,]*\d)?|[a-z$%]+")
_UNIDADES = frozenset("x h hs hr hrs hora horas min minuto minutos kg g gr km m ml l lt litro litros un und unidade unidades %".split())
_MULT = frozenset({"mil", "milhao", "milhoes"})
_CIFRAO = frozenset({"r", "rs", "r$", "$", "us$", "us"})
# "10reais", "R10", "30tão", "37r$", "10$", "US$10", "10reias": é o valor. "30no", "10cada" (preposição colada) também é
# número: um nome próprio não termina assim.
_MOEDA_COLADA = (_MOEDA - {"r"}) | _CIFRAO | {"tao", "brl", "reias", "reaiss", "rais"}
_LIGA_COLADA = frozenset("no na nos nas de do da em pra pro para com por cada".split())


def _malformado(num: str) -> bool:
    """Separador sem parte inteira (",50"), milhar fora do lugar ("1.23.456") ou vírgula de milhar ("1,200" o parser lê
    1,20; "50,123" fica para o T4 do escritor)."""
    return (num[0] in ",." or num.count(",") > 1 or not agrupamento_de_milhar_ok(num)
            or ("." not in num and bool(re.search(r",\d\d0$", num))))


def _numeros(texto: str) -> list[list]:
    """Os números soltos da contagem, [classe, trecho, início, fim] no texto normalizado: num (dígitos; "2 mil" é um) ou
    ext (grupo por extenso). Ficam de fora a medida ("24h", "5 kg", "30%") e o número colado a letras ("99pop", "ps5",
    "3x4"), que é palavra (E3); colado a moeda ("10reais", "R10") é o valor."""
    t = _norm(texto)
    brutos = list(_TOKEN_RE.finditer(t))
    out: list[list] = []
    ult_ext, ult_e, pula = -9, -9, False  # índices brutos: última palavra de um grupo por extenso, último "e"
    for i, m in enumerate(brutos):
        if pula:
            pula = False
            continue
        w, ini, fim = m.group(), m.start(), m.end()
        prox = brutos[i + 1].group() if i + 1 < len(brutos) else ""
        if w[-1].isdigit():
            antes, depois = re.search(r"[a-z$%]+\Z", t[:ini]), re.match(r"[a-z$%]+", t[fim:])
            if prox in _UNIDADES and t[fim:fim + 1 + len(prox)].strip() == prox:
                continue  # medida
            if (antes and antes.group() not in _CIFRAO) or (depois and depois.group() not in _MOEDA_COLADA | _LIGA_COLADA):
                continue  # colado a letras: palavra ("99pop", "ps5")
            if prox in _MULT:
                out.append(["num", f"{w} {prox}", ini, brutos[i + 1].end()])
                pula = True
            else:
                out.append(["num", w, ini, fim])
        elif w == "e":
            ult_e = i
        elif (w in _EXTENSO or w == "mil") and out and out[-1][0] == "ext" and (ult_ext == i - 1 or ult_e == i - 1 == ult_ext + 1):
            out[-1][1], out[-1][3], ult_ext = t[out[-1][2]:fim], fim, i  # "vinte e cinco", "dois mil": um grupo
        elif (w in _EXTENSO and w not in ("um", "uma")) or w == "mil":
            out.append(["ext", w, ini, fim])
            ult_ext = i
    return out


def _valor(tok: list) -> float | None:
    from parsers import _words_to_number
    from utils_text import parse_money
    return parse_money(tok[1]) if tok[0] == "num" else _words_to_number(tok[1])


def _e_numero(palavra: str) -> bool:
    return bool(re.match(r"[.,]?\d", palavra)) or palavra in _EXTENSO or palavra == "mil"


def _aproximado(conta: str, ini: int, fim: int) -> bool:
    """Algum marcador de aproximação aproxima o valor (`conta[ini:fim]`)?"""
    for m in _APROX_RE.finditer(conta):
        if m.group("epouco") and not _MOEDA_RE.sub("", conta[fim:m.start()]).strip():
            return True  # logo depois do valor recusa sempre (rodada 5): "50 e pouco 22h", "50 conto e pouco"
        depois = re.match(r"[\s,]*(?:r\$\s*)?([.,]?\d\S*|[a-z]+)", conta[m.end():])
        if depois and _e_numero(depois.group(1)):
            if m.end() + depois.start(1) == ini:
                return True
            continue  # ligado a outro número: "quase 2 horas", "por volta de 22h", "uns 2 kg"
        if m.group("livre") or m.group("epouco"):
            return True  # "50 no mercado mais ou menos", "80 no bar, por aí", "50 e pouco"
        # `colado` sem número depois: "uns lanches", "até hoje"
    return False


def _pedaco(conta: str, lido: float) -> str | None:
    """O pedaço com o valor que será gravado (`lido` > 0); `conta` é o texto normalizado sem data e hora."""
    nums = _numeros(conta)
    if re.search(r"(?<![a-z])cada\b", conta):  # "R$ 30 cada", "7,50cada", "a cada coxinha": preço da unidade (rodada 6:
        return tx.VALOR_INCERTO  # qualquer "cada", como na r4; "pago cada mês" recusa, falso positivo no ADR)
    if ruim := next((k[1] for k in nums if k[0] == "num" and _malformado(k[1])), None):
        return tx.valor_abreviado(ruim)
    if len(nums) != 1:  # nenhum ("30 min", "um real") ou mais de um número solto (E3: "R$ 80 no 99 pop", "foi 60")
        return tx.VALOR_INCERTO
    ini = nums[0][2]
    if _aproximado(conta, ini, nums[0][3]):
        return tx.APROXIMADO
    if round(_valor(nums[0]) or 0, 2) != round(lido, 2):  # P11: o parser grava OUTRO número ("dia 5 50" = 550)
        return tx.VALOR_INCERTO
    return None


def recusa(texto: str, valor: float | None = None) -> str | None:
    """O texto da recusa (T10-T12, T14-T17) se o valor da declaração não foi lido de forma inequívoca, senão None.
    `valor`: o número que o chamador grava (fila, classificador); sem ele, o que o parser lê."""
    from core.intent_classifier import is_comparative_question
    from parsers import (_extract_target_after_amount, _extract_valor, split_financial_transactions, texto_base,
                         tipo_do_texto)
    texto = texto or ""
    if len(texto) > _LIMITE:  # P0
        return tx.LONGA
    if m := _ABREVIADO_RE.search(texto):  # P1
        return tx.valor_abreviado(m.group(0))
    limpo = fp.limpar(texto, banco=True)  # sem a forma dita: "e 30 pelo banco" termina em "e 30"
    if _PARCELADO_RE.search(_norm(limpo)):  # P3
        return tx.PARCELADO
    partes = [p for p in split_financial_transactions(limpo) if not is_comparative_question(p)]  # P2
    bases = [texto_base(p)[0] for p in partes]
    soltos = [_numeros(_norm(_sem_datas(b))) for b in bases]
    if any(n and _NX_RE.search(_norm(b)) for b, n in zip(bases, soltos)):  # "3x" no pedaço do valor
        return tx.PARCELADO
    if trecho := _numeral_ambiguo(_sem_datas(limpo)):  # par por extenso que não compõe
        return tx.valor_ambiguo(trecho)
    if _VEZES_RE.search(_norm(limpo)):
        return tx.VALOR_INCERTO
    valor = valor if valor and valor > 0 else None  # o classificador sem valor (0, ausente) não traz valor a conferir
    lidos = [_extract_valor(b) or 0 for b in bases]
    if sum(bool(n) for n in soltos) > 1:  # mais de um pedaço com número solto (medida não conta)
        for i, (b, x) in enumerate(zip(bases, lidos)):
            if x > 0 and not _MOEDA_RE.sub("", _extract_target_after_amount(_sem_datas(b))).strip():
                a = min(i, len(partes) - 2)  # "gastei 2 reais" + "gastei 50 no mercado": um valor partido em dois
                ini, fim = partes[a:a + 2]
                verbo = ini.split()[0].lower() + " " if ini else ""
                return tx.valor_ambiguo(f"{ini} e {fim[len(verbo):] if verbo and fim.lower().startswith(verbo) else fim}")
        return tx.UM_POR_MENSAGEM  # E1: um gasto por mensagem
    if valor is not None and not any(x > 0 for x in lidos):
        return tx.VALOR_INCERTO  # o classificador trouxe um valor que não está no texto
    for b, x in zip(bases, lidos):
        if x <= 0 or (valor is None and not tipo_do_texto(b)):
            continue  # sem valor (a fila pergunta) ou o parser não grava (o fallback confere o valor do classificador)
        if r := _pedaco(_norm(_sem_datas(b)), valor if valor is not None else x):  # P5: data e hora fora
            return r
    return None


def reunir(partes: list[str]) -> str:
    """O áudio já partido, de volta numa frase, para `recusa` ver o "e" entre os pedaços. Tira o verbo repetido só onde o
    splitter o devolve (resto começa com dígito ou R$): "paguei internet 100" sem o verbo não seria partido de novo."""
    verbo = partes[0].split()[0].lower() + " " if partes and partes[0].split() else ""
    return " e ".join(p[len(verbo):] if i and verbo and p.lower().startswith(verbo)
                      and re.match(r"\s*(?:r\$|\d)", p[len(verbo):], re.IGNORECASE) else p for i, p in enumerate(partes))


def tem_cartao(partes: list[str]) -> bool:
    """Algum pedaço é no cartão de CRÉDITO ("cartão de débito" não): cartão nunca declara, a mensagem toda vai para o T8."""
    return any(fp._no_cartao(p) for p in partes)
