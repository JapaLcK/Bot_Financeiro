#!/usr/bin/env python3
"""Medição contínua do `/time-dev`: relatório semanal em markdown.

Uso:
    python scripts/medir_time_dev.py [--desde AAAA-MM-DD] [--ate AAAA-MM-DD]

Padrão: os últimos 7 dias até hoje. Pega os PRs MERGEADOS na janela que têm o
marcador (CLAUDE.md §0, `.claude/commands/time-dev.md`)
`<!-- time-dev: faixa=Leve tester=N tester_so=A manager=M manager_so=B codex_antes=K -->`
e junta, por PR:
  a) os bugs provados do Tester e do Manager, e quantos o Codex local não viu;
  b) os achados do Codex do GitHub depois, via `gh api` (repo fixo abaixo);
  c) os escapados: issues e PRs com `Origem: #N` criados até 14 dias depois do merge;
  d) tokens dos transcripts do Claude Code em `~/.claude/projects/`, filtrados
     pelo `head.ref` do PR.
A saída vai como comentário na issue "Medição do time-dev":
    python scripts/medir_time_dev.py --desde ... | gh issue comment N --body-file -

`gh` precisa de rede: sem `dangerouslyDisableSandbox`, a interceptação de TLS
do sandbox derruba a verificação de certificado do `gh` (medido nesta sessão).

As regras (marcador, escapados, resumo) moram em `_time_dev_metricas.py`; elas e
as de tokens daqui são puras (sem rede, sem disco) para o teste em
`tests/test_medir_time_dev.py`; `_gh`, `medir_codex` e `medir_tokens` fazem I/O.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from _time_dev_metricas import (JANELA_ESCAPADOS, conferir, contar_escapados,
                                parse_marcador, resumir)

REPO = "JapaLcK/Bot_Financeiro"
_RAIZ = Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
    capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip()).parent
_COD = re.sub(r"[^A-Za-z0-9]", "-", str(_RAIZ))  # como o Claude Code nomeia a pasta
PROJETOS_GLOBS = [os.path.expanduser(f"~/.claude/projects/{_COD}{s}") for s in ("", "--*")]

# Puro: tokens de um transcript já parseado (lista de dicts = linhas do jsonl)


def extrair_mapa_agentes(linhas: list[dict]) -> dict[str, str]:
    """agentId -> papel. O papel vem do `subagent_type` da chamada do Agent,
    ligada ao resultado pelo `tool_use_id`; `toolUseResult.agentType` só existe
    em parte dos transcripts (medido 2026-09-17: falta em ~metade)."""
    papel_da_chamada: dict[str, str] = {}
    mapa: dict[str, str] = {}
    for obj in linhas:
        conteudo = (obj.get("message") or {}).get("content")
        for b in conteudo if isinstance(conteudo, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and isinstance(b.get("input"), dict):
                if b["input"].get("subagent_type"):
                    papel_da_chamada[b.get("id")] = b["input"]["subagent_type"]
            tur = obj.get("toolUseResult")
            if b.get("type") == "tool_result" and isinstance(tur, dict) and tur.get("agentId"):
                papel = papel_da_chamada.get(b.get("tool_use_id")) or tur.get("agentType")
                if papel:
                    mapa[tur["agentId"]] = papel
    return mapa


def somar_tokens(linhas: list[dict], branch: str | None) -> dict[str, int]:
    """Soma entrada (input+cache_creation+cache_read) e saída, deduplicando por
    `message.id`. `branch=None` = linhas já vieram filtradas (arquivo de
    subagente sem `gitBranch` próprio); caso contrário compara exato contra
    `obj.get("gitBranch")` — cobre branch que é prefixo de outro."""
    entrada = saida = 0
    vistos: set[str] = set()
    for obj in linhas:
        if branch is not None and obj.get("gitBranch") != branch:
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict):
            continue
        mid = msg.get("id")
        if not mid or mid in vistos:
            continue
        vistos.add(mid)
        entrada += (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
        )
        saida += usage.get("output_tokens") or 0
    return {"entrada": entrada, "saida": saida}


def agregar_papel(destino: dict[str, dict[str, int]], papel: str, soma: dict[str, int]) -> None:
    d = destino.setdefault(papel, {"entrada": 0, "saida": 0})
    d["entrada"] += soma["entrada"]
    d["saida"] += soma["saida"]


# I/O: gh


def _gh(caminho: str) -> list:
    """`gh api --paginate --slurp <caminho>` achatado numa lista de itens."""
    saida = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", caminho],
        capture_output=True, text=True, check=True,
    ).stdout
    paginas = json.loads(saida)
    itens: list = []
    for pagina in paginas:
        itens.extend(pagina if isinstance(pagina, list) else [pagina])
    return itens


def _e_codex(login: str) -> bool:
    return "codex" in (login or "").lower()


def medir_codex(pr: int) -> dict:
    pr_obj = _gh(f"repos/{REPO}/pulls/{pr}")[0]
    comentarios = [c for c in _gh(f"repos/{REPO}/pulls/{pr}/comments") if _e_codex(c["user"]["login"])]
    achados = [c for c in comentarios if c.get("in_reply_to_id") is None]
    reviews_codex = sorted(
        (r for r in _gh(f"repos/{REPO}/pulls/{pr}/reviews") if _e_codex(r["user"]["login"])),
        key=lambda r: r["submitted_at"],
    )
    p1 = sum(1 for a in achados if "P1-" in a.get("body", ""))
    reacao_ok = [r["created_at"] for r in _gh(f"repos/{REPO}/issues/{pr}/reactions")
                 if _e_codex(r["user"]["login"]) and r.get("content") == "+1"]
    comentarios_ok = [c["created_at"] for c in _gh(f"repos/{REPO}/issues/{pr}/comments")
                      if _e_codex(c["user"]["login"]) and "Didn't find any major issues" in c.get("body", "")]
    oks = reacao_ok + comentarios_ok
    aprovado = bool(oks)
    # Rodada limpa não vira review no GitHub, mas é rodada — e se veio antes da 1ª
    # review, a 1ª rodada não achou nada.
    limpa_antes = bool(oks and reviews_codex) and min(oks) < reviews_codex[0]["submitted_at"]
    primeira_id = reviews_codex[0]["id"] if reviews_codex and not limpa_antes else None
    achados_1a_rodada = sum(1 for a in achados if a.get("pull_request_review_id") == primeira_id)
    rodadas = len(reviews_codex) + max(len(comentarios_ok), len(reacao_ok))
    sem_revisao = rodadas == 0

    return {
        "head_ref": pr_obj["head"]["ref"],
        "autor": pr_obj["user"]["login"],
        "body": pr_obj.get("body") or "",
        "rodadas": rodadas,
        "achados_total": len(achados),
        "achados_1a_rodada": achados_1a_rodada,
        "p1": p1,
        "aprovado": aprovado,
        "sem_revisao": sem_revisao,
    }


# I/O: transcripts


def _tem_campo(path: str, campo: str) -> bool:
    agulha = f'"{campo}"'.encode()
    with open(path, "rb") as f:
        for linha in f:
            if agulha in linha:
                return True
    return False


def _linhas_candidatas(path: str, branch: str | None) -> list[dict]:
    """Lê `path` linha a linha. Com `branch`, faz o pré-filtro por substring
    (evita `json.loads` na maioria das linhas) antes do parse; sem `branch`,
    parseia tudo (arquivo de subagente sem `gitBranch` próprio)."""
    agulha = f'"gitBranch":"{branch}"'.encode() if branch is not None else None
    candidatas = []
    with open(path, "rb") as f:
        for raw in f:
            if agulha is not None and agulha not in raw:
                continue
            try:
                candidatas.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return candidatas


def medir_tokens(branch: str) -> tuple[dict[str, dict[str, int]], bool]:
    tokens: dict[str, dict[str, int]] = {}
    achou_transcript = False
    for projeto_dir in (d for g in PROJETOS_GLOBS for d in glob.glob(g)):
        if not os.path.isdir(projeto_dir):
            continue
        for main_path in glob.glob(os.path.join(projeto_dir, "*.jsonl")):
            linhas = _linhas_candidatas(main_path, branch)
            if not linhas:
                continue
            achou_transcript = True
            agregar_papel(tokens, "principal", somar_tokens(linhas, branch))
            mapa_agentes = extrair_mapa_agentes(linhas)
            if not mapa_agentes:
                continue
            session_id = Path(main_path).stem
            sub_dir = os.path.join(projeto_dir, session_id, "subagents")
            for sub_path in glob.glob(os.path.join(sub_dir, "agent-*.jsonl")):
                agent_id = Path(sub_path).stem[len("agent-"):]
                papel = mapa_agentes.get(agent_id)
                if papel is None:
                    continue
                sub_branch = branch if _tem_campo(sub_path, "gitBranch") else None
                sub_linhas = _linhas_candidatas(sub_path, sub_branch)
                if sub_linhas:
                    agregar_papel(tokens, papel, somar_tokens(sub_linhas, sub_branch))
    return tokens, achou_transcript

# Saída (markdown)


def _fmt_num(n: float | None) -> str:
    return "n/d" if n is None else f"{n:,.0f}".replace(",", ".")


def _data(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Medição semanal do /time-dev.")
    ap.add_argument("--desde", type=date.fromisoformat, default=date.today() - timedelta(days=7))
    ap.add_argument("--ate", type=date.fromisoformat, default=date.today())
    args = ap.parse_args(argv)

    paginas = _gh(f"search/issues?q=repo:{REPO}+is:pr+is:merged"
                  f"+merged:{args.desde}..{args.ate}&per_page=100")
    prs = [(i["number"], _data(i["pull_request"]["merged_at"]), m)
           for p in paginas for i in p["items"] if (m := parse_marcador(i.get("body")))]
    print(f"## Medição do time-dev — {args.desde} a {args.ate}\n")
    if not prs:
        print("Nenhum PR mergeado na janela com o marcador do time-dev.")
        return 0

    merges = {n: merged for n, merged, _ in prs}
    itens = [(x["number"], x.get("body"), _data(x["created_at"]))
             for x in _gh(f"repos/{REPO}/issues?state=all&since={args.desde}T00:00:00Z&per_page=100")]
    escapados = contar_escapados(itens, merges)
    agora = datetime.now(timezone.utc)

    linhas = []
    print("| PR | faixa | tester (só) | manager (só) | Codex antes | Codex GitHub | escapados "
          "| tokens entrada | tokens saída | obs |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for n, merged, m in sorted(prs, key=lambda x: x[0]):
        codex = medir_codex(n)
        tokens, achou = medir_tokens(codex["head_ref"])
        tok = (sum(v["entrada"] for v in tokens.values()),
               sum(v["saida"] for v in tokens.values())) if achou else None
        aberta = agora - merged < JANELA_ESCAPADOS
        linhas.append({**m, "codex_github": codex["achados_total"],
                       "escapados": escapados[n], "aberta": aberta, "tokens": tok})
        obs = [o for o in (conferir(m, tokens, achou),
                           "sem revisão do Codex" if codex["sem_revisao"] else "") if o]
        so = {a: "n/d" if m["codex_antes"] is None else m[f"{a}_so"] for a in ("tester", "manager")}
        print(f"| #{n} | {m['faixa']} | {m['tester']} ({so['tester']}) "
              f"| {m['manager']} ({so['manager']}) "
              f"| {'n/d' if m['codex_antes'] is None else m['codex_antes']} "
              f"| {codex['achados_total']} | {escapados[n]}{' (janela aberta)' if aberta else ''} "
              f"| {_fmt_num(tok and tok[0])} | {_fmt_num(tok and tok[1])} | {'; '.join(obs)} |")

    print("\n### Resumo por faixa\n")
    print("| faixa | PRs | tester (só) | manager (só) | exclusivos | Codex GitHub | escapados "
          "| eficácia | tokens por exclusivo (entrada / saída) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for faixa, r in resumir(linhas).items():
        tpe = r["tokens_por_exclusivo"]
        abertas = f" ({r['abertas']} PR com janela aberta)" if r["abertas"] else ""
        print(f"| {faixa} | {r['prs']} | {r['tester']} ({r['tester_so']}) "
              f"| {r['manager']} ({r['manager_so']}) | {r['exclusivos']} | {r['codex_github']} "
              f"| {r['escapados']}{abertas} "
              f"| {'n/d' if r['eficacia'] is None else format(r['eficacia'], '.0%')} "
              f"| {'n/d' if tpe is None else f'{_fmt_num(tpe[0])} / {_fmt_num(tpe[1])}'} |")
    print("\neficácia = (tester + manager) / (tester + manager + Codex GitHub + escapados). "
          "Escapados com janela aberta ainda podem subir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
