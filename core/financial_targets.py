"""Resolução textual de alvos e quantidades de caixinhas/investimentos."""
from __future__ import annotations

import re
import unicodedata

from utils_text import contains_word, marcador_de_tudo, normalize_text

_PREP_RE = re.compile(r"^(?:d[aeo]|n[ao]|para|pra|em)\s+", re.I)
_SUBST_ALVO_RE = re.compile(r"(?:caixinha|investimento)\s+(.+)$", re.I)
ALVO_AMBIGUO = "Você mencionou mais de um alvo. De qual caixinha ou investimento quer retirar?"
QUANTIDADE_AMBIGUA = (
    "Você mencionou tudo e um valor. Qual valor quer retirar? "
    "Manda só o número, ou *tudo* para retirar o saldo inteiro."
)


def eh_nome_do_catalogo(resposta: str, existentes: list[str] | None = None) -> bool:
    alvo = normalize_text((resposta or "").strip())
    return bool(alvo) and any(normalize_text(n) == alvo for n in (existentes or []))


def nome_do_alvo(resposta: str, existentes: list[str] | None = None) -> str:
    """O catálogo vence o recorte sintático; só uma menção inequívoca é aceita."""
    t = resposta.strip()
    if eh_nome_do_catalogo(t, existentes):
        return t
    normal = normalize_text(t)
    mencoes = [
        (match.start(), match.end(), nome)
        for nome in (existentes or []) if contains_word(normal, normalize_text(nome))
        for match in re.finditer(rf"\b{re.escape(normalize_text(nome))}\b", normal)
    ]
    # Contenção é uma menção só: "viagem" dentro de "minha caixinha viagem".
    # Sobreposição e duas ocorrências são alvos distintos; não escolher pelo tamanho.
    maiores = [m for m in mencoes if not any(
        outro[0] <= m[0] and m[1] <= outro[1] and
        (outro[0], outro[1]) != (m[0], m[1]) for outro in mencoes
    )]
    if len(maiores) == 1:
        return maiores[0][2]
    if maiores:
        return t
    achou = _SUBST_ALVO_RE.search(t)
    return _PREP_RE.sub("", achou.group(1) if achou else t).strip() or t


def alvo_ambiguo(resposta: str, existentes: list[str]) -> bool:
    return (not eh_nome_do_catalogo(nome_do_alvo(resposta, existentes), existentes)
            and any(contains_word(normalize_text(resposta), normalize_text(n)) for n in existentes))


def texto_da_quantidade(resposta: str, existentes: list[str] | None = None) -> str:
    """Palavras e números do nome não são instruções de quantidade."""
    alvo = nome_do_alvo(resposta, existentes)
    if not eh_nome_do_catalogo(alvo, existentes):
        return resposta
    # normalize_text apaga vírgula decimal e sinal: serve para NOMES, não dinheiro.
    texto = "".join(c for c in unicodedata.normalize("NFKD", resposta.lower())
                    if not unicodedata.combining(c))
    padrao = r"\b" + r"[\W_]+".join(re.escape(p) for p in normalize_text(alvo).split()) + r"\b"
    return re.sub(padrao, " ", texto)


def pede_tudo(resposta: str, existentes: list[str] | None = None) -> bool:
    return marcador_de_tudo(texto_da_quantidade(resposta, existentes))


def resolve_saque(texto: str, entities: dict, chave: str, existentes: list[str]) -> tuple[dict, str | None]:
    """Resolve a entrada direta antes de um handler movimentar dinheiro.

    `want_all` explícito é a quantidade já resolvida pela clarification; o texto
    pode ser o comando ORIGINAL, anterior à correção de valor do usuário.
    """
    from parsers import _extract_valor

    ents = dict(entities)
    if "want_all" in ents:
        return ents, None
    if alvo_ambiguo(texto, existentes):
        ents.pop(chave, None)
        ents.pop("amount", None)
        ents["want_all"] = False
        return ents, "alvo_ambiguo"
    alvo = nome_do_alvo(texto, existentes)
    quantidade = texto_da_quantidade(texto, existentes)
    tudo = marcador_de_tudo(quantidade)
    valor = _extract_valor(quantidade)
    if eh_nome_do_catalogo(alvo, existentes) and (ents.get(chave) or tudo or valor is not None):
        ents[chave] = alvo
    if tudo and valor is not None:
        ents.pop("amount", None)
        ents["want_all"] = False
        return ents, "quantidade_ambigua"
    ents["want_all"] = tudo
    if tudo:
        ents.pop("amount", None)
    return ents, None
