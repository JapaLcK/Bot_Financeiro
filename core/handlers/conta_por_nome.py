"""Qual conta a pagar o "paguei <nome>" nomeia.

Dado o texto e as contas pendentes, devolve a conta, a pergunta "qual delas" ou
None (a mensagem vira lançamento avulso). Força do casamento, da maior à menor:

1. o nome inteiro, por palavra inteira ("net" não casa "internet"); entre esses,
   sai o que está DENTRO de outro ("Luz" some diante de "Luz casa"). Nomes que
   não se contêm ("Luz" e "Casa" em "luz de casa") empatam;
2. o alvo dentro do nome ("luz" em "Luz casa" e em "Luz escritorio");
3. uma palavra do nome no alvo ("energia" de "Energia eletrica").

Empate entre nomes diferentes pergunta. O mesmo nome, normalizado ("Água" e
"agua"; a mesma conta em dois meses), paga o de vencimento mais antigo, e no
mesmo vencimento a criada primeiro (menor id). Vale também para o "paguei"
solto. O resultado não depende da ordem de criação.
"""
from __future__ import annotations

import re

from utils_text import normalize_text

PAY_RE = re.compile(r"^(ja\s+)?(paguei|quitei)\b")
_STOP_TOKENS = {
    "o", "a", "os", "as", "de", "do", "da", "dos", "das", "meu", "minha",
    "conta", "boleto", "boletos", "fatura", "reais", "real", "rs", "r",
    "ja", "hoje", "ontem", "esse", "essa", "esta", "este",
}


def _contem(nome: str, texto: str) -> bool:
    return bool(re.search(rf"\b{re.escape(nome)}\b", texto))


def _nome(conta: dict) -> str:
    return normalize_text(conta.get("name") or "")


def _dentro_de_outro(nome: str, nomes: set[str]) -> bool:
    return any(outro != nome and _contem(nome, outro) for outro in nomes)


def _mais_antiga(contas: list[dict]) -> dict:
    """Vencimento, depois id: `list_bills` só ordena por vencimento. O vencimento
    é `not null` e chega em ISO (`db.bills._row`), então compara como texto."""
    return min(contas, key=lambda c: (c.get("due_date"), c.get("id")))


def _nomes_distintos(contas: list[dict]) -> list[str]:
    """Um nome por conta distinta, em ordem alfabética: "Água" e "agua" são um só."""
    por_nome: dict[str, str] = {}
    for conta in contas:
        por_nome.setdefault(_nome(conta), conta.get("name") or "?")
    return [por_nome[n] for n in sorted(por_nome)]


def _forca(nome: str, norm: str, alvo: str) -> int:
    if not nome:
        return 0
    if _contem(nome, norm) or (alvo and _contem(nome, alvo)):
        return 4
    if alvo and alvo in nome:
        return 3
    toks = [t for t in nome.split() if len(t) > 2 and t not in _STOP_TOKENS]
    if alvo and any(t in alvo.split() for t in toks):
        return 2
    return 0


def _pergunta_qual(nomes: list[str]) -> str:
    """Sem pendência: a resposta tem de ser um "paguei <nome>" novo. O número
    do nome não vira valor (`bills.try_pay_from_text`, #700), então a dica vale
    também para "IPVA 2025"."""
    return (f"Você tem contas a pagar pendentes: {', '.join(nomes)}. Qual delas você pagou? "
            f"Me manda *paguei {nomes[0]}*, com o nome inteiro.")


def escolher_conta(pend: list[dict], norm: str, exige_nome: bool = False) -> dict | str | None:
    """A conta, a pergunta "qual delas" ou None. `exige_nome`: a mensagem tinha
    pergunta comparativa (#568); sem nomear a conta, "paguei hoje e gastei mais
    em 2025 ou 2026?" não quita a única pendente, como na main."""
    alvo = PAY_RE.sub("", norm).strip()
    alvo = re.sub(r"\b\d[\d.,]*\b", " ", alvo)
    alvo = " ".join(t for t in alvo.split() if t not in _STOP_TOKENS).strip()

    forcas = [(_forca(_nome(b), norm, alvo), b) for b in pend]
    melhor = max(f for f, _ in forcas)
    if melhor == 0:
        # Nenhuma conta casou pelo nome. Sem alvo ("paguei" / "paguei essa
        # conta", como o lembrete pede) e com um nome só pendente, paga a mais
        # antiga; com vários, pergunta. Alvo que não casou vira lançamento avulso.
        if alvo or exige_nome:
            return None
        nomes = _nomes_distintos(pend)
        return _mais_antiga(pend) if len(nomes) == 1 else _pergunta_qual(nomes[:5])
    empate = [b for f, b in forcas if f == melhor]
    if melhor == 4:  # "Luz" some diante de "Luz casa"; "Luz" e "Casa" empatam
        inteiros = {_nome(b) for b in empate}
        empate = [b for b in empate if not _dentro_de_outro(_nome(b), inteiros)]
    nomes = _nomes_distintos(empate)
    return _mais_antiga(empate) if len(nomes) == 1 else _pergunta_qual(nomes[:5])
