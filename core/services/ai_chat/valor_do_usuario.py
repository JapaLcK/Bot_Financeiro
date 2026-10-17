"""
core/services/ai_chat/valor_do_usuario.py — o dinheiro que a IA grava tem que
ter sido escrito pelo usuário.

`add_launch` e irmãs gravam sem confirmação; sem esta âncora o modelo grava o
`valor` que ele escolhe (medido: copiou "50"/"450,73" dos exemplos do system
prompt). Vale em todo canal e com a flag WA_IA_PRIMEIRO desligada.

Só texto do USUÁRIO conta: linhas assistant e tool nunca (é o que impede copiar
exemplo do bot). Leitores reaproveitados: `ai_guard.collect_evidence` (dígitos BRL, já
descarta "dia 20", "12x", "em 2026", "#50"), `_extract_valor` (multiplicador: "1,2 mil"),
`_words_to_number` (extenso: "cinquenta") e `utils_text.extract_parcelas` (contagem, a mesma do
`parcelar` do cartão). Só uma regex é daqui: `_NAO_VALOR_RE` (número que não é valor: CPF, data,
cartão final).
"""
from __future__ import annotations

import json
import re

import db
from core.observability import log_system_event_sync
from core.services.ai_guard import _MASCARA_NAO_MONETARIA_RE, _NAO_MONETARIO_DO_USUARIO_RE, collect_evidence
from parsers import _extract_valor, _words_to_number
from utils_text import extract_parcelas

from ._context import CURRENT_PLATFORM, CURRENT_USER_MESSAGE

# ponytail: a mensagem atual + até 3 ANTERIORES do usuário desde o último corte
# (`system`). "gastei 500" + 2 mensagens de outro assunto + a atual ainda grava;
# com 3 entre elas é recusado. Janela por tempo/turno só se isso aparecer no log
# `ai_valor_sem_ancora`.
_MENSAGENS = 3

# Número que a mensagem do usuário traz e NÃO é valor (o `ai_guard` já cobre dia/Nx/
# ano com "em|de"): CPF/CNPJ, data, hora, porcentagem e "palavra + dígitos"
# (cartão final 1234, ano 2026, conta/ag/cep/tel). Local de propósito: o
# `_MASCARA_NAO_MONETARIA_RE` do ai_guard alimenta a guarda de afirmações. Os dígitos
# depois da palavra pedem 4+. `conta`/`cartão`/`final` sozinhos NÃO mascaram: vêm antes de
# dinheiro ("paguei a conta 1200", "valor final 1500"); só "cartão final"/"conta final" 1234.
_NAO_VALOR_RE = re.compile(
    r"\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-?\d{2}"
    r"|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b\d{1,2}:\d{2}\b|\d+(?:[.,]\d+)?\s*%"
    r"|\b(?:(?:cart[aã]o|conta)\s+final|ag[eê]ncia|ag|ano|n[º°]|n[uú]mero|cep|telefone|tel|cel)\.?\s*:?\s*\d[\d\-]{3,}",
    re.IGNORECASE,
)

# Resposta terminal (sem nova ida ao modelo) e o que o histórico guarda: sem o
# número inventado, senão o ai_guard o leria como evidência.
RECUSA = (
    json.dumps({"status": "valor_nao_informado", "message": "Nada foi gravado: o valor não está no "
               "que o usuário escreveu. Use só número que ele digitar; exemplo não é dado do usuário."},
               ensure_ascii=False),
    "🐷 Não anotei nada: não achei esse valor no que você escreveu. Qual é o valor?",
)
# O valor está no texto, a contagem de parcelas não: "não achei o valor" mentiria.
RECUSA_PARCELAS = (
    json.dumps({"status": "parcelas_nao_informadas", "message": "Nada foi gravado: a quantidade de parcelas "
               "não está no que o usuário escreveu. Pergunte quantas parcelas."}, ensure_ascii=False),
    "🐷 Não anotei nada: em quantas parcelas foi? Ex.: 12x.",
)
# Outra chamada da mesma rodada já deu resposta terminal (gravou, erro de validação ou a
# pergunta de forma de pagamento): a resposta não pode dizer "nada" nem esconder a recusa.
RECUSA_PARCIAL = "🐷 Não anotei um dos itens: não achei o valor dele no que você escreveu."
RECUSA_PARCIAL_PARCELAS = "🐷 Não anotei um dos itens: em quantas parcelas foi? Ex.: 12x."


