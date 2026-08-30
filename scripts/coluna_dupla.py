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

    # o mesmo, num caso que sai prova FRACA: nenhum teste chega a RODAR no antigo
    scripts/coluna_dupla.py --antes f95e485 --depois 17f3b49 --testes tests/test_ai_chat_commands.py

Sai 0 quando o grupo fica vermelho no antes e verde no depois.
Sai 1 quando um teste PASSA no antes (tautologico), quando falha no depois,
quando o vermelho e valido mas o resumo do pytest nao trouxe desfecho — nem
`FAILED` nem `ERROR`, tipico de um `PYTEST_ADDOPTS` com `-rN`/`-rs` —, ou quando
uma guarda dispara.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

# Codigos de saida do pytest. Sao a FAIXA de vermelho valido, NAO o criterio de
# prova: 1 = "Tests failed", 2 = coleta interrompida (ImportError/erro de sintaxe).
# Medido com pytest 9.0.2: rc=1 engloba tanto a asserção que falhou quanto o erro
# de setup/fixture, em que NENHUM corpo de teste chegou a rodar — por isso o exit
# code sozinho nao separa prova FORTE de FRACA. Quem separa e o desfecho impresso
# no resumo do pytest, lido pelo `veredito`. Qualquer outro codigo (3, 4, 5) e
# reprovado: o pytest reclamando de si mesmo nao e vermelho de teste.
PYTEST_FALHA, PYTEST_COLETA = 1, 2


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
    # SO o stdout alimenta o veredito. Concatenar os dois streams punha o stderr
    # INTEIRO depois do ultimo cabecalho de resumo, entao qualquer linha escrita
    # la (atexit, __del__ no shutdown, thread que sobrevive a captura, plugin)
    # passava por baixo do fatiamento; um cabecalho falso ali chegava a MOVER o
    # corte do `rpartition` e o gate carimbava FORTE citando um teste inexistente.
    # O resumo do pytest nunca sai pelo stderr, entao nada de decisao se perde —
    # e o texto continua visivel no NOSSO stderr, onde serve de diagnostico para
    # os rc 3/4/5, cuja mensagem so mostra o numero.
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r.returncode, r.stdout


# O `-q` fecha a saida com este cabecalho, e SO depois dele vem o desfecho de
# cada teste. As secoes `Captured stdout` aparecem ANTES. Fatiar aqui e o que
# separa o desfecho REAL de um `print("FAILED ...")` dentro de um teste ou de uma
# fixture — medido com pytest 9.0.2: um print desses no setup sai em `Captured
# stdout setup`, na coluna 0, e sem o fatiamento invertia o veredito para FORTE
# no caso exato em que NADA rodou. Medido tambem: o cabecalho aparece nos tres
# vermelhos validos (rc=1 por asserção, rc=1 por fixture, rc=2 por coleta) e
# some com `-rN`/`--no-summary` — mas some tambem SO o desfecho, com o cabecalho
# de pe (`-rs`), entao quem reprova e a ausencia de desfecho, nao a do cabecalho.
CABECALHO_RESUMO = "= short test summary info ="


