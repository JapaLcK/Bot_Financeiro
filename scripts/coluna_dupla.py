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

    # o mesmo, num caso que sai prova FRACA: no antigo o gate nao ve o corpo rodar
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

MUDANCA VISIVEL de comportamento: o `PYTEST_ADDOPTS` do ambiente deixou de
alcancar as duas colunas (ver `roda_pytest`). Quem o usava para afinar as
corridas internas do gate perde isso — de proposito: opcao herdada muda QUANTOS
testes rodam, e um ALVO posto ali entrava antes do `--` e virava prova falsa.

Todo comando escrito neste arquivo roda da RAIZ de um checkout que TENHA o `.venv`.
Um worktree de `.claude/worktrees/` nao tem (medido: o `grep` do `_makepath` la sai
`no matches found`, e o `.venv/bin/python` nao existe) — de dentro dele, aponte o
`.venv` da raiz principal. Os `grep -r` levam `--exclude-dir=.venv` porque sem ele o
resultado depende de QUAL grep a maquina tem (medido: o desta e `ugrep`, que le o
`.gitignore` e pula o `.venv` sozinho; o GNU grep nao pula, e o site-packages entra
na contagem).

Limites conhecidos, medidos e DECLARADOS. A lista nao esta em ordem de gravidade,
entao o que mexe no VEREDITO ou na NOTA vai nomeado em tres categorias; as demais
celulas abortam, reprovam, ou descrevem comportamento deliberado. (a) APROVADO falso,
TRES: o alvo de `--testes` que o pytest EXPANDE (acidente), o teste que le o proprio
`sys.argv` (ator deliberado) e o banco compartilhado que o `PYTEST_DB_ISOLATION=0`
devolve (ambiente de quem roda). (b) veredito certo com a NOTA INFLADA (FRACA carimbada
FORTE), DOIS: o `<failure>` que traz uma linha `arquivo:linha:` do proprio caso (item 3
da celula do traceback) e a ultima celula. (c) veredito certo com a NOTA DEFLACIONADA
(FORTE legitima rebaixada a FRACA): o item 1 da celula do traceback e os quatro
gatilhos do item 2 — todos subnotificam, que e a direcao segura escolhida de proposito,
e nenhum aprova falso. Nao valem conserto enquanto nao aparecerem:
    - `T` (`.py` virou symlink) some do `--diff-filter=AMRD` -> "nada a provar",
      zero na historia; `C` so aparece com `diff.renames=copies` na config de QUEM
      roda, e a chamada o desliga com `-c diff.renames=true` — medido: mesmos SHAs,
      padrao -> FORTE, `copies` -> REPROVADO/tautologico, acusando de tautologico um
      teste que nunca chegou a ser coletado; `U` o `git diff <sha> <sha>` nunca
      devolve, e a Guarda 1 ja barra conflito.
    - symlink em `tests/`: zero na historia, e o `copyfile` segue o link.
    - interpretador sem pytest: as DUAS colunas dao rc=1 e NENHUMA escreve o XML,
      entao quem dispara e o ramo do relatorio ausente ("o pytest nao escreveu o
      relatorio XML da coluna antiga"); o `No module named pytest` sai repassado
      pelo nosso stderr, duas vezes.
    - `--testes` fora do overlay: alvo RELATIVO inexistente da rc=4 -> REPROVADO.
      Alvo ABSOLUTO (ou com `..`) hoje ABORTA na guarda: ele nao vive em coluna
      nenhuma — o `cwd` do pytest e o worktree —, era coletado do checkout ATUAL, e
      ali uma revisao POSTERIOR do teste decidia o veredito dos SHAs impressos.
      Medido antes da guarda: par tautologico saia `APROVADO, prova FRACA`, exit 0.
    - banco compartilhado pelas duas colunas: residuo da vermelha tinge a verde nas
      DUAS direcoes — pode reprovar conserto real, e pode carimbar APROVADO falso num
      conserto que nao existe. Alcancabilidade ZERO enquanto o `tests/conftest.py`
      sortear um `pytest_<uuid>` por execucao no `pytest_configure`; a porta explicita
      que ele documenta, `PYTEST_DB_ISOLATION=0`, devolve as duas colunas ao MESMO
      banco — e o gate a repassa, porque o env dele parte de `{**os.environ}`.
    - alvo de `--testes` que o pytest EXPANDE e mesmo assim termina em `.py`: a
      guarda abaixo so olha o NOME, entao um diretorio chamado `pasta.py`, ou um
      symlink `x.py` -> `tests/`, passa e a coleta varre o que houver dentro —
      medido nos dois: rc=0 e `APROVADO, prova FORTE` citando teste alheio. E
      uma das celulas que viram APROVADO falso em vez de abortar — quais e
      quantas sao, quem diz e o preambulo; a contagem nao se repete aqui. Zero
      alcancavel neste repo: zero diretorios com nome terminado em `.py` e zero
      symlinks rastreados (modo 120000) na arvore. `os.path.isfile` seria pior:
      a guarda roda ANTES de os worktrees existirem, mediria contra o cwd e
      recusaria alvo que so existe em `--depois` — a fixture historica do Uso.
    - teste que le o PROPRIO `sys.argv`: ele acha ali o caminho do `--junitxml` e pode
      reescrever o arquivo depois que o pytest o fechou — e, pela mesma porta, forjar
      o exit code de dentro (`os._exit(1)` num `atexit`), que e o outro insumo do
      veredito. Medido: um teste TAUTOLOGICO (`assert True`, verde nas duas colunas)
      sai `APROVADO, prova FORTE` reescrevendo o XML e o rc — um APROVADO falso.
      Declarado, nao blindado: e a barra que a troca do texto pelo XML SUBIU — de "um
      `print` acidental engana o gate" para "o teste tem de ler o argv de proposito"
      —, e o caminho vive num `mkdtemp` sorteado, fora das duas arvores. Ator
      deliberado, nao acidente.
    - erro de COLETA no antigo e IMPAREAVEL: ele sai com `classname=""` e `name` =
      o modulo pontilhado, identidade que nao existe na coluna verde. Ele conta
      como vermelho (prova FRACA) e nunca como orfao — senao o caminho legitimo do
      Uso acima (teste novo importando modulo que so existe no corrigido) seria
      REPROVADO. Nessa forma o pareamento nao alcanca, e vale o criterio de grupo.
      A citacao dele NAO tenta um `::` que nao existe: sai o ARQUIVO. AQUI ele e
      colavel — o `file` E o teste, e `pytest tests/test_x.py` reproduz o erro de
      coleta (rc=2, medido). A mesma queda cobre o metodo HERDADO de uma base em
      OUTRO arquivo, e ali a citacao NAO e colavel: o `file` e o da BASE, que nao e
      arquivo de teste, e rerodar da rc=5, nenhum teste coletado (medido:
      `class BaseTests` em `base_comum.py`, `class TestFilho(BaseTests)` em
      `test_filho.py` -> citacao `base_comum.py` -> rc=5). Nessa metade a citacao e
      o lugar onde olhar, nao um comando. Zero hoje, e o comando remede:
          grep -rn "class Test[A-Za-z_]*(" --include="*.py" tests/
    - `xfail` na coluna VERDE conta como nao-passou (sai `<skipped>`): um teste que
      ficou vermelho no antigo e virou `xfail` no corrigido sai orfao -> REPROVADO.
      E o desfecho certo — `xfail` nao e conserto —, mas o relatorio o chama de
      "NAO passou" sem dizer que foi `xfail`; sem irmao passando, o caso nem chega
      ao ramo do orfao: cai no "0 passaram, 1 pularam".
    - `<failure>` x `<error>` e a FASE (`report.when == "call"`,
      `_pytest/junitxml.py`), nao "o corpo rodou": hook de `pytest_runtest_call`
      que estoure ANTES do corpo sai `<failure>`. Fechado pelo traceback — so vira
      FORTE se algum frame for do ARQUIVO do proprio caso (`file=`), em QUALQUER
      posicao e nao no fim: o corpo que chama producao que estoura termina em
      `lib.py:2:` e e o vermelho legitimo do #182. Sobra, medido: (1)
      `--tb=no|line|native` apaga os frames e TUDO vira FRACA — subnotifica, nunca
      aprova falso; zero alcancavel (nenhum `addopts` no repo, `PYTEST_ADDOPTS`
      zerado). (2) FORTE legitima REBAIXADA a FRACA, quatro gatilhos, cada um com ZERO
      ocorrencias quando isto foi escrito — os comandos abaixo remedem; o `--exclude`
      tira as auto-mencoes deste arquivo. O `.` final NAO e enfeite: sem operando de
      caminho o grep do macOS le STDIN, e colado num shell ele trava — ou, com stdin
      fechado, devolve `0` para qualquer arvore. (a) `__tracebackhide__` no corpo
      esconde o proprio frame:
          grep -rn "__tracebackhide__" --include="*.py" --exclude=coluna_dupla.py \
              --exclude-dir=.venv .
      (b) `os.chdir` / `monkeypatch.chdir` no corpo ou em fixture `autouse` muda a base
      do caminho do frame — e o `monkeypatch` NAO salva: o repr e montado na fase call,
      antes do teardown que restaura:
          grep -rn "chdir" --include="*.py" --exclude=coluna_dupla.py --exclude-dir=.venv .
      (c) um `pytest.ini`/`setup.cfg`/`tox.ini`/`pyproject.toml` dentro de `tests/`
      desloca o rootdir e derruba a CATEGORIA INTEIRA de uma vez (zero rastreados e
      zero nao rastreados em `tests/`). (d) `pytest.fail(msg, pytrace=False)`, que nao
      emite traceback nenhum:
          grep -rn "pytrace" --include="*.py" --exclude=coluna_dupla.py --exclude-dir=.venv .
      O (b) e o (c) tem UMA raiz: o `file=` do `<testcase>` e `bestrelpath` contra o
      ROOTDIR e o caminho do frame e `bestrelpath` contra o CWD — duas bases que so
      coincidem por acidente. Quem monta a segunda e o `FormattedExcinfo._makepath`,
      em `_pytest/_code/code.py`; a linha dele muda com a versao do pytest, entao ela
      nao fica escrita aqui:
          grep -n "def _makepath" .venv/lib/python*/site-packages/_pytest/_code/code.py
      (3) FRACA carimbada FORTE, e NAO exige ma-fe: basta o
      texto do `<failure>` trazer em coluna 0 uma linha `arquivo:linha: motivo` com o
      `file=` do proprio caso. `pytest.fail(msg, pytrace=False)` poe a MENSAGEM CRUA no
      `longrepr` — nao passa pelo `get_exconly`, entao nao recebe o `E   ` que a
      indentaria —, e um hook de `pytest_runtest_call` que reporte nesse formato
      universal sai `APROVADO, prova FORTE` com o corpo NUNCA chamado (medido: um
      `<failure>` de duas linhas, `boom` e `tests/test_valor.py:1: forjado`, sai FORTE).
      Nao da para fechar no codigo: a mensagem e texto arbitrario, e separar forjado de
      real pediria heuristica fragil.
    - a nota FORTE exige que ALGUM vermelho tenha `<failure>` COM frame do proprio
      arquivo, nao que o teste DO FIX tenha falhado. Medido: o teste do fix erra na
      fixture no antigo e um irmao pre-existente falha -> `APROVADO, prova FORTE`
      citando o IRMAO; sem o irmao o mesmo caso sairia FRACA, entao e uma FRACA
      carimbada FORTE — e o caminho onde o Manager decide se aceita. O veredito se
      sustenta (o irmao foi vermelho->verde sob o fix, e evidencia legitima); o que se
      perde e o significado de FORTE, que deixa de implicar "o teste que voce escreveu
      rodou e falhou". NAO e consertavel: o overlay copia o teste novo para dentro da
      coluna antiga, entao as duas colunas coletam nodeids IDENTICOS por construcao
      (medido: DEPOIS - ANTES = []) e o gate nao tem como saber qual e o teste do PR.
      O que o operador recebe e a CONTAGEM da linha do FORTE (`N teste(s) RODARAM e
      falharam`), sempre impressa e confrontavel com o unico nodeid citado; o `(e mais
      N ...)` so entra quando ha vermelho FORA de `falhados` — `<failure>` sem frame
      do arquivo do caso ou `<error>` —, e ele nomeia os dois separadamente; com dois
      `<failure>` COM frame e nada mais, ele nao sai. A CONTAGEM tambem nao mede poder
      discriminante: uma assinatura mudada acende varios casos de uma vez, e `N
      teste(s) RODARAM e falharam` nao separa isso de N provas independentes. Sem
      numero aqui de proposito — contagem que um comando responde nao e fato de
      documentacao (CLAUDE.md §2).
"""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import NamedTuple

# Codigos de saida do pytest que chegam a carregar um vermelho real no XML.
# 1 = "Tests failed"; 2 = "Interrupted". O rc=2 TAMBEM aparece quando a coleta
# para por ImportError/erro de sintaxe, mas nao prova isso sozinho: um
# KeyboardInterrupt depois de uma falha deixa XML parcial e tambem sai 2. Quem
# distingue a coleta legitima da execucao interrompida e o formato dos desfechos
# lidos pela `le_junit`. Qualquer outro codigo (3, 4, 5) e reprovado.
PYTEST_FALHA, PYTEST_INTERROMPIDO = 1, 2


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

    Os TRES baldes de vermelho sao `{(classname, name): (nodeid, causa)}` e
    `passados` e `{(classname, name): nodeid}`: a chave e a identidade que PAREIA as
    duas colunas — unica mesmo com classe, que o nome sozinho nao e —, e o valor
    carrega o que entra no relatorio. Sao tres porque o gate sabe TRES coisas
    diferentes, e imprimir a mesma frase para duas delas ja saiu falso:
      - `falhados`: `<failure>` COM frame do arquivo do caso — o corpo rodou.
      - `ambiguos`: `<failure>` SEM esse frame — pode ter rodado (item 2 da celula
        do traceback) ou nao (hook de fase call); o gate NAO distingue.
      - `errados`: `<error>`, ou seja, fase != call. So em coleta e setup isso
        implica corpo nao executado: no TEARDOWN o corpo ja rodou. Medido (pytest
        9.0.2, `junit_family=xunit1`): fixture `yield` que estoura no teardown sai
        um `<testcase>` com SO `<error>`, `message='failed on teardown with "..."'`,
        com o corpo executado e PASSADO.

    Os contadores saem dos `<testcase>`, nao dos atributos do `<testsuite>`: o
    cabecalho conta DUAS vezes o caso com `<failure>` E `<error>` (falhou no corpo,
    estourou no teardown), e o `int()` de um atributo forjado (`tests="abc"`)
    estourava `ValueError` numa funcao que promete `None`. Esse caso sai em DOIS
    `<testcase>` com a MESMA chave, e conta em dois baldes — aqui tambem.

    E o teste que PASSA e estoura no teardown nao entra em `passados` (o
    `<testcase>` dele tem `<error>`): na coluna verde ele vira orfao e o gate
    REPROVA. Falha fechada, e a direcao certa — a coluna corrigida com fixture
    quebrando nao provou conserto nenhum.
    """
    total: int
    pulados: int
    falhados: dict
    ambiguos: dict
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
    falhados, ambiguos, errados, passados = {}, {}, {}, {}
    for caso in suite.iter("testcase"):
        total += 1
        chave = (caso.get("classname", ""), caso.get("name", ""))
        # O node id EXECUTAVEL nao esta no XML: o `classname` do xunit1 e o modulo
        # pontilhado MAIS a cadeia de classes, e e ela que o pytest quer com `::` —
        # sem isso, metodo de classe saia `tests/x.py::test_y`, recusado com rc=4.
        # Quantos node ids desta suite estao em classe, remedir antes de reusar (o
        # console script `pytest` desta maquina tem shebang morto e sai VAZIO, que
        # o `grep -c` entrega como 0 — sempre pelo `-m pytest`):
        #     git ls-files 'tests/*.py' | xargs .venv/bin/python -m pytest -q \
        #         --collect-only | grep '::' | grep -c '::.*::'
        # O corte NAO usa marco nem busca: o `file` DA o modulo pontilhado inteiro,
        # e o casamento e por PREFIXO EXATO. Buscar o basename cortava na PRIMEIRA
        # ocorrencia — em `test_x/test_x.py` o marco casava no DIRETORIO e ate
        # `test_x/test_x.py::test_solto`, funcao de modulo, saia `::test_x::` e rc=4
        # (medido). O prefixo vale com e sem `__init__.py`: nos dois o classname e
        # o caminho pontilhado a partir do rootdir (medido nos dois).
        arq = caso.get("file") or ""
        mod = arq.removesuffix(".py").replace(os.sep, ".")
        if arq and (chave[0] == mod or chave[0].startswith(mod + ".")):
            classes = [c for c in chave[0][len(mod):].split(".") if c]
            cru = "::".join([arq, *classes, chave[1]])
        else:
            # O classname NAO parte do modulo do `file` em TRES formas, e em nenhuma a
            # identidade da para reconstruir. Nas duas primeiras ha `file`, e cai nele
            # em vez de inventar um `::` que nao existe — mas so uma e RERODAVEL:
            #   - erro de COLETA (`classname=""`, e o que este gate imprimiu contra o
            #     proprio repo): o `file` E o teste, e `pytest tests/test_x.py`
            #     reproduz o erro (rc=2, medido).
            #   - metodo HERDADO de base em outro arquivo (`file` = o da base): o
            #     `file` NAO e arquivo de teste, e rerodar a citacao da rc=5, nenhum
            #     teste coletado (medido: `base_comum.py` com a classe base). E o
            #     nome do lugar onde olhar, nao um comando colavel. Zero rastreados,
            #     ver o cabecalho.
            # A terceira NAO tem `file`, e e so por ela que o `::` deste `or` existe.
            # O `file` sai do `record_testreport` do `_pytest/junitxml.py`, e o
            # INTERNALERROR nao passa por ele: quem escreve o caso e o
            # `pytest_internalerror`, que so poe `classname` e `name`. Medido (pytest
            # 9.0.2, `junit_family=xunit1`, `raise` num
            # `pytest_collection_modifyitems`): `<testcase classname="pytest"
            # name="internal">`, atributos `{classname, name, time}` e nada mais, e o
            # `le_junit` devolve `('pytest','internal') -> 'pytest::internal'`. E
            # ROTULO, nao node id — nao se cola em lugar nenhum —, mas o `arq` vazio
            # daria citacao VAZIA, que e pior. Nao chega ao relatorio pelo caminho
            # medido: esse XML vem com rc=3 e a `veredito` recusa antes de olhar
            # desfecho.
            cru = arq or f"{chave[0]}::{chave[1]}"
        # Ja sai CITAVEL: id de parametrizacao carrega espaco e colchete, que o shell
        # come. E texto de relatorio, nao elemento de argv. Quantos precisam de aspas
        # nesta suite, remedir antes de reusar:
        #     git ls-files 'tests/*.py' | xargs .venv/bin/python -m pytest -q \
        #         --collect-only | grep '::' | .venv/bin/python -c \
        #         "import shlex,sys; print(sum(shlex.quote(l.strip()) != l.strip()\
        #                                      for l in sys.stdin))"
        nodeid = shlex.quote(cru)
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
        texto = no.text or ""
        causa = next((l[1:].strip() for l in texto.splitlines() if l.startswith("E ")),
                     no.get("message", "")).strip()
        # A TAG e a FASE (`report.when == "call"`, `_pytest/junitxml.py`), nao "o corpo rodou":
        # hook de `pytest_runtest_call` que estoure ANTES do corpo tambem sai `<failure>`. Quem
        # separa e o traceback — frame do ARQUIVO do caso em QUALQUER posicao, nao no fim: o
        # corpo que chama producao que estoura termina em `lib.py:2:` e e vermelho legitimo.
        # Sem `file` o prefixo vira `":"`, que frame nenhum casa: fechado por construcao.
        quadro = f"{caso.get('file') or ''}:"
        rodou = falha is not None and any(l.startswith(quadro) for l in texto.splitlines())
        # Tres estados, nao dois: `<error>` (fase != call) e `<failure>` sem frame nao
        # sao a mesma coisa e nao podem imprimir a mesma frase.
        destino = falhados if rodou else (ambiguos if falha is not None else errados)
        destino[chave] = (nodeid, causa)
    return Desfechos(total, pulados, falhados, ambiguos, errados, passados)


def roda_pytest(cwd: str, py: str, alvos: list[str], relatorio: str) -> tuple[int, Desfechos | None, str]:
    # `PYTEST_ADDOPTS` ZERADO: o pytest o PREPENDE, entao um ALVO posto ali entra
    # ANTES do nosso `--` e nada o barra — medido, com `PYTEST_ADDOPTS=<um teste>`
    # o mesmo lab que dava REPROVADO/tautologico passou a `APROVADO, prova FORTE`
    # citando um teste que o operador nao pediu, com a linha `testes = ...` impressa
    # mentindo sobre o que rodou. Nada no repo exporta a variavel, e o harness deste
    # gate ja a zerava pelo mesmo motivo: opcao herdada muda QUANTOS testes rodam,
    # que e justamente o que uma prova nao pode deixar variar.
    env = {**os.environ, "PYTHONPATH": ".", "PYTEST_ADDOPTS": ""}
    # `-o junit_family=xunit1` so pelo `file=` de cada `<testcase>`.
    # O `--` encerra o parsing de opcoes para os ALVOS: sem ele, alvo com cara de
    # opcao era consumido COMO opcao e o pytest ficava sem alvo nenhum -> coleta a
    # arvore inteira, o mesmo modo de falha do `--testes tests/`. Medido:
    # `--testes=--basetemp=<x>.py` passa pela guarda de nome (termina em `.py`) e
    # saia `APROVADO, prova FORTE` citando teste alheio; com o `--` vira alvo
    # inexistente -> rc=4 -> REPROVADO. NAO fecha a classe inteira: o
    # `consider_preparse` do pytest varre `args` linearmente e IGNORA o `--`, entao
    # `-p<algo>.py` ainda chega la — medido, e os tres casos (`-p=x.py`, `-pxml.py`,
    # `-pno:x.py`) fecham pela guarda do XML ausente ou por rc=4, nao pelo `--`.
    r = subprocess.run([py, "-m", "pytest", "-q", "-o", "junit_family=xunit1",
                        f"--junitxml={relatorio}", "--", *alvos],
                       cwd=cwd, env=env, capture_output=True, text=True)
    # O stderr continua repassado — e o unico diagnostico dos rc 3/4/5 e do
    # `No module named pytest`, que nao chegam a escrever XML. Ele nao alimenta
    # decisao nenhuma: quem decide e o arquivo.
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r.returncode, le_junit(relatorio), r.stdout


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
    # So 1 (falha) e 2 (interrompido, incluindo coleta abortada) podem carregar
    # vermelho util. Qualquer outro codigo e o pytest reclamando de si mesmo:
    # 3 interno, 4 uso (alvo inexistente — medido: rc=4), 5 nada coletado.
    if rc_antes not in (PYTEST_FALHA, PYTEST_INTERROMPIDO):
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
    # ExitCode 2 significa INTERRUPTED, nao "erro de coleta". A coleta legitima
    # tambem usa esse codigo, e no JUnit ela e reconhecivel: so existem `<error>`
    # impareaveis, com `classname` vazio; nenhum teste passou nem falhou no corpo.
    # Se uma falha vier antes de KeyboardInterrupt, o XML parcial preserva essa
    # `<failure>` — sem esta guarda o gate carimbava FORTE uma coluna incompleta. O
    # `ambiguos` entra na condicao pelo mesmo motivo que o `falhados`: uma coluna
    # interrompida com um `pytest.fail(pytrace=False)` ao lado dos erros de coleta
    # nao e coleta pura, e sem a clausula ela passaria pela guarda.
    if rc_antes == PYTEST_INTERROMPIDO:
        coleta_pura = (bool(antes.errados) and not antes.falhados and not antes.ambiguos
                       and not antes.passados
                       and all(not chave[0] for chave in antes.errados))
        if not coleta_pura:
            return 1, ("REPROVADO: a coluna antiga foi interrompida (rc=2).\n"
                       "               XML parcial nao prova o comportamento antigo.")
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
    vermelhos = {**antes.falhados, **antes.ambiguos, **antes.errados}
    orfaos = sorted(nodeid for chave, (nodeid, _) in vermelhos.items()
                    if chave[0] and chave not in depois.passados)
    if not depois.passados or orfaos:
        # "Nenhum passou" vem antes do orfao: quando a coluna verde inteira foi
        # pulada, a contagem e o diagnostico, e a lista de orfaos seria so a
        # consequencia dela.
        detalhe = (f"{len(depois.passados)} passaram, {depois.pulados} pularam, de {depois.total}."
                   if not depois.passados else
                   f"{len(orfaos)} teste(s) vermelhos no antigo NAO passaram no corrigido: "
                   f"{' '.join(orfaos)}")
        return 1, ("REPROVADO: a coluna corrigida nao provou o conserto (rc=0).\n"
                   f"               {detalhe}")

    # Falha fechada: vermelho valido cujo XML nao traz falha nem erro. Sem desfecho
    # o gate nao pode carimbar FORTE nem FRACA.
    if not vermelhos:
        return 1, (f"REPROVADO: a coluna antiga ficou vermelha (rc={rc_antes}) e o XML nao trouxe\n"
                   f"               falha nem erro ({antes.total} caso(s), {antes.pulados} pulado(s)).")

    # Prova FORTE = algum teste RODOU e falhou no codigo antigo. Nao e o tipo da
    # excecao que decide (o #182 tinha vermelho legitimo por ValueError, zero
    # asserções): e se o corpo do teste chegou a executar. A TAG do JUnit nao diz
    # isso — ela e a FASE (`report.when == "call"`), e um hook de fase call que
    # estoure ANTES do corpo tambem sai `<failure>`. Quem separa e o frame do
    # proprio arquivo do caso no traceback (ver `le_junit`); sem ele o `<failure>`
    # cai em `ambiguos`, onde o gate NAO sabe se o corpo rodou. O `<error>` e outro
    # balde: fase != call. So em COLETA e SETUP ele prova que o ambiente nao montou
    # — no TEARDOWN o corpo ja rodou. Medido: erro de fixture sai rc=1 com `<error>`
    # e ZERO `<failure>` — igual ao rc=2 da coleta.
    # A citacao sai do proprio conjunto vermelho: depois da varredura acima, todo
    # vermelho pareavel PASSOU no corrigido. Isso NAO garante que o citado seja o
    # teste do PR — um irmao pre-existente pode carregar a nota sozinho (ultima
    # celula dos limites, no topo do arquivo).
    if antes.falhados:
        nodeid, causa = next(iter(antes.falhados.values()))
        # Chamar de nao-rodado tudo o que nao e `<failure>` COM frame era falso nos
        # dois baldes: o `<failure>` sem frame pode ter rodado, e o `<error>` de
        # teardown rodou. Cada um sai nomeado e contado.
        restos = [f"{len(m)} {rotulo}" for m, rotulo in
                  ((antes.ambiguos, "com <failure> sem frame do arquivo do caso"),
                   (antes.errados, "com <error> fora da fase call")) if m]
        extra = f" (e mais {' e '.join(restos)})" if restos else ""
        return 0, (f"APROVADO, prova FORTE: {len(antes.falhados)} teste(s) RODARAM e falharam no codigo\n"
                   f"               antigo{extra}; o grupo fica verde no corrigido.\n"
                   f"               {nodeid} - {causa}")
    # Aqui `ambiguos` ou `errados` e nao-vazio por construcao: o ramo da falha
    # fechada ja devolveu quando os tres mapas estavam vazios. A frase e sobre os
    # VERMELHOS, e nao sobre o grupo: dizer "NENHUM teste chegou a RODAR" era falso
    # sempre que a coluna antiga tinha teste passando ao lado do que errou — e e esta
    # a linha que o Manager le para decidir se aceita prova fraca.
    # O cabecalho e a afirmacao mais fraca que vale nos DOIS baldes ("o gate nao viu
    # o corpo rodar"), e cada balde presente ganha a sua linha: sao coisas diferentes,
    # e a versao que os fundia dizia "o corpo nem chegou a rodar" de um `<error>` de
    # TEARDOWN, onde o corpo rodou e passou. Um grupo so-`<error>` nao fala de frame
    # nem de traceback: o gate nao olhou traceback nenhum dele.
    sobrando = {**antes.ambiguos, **antes.errados}
    nodeid, causa = next(iter(sobrando.values()))
    detalhe = ""
    if antes.ambiguos:
        detalhe += (f"               {len(antes.ambiguos)} com <failure> e SEM frame do arquivo do caso no traceback: pode ter\n"
                    "               rodado (pytrace=False, chdir, --tb=no, rootdir deslocado) ou nao (hook\n"
                    "               de fase call) — o gate nao distingue.\n")
    if antes.errados:
        detalhe += (f"               {len(antes.errados)} com <error>, ou seja, fora da fase call: em coleta e setup o corpo\n"
                    "               NAO rodou; no teardown ele rodou (o gate nao separa os tres).\n")
    return 0, (f"APROVADO, prova FRACA: em nenhum dos {len(sobrando)} teste(s) VERMELHOS o gate viu o corpo do\n"
               f"               teste rodar ({len(antes.passados)} passado(s) ao lado). E so isso que ele sabe:\n"
               f"{detalhe}"
               f"               {nodeid} - {causa}\n"
               "               O Manager decide se aceita.")


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
    # nao e so o fix. Medido: `origin/main` x o branch do PR #166 sao divergentes e
    # arquivos de PRODUCAO diferem, qualquer um deles podendo dar o vermelho. A
    # CONTAGEM saiu daqui: ela foi medida contra uma `origin/main` que ja andou
    # varias vezes desde entao, e nao se remede sem a rede (CLAUDE.md §2).
    # Falha fechada de proposito: `--is-ancestor` que erre por outro motivo (ref
    # inalcancavel) reprova, em vez de liberar.
    if subprocess.run(["git", "merge-base", "--is-ancestor", antes, depois],
                      capture_output=True).returncode != 0:
        sys.exit(f"[coluna-dupla] {antes[:8]} nao e ancestral de {depois[:8]}: ou a coluna 'antiga' JA CONTEM\n"
                 "                o fix, ou as colunas divergiram e a diferenca entre elas nao e so o fix.\n"
                 "                Para uma correcao ja mergeada, use --antes <base do PR> --depois <merge do PR>.")

    # ── Overlay: reproduz a arvore corrigida de tests/ sobre o codigo antigo ──
    # A coluna antiga recebe o TESTE novo por cima do CODIGO velho. Nenhum
    # arquivo de producao atravessa — o pathspec `tests/` garante isso.
    # `R` leva o destino e remove a origem; `D` remove o caminho apagado. Sem a
    # remocao, arquivo de apoio obsoleto permanecia na coluna antiga e podia ser a
    # unica causa do vermelho — APROVADO falso sem exercitar producao.
    # E copiamos tudo, nao so `.py`: se o teste novo le uma fixture de dados
    # adicionada junto, sem ela a coluna antiga quebra por arquivo faltando, sai
    # `FAILED`, e o gate carimbaria FORTE tendo medido a ausencia do insumo — nao
    # o comportamento antigo. Hoje `tests/` nao tem fixture de dados nenhuma — os
    # nao-`.py` rastreados sao os `.mjs` do frontend e o `.json` de baseline deles,
    # que o pytest ignora —, entao isto e guarda contra a convencao que ainda nao
    # existe, ao custo de copiar arquivo inerte. Sem contagem aqui: a que estava
    # escrita ja tinha envelhecido, e o comando responde, da raiz do repo:
    #     git ls-files tests/ | grep -v '\.py$'
    # `-z` porque o `core.quotePath` (padrao `true`) devolve nome nao-ASCII entre
    # aspas e com escape octal — medido: `"tests/test_transfer\303\252ncia.py"`.
    # Sozinho, esse nome nao passa no `endswith(".py")` e o gate abortava com
    # "nenhum teste .py mudou", mensagem FALSA; junto de outro `.py`, dava
    # `FileNotFoundError` no copyfile. O `-z` entrega o byte cru. O `git()` faz
    # `.strip()`, que NAO remove `\0` — dai o descarte do vazio final.
    itens = [p for p in git("-c", "diff.renames=true", "diff", "--name-status", "-z",
                            "--diff-filter=AMRD", antes, depois, "--", "tests/").split("\0") if p]
    mudados, removidos = [], []
    i = 0
    while i < len(itens):
        status = itens[i]
        i += 1
        if status.startswith("R"):
            origem, destino = itens[i], itens[i + 1]
            i += 2
            removidos.append(origem)
            mudados.append(destino)
        else:
            caminho = itens[i]
            i += 1
            (removidos if status == "D" else mudados).append(caminho)
    # Os alvos continuam presos ao `.py`: um `.test.mjs` sozinho nao da o que
    # provar aqui.
    py_mudados = [p for p in mudados if p.endswith(".py")]
    # O conftest carrega as chaves de PII e o skip de dependencia ausente; sem
    # a versao nova dele, a coluna antiga pode falhar por ambiente, nao por bug.
    if "tests/conftest.py" not in mudados and "tests/conftest.py" not in removidos:
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
    # Uma recusa, DOIS motivos que nao se parecem: quem digita `--testes tests/` — o
    # atalho humano, e o caso mais comum — nao tem o que fazer com dois paragrafos
    # sobre caminho absoluto. Cada um le o seu.
    for t in alvos:
        arq = t.split("::", 1)[0]
        fora = os.path.isabs(arq) or os.path.normpath(arq).startswith("..")
        if not arq.endswith(".py") or fora:
            motivo = ("Absoluto (ou com `..`) sai das DUAS colunas: o\n"
                      "                `cwd` do pytest e o worktree, entao o alvo e coletado do checkout\n"
                      "                ATUAL e uma revisao POSTERIOR do teste decide o veredito dos SHAs\n"
                      "                impressos — medido: par tautologico sai `APROVADO, prova FRACA`."
                      if fora else
                      "Um DIRETORIO (`tests/`, o que um `$(dirname \"$ARQ\")`\n"
                      "                produz) faz a coleta varrer a ARVORE INTEIRA, e ali o vermelho de\n"
                      "                qualquer teste alheio viraria 'prova' — medido: rc=0 e `APROVADO,\n"
                      "                prova FORTE` citando um `test_alheio`. Esta guarda so checa o NOME\n"
                      "                (ver os limites no topo do arquivo).")
            sys.exit(f"[coluna-dupla] --testes {t!r} nao aponta para um arquivo de teste .py DENTRO\n"
                     f"                do worktree. {motivo}\n"
                     "                Passe o caminho relativo (`tests/test_x.py`) ou o node id.")

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

        # Remover antes de copiar tambem cobre rename cujo destino reutiliza uma
        # arvore antiga. `os.remove` falha fechado em diretorio/tipo inesperado.
        for rel in removidos:
            os.remove(os.path.join(wt_antes, rel))
            # Diretorio que ficou vazio nao existe na arvore corrigida: um teste
            # que checa a ausencia dele falha na coluna antiga por ARTEFATO do
            # overlay e passa na corrigida — `APROVADO, prova FORTE` falsa. O
            # `rmdir` falha fechado em diretorio nao vazio (OSError -> break), e a
            # subida para quando o `dirname` do caminho RELATIVO esvazia, entao a
            # raiz do worktree nunca chega a ser argumento — por construcao.
            pai = os.path.dirname(rel)
            while pai:
                try:
                    os.rmdir(os.path.join(wt_antes, pai))
                except OSError:
                    break
                pai = os.path.dirname(pai)

        for rel in mudados:
            src, dst = os.path.join(wt_depois, rel), os.path.join(wt_antes, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            # `copyfile` NAO leva o modo, e o destino herda o do arquivo antigo:
            # um teste que ganhou bit de execucao (ou um conftest que perdeu o de
            # leitura) rodava na coluna antiga com a permissao errada. `copymode`
            # depois da copia resolve, e SEM `shutil.copy`: o ramo
            # `if os.path.isdir(dst)` dele copiaria PARA DENTRO do diretorio, em
            # silencio — overlay errado que pode virar FORTE falso. Hoje esse ramo
            # e INALCANCAVEL, e quem o impede e a poda acima (o diretorio so
            # sobreviveria vazio, e ela o apaga antes da copia): mexeu na poda,
            # esta rede cai junto.
            shutil.copyfile(src, dst)
            shutil.copymode(src, dst)

        print(f"[coluna-dupla] antes  = {antes}")
        print(f"[coluna-dupla] depois = {depois}")
        print(f"[coluna-dupla] testes = {' '.join(alvos)}")
        # `flush` pelo mesmo motivo do veredito la embaixo, so que ANTES: o proximo
        # a escrever nao e o `print` seguinte, e o stderr do pytest que a
        # `roda_pytest` repassa. LINE-buffered, nao "sem buffer" (medido no pai e no
        # filho, os dois com os canais em pipe: `sys.stderr.line_buffering` True,
        # `write_through` False). A ordem daqui DEPENDE disso: o repasse so sai na
        # quebra de linha, entao um chunk sem nenhum `\n` ficaria no buffer e sairia
        # depois, colado no meio de outra linha. Medido nos dois repasses que este
        # gate produz hoje, e os dois terminam em `\n`: o INTERNALERROR de
        # `pytest_configure` e o `No module named pytest` do interpretador sem pytest.
        # Medido com os canais JUNTOS e rc=3 vindo de `pytest_configure` (stdout
        # VAZIO): sem isto as 4 linhas que dizem O QUE foi provado saem enterradas
        # sob o INTERNALERROR. O TAMANHO do stderr nao fica escrito: ele leva o
        # caminho absoluto do lab no traceback e muda com o `tmp_path` de quem roda
        # (CLAUDE.md §2). Quem prende a ordem e o
        # `test_cabecalho_sai_antes_do_stderr_do_pytest_com_os_canais_juntos`.
        print(f"[coluna-dupla] python = {py}\n", flush=True)

        # Os XML vivem no `mkdtemp`, fora das duas arvores — nenhum worktree fica
        # sujo — e morrem com ele no `rmtree` do `finally`.
        rc_antes, desf_antes, out_antes = roda_pytest(wt_antes, py, alvos, os.path.join(tmp, "antes.xml"))
        rc_depois, desf_depois, out_depois = roda_pytest(wt_depois, py, alvos, os.path.join(tmp, "depois.xml"))

        # Por palavra-chave: os dois `int` e os dois `Desfechos` se alternam na
        # assinatura e nenhum teste passa por este call site, entao trocar a ordem
        # seria indetectavel.
        rc, relatorio = veredito(rc_antes=rc_antes, antes=desf_antes,
                                 rc_depois=rc_depois, depois=desf_depois)
        # `flush` porque o proximo canal a escrever e o STDERR: com stdout em pipe
        # (`> log 2>&1`, `| tee`, CI) o Python o bufferiza por BLOCO e so o
        # descarregaria no `sys.exit`, enquanto o stderr e LINE-buffered e sai na
        # quebra de linha (ver a medicao no `flush` do cabecalho). Medido nos canais
        # JUNTOS, com um teste que imprime muito e falha nas duas colunas: o veredito
        # saia DEPOIS do despejo inteiro, que e exatamente o que separar os canais
        # existe para evitar. A POSICAO da linha nao fica escrita: ela e o tamanho do
        # despejo, que e do lab (CLAUDE.md §2) — quem prende a ordem e o
        # `test_veredito_sai_antes_do_despejo_com_os_canais_juntos`. Cobre so o que
        # ainda esta no buffer AQUI: o cabecalho ja escreveu antes das duas corridas
        # e tem `flush` proprio, senao sairia atras do stderr repassado por elas.
        print(f"[coluna-dupla] {relatorio}", flush=True)
        # O stdout so sai quando o gate REPROVA — e o traceback das assercoes vive
        # nele. Sem isto, "o grupo falha tambem no codigo corrigido (rc=1)" era todo o
        # diagnostico. No caminho APROVADO nao sai nada: zero byte a mais, e o
        # relatorio continua sendo a unica coisa no stdout. Isto NAO reabre a classe
        # dos tres furos de texto: e impresso DEPOIS da decisao, e a `veredito`
        # continua funcao pura do rc + XML.
        #
        # Duas restricoes, cada uma contra um excesso medido. POR COLUNA, so a que
        # SAIU != 0: o dump saia tambem na coluna que nao tem traceback nenhum para
        # mostrar — no veredito tautologico as duas colunas estao em rc=0 e as duas
        # eram despejadas. NAO da para restringir mais que isso. A versao anterior
        # desta linha listava `(1, 2)` alegando que "os rc 3/4/5 ja tem o stderr
        # repassado pela `roda_pytest`", e isso e meia-verdade: o INTERNALERROR so
        # vai para o STDERR quando estoura antes de o reporter existir. Medido, mesmo
        # rc=3 nos dois, e a troca de canal e exata: de `pytest_configure` o
        # INTERNALERROR sai no stderr e o stdout fica VAZIO; de
        # `pytest_collection_modifyitems` quem escreve e o
        # `TerminalReporter.pytest_internalerror` — INTERNALERROR no stdout, stderr
        # VAZIO —, ou seja, o operador recebia a linha do veredito e mais nada. QUAL
        # canal fica vazio nao muda de maquina; o TAMANHO do outro muda com o
        # `tmp_path`, e por isso nao esta escrito aqui (CLAUDE.md §2).
        #
        # E so a CAUDA: um teste que imprime muito empurra o traceback para longe, e
        # a captura do pytest sai DEPOIS dele — num teste com 20 mil `print` falhando
        # nas duas colunas, o stderr do gate ia a MEGABYTES (o tamanho e do lab e nao
        # fica escrito aqui, CLAUDE.md §2). O `-q`
        # fecha cada coluna com o resumo que NOMEIA o que falhou, entao a cauda e o
        # pedaco que sempre diagnostica.
        if rc:
            # 200 = algumas dezenas de blocos de falha. Quantas linhas o `-q` gasta
            # por falha muda com a versao do pytest: medido em 2026-09-03, pytest
            # 9.0.2, 12/20/36 linhas para 1/2/4 falhas — a versao anterior desta
            # linha dizia 13/22/40, que nao reproduz mais. Remedir antes de reusar,
            # de um diretorio vazio fora do repo:
            #     printf 'def test_a():\n    assert 0\n' > test_1.py
            #     <raiz>/.venv/bin/python -m pytest -q -p no:cacheprovider test_1.py | wc -l
            cauda = 200
            for rotulo, rc_col, saida in (("antiga", rc_antes, out_antes),
                                          ("corrigida", rc_depois, out_depois)):
                if not saida or not rc_col:
                    continue
                linhas = saida.splitlines(keepends=True)
                omitidas = len(linhas) - cauda
                aviso = f" ({omitidas} linha(s) iniciais omitidas)" if omitidas > 0 else ""
                sys.stderr.write(f"[coluna-dupla] stdout da coluna {rotulo}{aviso}:\n"
                                 + "".join(linhas[-cauda:]))
        return rc
    finally:
        for wt in (wt_antes, wt_depois):
            subprocess.run(["git", "worktree", "remove", "--force", wt], capture_output=True)
        # rmtree ANTES do prune: o prune so descarta registro cujo diretorio ja
        # sumiu. Na ordem inversa, um `remove` que falhasse deixaria registro
        # orfao para sempre — e este repositorio ja acumulou orfaos assim. Sem
        # contagem: ela muda a cada limpeza, e o comando lista os pendentes de
        # agora (saida vazia = nenhum):
        #     git worktree prune -n --verbose
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
