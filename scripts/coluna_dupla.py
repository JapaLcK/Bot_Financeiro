#!/usr/bin/env python3
"""Prova de coluna dupla: o teste novo fica VERMELHO no codigo antigo e VERDE no corrigido.

O `CLAUDE.md` (secao 3) exige essa prova — "reverta o fix, rode o teste, veja
vermelho" — e ela era feita a mao. Feita a mao, ja saiu errada: uma varredura
"comparava o branch com uma versao anterior dele mesmo", e um controle negativo
foi injetado num caso que ja estava vermelho. Este script tira a comparacao da
mao e nomeia as duas colunas no relatorio.

O SHA da coluna antiga NUNCA e presumido. `origin/main` so serve de padrao para
uma tarefa em voo, onde o fix ainda nao esta la; para conferir um conserto ja
mergeado, a main atual JA CONTEM o fix e nao serve de coluna vermelha — passe os
dois SHAs a mao.

Uso:
    # tarefa em voo (padrao): antes = merge-base com origin/main, depois = HEAD
    scripts/coluna_dupla.py

    # fixture historica: os dois SHAs explicitos (base e merge do PR)
    scripts/coluna_dupla.py --antes d3ff537f --depois c917f1cf

Sai 0 quando o grupo fica vermelho no antes e verde no depois.
Sai 1 quando um teste PASSA no antes (tautologico), quando falha no depois, ou
quando uma guarda dispara.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

# Codigos de saida do pytest. Sao o que separa prova FORTE de prova FRACA:
# 1 = asserção falhou (o comportamento antigo estava errado — e o que queremos);
# 2 = coleta interrompida (ImportError/erro de sintaxe) — o teste so provou que
#     um simbolo novo nao existia, nao que o comportamento estava errado;
# 5 = nenhum teste coletado — nao mede nada, e reprovado como guarda.
PYTEST_FALHA, PYTEST_COLETA, PYTEST_VAZIO = 1, 2, 5


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"[coluna-dupla] git {' '.join(args)} falhou:\n{r.stderr.strip()}")
    return r.stdout.strip()


def raiz_principal() -> str:
    """Raiz do checkout principal — o .venv vive la, nao dentro do worktree."""
    return os.path.dirname(git("rev-parse", "--path-format=absolute", "--git-common-dir"))


def roda_pytest(cwd: str, py: str, alvos: list[str]) -> tuple[int, str]:
    env = {**os.environ, "PYTHONPATH": "."}
    r = subprocess.run([py, "-m", "pytest", "-q", *alvos],
                       cwd=cwd, env=env, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--antes", default="", help="SHA do codigo ANTIGO (padrao: merge-base com origin/main)")
    ap.add_argument("--depois", default="HEAD", help="SHA do codigo CORRIGIDO (padrao: HEAD)")
    ap.add_argument("--testes", nargs="*", default=[], help="node ids explicitos; padrao: os testes que mudaram")
    ap.add_argument("--python", default="", help="interpretador (padrao: .venv do checkout principal)")
    a = ap.parse_args()

    # ── Guarda 1: arvore suja ────────────────────────────────────────────────
    # Teste nao commitado NAO existe no worktree que o git cria a partir de um
    # SHA. Sem esta guarda, a coluna verde rodaria sem a mudanca e o veredito
    # sairia invertido, sem aviso.
    if git("status", "--porcelain"):
        sys.exit("[coluna-dupla] arvore suja. Commite antes — o worktree do SHA nao enxerga o nao-commitado.\n"
                 "                Nunca use `git stash` aqui: o stash e compartilhado entre worktrees.")

    depois = git("rev-parse", a.depois)
    antes = git("rev-parse", a.antes) if a.antes else git("merge-base", "HEAD", "origin/main")

    # ── Guarda 2: branch contra ela mesma ────────────────────────────────────
    if antes == depois:
        sys.exit(f"[coluna-dupla] antes == depois ({antes[:8]}). Nao ha duas colunas.")

    # ── Guarda 3: a coluna "antiga" ja contem o fix ──────────────────────────
    # E o erro que este script existe para impedir: apontar a coluna vermelha
    # para uma main que ja foi corrigida. Se `depois` e ancestral de `antes`, o
    # conserto ja esta la dentro e o vermelho nunca viria.
    if subprocess.run(["git", "merge-base", "--is-ancestor", depois, antes],
                      capture_output=True).returncode == 0:
        sys.exit(f"[coluna-dupla] {depois[:8]} e ancestral de {antes[:8]}: a coluna 'antiga' JA CONTEM o fix.\n"
                 "                Para uma correcao ja mergeada, use --antes <base do PR> --depois <merge do PR>.")

    # ── O que copiar: so teste, e so .py ─────────────────────────────────────
    # A coluna antiga recebe o TESTE novo por cima do CODIGO velho. Nenhum
    # arquivo de producao atravessa — o pathspec `tests/` garante isso.
    mudados = [p for p in git("diff", "--name-only", "--diff-filter=AM", antes, depois, "--", "tests/").splitlines()
               if p.endswith(".py")]
    if not mudados:
        sys.exit(f"[coluna-dupla] nenhum teste .py mudou entre {antes[:8]} e {depois[:8]}. Nada a provar.")
    # O conftest carrega as chaves de PII e o skip de dependencia ausente; sem
    # a versao nova dele, a coluna antiga pode falhar por ambiente, nao por bug.
    if "tests/conftest.py" not in mudados:
        mudados.append("tests/conftest.py")
    alvos = a.testes or [p for p in mudados if p != "tests/conftest.py"]

    py = a.python or os.path.join(raiz_principal(), ".venv", "bin", "python")
    if not os.path.exists(py):
        py = sys.executable

    tmp = tempfile.mkdtemp(prefix="coluna-dupla-")
    wt_antes, wt_depois = os.path.join(tmp, "antes"), os.path.join(tmp, "depois")
    try:
        # Duas arvores proprias, sempre — inclusive para `depois`. O worktree
        # atual nunca e usado como coluna, entao o script nao depende de o
        # repositorio inteiro estar limpo, so de ESTA arvore estar.
        git("worktree", "add", "--detach", "--quiet", wt_antes, antes)
        git("worktree", "add", "--detach", "--quiet", wt_depois, depois)

        for rel in mudados:
            dst = os.path.join(wt_antes, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(wt_depois, rel), dst)

        print(f"[coluna-dupla] antes  = {antes}")
        print(f"[coluna-dupla] depois = {depois}")
        print(f"[coluna-dupla] testes = {' '.join(alvos)}")
        print(f"[coluna-dupla] python = {py}\n")

        rc_antes, saida_antes = roda_pytest(wt_antes, py, alvos)
        rc_depois, _ = roda_pytest(wt_depois, py, alvos)

        if rc_antes == PYTEST_VAZIO:
            print("[coluna-dupla] REPROVADO: nenhum teste coletado na coluna antiga — nao mede nada.")
            return 1
        if rc_antes == 0:
            print("[coluna-dupla] REPROVADO: o grupo PASSA no codigo antigo. Teste tautologico —\n"
                  "               ele afirma o que o codigo faz, e ficaria verde com e sem o fix.")
            return 1
        if rc_depois != 0:
            print(f"[coluna-dupla] REPROVADO: o grupo falha tambem no codigo corrigido (rc={rc_depois}).")
            return 1

        if rc_antes == PYTEST_COLETA:
            causa = next((l for l in saida_antes.splitlines()
                          if "Error" in l and ("Import" in l or "Module" in l)), "erro de coleta")
            print("[coluna-dupla] APROVADO, prova FRACA: vermelho por COLETA, nao por asserção.")
            print(f"               {causa.strip()}")
            print("               Isso prova que um simbolo novo nao existia, nao que o\n"
                  "               comportamento antigo estava errado. O Manager decide se aceita.")
        else:
            print("[coluna-dupla] APROVADO, prova FORTE: vermelho por asserção no antigo, verde no corrigido.")
        return 0
    finally:
        for wt in (wt_antes, wt_depois):
            subprocess.run(["git", "worktree", "remove", "--force", wt], capture_output=True)
        subprocess.run(["git", "worktree", "prune"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