def _textos_do_usuario(user_id: int) -> list[str]:
    textos: list[str] = []
    for m in db.ai_get_last_messages(user_id, 30):  # da mais nova para a mais velha
        if m["role"] == "system":  # turno encerrado fora da IA: o que veio antes já foi atendido
            break
        if m["role"] == "user":
            textos.append(m["content"] or "")
    atual = CURRENT_USER_MESSAGE.get()
    if textos and textos[0].strip() == atual:  # o runner já gravou a atual: não conta duas vezes
        textos = textos[1:]
    return textos[:_MENSAGENS] + [atual]


# "50 centavos" são 50 centavos (não R$ 50); "30 reais e 50 centavos" fica com o `_extract_valor`.
# Por extenso ("cinquenta centavos") é o `_extensos` que separa, abaixo.
_CENTAVOS_RE = re.compile(r"(?<!reais e )(?<!real e )\b(\d{1,9})\s+centavos?\b", re.IGNORECASE)


# Extenso só é valor se a PRÓXIMA palavra for dinheiro, conector ou o fim: "dois amigos", "duas tvs",
# "uma pizza" são contagem de coisa. Lista fechada de propósito (sem substantivo).
_REAIS = ("real", "reais", "conto", "contos", "pila", "pilas")
_CENTAVOS = ("centavo", "centavos")
_CONECTORES = frozenset(
    "de do da dos das no na nos nas num numa em pra pro para por pelo pela pelos pelas com sem e ou a ao aos "
    "à às até ate só so mais menos".split())
_ADMITE = frozenset(_REAIS + _CENTAVOS) | _CONECTORES


# "gastei 49.90" é 49,90: o `collect_evidence` lê todo ponto como milhar (499000) e abriria a porta
# para o modelo gravar R$ 4.990. Ponto + 1-2 dígitos, sem mais dígito nem ".dígito" depois, é decimal;
# 3 dígitos ("1.200", "12.345", "1.000.000") seguem milhar e data ("10.12.2026") fica como está.
# ponytail: "1,234.56" (formato US) não é normalizado; a leitura de hoje é mantida.
_PONTO_DECIMAL_RE = re.compile(r"(?<![\d.,])(\d+)\.(\d{1,2})(?!\d|\.\d|,\d)")

# "dois milhões": o extenso por trecho multiplica; "e meio" / "e trezentos mil" ficam de fora.
_MULT = {"milhão": 1e6, "milhao": 1e6, "milhões": 1e6, "milhoes": 1e6,
         "bilhão": 1e9, "bilhao": 1e9, "bilhões": 1e9, "bilhoes": 1e9}


def _nivel(v: float) -> int:  # milhar > centena > dezena > unidade
    return 4 if v >= 1000 else 3 if v >= 100 else 2 if v >= 20 else 1


def _extensos(t: str) -> list[tuple[float | None, str]]:
    """Um número por extenso por trecho, com a palavra que vem depois dele, só os que passam em
    `_ADMITE`: "cinquenta no mercado e vinte no uber" → [(50, "no"), (20, "no")], não 70.
    O "e" só junta enquanto o nível desce ("cinquenta e cinco", "cento e vinte", "mil e duzentos");
    "cinquenta e vinte" são dois. "<reais> reais e <centavos> centavos" também devolve o total
    ("trinta reais e cinquenta centavos" → (30.5, "reais"))."""
    toks = re.findall(r"\w+|[^\w\s]", t.lower())  # pontuação é token: "gastei dois, ontem" termina o trecho
    runs, prev = [], None
    for i, tok in enumerate(toks):
        v = _words_to_number(tok)
        if v is None:
            prev = prev if tok == "e" else None
            continue
        n = _nivel(v)
        if prev is not None and (n < prev or n == 4 > prev):
            runs[-1][1] = i + 1
        else:
            runs.append([i, i + 1])
        prev = n
    vs = []
    for a, b in runs:
        v, d = _words_to_number(" ".join(toks[a:b])), toks[b] if b < len(toks) else ""
        if d in _MULT:  # a palavra seguinte ao multiplicador é que passa em `_ADMITE`
            v, b = v * _MULT[d], b + 1
            d = toks[b] if b < len(toks) else ""
        vs.append((v, d, a, b))
    out = [(v, d) for v, d, _, _ in vs if not d or not d[0].isalnum() or d in _ADMITE]
    for v, d, _, b in vs:
        if d not in _REAIS or toks[b + 1:b + 2] != ["e"]:
            continue
        cent = [c for c, d2, a2, _ in vs if a2 == b + 2 and d2 in _CENTAVOS]  # extenso: "e cinquenta centavos"
        if not cent and toks[b + 2:b + 3] and toks[b + 2].isdigit() and toks[b + 3:b + 4] and toks[b + 3] in _CENTAVOS:
            cent = [int(toks[b + 2])]  # misto: "e 50 centavos"
        out += [(v + c / 100, "reais") for c in cent if c < 100]
    return out


