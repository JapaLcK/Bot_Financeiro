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

O desfecho de cada teste vem do XML do `--junitxml`, lido com a `ElementTree` da
stdlib — nao mais do texto do resumo. Isso fecha de uma vez a classe de furo que
ja apareceu TRES vezes (um `print` em fixture, um `atexit` no stderr, um `atexit`
no stdout): nenhum dos dois streams decide mais coisa alguma.

Sai 0 quando o grupo fica vermelho no antes e verde no depois — e verde aqui e
PAREADO, nao `rc=0`: todo teste que ficou VERMELHO no antigo (falha OU erro) tem
de ter passado no corrigido. rc=0 e tambem o que o pytest devolve quando TUDO foi
PULADO (o `tests/conftest.py:99` pula por dependencia ausente), e grupo pulado nao
prova conserto nenhum.
Sai 1 quando um teste PASSA no antes (tautologico), quando falha no depois,
quando a coluna corrigida nao prova o conserto, quando o pytest nao escreveu o
XML, ou quando uma guarda dispara.

MUDANCA VISIVEL de comportamento: `PYTEST_ADDOPTS=-rN`/`-rs`/`--no-summary` iam
para REPROVADO ("sem desfecho") e agora saem `APROVADO, prova FORTE` — eles
filtram o resumo impresso e nao alcancam o XML. E o gate ficando certo, mas quem
lia aquele REPROVADO como "seu ambiente esta torto" perde o aviso.

