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

Limites conhecidos, medidos e DECLARADOS: todos abortam ou reprovam, MENOS o
ultimo — o unico que pode virar APROVADO falso. Nao valem conserto enquanto nao
aparecerem:
    - teste apagado (`D`) nao entra no overlay nem nos alvos, logo nunca roda
      (3 na historia de `tests/`); `D` do proprio conftest da FileNotFoundError.
    - `T` (`.py` virou symlink) some do `--diff-filter=AMR` -> "nada a provar",
      zero na historia; `C` o `git diff` sem `-C` nunca emite (o destino sai `A`);
      `U` o `git diff <sha> <sha>` nunca devolve, e a Guarda 1 ja barra conflito.
    - arquivo <-> diretorio no mesmo caminho: FileExistsError/IsADirectoryError.
    - symlink em `tests/`: zero na historia, e o `copyfile` segue o link.
    - interpretador sem pytest: as DUAS colunas dao rc=1, entao quem dispara e o
      ramo `rc_depois != 0` ("falha tambem no codigo corrigido") — a guarda de
      desfecho nunca chega a ser alcancada aqui; o `No module named pytest` sai
      repassado pelo nosso stderr, duas vezes.
    - `--testes` fora do overlay: alvo inexistente da rc=4 -> REPROVADO.
    - banco compartilhado pelas duas colunas: residuo da vermelha pode tingir a
      verde -> REPROVADO (fecha, nao mente).
    - alvo de `--testes` que o pytest EXPANDE e mesmo assim termina em `.py`: a
      guarda abaixo so olha o NOME, entao um diretorio chamado `pasta.py`, ou um
      symlink `x.py` -> `tests/`, passa e a coleta varre o que houver dentro —
      medido nos dois: rc=0 e `APROVADO, prova FORTE` citando teste alheio. E a
      UNICA celula desta lista que vira APROVADO falso em vez de abortar. Zero
      alcancavel neste repo: zero diretorios com nome terminado em `.py` e zero
      symlinks rastreados (modo 120000) na arvore. `os.path.isfile` seria pior:
      a guarda roda ANTES de os worktrees existirem, mediria contra o cwd e
      recusaria alvo que so existe em `--depois` — a fixture historica do Uso.
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

    # ── O que copiar: so teste, mas TUDO que mudou sob tests/ ────────────────
    # A coluna antiga recebe o TESTE novo por cima do CODIGO velho. Nenhum
    # arquivo de producao atravessa — o pathspec `tests/` garante isso.
    # `R` na lista de filtros: sem ele, um arquivo de teste RENOMEADO junto com o
    # caso novo some do overlay em silencio — medido, num rename `R096` o
    # `--diff-filter=AM` devolve VAZIO e o `AMR` devolve o destino.
    # E copiamos tudo, nao so `.py`: se o teste novo le uma fixture de dados
    # adicionada junto, sem ela a coluna antiga quebra por arquivo faltando, sai
    # `FAILED`, e o gate carimbaria FORTE tendo medido a ausencia do insumo — nao
    # o comportamento antigo. Hoje `tests/` nao tem fixture de dados nenhuma (os
    # 12 nao-`.py` sao `.test.mjs`, que o pytest ignora), entao isto e guarda
    # contra a convencao que ainda nao existe, ao custo de copiar arquivo inerte.
    # `-z` porque o `core.quotePath` (padrao `true`) devolve nome nao-ASCII entre
    # aspas e com escape octal — medido: `"tests/test_transfer\303\252ncia.py"`.
    # Sozinho, esse nome nao passa no `endswith(".py")` e o gate abortava com
    # "nenhum teste .py mudou", mensagem FALSA; junto de outro `.py`, dava
    # `FileNotFoundError` no copyfile. O `-z` entrega o byte cru. O `git()` faz
    # `.strip()`, que NAO remove `\0` — dai o `if p`, que descarta o vazio final.
    mudados = [p for p in git("diff", "--name-only", "-z", "--diff-filter=AMR", antes, depois,
                              "--", "tests/").split("\0") if p]
    # Os alvos continuam presos ao `.py`: um `.test.mjs` sozinho nao da o que
    # provar aqui.
    py_mudados = [p for p in mudados if p.endswith(".py")]
    # O conftest carrega as chaves de PII e o skip de dependencia ausente; sem
    # a versao nova dele, a coluna antiga pode falhar por ambiente, nao por bug.
    if "tests/conftest.py" not in mudados:
        mudados.append("tests/conftest.py")
    # O criterio e "alvo que aponta para UM arquivo `.py`" — nao "elemento truthy"
    # nem "lista nao vazia". Duas entradas passavam e mandavam o pytest coletar a
    # arvore INTEIRA, que e o bug que a guarda abaixo declara fechado: `--testes ""`
    # (wrapper com `--testes "$VAR"` e `$VAR` vazia) dava `alvos == [""]`, e
    # `--testes tests/` — o atalho humano, e o que um `$(dirname "$ARQ")` produz —
    # dava um DIRETORIO; medido nos dois: rc=0 e `APROVADO, prova FORTE` citando um
    # `test_alheio` que nada tem com a mudanca. Os dois casos NAO sao o mesmo: vazio
    # e ausencia, cai fora aqui e o resto segue (`--testes "" tests/test_x.py` mantem
    # o alvo); nao-`.py` e intencao errada e ABORTA logo abaixo, com o nome do alvo.
    alvos = [t for t in a.testes if t] or [p for p in py_mudados if p != "tests/conftest.py"]
    # Uma guarda so, no ALVO — nao no que mudou. No caminho PADRAO `py_mudados`
    # vazio implica `alvos` vazio, entao ali a guarda antiga (`if not py_mudados`)
    # virou subconjunto desta; o que ela nao pegava e o conftest mudando SOZINHO,
    # que dava `alvos == []` e mandava o `pytest -q` rodar a SUITE INTEIRA.
    # Com `--testes` NAO e subconjunto: o gate mudou de comportamento de proposito.
    # A guarda antiga abortava ("nenhum teste .py mudou") sempre que o diff nao
    # tocava teste, o que matava a escotilha justamente quando ela serve; hoje o
    # alvo explicito e provado. Medido num diff sem teste nenhum, com `--testes`
    # de um teste pre-existente: `f7a4daf` abortava, hoje sai APROVADO/FORTE.
    if not alvos:
        sys.exit(f"[coluna-dupla] nenhum teste .py alem do conftest mudou entre {antes[:8]} e {depois[:8]}.\n"
                 "                Nada a provar — e sem alvo o `pytest -q` roda a SUITE INTEIRA, onde o\n"
                 "                vermelho de qualquer teste alheio viraria 'prova'. Ou passe --testes.")

    # Descartar o alvo ruim em silencio seria pior que recusar: pelo `or`, o gate
    # passaria a provar OUTRO conjunto (o padrao) sem o operador pedir. O `split`
    # aceita node id, que e alvo legitimo e carrega `::` — `tests/x.py::test_y` e
    # `tests/x.py::Classe::test_y` valem, quem decide e o arquivo antes do `::`.
    mau = next((t for t in alvos if not t.split("::", 1)[0].endswith(".py")), None)
    if mau is not None:
        sys.exit(f"[coluna-dupla] --testes {mau!r} nao aponta para um arquivo de teste .py.\n"
                 "                Esta guarda so checa o NOME (ver os limites no topo do arquivo): um\n"
                 "                diretorio como `tests/` faz a coleta varrer a ARVORE INTEIRA, onde o\n"
                 "                vermelho de um teste alheio viraria 'prova'.\n"
                 "                Passe o arquivo (`tests/test_x.py`) ou o node id (`tests/x.py::test_y`).")

    # `shutil.which` cobre as duas formas de `--python` numa chamada: nome no PATH
    # (`python3`) e caminho (relativo ou absoluto), e exige X_OK — o
    # `os.path.exists` de antes aceitava arquivo sem bit de execucao. O fallback
    # silencioso para o `sys.executable` fica SO no padrao, que ja e impresso;
    # `--python` que nao resolve aborta em vez de rodar outro interpretador calado.
    # `abspath`, nao `realpath`: o cwd do subprocesso e o worktree, entao o caminho
    # PRECISA ser absoluto — e o abspath mantem legivel o que o operador escreveu.
    py = shutil.which(a.python or os.path.join(raiz_principal(), ".venv", "bin", "python"))
    if not py:
        if a.python:
            sys.exit(f"[coluna-dupla] --python {a.python!r}: nao e executavel nem como caminho nem como nome no PATH.")
        py = sys.executable
    py = os.path.abspath(py)

    tmp = tempfile.mkdtemp(prefix="coluna-dupla-")
    wt_antes, wt_depois = os.path.join(tmp, "antes"), os.path.join(tmp, "depois")
    try:
        # Duas arvores proprias, sempre — inclusive para `depois`. O worktree
        # atual nunca e usado como coluna, entao o script nao depende de o
        # repositorio inteiro estar limpo, so de ESTA arvore estar.
        git("worktree", "add", "--detach", "--quiet", wt_antes, antes)
        git("worktree", "add", "--detach", "--quiet", wt_depois, depois)

        for rel in mudados:
            src, dst = os.path.join(wt_depois, rel), os.path.join(wt_antes, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            # `copyfile` NAO leva o modo, e o destino herda o do arquivo antigo:
            # um teste que ganhou bit de execucao (ou um conftest que perdeu o de
            # leitura) rodava na coluna antiga com a permissao errada. `copymode`
            # depois da copia resolve, e SEM `shutil.copy`: ele tem um ramo
            # `if os.path.isdir(dst)` que passaria a copiar PARA DENTRO do
            # diretorio, em silencio, na celula diretorio->arquivo — trocaria um
            # `IsADirectoryError` que fecha por um overlay errado que pode virar
            # FORTE falso.
            shutil.copyfile(src, dst)
            shutil.copymode(src, dst)

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