def _centavos_lidos(textos: list[str]) -> set[int]:
    textos = [_PONTO_DECIMAL_RE.sub(r"\1,\2", _NAO_VALOR_RE.sub(" ", t)) for t in textos]
    lidos = {int(n) for t in textos for n in _CENTAVOS_RE.findall(t)}
    textos = [_CENTAVOS_RE.sub(" ", t) for t in textos]  # "50 centavos" não vira também "R$ 50"
    lidos |= set(collect_evidence([], "\n".join(textos))[0])
    for t in textos:
        limpo = _NAO_MONETARIO_DO_USUARIO_RE.sub(" ", _MASCARA_NAO_MONETARIA_RE.sub(" ", t))
        # sem dígito o `_extract_valor` cai no extenso da mensagem inteira (soma): os `_extensos` leem por trecho
        vs = [_extract_valor(limpo) if re.search(r"\d", limpo) else None]
        for v, depois in _extensos(limpo):
            vs.append(v / 100 if depois.startswith("centavo") else v)   # "cinquenta centavos" são 50, não R$ 50
        for v in vs:
            if v and 0 < v < 1e12:  # também descarta NaN/inf, que o parse_money pode devolver
                lidos.add(round(v * 100))
    return lidos


def _parcelas(bruto, textos: list[str]) -> tuple[int, bool]:
    """(contagem do modelo, o usuário a escreveu?). >1 só vale se ele escreveu essa
    contagem ("12x", "em 12 vezes", "em 12", "doze vezes"); 1/ausente/lixo (Infinity, NaN,
    lista) é 1 e vale."""
    try:
        p = int(bruto or 1)
    except (TypeError, ValueError, OverflowError):
        return 1, True
    return p, p <= 1 or p in {extract_parcelas(t.lower()) for t in textos}


def ancora_valor(user_id: int, tool, args: dict) -> tuple[dict, str | None]:
    """(args, acao). `acao`: None = valor ancorado (ou nada a conferir); "recusa" =
    campo obrigatório sem âncora; "recusa_parcelas" = idem, mas o valor está no texto e
    a contagem de parcelas não; "descarta" = campo opcional sem âncora, já
    removido dos args devolvidos."""
    campo = getattr(tool, "valor_do_usuario", None)  # getattr: o runner trata assim todo atributo de tool
    if campo is None or not isinstance(args, dict) or args.get(campo) is None:
        return args, None
    try:
        v = round(abs(float(args[campo])) * 100)
    except (TypeError, ValueError, OverflowError):  # ilegível/NaN/inf: o execute recusa
        return args, None
    textos = _textos_do_usuario(user_id)
    lidos = _centavos_lidos(textos)
    tem_parcelas = "parcelas" in tool.schema["function"]["parameters"].get("properties", {})
    p, escrita = _parcelas(args.get("parcelas") if tem_parcelas else None, textos)
    if escrita and (v in lidos or (p > 1 and any(v == c * p for c in lidos))):
        return args, None
    acao = "recusa" if campo in tool.schema["function"]["parameters"].get("required", []) else "descarta"
    log_system_event_sync("info", "ai_valor_sem_ancora", "valor da IA sem âncora no texto do usuário",
                          source="ai_chat/valor_do_usuario", user_id=user_id,
                          details={"tool": tool.name, "acao": acao, "motivo": "valor" if escrita else "parcelas",
                                   "plataforma": CURRENT_PLATFORM.get(), "texto_tem_numero": bool(lidos)})
    if acao == "recusa" and not escrita:
        acao = "recusa_parcelas"  # só o retorno: o log acima segue "recusa" + motivo
    return ({k: x for k, x in args.items() if k != campo} if acao == "descarta" else args), acao