def veredito(rc_antes: int, saida_antes: str, rc_depois: int) -> tuple[int, str]:
    """Classifica as duas colunas: devolve (codigo de saida, relatorio).

    Nao escreve nada e nenhum codigo de saida depende do ambiente; so LE
    `PYTEST_ADDOPTS` para montar a mensagem de diagnostico. O `main()` apenas
    imprime o que sai daqui — e onde mora TODA a decisao do gate, entao e o que o
    teste consegue cobrir sem git nem worktree.
    """
    # A ORDEM importa: o verde (tautologia) e diagnostico proprio e tem de ser
    # lido antes da faixa de codigos invalidos, senao vira "pytest saiu 0, isso
    # nao e falha de teste" e a mensagem que interessa se perde.
    if rc_antes == 0:
        return 1, ("REPROVADO: o grupo PASSA no codigo antigo. Teste tautologico —\n"
                   "               ele afirma o que o codigo faz, e ficaria verde com e sem o fix.")
    # Vermelho VALIDO e so 1 (falha) ou 2 (coleta). Qualquer outro codigo e o
    # pytest reclamando de si mesmo, nao do codigo: 3 interno, 4 uso (alvo
    # inexistente — medido: rc=4), 5 nada coletado. Aceitar "!= 0" como vermelho
    # daria APROVADO a um alvo digitado errado.
    if rc_antes not in (PYTEST_FALHA, PYTEST_COLETA):
        return 1, (f"REPROVADO: pytest saiu {rc_antes} na coluna antiga — isso nao e falha\n"
                   "               de teste (3=interno, 4=uso/alvo inexistente, 5=nada coletado).")
    if rc_depois != 0:
        return 1, f"REPROVADO: o grupo falha tambem no codigo corrigido (rc={rc_depois})."

    # `rpartition`: o resumo de verdade e sempre o ULTIMO: qualquer texto que um
    # teste imprima ja foi despejado antes dele. Sem o cabecalho o `rpartition`
    # devolve a saida INTEIRA na cauda, por isso `linhas` fica vazia ali: senao um
    # `print("FAILED ...")` de dentro de um teste passaria a valer por desfecho.
    _, achou, resumo = saida_antes.rpartition(CABECALHO_RESUMO)
    linhas = resumo.splitlines() if achou else []

    # Prova FORTE = algum teste RODOU e falhou no codigo antigo. Nao e o tipo da
    # excecao que decide (o #182 tinha vermelho legitimo por ValueError, zero
    # asserções): e se o corpo do teste chegou a executar. `FAILED` = executou e
    # explodiu; `ERROR` = nunca rodou (coleta, setup, fixture ou teardown), e ai
    # o vermelho so prova que o ambiente nao montou. Medido: erro de fixture sai
    # rc=1 com `ERROR` e ZERO `FAILED` — igual ao rc=2 da coleta.
    falharam = [l for l in linhas if l.startswith("FAILED ")]
    erros = [l for l in linhas if l.startswith("ERROR ")]
    # NENHUM desfecho = o gate nao mediu nada, e APROVAR aqui seria carimbar prova
    # sem prova. Duas causas caem no mesmo estado, por isso um ramo so: cabecalho
    # ausente (`-rN`, `--no-summary`, `-p no:terminal`) ou cabecalho presente com
    # as linhas de desfecho filtradas (medido: `-rs` da rc=1, cabecalho, e zero
    # FAILED/ERROR — um teste falhou e o gate diria "nenhum chegou a rodar").
    if not falharam and not erros:
        return 1, (f"REPROVADO: a coluna antiga ficou vermelha (rc={rc_antes}), mas o resumo do\n"
                   "               pytest nao trouxe uma linha FAILED nem ERROR — sem desfecho nao\n"
                   "               da para saber se algum teste chegou a RODAR, e o gate nao pode\n"
                   "               carimbar FORTE nem FRACA.\n"
                   f"               PYTEST_ADDOPTS={os.environ.get('PYTEST_ADDOPTS', '')!r} — `-rN`/`-rs`/`--no-summary`\n"
                   "               filtram o resumo e `-p no:terminal` tira a saida toda.\n"
                   "               Rode `unset PYTEST_ADDOPTS` e repita.")
    if falharam:
        extra = f" (e mais {len(erros)} que nem chegaram a rodar)" if erros else ""
        return 0, (f"APROVADO, prova FORTE: {len(falharam)} teste(s) RODARAM e falharam no codigo\n"
                   f"               antigo{extra}; o grupo fica verde no corrigido.\n"
                   f"               {falharam[0].strip()}")
    # Aqui `erros` e nao-vazio por construcao: o ramo acima ja devolveu quando as
    # duas listas estavam vazias. Nao ha mais desfecho que o gate nao saiba nomear.
    return 0, ("APROVADO, prova FRACA: NENHUM teste chegou a RODAR no codigo antigo —\n"
               "               coleta, setup ou fixture quebrou antes do corpo do teste.\n"
               f"               {erros[0].strip()}\n"
               "               Isso prova que o ambiente nao montou, nao que o comportamento\n"
               "               antigo estava errado. O Manager decide se aceita.")


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

    # ── Guarda 3: as duas colunas na mesma linha do tempo ────────────────────
    # Para a prova valer, `depois` tem de ser `antes` MAIS o fix. Se `antes` nao e
    # ancestral de `depois`, e uma de duas coisas, e as duas invalidam a prova:
    # ou o conserto ja esta dentro da coluna "antiga" (o erro que este script
    # existe para impedir — apontar a coluna vermelha para uma main ja corrigida,
    # e o vermelho nunca viria), ou as colunas divergiram e a diferenca entre elas
    # nao e so o fix. Medido: `origin/main` x o branch do PR #166 sao divergentes,
    # 44 arquivos de PRODUCAO diferem, e qualquer um deles pode dar o vermelho.
    # Falha fechada de proposito: `--is-ancestor` que erre por outro motivo (ref
    # inalcancavel) reprova, em vez de liberar.
    if subprocess.run(["git", "merge-base", "--is-ancestor", antes, depois],
                      capture_output=True).returncode != 0:
        sys.exit(f"[coluna-dupla] {antes[:8]} nao e ancestral de {depois[:8]}: ou a coluna 'antiga' JA CONTEM\n"
                 "                o fix, ou as colunas divergiram e a diferenca entre elas nao e so o fix.\n"
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

        # Por palavra-chave: sao dois `int` na mesma assinatura e nenhum teste passa
        # por este call site, entao trocar a ordem seria indetectavel.
        rc, relatorio = veredito(rc_antes=rc_antes, saida_antes=saida_antes, rc_depois=rc_depois)
        print(f"[coluna-dupla] {relatorio}")
        return rc
    finally:
        for wt in (wt_antes, wt_depois):
            subprocess.run(["git", "worktree", "remove", "--force", wt], capture_output=True)
        # rmtree ANTES do prune: o prune so descarta registro cujo diretorio ja
        # sumiu. Na ordem inversa, um `remove` que falhasse deixaria registro
        # orfao para sempre — e o repositorio ja carrega 10 desses.
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
