"""Regras da medição do `/time-dev` (CLAUDE.md §0, `.claude/commands/time-dev.md`):
o marcador do PR, os escapados, a conferência com o transcript e o resumo por
faixa. Tudo puro (sem rede, sem disco); o I/O mora em `medir_time_dev.py`."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

FAIXAS_VALIDAS = ("Leve", "Completo")
AGENTES = ("tester", "manager")
# Só no começo da linha: um exemplo citado no texto (entre crases, no meio da frase) não conta.
MARCADOR_RE = re.compile(
    r"^[ \t]*<!--\s*time-dev:\s*faixa=(\w+)\s+tester=(\d+)\s+tester_so=(\d+)\s+"
    r"manager=(\d+)\s+manager_so=(\d+)\s+codex_antes=(\d+|nd)\s*-->",
    re.MULTILINE,
)
ORIGEM_RE = re.compile(r"\borigem\s*[:\-–—]?\s*(?:PR\s*|issue\s*)?#(\d+)", re.IGNORECASE)
JANELA_ESCAPADOS = timedelta(days=14)

# Puro: marcador


def parse_marcador(corpo: str | None) -> dict | None:
    """Marcador novo -> dict; `codex_antes=nd` vira None. Ausente, formato antigo
    (`grupo=...`), faixa desconhecida ou `*_so` maior que o total -> None."""
    m = MARCADOR_RE.search(corpo or "")
    if not m or m[1] not in FAIXAS_VALIDAS:
        return None
    d = {"faixa": m[1], "tester": int(m[2]), "tester_so": int(m[3]),
         "manager": int(m[4]), "manager_so": int(m[5]),
         "codex_antes": None if m[6] == "nd" else int(m[6])}
    if any(d[f"{a}_so"] > d[a] for a in AGENTES):
        return None
    return d


# Puro: escapados, conferência e resumo


def contar_escapados(itens: list[tuple[int, str | None, datetime]],
                     merges: dict[int, datetime]) -> dict[int, int]:
    """itens = (número, body, created_at) de issues e PRs; merges = PR -> merged_at.
    Conta, por PR, os itens com `Origem: #PR` criados até 14 dias depois do merge.
    O próprio PR citando a si não conta; o mesmo item conta uma vez por PR citado."""
    contagem = {pr: 0 for pr in merges}
    for numero, body, criado in itens:
        for alvo in {int(n) for n in ORIGEM_RE.findall(body or "")}:
            if alvo != numero and alvo in merges \
                    and merges[alvo] <= criado <= merges[alvo] + JANELA_ESCAPADOS:
                contagem[alvo] += 1
    return contagem


def conferir(marcador: dict, tokens: dict[str, dict[str, int]], achou: bool) -> str:
    """Marcador diz que um agente achou bug, mas o transcript não tem tokens dele."""
    if not achou:
        return "não conferido"
    for a in AGENTES:
        if marcador[a] > 0 and not sum(tokens.get(a, {}).values()):
            return "marcador não bate com o transcript"
    return ""


def resumir(linhas: list[dict]) -> dict[str, dict]:
    """Por faixa. `linhas` = um dict por PR com as chaves do marcador, mais
    `codex_github`, `escapados`, `aberta` (janela de escapados) e `tokens`
    ((entrada, saída) ou None sem transcript). Exclusivos só existem em PR com a
    lista do Codex local (`codex_antes` não None); tokens por exclusivo usam só os
    PRs com essa lista E com transcript: custo e achados da mesma coorte; n/d não vira 0."""
    res = {}
    for faixa in FAIXAS_VALIDAS:
        ls = [l for l in linhas if l["faixa"] == faixa]
        com_codex = [l for l in ls if l["codex_antes"] is not None]
        s = {c: sum(l[c] for l in ls) for c in ("tester", "manager", "codex_github", "escapados")}
        s.update({c: sum(l[c] for l in com_codex) for c in ("tester_so", "manager_so")})
        achados = s["tester"] + s["manager"]
        denom = achados + s["codex_github"] + s["escapados"]
        com_tok = [l for l in com_codex if l["tokens"] is not None]
        excl_tok = sum(l["tester_so"] + l["manager_so"] for l in com_tok)
        res[faixa] = {
            **s, "prs": len(ls), "abertas": sum(1 for l in ls if l["aberta"]),
            "exclusivos": s["tester_so"] + s["manager_so"],
            "eficacia": achados / denom if denom else None,
            "tokens_por_exclusivo": None if not excl_tok else tuple(
                sum(l["tokens"][i] for l in com_tok) / excl_tok for i in (0, 1)),
        }
    return res
