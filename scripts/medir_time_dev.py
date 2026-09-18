#!/usr/bin/env python3
"""Mede se o `/time-dev` vale o custo em tokens, PR por PR.

Uso:
    python scripts/medir_time_dev.py 455 456 457

Junta três fontes por PR:
  a) achados/rodadas/aprovação do Codex, via `gh api` (repo fixo abaixo);
  b) o marcador `<!-- time-dev: grupo=... faixa=... internos=N bloqueantes=M -->`
     no corpo do PR (CLAUDE.md §0, `.claude/commands/time-dev.md`);
  c) tokens dos transcripts do Claude Code em `~/.claude/projects/`, filtrados
     pelo `head.ref` do PR.

`gh` precisa de rede: sem `dangerouslyDisableSandbox`, a interceptação de TLS
do sandbox derruba a verificação de certificado do `gh` (medido nesta sessão).

As funções de parse/agregação são puras (sem rede, sem disco) para o teste em
`tests/test_medir_time_dev.py`; `_gh`, `medir_codex` e `medir_tokens` fazem I/O.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = "JapaLcK/Bot_Financeiro"
PROJETOS_GLOB = os.path.expanduser(
    "~/.claude/projects/-Users-lucaskuramoti-Desktop-bot-bot-wa*"
)
GRUPOS_VALIDOS = {"com", "sem"}
FAIXAS_VALIDAS = {"Leve", "Completo"}
MARCADOR_RE = re.compile(
    r"<!--\s*time-dev:\s*grupo=(\w+)\s+faixa=(\w+)\s+internos=(\d+)\s+"
    r"bloqueantes=(\d+)\s*-->"
)

# Puro: marcador


def parse_marcador(corpo: str) -> dict | None:
    """`<!-- time-dev: grupo=com faixa=Leve internos=3 bloqueantes=1 -->` -> dict.
    Ausente ou fora do formato/valores esperados -> None."""
    if not corpo:
        return None
    m = MARCADOR_RE.search(corpo)
    if not m:
        return None
    grupo, faixa, internos, bloqueantes = m.groups()
    if grupo not in GRUPOS_VALIDOS or faixa not in FAIXAS_VALIDAS:
        return None
    return {
        "grupo": grupo,
        "faixa": faixa,
        "internos": int(internos),
        "bloqueantes": int(bloqueantes),
    }


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
    """`gh api --paginate --slurp <caminho>` achatado: lista de itens (objeto
    único endpoint vira lista de 1)."""
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
    primeira_id = reviews_codex[0]["id"] if reviews_codex else None
    achados_1a_rodada = sum(1 for a in achados if a.get("pull_request_review_id") == primeira_id)
    p1 = sum(1 for a in achados if "P1-" in a.get("body", ""))

    reacoes_ok = any(
        _e_codex(r["user"]["login"]) and r.get("content") == "+1"
        for r in _gh(f"repos/{REPO}/issues/{pr}/reactions")
    )
    comentario_ok = any(
        _e_codex(c["user"]["login"]) and "Didn't find any major issues" in c.get("body", "")
        for c in _gh(f"repos/{REPO}/issues/{pr}/comments")
    )
    aprovado = reacoes_ok or comentario_ok
    # A execução que aprova não vira review no GitHub, mas é uma rodada.
    rodadas = len(reviews_codex) + int(aprovado)
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
    for projeto_dir in glob.glob(PROJETOS_GLOB):
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


# Saída

PAPEIS_TIME = ("arquiteto", "coder", "tester", "manager")


def _fmt_num(n: float | None) -> str:
    return "n/d" if n is None else f"{n:,.0f}".replace(",", ".")


def _fmt_1c(n: float | None) -> str:
    return "n/d" if n is None else f"{n:.1f}"


def main(argv: list[str]) -> int:
    if not argv:
        print("uso: python scripts/medir_time_dev.py <PR> [PR ...]", file=sys.stderr)
        return 2

    linhas_tabela = []
    grupos: dict[str, list[dict]] = {"com": [], "sem": []}
    leve_por_grupo = {"com": 0, "sem": 0}

    for arg in argv:
        pr = int(arg)
        codex = medir_codex(pr)
        marcador = parse_marcador(codex["body"])
        tokens, achou = medir_tokens(codex["head_ref"])
        total_entrada = sum(v["entrada"] for v in tokens.values())
        total_saida = sum(v["saida"] for v in tokens.values())
        time_entrada = sum(tokens.get(p, {}).get("entrada", 0) for p in PAPEIS_TIME)
        time_saida = sum(tokens.get(p, {}).get("saida", 0) for p in PAPEIS_TIME)

        linha = {
            "pr": pr,
            "grupo": marcador["grupo"] if marcador else "-",
            "faixa": marcador["faixa"] if marcador else "-",
            "internos": marcador["internos"] if marcador else "-",
            "bloqueantes": marcador["bloqueantes"] if marcador else "-",
            "codex_1a": codex["achados_1a_rodada"],
            "codex_total": codex["achados_total"],
            "p1": codex["p1"],
            "rodadas": codex["rodadas"],
            "sem_revisao": codex["sem_revisao"],
            "tokens_entrada": total_entrada,
            "tokens_saida": total_saida,
            "tokens_time_entrada": time_entrada,
            "tokens_time_saida": time_saida,
            "transcript": achou,
        }
        linhas_tabela.append(linha)

        # Só Leve se compara: Completo é sempre "com", misturá-lo enviesa o grupo.
        if marcador and marcador["faixa"] == "Leve":
            leve_por_grupo[marcador["grupo"]] += 1
            # Sem transcript fica fora: custo e achados vêm da mesma coorte.
            if not codex["sem_revisao"] and achou:
                grupos[marcador["grupo"]].append({**linha, **marcador})

    print(f"{'PR':>5} {'grupo':6} {'faixa':9} {'int':>3} {'bloq':>4} {'cx1a':>4} "
          f"{'cxtot':>5} {'P1':>3} {'rod':>3} {'tok_in':>10} {'tok_out':>9} "
          f"{'time_in':>10} {'time_out':>9}")
    for l in linhas_tabela:
        obs = (" (sem transcript)" if not l["transcript"] else "") + \
              (" (sem revisão do Codex)" if l["sem_revisao"] else "")
        print(f"{l['pr']:>5} {str(l['grupo']):6} {str(l['faixa']):9} "
              f"{str(l['internos']):>3} {str(l['bloqueantes']):>4} "
              f"{l['codex_1a']:>4} {l['codex_total']:>5} {l['p1']:>3} {l['rodadas']:>3} "
              f"{_fmt_num(l['tokens_entrada']):>10} {_fmt_num(l['tokens_saida']):>9} "
              f"{_fmt_num(l['tokens_time_entrada']):>10} {_fmt_num(l['tokens_time_saida']):>9}"
              f"{obs}")

    def media(lst, chave):
        return sum(x[chave] for x in lst) / len(lst) if lst else None

    campos_media = ("codex_1a", "codex_total", "p1", "rodadas", "tokens_entrada", "tokens_saida")
    print()
    print(f"{'grupo':6} {'n':>3} {'cx1a':>7} {'cxtot':>7} {'P1':>6} {'rod':>6} {'tok_in':>10} {'tok_out':>9}")
    medias = {g: {c: media(grupos[g], c) for c in campos_media} for g in ("com", "sem")}
    for g in ("com", "sem"):
        m = medias[g]
        print(f"{g:6} {len(grupos[g]):>3} {_fmt_1c(m['codex_1a']):>7} "
              f"{_fmt_1c(m['codex_total']):>7} {_fmt_1c(m['p1']):>6} {_fmt_1c(m['rodadas']):>6} "
              f"{_fmt_num(m['tokens_entrada']):>10} {_fmt_num(m['tokens_saida']):>9}")

    m_com, m_sem = medias["com"], medias["sem"]
    print()
    if m_com["tokens_entrada"] is None or m_sem["tokens_entrada"] is None:
        print("tokens a mais por achado do Codex evitado: n/d (falta PR num dos grupos)")
    else:
        denom = m_sem["codex_total"] - m_com["codex_total"]
        if denom <= 0:
            print("tokens a mais por achado do Codex evitado: n/d (o grupo 'com' não achou menos)")
        else:
            for c in ("tokens_entrada", "tokens_saida"):
                custo = (m_com[c] - m_sem[c]) / denom
                print(f"{c} a mais por achado do Codex evitado: {_fmt_num(custo)}")

    print()
    n_com_leve, n_sem_leve = leve_por_grupo["com"], leve_por_grupo["sem"]
    proximo = "com" if n_com_leve <= n_sem_leve else "sem"
    print(f"PRs Leve marcados: com={n_com_leve} sem={n_sem_leve}")
    print(f"próximo PR Leve: {proximo}")
    if n_com_leve >= 10 and n_sem_leve >= 10:
        print("experimento encerrado")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