Limites conhecidos, medidos e DECLARADOS: quase todos abortam ou reprovam. Os
DOIS que podem virar APROVADO falso sao o alvo de `--testes` que o pytest EXPANDE
(acidente) e o teste que le o proprio `sys.argv` (ator deliberado); um terceiro
APROVA com o veredito certo e a NOTA inflada (a ultima celula) — a lista nao
esta em ordem de gravidade, entao eles vao nomeados. Nao valem conserto enquanto
nao aparecerem:
    - teste apagado (`D`) nao entra no overlay nem nos alvos, logo nunca roda
      (3 na historia de `tests/`); `D` do proprio conftest da FileNotFoundError.
    - `T` (`.py` virou symlink) some do `--diff-filter=AMR` -> "nada a provar",
      zero na historia; `C` o `git diff` sem `-C` nunca emite (o destino sai `A`);
      `U` o `git diff <sha> <sha>` nunca devolve, e a Guarda 1 ja barra conflito.
    - arquivo <-> diretorio no mesmo caminho: FileExistsError/IsADirectoryError.
    - symlink em `tests/`: zero na historia, e o `copyfile` segue o link.
    - interpretador sem pytest: as DUAS colunas dao rc=1 e NENHUMA escreve o XML,
      entao quem dispara e o ramo do relatorio ausente ("o pytest nao escreveu o
      relatorio XML da coluna antiga"); o `No module named pytest` sai repassado
      pelo nosso stderr, duas vezes.
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
    - teste que le o PROPRIO `sys.argv`: ele acha ali o caminho do `--junitxml` e
      pode reescrever o arquivo depois que o pytest o fechou — e, pela mesma
      porta, forjar o exit code de dentro (`os._exit(1)` num `atexit`), que e o
      outro insumo do veredito. Medido: um teste TAUTOLOGICO (`assert True`, verde
      nas duas colunas) sai `APROVADO, prova FORTE` reescrevendo o XML e o rc.
      Declarado, nao blindado: e a barra que a troca do texto pelo XML SUBIU — de
      "um `print` acidental engana o gate" para "o teste tem de ler o argv de
      proposito" —, e o caminho vive num `mkdtemp` sorteado, fora das duas
      arvores. Ator deliberado, nao acidente.
    - erro de COLETA no antigo e IMPAREAVEL: ele sai com `classname=""` e `name` =
      o modulo pontilhado, identidade que nao existe na coluna verde. Ele conta
      como vermelho (prova FRACA) e nunca como orfao — senao o caminho legitimo do
      Uso acima (teste novo importando modulo que so existe no corrigido) seria
      REPROVADO. Nessa forma o pareamento nao alcanca, e vale o criterio de grupo.
    - `xfail` na coluna VERDE conta como nao-passou (sai `<skipped>`): um teste que
      ficou vermelho no antigo e virou `xfail` no corrigido sai orfao -> REPROVADO.
      E o desfecho certo — `xfail` nao e conserto —, mas o relatorio o chama de
      "NAO passou" sem dizer que foi `xfail`; sem irmao passando, o caso nem chega
      ao ramo do orfao: cai no "0 passaram, 1 pularam".
    - `PYTEST_ADDOPTS=--co`: as duas colunas so coletam, o XML sai sem desfecho e o
      rc da antiga e 0 -> REPROVADO por "o grupo PASSA no codigo antigo. Teste
      tautologico". O veredito e o certo; o diagnostico impresso e falso, porque
      nada chegou a rodar.
    - a nota FORTE exige que ALGUM vermelho tenha `<failure>`, nao que o teste DO
      FIX tenha falhado. Medido: o teste do fix erra na fixture no antigo e um
      irmao pre-existente falha -> `APROVADO, prova FORTE` citando o IRMAO; sem o
      irmao o mesmo caso sairia FRACA, que e o caminho onde o Manager decide se
      aceita. O veredito se sustenta (o irmao foi vermelho->verde sob o fix, e
      evidencia legitima); o que se perde e o significado de FORTE, que deixa de
      implicar "o teste que voce escreveu rodou e falhou". NAO e consertavel: o
      overlay copia o teste novo para dentro da coluna antiga, entao as duas
      colunas coletam nodeids IDENTICOS por construcao (medido: DEPOIS - ANTES =
      []) e o gate nao tem como saber qual e o teste do PR. O `(e mais N que nem
      chegaram a rodar)` da linha do FORTE e a UNICA pista que o operador recebe.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import NamedTuple

# Codigos de saida do pytest. Sao a FAIXA de vermelho valido, NAO o criterio de
# prova: 1 = "Tests failed", 2 = coleta interrompida (ImportError/erro de sintaxe).
# Medido com pytest 9.0.2: rc=1 engloba tanto a asserção que falhou quanto o erro
# de setup/fixture, em que NENHUM corpo de teste chegou a rodar — por isso o exit
# code sozinho nao separa prova FORTE de FRACA. Quem separa e o `<failure>` x
# `<error>` do XML, lido pela `le_junit`. Qualquer outro codigo (3, 4, 5) e
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


class Desfechos(NamedTuple):
    """O que o XML de uma coluna diz. Registro, nao camada — sem metodos.

    `falhados` e `errados` sao `{(classname, name): (nodeid, causa)}` e `passados`
    e `{(classname, name): nodeid}`: a chave e a identidade que PAREIA as duas
    colunas — unica mesmo com classe, que o nome sozinho nao e —, e o valor
    carrega o que entra no relatorio.

    Os contadores saem dos `<testcase>`, nao dos atributos do `<testsuite>`: o
    cabecalho conta DUAS vezes o caso com `<failure>` E `<error>` (falhou no corpo,
    estourou no teardown), e o `int()` de um atributo forjado (`tests="abc"`)
    estourava `ValueError` numa funcao que promete `None`.
    """
    total: int
    pulados: int
    falhados: dict
    errados: dict
    passados: dict


def le_junit(caminho: str) -> Desfechos | None:
    """Le o XML do `--junitxml`. `None` quando ele nao existe ou nao e XML valido.

    O XML e imune ao que qualquer teste imprima: `-rN`/`-rs`/`--no-summary` nao o
    alcancam e um `atexit` escrevendo num stream nao entra aqui. Era o texto do
    resumo que decidia, e a mesma classe de furo apareceu tres vezes.

    A raiz e `<testsuites>`, com um `<testsuite>` dentro; o `iter` acha os dois
    casos e o `None` cobre o XML sem suite (medido: rc=4/5 escreve o arquivo).
    """
    try:
        suite = next(ET.parse(caminho).iter("testsuite"), None)
    except (OSError, ET.ParseError):
        return None
    if suite is None:
        return None

    total = pulados = 0
    falhados, errados, passados = {}, {}, {}
    for caso in suite.iter("testcase"):
        total += 1
        chave = (caso.get("classname", ""), caso.get("name", ""))
        # O `file` vem do `junit_family=xunit1`; sem ele a citacao sairia com o
        # modulo pontilhado (`tests.test_x::test_y`), que nao se cola no pytest.
        nodeid = f"{caso.get('file') or chave[0]}::{chave[1]}"
        falha, erro = caso.find("failure"), caso.find("error")
        no = falha if falha is not None else erro
        if no is None:
            # Passou = nem falha, nem erro, nem `skipped` (e o `xfail` sai
            # `<skipped>`, entao ele nao conta como passado).
            if caso.find("skipped") is None:
                passados[chave] = nodeid
            else:
                pulados += 1
            continue
        # A causa e a primeira linha `E ` do corpo, com o `message` de reserva: e o
        # que faltava na citacao de coleta, que saia sem dizer o que quebrou.
        causa = next((l[1:].strip() for l in (no.text or "").splitlines() if l.startswith("E ")),
                     no.get("message", "")).strip()
        (falhados if falha is not None else errados)[chave] = (nodeid, causa)
    return Desfechos(total, pulados, falhados, errados, passados)


def roda_pytest(cwd: str, py: str, alvos: list[str], relatorio: str) -> tuple[int, Desfechos | None]:
    env = {**os.environ, "PYTHONPATH": "."}
    # `-o junit_family=xunit1` so pelo `file=` de cada `<testcase>`. Um
    # `PYTEST_ADDOPTS` com outro `--junitxml` nao vence a linha de comando.
    r = subprocess.run([py, "-m", "pytest", "-q", "-o", "junit_family=xunit1",
                        f"--junitxml={relatorio}", *alvos],
                       cwd=cwd, env=env, capture_output=True, text=True)
    # O stderr continua repassado — e o unico diagnostico dos rc 3/4/5 e do
    # `No module named pytest`, que nao chegam a escrever XML. Ele nao alimenta
    # decisao nenhuma: quem decide e o arquivo.
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r.returncode, le_junit(relatorio)


def veredito(rc_antes: int, antes: Desfechos | None,
             rc_depois: int, depois: Desfechos | None) -> tuple[int, str]:
    """Classifica as duas colunas: devolve (codigo de saida, relatorio).

    Funcao pura dos quatro argumentos: nao escreve nada e nao le ambiente. O
    `main()` apenas imprime o que sai daqui — e onde mora TODA a decisao do gate,
    entao e o que o teste consegue cobrir sem git nem worktree.
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
    # Sem XML nao ha desfecho nenhum para ler, e tratar arquivo ausente como
    # "zero falhas" seria decidir no escuro. Carga real: um interpretador sem
    # pytest sai rc=1 — vermelho VALIDO, que passa pela faixa acima — e nao
    # escreve arquivo.
    if antes is None or depois is None:
        qual = "antiga" if antes is None else "corrigida"
        return 1, (f"REPROVADO: o pytest nao escreveu o relatorio XML da coluna {qual}.\n"
                   "               Sem desfecho nao ha prova — veja o stderr repassado acima.")
    if rc_depois != 0:
        return 1, f"REPROVADO: o grupo falha tambem no codigo corrigido (rc={rc_depois})."

    # Verde e PAREADO, nao `rc=0`: rc=0 e tambem o que o pytest devolve quando
    # TUDO foi pulado, e grupo pulado nao prova conserto (o `tests/conftest.py:99`
    # pula por dependencia ausente — o caso e alcancavel, nao teorico). Contar
    # ("algum passou") tambem nao fecha: medido num lab de dois testes, com o teste
    # DO FIX pulado no corrigido e um irmao passando, a contagem carimbava
    # `APROVADO, prova FORTE`. Quem discrimina e o pareamento por (classname,
    # name): quem ficou VERMELHO no antigo tem de ter passado no corrigido.
    #
    # Vermelho aqui e falha E erro. Varrer so a falha deixava a porta lateral pela
    # qual o mesmo furo voltava: o teste que ERRA na fixture no antigo — o
    # mecanismo que motivou este script, 21 erros de fixture — e e PULADO no
    # corrigido nao entrava na varredura, e um irmao carregava o carimbo FORTE
    # sozinho. Medido no lab: `APROVADO, prova FORTE`, exit 0.
    #
    # Excecao MEDIDA, e a unica: o erro de COLETA sai com `classname=""` e `name`
    # igual ao MODULO pontilhado (`pac.test_x`, `file=pac/test_x.py`) — identidade
    # que nao existe na coluna verde, onde o modulo vira testes de verdade. Ele e
    # impareavel por construcao, e exigir o par dele reprovaria o caminho FRACA
    # legitimo: teste novo importando modulo de producao que so existe no
    # corrigido, que e o exemplo do Uso la em cima. Por isso o `chave[0]` — so
    # entra na varredura quem tem classname, ou seja, um teste de verdade.
    #
    # Isto nao recusa trabalho honesto: um `skipif` de plataforma, ou o skip por
    # dependencia ausente do `tests/conftest.py:99` (que depende de ambiente +
    # pacote, iguais nas duas colunas), vale igual nos dois lados — o teste pulado
    # no verde tambem foi pulado no vermelho, e quem foi pulado nunca fica
    # vermelho para comecar.
    vermelhos = {**antes.falhados, **antes.errados}
    orfaos = sorted(nodeid for chave, (nodeid, _) in vermelhos.items()
                    if chave[0] and chave not in depois.passados)
    if not depois.passados or orfaos:
        # "Nenhum passou" vem antes do orfao: quando a coluna verde inteira foi
        # pulada, a contagem e o diagnostico, e a lista de orfaos seria so a
        # consequencia dela.
        detalhe = (f"{len(depois.passados)} passaram, {depois.pulados} pularam, de {depois.total}."
                   if not depois.passados else
                   f"{len(orfaos)} teste(s) vermelhos no antigo NAO passaram no corrigido: "
                   f"{', '.join(orfaos)}")
        return 1, ("REPROVADO: a coluna corrigida nao provou o conserto (rc=0).\n"
                   f"               {detalhe}")

    # Falha fechada: vermelho valido cujo XML nao traz falha nem erro. Sem desfecho
    # o gate nao pode carimbar FORTE nem FRACA.
    if not vermelhos:
        return 1, (f"REPROVADO: a coluna antiga ficou vermelha (rc={rc_antes}) e o XML nao trouxe\n"
                   f"               falha nem erro ({antes.total} caso(s), {antes.pulados} pulado(s)).")

    # Prova FORTE = algum teste RODOU e falhou no codigo antigo. Nao e o tipo da
    # excecao que decide (o #182 tinha vermelho legitimo por ValueError, zero
    # asserções): e se o corpo do teste chegou a executar. `<failure>` = executou e
    # explodiu; `<error>` = nunca rodou (coleta, setup, fixture ou teardown), e ai
    # o vermelho so prova que o ambiente nao montou. Medido: erro de fixture sai
    # rc=1 com `<error>` e ZERO `<failure>` — igual ao rc=2 da coleta.
    # A citacao sai do proprio conjunto vermelho: depois da varredura acima, todo
    # vermelho pareavel PASSOU no corrigido. Isso NAO garante que o citado seja o
    # teste do PR — um irmao pre-existente pode carregar a nota sozinho (ultima
    # celula dos limites, no topo do arquivo).
    if antes.falhados:
        nodeid, causa = next(iter(antes.falhados.values()))
        extra = f" (e mais {len(antes.errados)} que nem chegaram a rodar)" if antes.errados else ""
        return 0, (f"APROVADO, prova FORTE: {len(antes.falhados)} teste(s) RODARAM e falharam no codigo\n"
                   f"               antigo{extra}; o grupo fica verde no corrigido.\n"
                   f"               {nodeid} - {causa}")
    # Aqui `antes.errados` e nao-vazio por construcao: o ramo da falha fechada ja
    # devolveu quando os dois mapas estavam vazios. A frase e sobre os VERMELHOS, e
    # nao sobre o grupo: dizer "NENHUM teste chegou a RODAR" era falso sempre que a
    # coluna antiga tinha teste passando ao lado do que errou — e e esta a linha
    # que o Manager le para decidir se aceita prova fraca.
    nodeid, causa = next(iter(antes.errados.values()))
    return 0, (f"APROVADO, prova FRACA: nenhum dos {len(antes.errados)} teste(s) VERMELHOS chegou a\n"
               f"               RODAR no codigo antigo ({len(antes.passados)} passado(s) ao lado) —\n"
               "               coleta, setup ou fixture quebrou antes do corpo do teste.\n"
               f"               {nodeid} - {causa}\n"
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

        # Os XML vivem no `mkdtemp`, fora das duas arvores — nenhum worktree fica
        # sujo — e morrem com ele no `rmtree` do `finally`.
        rc_antes, desf_antes = roda_pytest(wt_antes, py, alvos, os.path.join(tmp, "antes.xml"))
        rc_depois, desf_depois = roda_pytest(wt_depois, py, alvos, os.path.join(tmp, "depois.xml"))

        # Por palavra-chave: os dois `int` e os dois `Desfechos` se alternam na
        # assinatura e nenhum teste passa por este call site, entao trocar a ordem
        # seria indetectavel.
        rc, relatorio = veredito(rc_antes=rc_antes, antes=desf_antes,
                                 rc_depois=rc_depois, depois=desf_depois)
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
