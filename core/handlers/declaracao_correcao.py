"""O que a mensagem é, diante da confirmação viva da declaração pelo banco (#1315): correção de valor, cortesia, ou
uma resposta que começa por "sim"/"não". Só lê o texto; quem decide é `declaracao_confirma.responde`.

Vocabulário (uma fonte só, R4-4 e R4-5):
- o início de resposta sai das formas de "sim"/"não" da IA (`_CONFIRM_EXACT`, `_CANCEL_EXACT`) e das formas
  completas da declaração (`declaracao_banco`), sem as letras soltas ("s", "n"), mais o "simm" e o 👍;
- `ABREM`: abrem uma correção sem "sim"/"não" na frente ("foi 60", "é 60", "na verdade 60", "o valor é 60");
- `DUVIDAS`: mudam o gasto sem valor certo ("mais", "ou", "errei");
- `ENCHIMENTO`: palavras que não mudam nada no "só um valor".
"""
from __future__ import annotations

import re
import unicodedata

from core.handlers import declaracao_banco as decl
from core.services.ai_chat.confirmations import _CANCEL_EXACT, _CONFIRM_EXACT, _normalize

ABREM = ("foi", "foram", "era", "é", "na verdade", "o valor")
DUVIDAS = ("mais", "menos", "ou", "errado", "errada", "errei")
ENCHIMENTO = ("mas", "e", "r$", "reais", "real")
# Cortesia sozinha (M-1 A): não é comando nem resposta; a pergunta se repete. "ok" e "beleza" são "sim" (`is_confirm`).
CORTESIA = ("obrigado", "obrigada", "obg", "brigado", "brigada", "valeu", "vlw", "tmj", "por favor", "pfv", "show",
            "legal", "massa", "top")

_ACENTOS = {"a": "[aáàâã]", "e": "[eéê]", "i": "[ií]", "o": "[oóôõ]", "u": "[uú]", "c": "[cç]"}


def _padrao(frase: str) -> str:
    """A frase (sem acento, como nos conjuntos da IA) como regex que aceita o acento e qualquer espaço."""
    return r"\s+".join("".join(_ACENTOS.get(ch, re.escape(ch)) for ch in palavra) for palavra in frase.split())


_FORMAS = sorted({f for f in _CONFIRM_EXACT | _CANCEL_EXACT | decl._SIM_COMPLETO | decl._NAO_COMPLETO if len(f) > 1},
                 key=len, reverse=True)
_INICIO_RE = re.compile(r"^\s*(?:(?:👍[\U0001F3FB-\U0001F3FF️]*|(?:" + decl.SIM_ALONGADO + "|"
                        + "|".join(_padrao(f) for f in _FORMAS) + r")(?!\w))[\s,.;:!-]*)+", re.IGNORECASE)
_ABRE_RE = re.compile(r"^\s*(?:" + "|".join(_padrao(f) for f in ABREM) + r")(?!\w)", re.IGNORECASE)
_MUDA_RE = re.compile(r"(?<!\w)(?:" + "|".join(_padrao(f) for f in ABREM + DUVIDAS) + r")(?!\w)", re.IGNORECASE)
_LANCAMENTO_RE = re.compile(r"\b(?:gastei|paguei|recebi|comprei|ganhei)\b")
# "1,5k", "1.5k", "1,500k" (#1404, DP-k-2 A e 2-A): com "k" quem julga o número é o leitor estrito, como na mensagem original
_DIGITOS_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?|\d[\d.,]*k")
_COMECA_POR_VALOR_RE = re.compile(r"^\s*(?:r\$\s*)?\d", re.IGNORECASE)
_ENCHE = frozenset(w for f in ABREM + ENCHIMENTO for w in f.split()) | {"verdade"}


def resto(texto: str) -> str | None:
    """O que vem depois do "sim"/"não"/👍 inicial, ou None se a mensagem não começa por um (ou não traz mais nada)."""
    m = _INICIO_RE.match(texto or "")
    sobra = (texto or "")[m.end():].strip() if m else ""
    return sobra or None


def cortesia(trecho: str) -> bool:
    """Só cortesia ("obrigado", "valeu", "por favor")?"""
    n = f" {_normalize(trecho)} "
    for c in sorted(CORTESIA, key=len, reverse=True):
        n = n.replace(f" {_normalize(c)} ", " ")
    return bool(_normalize(trecho)) and not n.strip()


def _muda(trecho: str) -> bool:
    """O trecho muda o gasto (traz número ou palavra de mudança)? Um gasto inteiro ("e gastei 20 no uber") não."""
    from utils_text import normalize_text
    from parsers import _extract_valor
    return not _LANCAMENTO_RE.search(normalize_text(trecho)) and bool(
        _extract_valor(trecho) or re.search(r"\d", trecho) or _MUDA_RE.search(trecho))


def correcao(texto: str) -> str | None:
    """O trecho da correção: depois do "sim"/"não"/👍 ("sim, mas foi 60"), ou a mensagem inteira quando ela começa
    pelo vocabulário que abre correção ("foi 60", "é 60", "o valor é 60") ou por um valor ("60", "60 ontem"); None se
    não é correção. A mensagem inteira vai adiante: nada dela é descartado (`so_valor` decide se é só o valor)."""
    sobra = resto(texto)
    if sobra:
        return sobra if _muda(sobra) else None
    if not _INICIO_RE.match(texto or "") and (_ABRE_RE.match(texto or "") or _COMECA_POR_VALOR_RE.match(texto or "")
                                              or so_valor(texto) is not None):
        return texto
    return None


def so_valor(trecho: str) -> float | None:
    """O valor de uma correção que é só um valor, lido com certeza (leitor estrito, 2 casas, teto); senão None. "Só um
    valor": tirando o vocabulário de `ABREM` e `ENCHIMENTO`, sobra um número só, em dígitos ("60", "60,50", "1.500") ou
    por extenso ("sessenta e cinco", "2 mil"), ou com "k" ("1,5k", "1,5 k", "1.5k", #1404). Qualquer outra palavra
    ("ontem", "na padaria", "em dinheiro", "uns", "ou", "-60", "1,5kk", "6o") não é."""
    from utils_text import valor_valido
    from core.handlers import declaracao_valor
    from core.services.ai_chat.tools.declaracao_ia import _cabe
    from parsers import _extract_valor
    junto = re.sub(r"(\d)\s+k(?!\w)", r"\1k", (trecho or "").lower())  # "1,5 k" é "1,5k" (2-A)
    toques = [t.strip(",.;:!?") for t in junto.replace("r$", " r$ ").split()]
    sobra = [t for t in toques if t and t not in _ENCHE]
    digitos = [t for t in sobra if _DIGITOS_RE.fullmatch(t)]
    extenso = [t for t in sobra
               if unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode() in declaracao_valor._EXTENSO
               or t == "mil"]
    if not sobra or len(digitos) + len(extenso) != len(sobra) or len(digitos) > 1:
        return None
    valor = _extract_valor(trecho) or 0
    ok = valor > 0 and valor_valido(valor) and _cabe(valor) and declaracao_valor.recusa(trecho, valor) is None
    return float(valor) if ok else None
