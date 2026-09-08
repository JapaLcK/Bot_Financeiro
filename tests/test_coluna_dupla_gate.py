"""O criterio de prova do gate de coluna dupla, lido no XML de cada coluna.

Vale um teste porque os dois modos de falha sao SILENCIOSOS e os dois ja
aconteceram: um grupo em que NENHUM corpo de teste chegou a rodar (21 erros de
fixture, zero asserções) saia `APROVADO, prova FORTE`; e uma coluna corrigida com
TUDO pulado sai rc=0, que o gate lia como verde. O exit code nao separa nenhum
dos dois — erro de fixture e asserção que falha saem os DOIS com rc=1, e "tudo
pulado" sai igual a "tudo passou" —, entao a regra le o XML do `--junitxml`.

Testa a `veredito`, que e onde mora TODA a decisao do gate: o `main()` so imprime
o que ela devolve. Sem isso o teste passaria por fora da linha que decide.

O pytest roda DE VERDADE, num diretorio fora do repo (sem conftest, sem banco),
em vez de inventar strings: o que sustenta a regra e o formato real da saida, e
uma string inventada so provaria que `startswith` funciona. O script nao e
importavel como modulo do pacote, entao vem por caminho.

A segunda metade do arquivo sobe um repo-laboratorio de dois commits e roda o
gate INTEIRO por cima dele: e o unico jeito de cobrir o `main()` — a montagem do
overlay e a escolha do interpretador nao passam pela `veredito`.
"""
import importlib.util
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

import pytest

_CAMINHO = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "coluna_dupla.py"
_spec = importlib.util.spec_from_file_location("coluna_dupla", _CAMINHO)
coluna_dupla = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coluna_dupla)

# O `print` na fixture e a PRIMEIRA porta da isca: ele cai em `Captured stdout
# setup`, na coluna 0, com o formato exato de uma linha de resumo. Enquanto o
# desfecho vinha do texto, o gate lia esse print como "um teste rodou e falhou" —
# no caso EXATO em que zero corpos de teste executaram, que e o que a regra existe
# para pegar.
_FIXTURE_QUE_ESTOURA_E_ENGANA = '''
import pytest

@pytest.fixture
def db():
    print("FAILED tests/test_engana.py::test_nunca_chega_a_rodar - AttributeError: db")
    raise AttributeError("<module 'db'> does not have the attribute 'nao_existe'")

def test_nunca_chega_a_rodar(db):
    assert db
'''

_ASSERCAO_QUE_FALHA = '''
def test_roda_e_falha():
    assert 1 == 2
'''

_FALHA_DEPOIS_INTERROMPE = '''
def test_1_falha_antes_da_interrupcao():
    assert 1 == 2

def test_2_interrompe_a_coluna():
    raise KeyboardInterrupt()
'''

# O mesmo ataque pela SEGUNDA porta: um `atexit` escreve o cabecalho falso depois
# de a captura do pytest ja ter sido desmontada, entao ele sai por baixo de
# qualquer fatiamento do texto. Houve uma TERCEIRA porta (o mesmo `atexit`, no
# stdout), e e por isso que hoje nenhum dos dois streams decide: o desfecho vem do
# XML, que o pytest fecha antes de o teste voltar a escrever.
_ATEXIT_QUE_ENGANA_PELO_STDERR = '''
import atexit, sys, pytest

atexit.register(lambda: sys.stderr.write(
    "=========================== short test summary info ============================\\n"
    "FAILED tests/inventado.py::test_que_nunca_existiu - inventado\\n"))

@pytest.fixture
def db():
    raise AttributeError("<module 'db'> does not have the attribute 'nao_existe'")

def test_nunca_chega_a_rodar(db):
    assert db
'''


# As QUATRO formas de node id no mesmo arquivo: modulo, classe, classe ANINHADA e
# parametrizacao. Todas vermelhas — citacao so existe para vermelho.
_QUATRO_FORMAS_DE_NODEID = '''
import pytest


def test_modulo():
    assert 1 == 2


class TestClasse:
    def test_metodo(self):
        assert 1 == 2

    class TestAninhada:
        def test_fundo(self):
            assert 1 == 2


@pytest.mark.parametrize("v", [1])
def test_param(v):
    assert v == 2
'''


def _pytest_em(monkeypatch, tmp_path, nome, fonte):
    """Roda o pytest de verdade em `tmp_path`, PELA `roda_pytest`: e ela que monta a
    linha de comando do XML, e uma copia dela aqui seria uma segunda fonte de
    verdade. `PYTEST_ADDOPTS` e zerado — herdado do ambiente, mudaria a corrida e o
    teste mediria outra coisa. O XML fica em `tmp_path/relatorio.xml`."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "")
    (tmp_path / nome).parent.mkdir(parents=True, exist_ok=True)  # `nome` pode ter diretorio
    (tmp_path / nome).write_text(fonte)
    rc, desf, _ = coluna_dupla.roda_pytest(str(tmp_path), sys.executable, [nome],
                                           str(tmp_path / "relatorio.xml"))
    return rc, desf


def _reroda(cwd, citacao, xml):
    """Reroda o pytest com a CITACAO do relatorio, exatamente como ela sai. O que
    prova "rerodavel" e o rc — assertar a string so provaria que `join` funciona —,
    e o `shlex.split` faz o papel do shell para onde a citacao e colada.

    O XML sai FORA do `cwd`: quando ele e um repo-laboratorio, escrever la dentro
    deixa `?? reroda.xml` e um `_gate(lab)` posterior aborta na Guarda 1 ("arvore
    suja"). Hoje quem salva e a ordem das chamadas, e ordem nao e garantia."""
    return coluna_dupla.roda_pytest(cwd, sys.executable, shlex.split(citacao), str(xml))[0]


def _verde(antes=None):
    """A coluna CORRIGIDA das chamadas unitarias: um teste que passou, mais tudo o
    que ficou VERMELHO no antigo — falha, ambiguo E erro — agora passando. O
    criterio de verde e PAREADO e varre os TRES, entao sem isto o caso sairia
    REPROVADO por um motivo que ele nao quer medir."""
    vermelhos = {**antes.falhados, **antes.ambiguos, **antes.errados} if antes else {}
    passados = {("lab", "test_verde"): "lab.py::test_verde",
                **{chave: nodeid for chave, (nodeid, _) in vermelhos.items()}}
    return coluna_dupla.Desfechos(len(passados), 0, {}, {}, {}, passados)


def test_erro_de_fixture_e_prova_FRACA_mesmo_imprimindo_FAILED(monkeypatch, tmp_path):
    """rc=1 igual ao da asserção, e a isca `FAILED ...` impressa pela fixture. Ela
    chega ate DENTRO do XML (no corpo do `<error>`, que traz a fonte da fixture), e
    ainda assim nao vira desfecho: quem conta e a TAG, nao o texto."""
    rc, desfechos = _pytest_em(monkeypatch, tmp_path, "test_engana.py",
                               _FIXTURE_QUE_ESTOURA_E_ENGANA)
    assert rc == coluna_dupla.PYTEST_FALHA  # o exit code NAO delata que nada rodou
    xml = (tmp_path / "relatorio.xml").read_text()
    assert "FAILED tests/test_engana.py" in xml  # a isca esta mesmo dentro do XML
    assert (len(desfechos.falhados), len(desfechos.errados)) == (0, 1)  # nao virou `<failure>`
    codigo, relatorio = coluna_dupla.veredito(rc, desfechos, 0, _verde(desfechos))
    assert codigo == 0
    assert "prova FRACA" in relatorio
    assert "- AttributeError: db" not in relatorio  # a citacao e a causa real, nao a isca


def test_assercao_que_falha_e_prova_FORTE(monkeypatch, tmp_path):
    """Controle positivo: sem ele, um criterio que nunca contasse `<failure>`
    passaria no teste acima e o gate classificaria TODA prova como fraca — pior
    que o bug."""
    rc, desfechos = _pytest_em(monkeypatch, tmp_path, "test_assercao_falha.py",
                               _ASSERCAO_QUE_FALHA)
    assert rc == coluna_dupla.PYTEST_FALHA  # o mesmo rc do caso acima; o XML e que difere
    codigo, relatorio = coluna_dupla.veredito(rc, desfechos, 0, _verde(desfechos))
    assert codigo == 0
    assert "prova FORTE" in relatorio
    assert "test_assercao_falha.py::test_roda_e_falha" in relatorio  # nodeid colavel


def test_nodeid_citado_reconstroi_a_classe(monkeypatch, tmp_path):
    """A citacao tem de ser RERODAVEL, e o node id executavel NAO esta no XML: o
    `classname` do xunit1 e o modulo pontilhado MAIS a cadeia de classes. Montando
    so `file::name`, metodo de classe saia `test_ids.py::test_metodo` — node id que
    nao existe, e o pytest recusa a linha inteira com rc=4.

    Negativo: com a construcao anterior, `test_metodo` e `test_fundo` saem sem a
    classe e o `_reroda` devolve 4. Positivo: `test_modulo` e `test_param` provam
    que a reconstrucao nao estraga o caminho da MAIORIA — os node ids fora de
    classe. Quantos sao, remedir antes de reusar (o `-v` e o que faz a conta bater
    com a prosa: ele e o ESPELHO do irmao em `scripts/coluna_dupla.py`, que conta os
    ids EM classe. Sem ele o comando devolve o total, que INCLUI justamente o
    conjunto que este positivo existe para excluir. E sempre pelo `-m pytest`: o
    console script `pytest` desta maquina tem shebang morto e sai VAZIO, que o
    `grep -c` entrega como 0):
        .venv/bin/python -m pytest tests/ -q --collect-only | grep '::' | grep -vc '::.*::' """
    rc, desfechos = _pytest_em(monkeypatch, tmp_path, "test_ids.py", _QUATRO_FORMAS_DE_NODEID)
    assert rc == coluna_dupla.PYTEST_FALHA
    citados = sorted(nodeid for nodeid, _ in desfechos.falhados.values())
    assert citados == ["'test_ids.py::test_param[1]'",
                       "test_ids.py::TestClasse::TestAninhada::test_fundo",
                       "test_ids.py::TestClasse::test_metodo",
                       "test_ids.py::test_modulo"]
    # O que fecha o caso: colada de volta, a citacao COLETA os quatro e da rc=1.
    assert _reroda(str(tmp_path), " ".join(citados),
                   tmp_path / "reroda.xml") == coluna_dupla.PYTEST_FALHA


def test_nodeid_em_subdiretorio_que_repete_o_nome_do_arquivo(monkeypatch, tmp_path):
    """O caso que a reconstrucao POR PREFIXO existe para fechar, e que nenhum outro
    teste deste arquivo alcanca: os alvos daqui sao todos arquivos PLANOS em
    `tests/`, e ali qualquer heuristica de "ache o nome do arquivo dentro do
    classname" acerta por sorte. Em `test_x/test_x.py` o classname e
    `test_x.test_x` e o nome aparece DUAS vezes.

    Negativo: com a heuristica anterior (`basename` + `partes.index`) o marco casa
    na PRIMEIRA ocorrencia, que e o DIRETORIO, e sobra `test_x` como se fosse
    classe — ate `test_modulo`, funcao solta, sai
    `test_x/test_x.py::test_x::test_modulo`, node id que nao existe. Medido nesta
    mesma chamada: `_reroda` da 4 (alvo inexistente) com a heuristica e 1 com o
    prefixo. Positivo: as duas formas no mesmo arquivo — funcao de MODULO e metodo
    de CLASSE —, entao um criterio que consertasse a classe quebrando a funcao (ou
    o contrario) nao passa aqui."""
    rc, desfechos = _pytest_em(monkeypatch, tmp_path, "test_x/test_x.py",
                               _QUATRO_FORMAS_DE_NODEID)
    assert rc == coluna_dupla.PYTEST_FALHA
    citados = sorted(nodeid for nodeid, _ in desfechos.falhados.values())
    assert citados == ["'test_x/test_x.py::test_param[1]'",
                       "test_x/test_x.py::TestClasse::TestAninhada::test_fundo",
                       "test_x/test_x.py::TestClasse::test_metodo",
                       "test_x/test_x.py::test_modulo"]
    assert _reroda(str(tmp_path), " ".join(citados),
                   tmp_path / "reroda.xml") == coluna_dupla.PYTEST_FALHA


def test_coluna_antiga_interrompida_reprova_mesmo_com_falha_no_xml(monkeypatch, tmp_path):
    """O pytest usa rc=2 tanto para erro de coleta quanto para interrupcao. Se um
    teste falha antes de outro levantar KeyboardInterrupt, o JUnit preserva a
    falha anterior e o gate antigo a carimbava como prova FORTE apesar de a
    coluna nao ter terminado. O XML parcial nao transforma interrupcao em prova."""
    rc, desfechos = _pytest_em(monkeypatch, tmp_path, "test_interrompe.py",
                               _FALHA_DEPOIS_INTERROMPE)
    assert rc == 2
    assert len(desfechos.falhados) == 1  # existe vermelho suficiente para enganar o gate antigo
    codigo, relatorio = coluna_dupla.veredito(rc, desfechos, 0, _verde(desfechos))
    assert codigo == 1
    assert "interrompida" in relatorio
    assert "prova FORTE" not in relatorio


def test_texto_dos_streams_nao_alimenta_o_veredito(monkeypatch, tmp_path):
    """Exercita a `roda_pytest`, nao so a `veredito`: o furo estava na linha que
    entregava TEXTO ao veredito, e um teste que monta a string a mao nunca o veria.

    O payload escreve pelo `atexit` um cabecalho de resumo FALSO com uma linha
    `FAILED` de um teste inventado. Enquanto o desfecho vinha do texto, isso
    aprovava FORTE citando `test_que_nunca_existiu`; com o XML, o que os streams
    dizem nao entra em lugar nenhum."""
    rc, desfechos = _pytest_em(monkeypatch, tmp_path, "test_hdr.py",
                               _ATEXIT_QUE_ENGANA_PELO_STDERR)
    assert rc == coluna_dupla.PYTEST_FALHA
    assert "test_que_nunca_existiu" not in str(desfechos.errados)
    assert (len(desfechos.falhados), len(desfechos.errados)) == (0, 1)  # o `FAILED` nao contou
    codigo, relatorio = coluna_dupla.veredito(rc, desfechos, 0, _verde(desfechos))
    assert codigo == 0
    assert "prova FRACA" in relatorio
    assert "test_que_nunca_existiu" not in relatorio


def test_xml_ausente_reprova(tmp_path):
    """Sem XML nao ha desfecho, e ler zeros de um arquivo que nao existe seria
    aprovar no escuro. Carga real: interpretador sem pytest sai rc=1 — vermelho
    VALIDO, que passa pela faixa de codigos — e nao escreve arquivo nenhum."""
    assert coluna_dupla.le_junit(str(tmp_path / "nao_existe.xml")) is None
    truncado = tmp_path / "truncado.xml"
    truncado.write_text('<testsuites><testsuite tests="1"')
    assert coluna_dupla.le_junit(str(truncado)) is None
    codigo, relatorio = coluna_dupla.veredito(coluna_dupla.PYTEST_FALHA, None, 0, _verde())
    assert codigo == 1
    assert "nao escreveu o relatorio XML" in relatorio


# ── O gate INTEIRO, num repo-laboratorio ────────────────────────────────────
# Os testes acima cobrem so a `veredito` e a `roda_pytest`. Os quatro grupos
# abaixo cobrem o que elas nao veem, tudo dentro do `main()`: quais arquivos
# atravessam o overlay, com que MODO, e qual interpretador roda as duas colunas.
# Cada caso ponta a ponta paga 2 worktrees + 2 pytest, e o que aborta cedo paga so
# a parte do git. O TEMPO nao fica escrito aqui — ele e da maquina (CLAUDE.md §2) —
# e sai do proprio pytest, da raiz de um checkout que tenha o `.venv` (um worktree
# de `.claude/worktrees/` nao tem; use o `.venv` da raiz principal):
#     .venv/bin/python -m pytest tests/test_coluna_dupla_gate.py --durations=0
# O laboratorio e descartavel e nao encosta no
# repositorio real: o `_lab` fica no `tmp_path`, e os worktrees que o gate cria
# nascem e morrem num `mkdtemp(prefix="coluna-dupla-")` proprio, sob o `$TMPDIR`.
_LIB_ANTIGA = "def valor():\n    return 0\n"
_LIB_CORRIGIDA = "def valor():\n    return 42\n"
_TESTE_DO_FIX = "import lib\n\n\ndef test_valor():\n    assert lib.valor() == 42\n"

# Le uma fixture de DADOS copiada junto (sem bit de execucao). O vermelho tem de
# ser a asserção sobre o valor antigo — `assert 0 == 42` —, nao um erro de I/O.
_TESTE_QUE_LE_A_FIXTURE = '''
import pathlib

import lib


def test_valor():
    esperado = int((pathlib.Path(__file__).parent / "dados.txt").read_text())
    assert lib.valor() == esperado
'''

# Fixture EXECUTAVEL: o conteudo e o mesmo nos dois commits, so o modo muda
# (0644 -> 0755). Ela le `valor.txt`, que fica FORA de `tests/` e por isso nao
# atravessa o overlay — e o que da o vermelho legitimo na coluna antiga.
_FERRAMENTA = '#!/bin/sh\ncat "$(dirname "$0")/../valor.txt"\n'
_TESTE_QUE_EXECUTA = '''
import pathlib
import subprocess


def test_ferramenta():
    saida = subprocess.run([str(pathlib.Path(__file__).parent / "ferramenta.sh")],
                           capture_output=True, text=True).stdout
    assert int(saida) == 42
'''


# A forma exata do `tests/conftest.py:99`: um `pytest_collection_modifyitems` que
# PULA por condicao de ambiente. No laboratorio a unica coisa que difere entre as
# colunas e o `lib.py`, entao o valor NOVO faz o papel da dependencia ausente — a
# coluna corrigida pula tudo e sai rc=0, que era lido como verde.
_CONFTEST_QUE_PULA_NO_CORRIGIDO = '''
import pytest

import lib


def pytest_collection_modifyitems(items):
    if lib.valor() != 42:
        return
    for item in items:
        item.add_marker(pytest.mark.skip(reason="dependencia ausente"))
'''

# O mesmo, pulando SO o teste do fix — o irmao continua passando no corrigido.
_CONFTEST_QUE_PULA_SO_O_FIX = '''
import pytest

import lib


def pytest_collection_modifyitems(items):
    if lib.valor() != 42:
        return
    for item in items:
        if item.name == "test_valor":
            item.add_marker(pytest.mark.skip(reason="dependencia ausente"))
'''

_TESTE_DO_FIX_COM_IRMAO = _TESTE_DO_FIX + "\n\ndef test_irmao():\n    assert True\n"

# O teste do fix ERRA na fixture no antigo (a fixture depende do codigo corrigido)
# e o irmao pre-existente FALHA. Os dois desfechos vermelhos na mesma coluna, que e
# o que separa a varredura de orfaos por falha da varredura por vermelho.
_TESTE_QUE_ERRA_COM_IRMAO_VERMELHO = '''
import pytest

import lib


@pytest.fixture
def dado():
    if lib.valor() != 42:
        raise RuntimeError("o fix nao esta no lugar")
    return lib.valor()


def test_novo(dado):
    assert dado == 42


def test_ja_vermelho():
    assert lib.valor() == 42
'''

# O mesmo `pytest_collection_modifyitems` do `_CONFTEST_QUE_PULA_SO_O_FIX`, mirando
# o `test_novo`: no corrigido ele e PULADO, e o irmao continua passando.
_CONFTEST_QUE_PULA_SO_O_NOVO = _CONFTEST_QUE_PULA_SO_O_FIX.replace('"test_valor"', '"test_novo"')

# Teste novo que importa um modulo de PRODUCAO existente so no corrigido: no antigo
# nao ha teste nenhum para parear — o pytest emite UM `<testcase>` com
# `classname=""` e `name` = o modulo pontilhado. E o exemplo do `Uso` do script.
_TESTE_QUE_IMPORTA_MODULO_NOVO = "import lib_novo\n\n\ndef test_a():\n    assert lib_novo.valor() == 42\n"

# `skipif` de plataforma: vale IGUAL nas duas colunas, entao o caso pulado no
# verde tambem foi pulado no vermelho e nunca entra no conjunto exigido.
_TESTE_DO_FIX_COM_SKIPIF = '''
import sys

import pytest

import lib


@pytest.mark.skipif(sys.platform != "plan9", reason="so no Plan 9")
def test_so_em_outra_plataforma():
    assert False


def test_valor():
    assert lib.valor() == 42
'''

# `lib.reset()` so existe no CORRIGIDO: no antigo o hook abaixo estoura com
# `AttributeError` na fase call, ANTES do corpo do teste.
_LIB_CORRIGIDA_COM_RESET = _LIB_CORRIGIDA + "\n\ndef reset():\n    return None\n"

# Hook com ZERO condicoes, que so chama producao — o acidente, nao o ataque.
_HOOK_QUE_SO_CHAMA_PRODUCAO = "import lib\n\n\ndef pytest_runtest_call(item):\n    lib.reset()\n"

# Producao que ESTOURA no antigo: o corpo do teste RODA e o traceback termina em
# `lib.py`, nao no arquivo do teste.
_LIB_ANTIGA_QUE_ESTOURA = 'def valor():\n    raise ValueError("nao suportado")\n'

# Fixture `yield` que estoura no TEARDOWN: o corpo do teste roda e PASSA, e so
# depois a fixture quebra. Medido (pytest 9.0.2, `junit_family=xunit1`): sai UM
# `<testcase>` com SO `<error>`, `message='failed on teardown with "..."'` — ou
# seja, `<error>` NAO implica "o corpo nao rodou".
_FIXTURE_QUE_ESTOURA_NO_TEARDOWN = '''
import pytest

import lib


@pytest.fixture
def recurso():
    yield 1
    if lib.valor() != 42:
        raise RuntimeError("estourou no TEARDOWN, com o corpo ja executado")


def test_valor(recurso):
    assert recurso == 1
'''


# Parametrizacao com virgula E espaco no id: a forma que morre num shell sem
# aspas. Quantos node ids desta suite precisam delas, remedir antes de reusar
# (sempre pelo `-m pytest`: o console script `pytest` desta maquina tem shebang
# morto e sai VAZIO; e `python` nu nao existe no PATH, so `python3`):
#     .venv/bin/python -m pytest tests/ -q --collect-only | grep '::' \
#         | .venv/bin/python -c "import shlex,sys; \
#           print(sum(shlex.quote(l.strip())!=l.strip() for l in sys.stdin))"
_TESTE_PARAMETRIZADO = '''
import pytest

import lib


@pytest.mark.parametrize("v", ["2,5 mil"])
def test_valor(v):
    assert lib.valor() == 42
'''

# Falha nas DUAS colunas (`0 != 99` e `42 != 99`): o unico caminho em que o gate
# REPROVA tendo rodado os dois pytest, e onde o traceback e o diagnostico.
_TESTE_QUE_FALHA_NAS_DUAS = "import lib\n\n\ndef test_valor():\n    assert lib.valor() == 99\n"

# 500 `print` ANTES do assert. No `-q` a captura do stdout sai DEPOIS do
# traceback, entao sao eles que empurram o resumo — a parte que NOMEIA o que
# falhou — para o fim da saida, e e por isso que o guardado e a CAUDA.
_TESTE_QUE_IMPRIME_MUITO_E_FALHA_NAS_DUAS = (
    "import lib\n\n\ndef test_valor():\n"
    "    for i in range(500):\n"
    "        print(f'LINHA_{i:04d}')\n"
    "    assert lib.valor() == 99\n")

# O MESMO acidente do `_HOOK_QUE_SO_CHAMA_PRODUCAO`, uma fase antes: hook de
# COLETA, com zero condicoes, chamando producao que so existe no corrigido. A
# coluna antiga sai rc=3 (INTERNALERROR), e o diagnostico dele vai para o STDOUT.
_HOOK_DE_COLETA_QUE_SO_CHAMA_PRODUCAO = (
    "import lib\n\n\ndef pytest_collection_modifyitems(config, items):\n    lib.reset()\n")

# O mesmo acidente uma fase ANTES: `pytest_configure` roda enquanto o
# `TerminalReporter` ainda nao existe, entao o INTERNALERROR da coluna antiga vai
# para o STDERR e o stdout fica VAZIO — e o stderr e o unico canal que a
# `roda_pytest` repassa, LINE-buffered (`line_buffering` True, `write_through`
# False) e portanto na primeira quebra de linha, ANTES de o cabecalho ter sido
# descarregado. O TAMANHO do stderr nao fica escrito: ele leva o caminho absoluto
# do lab no traceback e muda com o `tmp_path` (CLAUDE.md §2).
_HOOK_DE_CONFIGURE_QUE_SO_CHAMA_PRODUCAO = (
    "import lib\n\n\ndef pytest_configure(config):\n    lib.reset()\n")

# Oito tautologicos, para o `git` ver SIMILARIDADE suficiente e chamar o arquivo
# novo de COPIA do antigo (medido: `C083`).
_OITO_TAUTOLOGICOS = "".join(f"def test_taut_{i}():\n    assert True\n\n\n" for i in range(8))
_COPIA_COM_FIX = _OITO_TAUTOLOGICOS + "def test_fix():\n    import lib\n    assert lib.valor() == 42\n"


def _escreve(raiz, arquivos):
    for rel, conteudo in arquivos.items():
        alvo = raiz / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo)


def _git(lab, *args):
    return subprocess.run(["git", *args], cwd=lab, check=True, capture_output=True, text=True)


def _lab(tmp_path, arquivos=None):
    """Repo de UM commit: `lib.valor() == 0` (o codigo ANTIGO) e conftest vazio."""
    lab = tmp_path / "lab"
    lab.mkdir()
    _escreve(lab, {"tests/conftest.py": "", "lib.py": _LIB_ANTIGA, **(arquivos or {})})
    _git(lab, "init", "-q", ".")
    _git(lab, "config", "user.email", "gate@lab")
    _git(lab, "config", "user.name", "lab")
    _git(lab, "add", "-A")
    _git(lab, "commit", "-qm", "antigo")
    return lab


def _commita(lab, arquivos):
    _escreve(lab, arquivos)
    _git(lab, "add", "-A")  # `-A` registra tambem o que foi apagado e a troca de modo
    _git(lab, "commit", "-qm", "corrigido")


def _gate(lab, *args, addopts="", juntos=False):
    """O gate de verdade, sobre o laboratorio. `PYTEST_ADDOPTS` e fixado pelo mesmo
    motivo do `_pytest_em`: herdado, mudaria a corrida das DUAS colunas. O
    `COLUMNS` que estava aqui saiu junto com a leitura do resumo — era o pytest
    que CORTAVA a linha do resumo na largura do terminal; o XML nao corta nada.

    `addopts` existe para o teste que prova que o gate IGNORA a variavel: zerar
    aqui, sempre, esconderia justamente esse caso de todos os outros testes.

    `juntos` funde os canais (`2>&1`) — a configuracao do `> log`, do `| tee` e do
    CI. Todo o resto le os dois como pipes SEPARADOS, e ali a ordem entre eles nao
    existe para ser medida; e por isso que `juntos` e opcional em vez de padrao."""
    return subprocess.run([sys.executable, str(_CAMINHO), "--antes", "HEAD~1", "--depois", "HEAD", *args],
                          cwd=lab, env={**os.environ, "PYTEST_ADDOPTS": addopts},
                          stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT if juntos else subprocess.PIPE,
                          text=True)


def test_nome_acentuado_atravessa_o_overlay(tmp_path):
    """O `core.quotePath` (padrao `true`) devolve `"tests/test_transfer\\303\\252ncia.py"`
    — entre aspas e com escape octal. Sem o `-z`, esse nome nao passa no
    `endswith('.py')`, some do overlay e o gate aborta com "nenhum teste .py
    mudou": mensagem FALSA. Injetado onde DISCRIMINA — um nome ASCII da o mesmo
    resultado com e sem o `-z`."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_transferência.py": _TESTE_DO_FIX})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FORTE" in r.stdout


def test_caso_comum_com_fixture_de_dados_aprova_forte(tmp_path):
    """Controle positivo dos quatro grupos: nome ASCII e fixture de dados comum,
    sem bit de execucao. Sem ele, uma guarda que abortasse SEMPRE, um `-z` que
    quebrasse o caminho comum ou um `copymode` que estragasse a copia passariam
    verdes — e recusar tudo e pior que o bug."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/dados.txt": "42\n",
                   "tests/test_com_fixture.py": _TESTE_QUE_LE_A_FIXTURE})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FORTE" in r.stdout
    assert "assert 0 == 42" in r.stdout  # a fixture atravessou legivel
    assert "assert 0 == 42" not in r.stderr  # ...e o stdout das colunas NAO saiu junto


def test_fixture_apagada_some_da_coluna_antiga(tmp_path):
    """O overlay antigo so copiava A/M/R. Uma fixture apagada permanecia na
    coluna vermelha; o teste novo falhava por ela existir e o gate aprovava FORTE
    sem chegar ao comportamento de producao. A causa agora e o valor antigo."""
    teste = '''
import pathlib
import lib

def test_valor():
    assert not (pathlib.Path(__file__).parent / "obsoleto.txt").exists()
    assert lib.valor() == 42
'''
    lab = _lab(tmp_path, {"tests/obsoleto.txt": "velho\n"})
    (lab / "tests" / "obsoleto.txt").unlink()
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_sem_obsoleto.py": teste})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "assert 0 == 42" in r.stdout
    assert "assert not True" not in r.stdout


def test_origem_de_fixture_renomeada_some_da_coluna_antiga(tmp_path):
    """Rename precisa de duas operacoes: copiar o destino e remover a origem.
    Deixar a origem mede a arvore errada do mesmo modo que um `D` omitido."""
    teste = '''
import pathlib
import lib

def test_valor():
    pasta = pathlib.Path(__file__).parent
    assert not (pasta / "velho.txt").exists()
    assert (pasta / "novo.txt").read_text() == "dado\\n"
    assert lib.valor() == 42
'''
    lab = _lab(tmp_path, {"tests/velho.txt": "dado\n"})
    (lab / "tests" / "velho.txt").rename(lab / "tests" / "novo.txt")
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_rename.py": teste})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "assert 0 == 42" in r.stdout
    assert "assert not True" not in r.stdout


def test_diretorio_esvaziado_some_da_coluna_antiga(tmp_path):
    """Controle NEGATIVO do grupo. Apagar o ultimo arquivo rastreado de um
    diretorio deixava a PASTA vazia na coluna antiga — pasta que nao existe na
    arvore corrigida. O teste falhava na coluna vermelha por artefato do overlay
    e passava na verde: `APROVADO, prova FORTE` sem tocar em producao. Aninhado
    de proposito: poda de UM nivel so deixaria `tests/obsoleto` de pe."""
    teste = '''
import pathlib
import lib

def test_valor():
    assert not (pathlib.Path(__file__).parent / "obsoleto").exists()
    assert lib.valor() == 42
'''
    lab = _lab(tmp_path, {"tests/obsoleto/sub/dado.txt": "velho\n"})
    (lab / "tests" / "obsoleto" / "sub" / "dado.txt").unlink()
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_sem_pasta.py": teste})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "assert 0 == 42" in r.stdout
    assert "assert not True" not in r.stdout


def test_diretorio_com_irmao_vivo_nao_e_podado(tmp_path):
    """Controle POSITIVO da poda: `fica.txt` sobrevive a remocao do irmao. A
    assercao esta no TEXTO da falha porque e ela que morde — uma poda gulosa
    levaria o irmao junto e o vermelho viraria `FileNotFoundError`, ainda rc=0,
    ainda FORTE."""
    teste = '''
import pathlib
import lib

def test_valor():
    pasta = pathlib.Path(__file__).parent / "pasta"
    assert (pasta / "fica.txt").read_text() == "vivo\\n"
    assert lib.valor() == 42
'''
    lab = _lab(tmp_path, {"tests/pasta/velho.txt": "dado\n", "tests/pasta/fica.txt": "vivo\n"})
    (lab / "tests" / "pasta" / "velho.txt").unlink()
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_irmao_vivo.py": teste})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "assert 0 == 42" in r.stdout


def test_diretorio_que_virou_arquivo_atravessa_o_overlay(tmp_path):
    """Trava a ORDEM: a poda roda logo apos cada remocao, portanto ANTES da
    copia. Mover a poda para depois devolve o `IsADirectoryError` desta celula em
    silencio. Com o bullet apagado, este teste e o unico registro no repo de que
    a celula `diretorio -> arquivo` e suportada."""
    teste = '''
import pathlib
import lib

def test_valor():
    p = pathlib.Path(__file__).parent / "obsoleto"
    assert p.is_file()
    assert p.read_text() == "agora arquivo\\n"
    assert lib.valor() == 42
'''
    lab = _lab(tmp_path, {"tests/obsoleto/dado.txt": "velho\n"})
    shutil.rmtree(lab / "tests" / "obsoleto")
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/obsoleto": "agora arquivo\n",
                   "tests/test_virou_arquivo.py": teste})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "assert 0 == 42" in r.stdout


def test_arquivo_que_virou_diretorio_atravessa_o_overlay(tmp_path):
    """A direcao inversa da celula acima, que tambem atravessa: o laco dos
    removidos roda INTEIRO antes do de copia, entao o `os.remove` do arquivo
    velho abre caminho para o `os.makedirs` do diretorio novo. Quem sustenta esta
    celula e a ORDEM dos dois lacos — trocada, o `makedirs` bate no arquivo que
    ainda esta la."""
    teste = '''
import pathlib
import lib

def test_valor():
    p = pathlib.Path(__file__).parent / "obsoleto"
    assert p.is_dir()
    assert (p / "dado.txt").read_text() == "agora diretorio\\n"
    assert lib.valor() == 42
'''
    lab = _lab(tmp_path, {"tests/obsoleto": "velho\n"})
    (lab / "tests" / "obsoleto").unlink()
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/obsoleto/dado.txt": "agora diretorio\n",
                   "tests/test_virou_diretorio.py": teste})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "assert 0 == 42" in r.stdout


def test_conftest_sozinho_nao_libera_a_suite_inteira(tmp_path):
    """O conftest e o UNICO `.py` a mudar: `alvos` fica vazio e o `pytest -q` sem
    alvo roda a SUITE INTEIRA. Medido sem a guarda: o `test_alheio` (vermelho no
    antes, apagado no depois) sai `APROVADO, prova FORTE` com exit 0 — o gate
    carimba como prova um teste que nao tem nada a ver com a mudanca."""
    lab = _lab(tmp_path, {"tests/test_alheio.py": "def test_alheio():\n    assert False\n",
                          "tests/test_ok.py": "def test_ok():\n    assert True\n"})
    (lab / "tests" / "test_alheio.py").unlink()
    _commita(lab, {"tests/conftest.py": "# so o conftest mudou\n"})
    r = _gate(lab)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "nenhum teste .py alem do conftest" in r.stderr


def test_testes_so_com_vazio_nao_libera_a_suite_inteira(tmp_path):
    """O mesmo laboratorio do caso acima, pela porta do `--testes`. `--testes ""`
    dava `alvos == [""]` — lista NAO vazia, entao a guarda `if not alvos` deixava
    passar e o `pytest -q ""` varria a arvore inteira. Medido sem o `if t`:
    `APROVADO, prova FORTE` com exit 0 citando `test_alheio`, palavra por palavra o
    bug que a guarda declara fechado. Chega-se aqui por `--testes "$VAR"` com a
    variavel vazia, que e como um wrapper ou agente invoca o gate.

    A segunda metade e o controle de que o descarte pega SO o vazio: com o alvo
    util preservado, o gate roda apenas o `test_ok` e reprova por tautologia. Se o
    `""` vazasse junto, a arvore inteira entraria e o `test_alheio` daria FORTE."""
    lab = _lab(tmp_path, {"tests/test_alheio.py": "def test_alheio():\n    assert False\n",
                          "tests/test_ok.py": "def test_ok():\n    assert True\n"})
    (lab / "tests" / "test_alheio.py").unlink()
    _commita(lab, {"tests/conftest.py": "# so o conftest mudou\n"})

    r = _gate(lab, "--testes", "")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "nenhum teste .py alem do conftest" in r.stderr

    r = _gate(lab, "--testes", "", "tests/test_ok.py")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "[coluna-dupla] testes = tests/test_ok.py" in r.stdout  # so o vazio caiu
    assert "tautologico" in r.stdout and "test_alheio" not in r.stdout


def test_testes_com_diretorio_nao_libera_a_suite_inteira(tmp_path):
    """O mesmo laboratorio, pela porta que o `if t` NAO fechava: ele fechou a
    instancia (o vazio), nao a classe — "alvo truthy que o pytest expande para a
    arvore inteira". Medido sem a recusa: `--testes tests/` (e `--testes .`) sai
    `APROVADO, prova FORTE` com exit 0 citando `test_alheio`, o mesmo bug do vazio.
    Nao e entrada exotica: e o atalho humano de "prove meu fix sobre a pasta de
    testes", e e o que um wrapper com `$(dirname "$ARQ")` produz.

    A segunda metade e o controle positivo: node id CARREGA `::` e continua alvo
    legitimo. Sem ele, um criterio que recusasse tudo passaria no primeiro assert e
    seria pior que o bug — o gate nunca mais provaria nada."""
    lab = _lab(tmp_path, {"tests/test_alheio.py": "def test_alheio():\n    assert False\n",
                          "tests/test_ok.py": "def test_ok():\n    assert True\n"})
    (lab / "tests" / "test_alheio.py").unlink()
    _commita(lab, {"tests/conftest.py": "# so o conftest mudou\n"})

    r = _gate(lab, "--testes", "tests/")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "nao aponta para um arquivo de teste .py" in r.stderr
    # O prefixo acima e a parte COMUM aos dois motivos, entao ele sozinho nao ve a
    # troca: sao os dois asserts abaixo que prendem o motivo do DIRETORIO a este
    # caso — invertida a condicao dos motivos, o operador que digitou `tests/` leva
    # dois paragrafos sobre caminho absoluto e este assert fica vermelho.
    assert "Um DIRETORIO" in r.stderr
    assert "Absoluto (ou com `..`)" not in r.stderr
    assert "prova FORTE" not in r.stdout and "test_alheio" not in r.stdout

    r = _gate(lab, "--testes", "tests/test_ok.py::test_ok")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "[coluna-dupla] testes = tests/test_ok.py::test_ok" in r.stdout  # o `::` passou
    assert "tautologico" in r.stdout  # chegou ao veredito


def test_python_relativo_vira_absoluto(tmp_path):
    """`--python` relativo era guardado como veio: o `os.path.exists` casava com o
    cwd do GATE, e o `subprocess.run` resolvia contra o cwd do WORKTREE. Medido sem
    o `abspath`: `FileNotFoundError` antes de qualquer pytest.

    O relativo e um `..` unico, para FORA do repo, e as duas escolhas foram
    medidas. `os.path.relpath(sys.executable, lab)` nao serve de ataque: ele sai com
    MAIS niveis de `..` do que o worktree temporario tem, os que sobram morrem em `/`
    (`/.. == /`) e o caminho errado acerta o alvo por acidente. O ataque ficaria
    refem da profundidade do `TMPDIR` — que e o que a contagem de niveis mede, e por
    isso ela nao fica escrita aqui (CLAUDE.md §2). Um relativo para DENTRO do
    repo tambem nao: o mesmo caminho existe no worktree, e o gate rodaria o
    arquivo errado — silenciosamente, se ele fosse executavel la."""
    interpretador = tmp_path / "interpretador"  # fora do repo: o worktree nao tem esse caminho
    interpretador.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    interpretador.chmod(0o755)  # o X_OK que o `shutil.which` exige e o `os.path.exists` nao exigia
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_valor.py": _TESTE_DO_FIX})
    r = _gate(lab, "--python", "../interpretador")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FORTE" in r.stdout
    assert f"[coluna-dupla] python = {interpretador}" in r.stdout  # e nao o relativo que chegou


def test_python_por_nome_no_path_e_respeitado(tmp_path):
    """`--python python3` nao era um caminho existente, entao o gate caia CALADO no
    `sys.executable` e rodava as duas colunas no interpretador errado.

    O assert e so sobre a linha `python =`: o `python3` do PATH pode nao ter pytest
    (CLAUDE.md §6a), e ai o veredito e REPROVADO com e sem a correcao — nao
    discriminaria nada."""
    py3 = shutil.which("python3")
    # Precondicao, nao asserção: quando o `python3` do PATH E o interpretador que
    # roda a suite (`.venv/bin/python3 -m pytest`, o mesmo symlink por outro nome —
    # e o `abspath` nao resolve symlink), a linha `python =` sai igual com e sem a
    # correcao. Isso e "o ambiente nao discrimina", que se PULA; como assert virava
    # vermelho em quem so invocou o pytest pelo outro nome.
    if not py3 or os.path.abspath(py3) == sys.executable:
        pytest.skip("o `python3` do PATH e o proprio interpretador da suite: "
                    "o caso nao discriminaria o fallback calado para o `sys.executable`")
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_valor.py": _TESTE_DO_FIX})
    r = _gate(lab, "--python", "python3")
    assert f"[coluna-dupla] python = {os.path.abspath(py3)}" in r.stdout


def test_bit_de_execucao_atravessa_o_overlay(tmp_path):
    """O `copyfile` nao leva o modo, e o destino que ja existe herda o modo ANTIGO:
    a fixture executavel chega na coluna vermelha sem o bit de execucao.

    O exit code e 0 com e SEM o `copymode` — nos dois casos um teste roda e falha —,
    entao um assert sobre o rc seria CEGO. Quem discrimina e o texto do relatorio:
    com o modo certo o vermelho e a asserção sobre o valor antigo; sem ele e o
    `PermissionError` de nao poder executar a fixture."""
    lab = _lab(tmp_path, {"tests/ferramenta.sh": _FERRAMENTA, "valor.txt": "0\n"})
    os.chmod(lab / "tests" / "ferramenta.sh", 0o755)  # so o MODO muda; o conteudo e o mesmo
    _commita(lab, {"valor.txt": "42\n", "tests/test_ferramenta.py": _TESTE_QUE_EXECUTA})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "AssertionError: assert 0 == 42" in r.stdout  # a fixture EXECUTOU...
    assert "PermissionError" not in r.stdout  # ...em vez de bater no bit de execucao


def test_coluna_verde_inteiramente_pulada_reprova(tmp_path):
    """rc=0 e tambem o que o pytest devolve quando TUDO foi PULADO, e o gate lia
    rc=0 como verde. Nao e caso teorico: o `tests/conftest.py:99` pula assim, por
    dependencia ausente. Medido sem o criterio pareado: `APROVADO, prova FORTE`
    com exit 0 — a coluna corrigida nao rodou UMA linha de teste."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/conftest.py": _CONFTEST_QUE_PULA_NO_CORRIGIDO,
                   "tests/test_valor.py": _TESTE_DO_FIX})
    r = _gate(lab)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "nao provou o conserto" in r.stdout
    assert "0 passaram, 1 pularam, de 1" in r.stdout
    assert "prova FORTE" not in r.stdout


def test_verde_que_pula_SO_o_teste_do_fix_reprova(tmp_path):
    """O caso que separa o criterio PAREADO da CONTAGEM: no corrigido o teste DO
    FIX e pulado e um irmao passa. Medido: com "pelo menos um passou" sai
    `APROVADO, prova FORTE` — o mesmo carimbo do caso legitimo —, e so o
    pareamento por (classname, name) reprova, nomeando o orfao."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/conftest.py": _CONFTEST_QUE_PULA_SO_O_FIX,
                   "tests/test_valor.py": _TESTE_DO_FIX_COM_IRMAO})
    r = _gate(lab)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1 teste(s) vermelhos no antigo NAO passaram no corrigido" in r.stdout
    assert "tests/test_valor.py::test_valor" in r.stdout  # o orfao, pelo nodeid
    assert "prova FORTE" not in r.stdout


def test_verde_com_skip_parcial_continua_aprovando(tmp_path):
    """Controle positivo dos dois casos acima: um `skipif` de plataforma vale igual
    nas duas colunas, entao o caso que ele pula no verde tambem foi pulado no
    vermelho e nunca entra no conjunto exigido. Sem este teste, um criterio
    "nenhum skip na coluna verde" passaria nos dois acima e recusaria trabalho
    honesto — que e pior que o bug."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_valor.py": _TESTE_DO_FIX_COM_SKIPIF})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FORTE" in r.stdout
    assert "tests/test_valor.py::test_valor" in r.stdout


def test_verde_que_pula_o_teste_que_ERROU_no_antigo_reprova(tmp_path):
    """A porta lateral pela qual o furo do `test_verde_que_pula_SO_o_teste_do_fix`
    voltava a passar inteiro: a varredura de
    orfaos olhava so `<failure>`, e o teste que ERRA no antigo — o mecanismo que
    motivou este script inteiro, 21 erros de fixture — nao entrava nela. Aqui o
    `test_novo` erra na fixture no antigo e e PULADO no corrigido, enquanto o irmao
    pre-existente (vermelho no antigo, verde no corrigido) carrega o vermelho
    sozinho.

    Medido com a varredura voltando a olhar so `antes.falhados`: `APROVADO, prova
    FORTE` com exit 0, citando `tests/test_x.py::test_ja_vermelho - assert 0 == 42`
    — o irmao carimbando como provado um conserto que o teste do fix nunca
    exercitou."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/conftest.py": _CONFTEST_QUE_PULA_SO_O_NOVO,
                   "tests/test_x.py": _TESTE_QUE_ERRA_COM_IRMAO_VERMELHO})
    r = _gate(lab)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1 teste(s) vermelhos no antigo NAO passaram no corrigido" in r.stdout
    assert "tests/test_x.py::test_novo" in r.stdout  # o orfao e o teste do FIX
    assert "prova FORTE" not in r.stdout


def test_erro_de_coleta_impareavel_continua_aprovando(tmp_path):
    """Controle positivo do caso acima, e ele NAO e teorico: e o exemplo de prova
    FRACA que o `Uso` do script traz. Teste novo importando modulo de producao que
    so existe no corrigido nao coleta no antigo, e o pytest emite UM `<testcase>`
    com `classname=""` e `name` igual ao MODULO pontilhado — identidade que a
    coluna verde nunca tem, porque la o modulo vira testes de verdade.

    Medido sem a excecao do impareavel (`if chave[0] and ...`): `REPROVADO: a
    coluna corrigida nao provou o conserto`, citando `tests/test_novo.py::
    tests.test_novo` como orfao. A varredura ampliada recusaria o caminho legitimo
    inteiro — que e pior que o furo que ela fecha."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib_novo.py": _LIB_CORRIGIDA,
                   "tests/test_novo.py": _TESTE_QUE_IMPORTA_MODULO_NOVO})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FRACA" in r.stdout
    assert "No module named 'lib_novo'" in r.stdout
    assert "nao provou o conserto" not in r.stdout
    # A identidade impareavel nao da node id: `tests/test_novo.py::tests.test_novo`
    # nao existe em coluna nenhuma. A citacao cai no ARQUIVO, que e colavel.
    assert "::tests.test_novo" not in r.stdout
    assert "tests/test_novo.py -" in r.stdout
    citada = next(l.strip().split(" - ")[0] for l in r.stdout.splitlines()
                  if "test_novo.py" in l and " - " in l)
    # o XML vai para o `tmp_path`, e nao para dentro do lab: ver `_reroda`
    assert _reroda(str(lab), citada,
                   tmp_path / "reroda.xml") != 4  # o pytest ACEITA o alvo (rc=4 = inexistente)


def test_hook_de_fase_call_que_estoura_antes_do_corpo_e_prova_FRACA(tmp_path):
    """Controle NEGATIVO da classificacao. Quem escolhe `<failure>` x `<error>` no
    JUnit e a FASE (`report.when == "call"`), nao a entrada no corpo do teste: um
    `pytest_runtest_call` que estoure ANTES de chamar o corpo sai `<failure>` do
    mesmo jeito. E acidente alcancavel, nao ataque — o hook aqui tem ZERO condicoes
    e so chama producao (`lib.reset()`), que so existe no corrigido.

    Medido com a classificacao pela tag: `APROVADO, prova FORTE` com exit 0,
    citando `tests/test_valor.py::test_valor - AttributeError` — o carimbo de "o
    corpo do teste rodou e falhou" sobre um corpo que nunca foi chamado."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA_COM_RESET,
                   "tests/conftest.py": _HOOK_QUE_SO_CHAMA_PRODUCAO,
                   "tests/test_valor.py": _TESTE_DO_FIX})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FRACA" in r.stdout
    assert "prova FORTE" not in r.stdout
    assert "has no attribute 'reset'" in r.stdout  # a causa real, e nao o assert do corpo
    # O balde: e ESTE lab que enche `ambiguos` num pytest de verdade. Sem este
    # assert, fundir os dois baldes de volta no `le_junit` deixava a suite inteira
    # verde — a linha do roteamento nao tinha quem a protegesse.
    assert "1 com <failure> e SEM frame" in r.stdout


def test_corpo_que_chama_producao_que_estoura_continua_prova_FORTE(tmp_path):
    """Controle POSITIVO do caso acima, e o que mata o criterio "o ULTIMO frame tem
    de ser do arquivo do teste": aqui o corpo RODA, chama producao, e o traceback
    termina em `lib.py:2: ValueError`. O frame do proprio arquivo existe, mas no
    MEIO — e o vermelho legitimo do #182, que nao tem um `assert` sequer.

    Sem este teste, um criterio que exigisse o frame no fim reprovaria o caminho
    honesto e rebaixaria toda prova a FRACA, que e pior que o bug."""
    lab = _lab(tmp_path, {"lib.py": _LIB_ANTIGA_QUE_ESTOURA})
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_valor.py": _TESTE_DO_FIX})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FORTE" in r.stdout
    assert "tests/test_valor.py::test_valor - ValueError: nao suportado" in r.stdout


def test_erro_de_TEARDOWN_nao_vira_corpo_que_nao_rodou(tmp_path):
    """`<error>` e a FASE (`report.when != "call"`), e nem toda fase e ANTES do
    corpo: no teardown o corpo ja rodou e PASSOU. Aqui o `test_valor` passa nas duas
    colunas e e a fixture que quebra no antigo, depois do `yield`.

    O gate nao tem como saber que o corpo rodou (o XML nao carrega a fase), entao o
    veredito continua FRACA — o que ele NAO pode fazer e afirmar que o corpo nao
    rodou. Medido com a frase antiga: `APROVADO, prova FRACA` dizendo "o corpo nem
    ter chegado a rodar" sobre um corpo que executou e passou. Este e o unico
    registro mecanico no repo de que `<error>` != "nao rodou"."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA,
                   "tests/test_teardown.py": _FIXTURE_QUE_ESTOURA_NO_TEARDOWN})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FRACA" in r.stdout
    assert "estourou no TEARDOWN" in r.stdout  # a causa real, do teardown
    assert "o corpo nem ter chegado a rodar" not in r.stdout
    assert "nem chegaram a rodar" not in r.stdout


def test_prova_FRACA_separa_o_erro_do_failure_sem_frame():
    """A frase da prova FRACA e a linha que o Manager le para decidir se aceita, e
    ela tratava DOIS estados como um so. `<failure>` sem frame do arquivo do caso
    PODE ter rodado (os quatro gatilhos do item 2 da celula do traceback, no topo
    do `coluna_dupla.py`) e o gate nao distingue; `<error>` e fase != call, e so em
    coleta e setup isso implica corpo nao executado — no teardown o corpo JA rodou.

    Os gatilhos nao sao citados pelo NOME aqui de proposito: os tres greps do
    `coluna_dupla.py` contam ocorrencias deles na arvore, e uma mencao em prosa
    entraria na conta como se fosse uso.

    Medido com os dois no mesmo balde: os relatorios dos dois grupos saem byte a
    byte IGUAIS, e o do `<error>` fala de traceback que o gate nunca olhou. O
    assert que discrimina e o `rel_erro != rel_ambiguo`; os demais nomeiam o que
    cada um tem de dizer.

    A versao anterior deste teste era quase tautologica: ela asseria a AUSENCIA de
    "NENHUM teste chegou a RODAR", string que o fonte ja nao continha."""
    passado = {("lab", "test_passa"): "lab.py::test_passa"}
    so_erro = coluna_dupla.Desfechos(
        2, 0, {}, {},
        {("lab", "test_erra"): ("lab.py::test_erra", "RuntimeError: x")}, passado)
    so_ambiguo = coluna_dupla.Desfechos(
        2, 0, {}, {("lab", "test_amb"): ("lab.py::test_amb", "Failed: forjado")},
        {}, passado)
    misto = coluna_dupla.Desfechos(3, 0, {}, so_ambiguo.ambiguos, so_erro.errados, passado)

    saidas = [coluna_dupla.veredito(coluna_dupla.PYTEST_FALHA, d, 0, _verde(d))
              for d in (so_erro, so_ambiguo, misto)]
    (_, rel_erro), (_, rel_ambiguo), (_, rel_misto) = saidas
    assert [codigo for codigo, _ in saidas] == [0, 0, 0]
    assert all("prova FRACA" in r for _, r in saidas)
    assert all("1 passado(s) ao lado" in r for _, r in saidas)  # o passado ao lado, literal

    assert rel_erro != rel_ambiguo  # o assert que discrimina: hoje saem iguais
    # O gate nao olhou traceback nenhum de um `<error>` — dizer "frame" ali e falar
    # de uma evidencia que ele nao tem.
    assert "frame do arquivo do caso" not in rel_erro
    assert "traceback" not in rel_erro
    assert "<error>" in rel_erro and "<error>" not in rel_ambiguo
    assert "1 com <failure>" in rel_misto and "1 com <error>" in rel_misto


def test_prova_FORTE_nao_chama_de_nao_rodado_o_failure_sem_frame():
    """O `(e mais N ...)` da linha FORTE dizia "que nem chegaram a rodar" de tudo o
    que nao fosse `<failure>` COM frame. E falso nos dois baldes que sobram: o
    `<failure>` sem frame pode ter rodado, e o `<error>` de teardown rodou. Agora
    ele nomeia e conta os dois, separados."""
    antes = coluna_dupla.Desfechos(
        5, 0,
        {("lab", "test_falha"): ("lab.py::test_falha", "assert 0 == 42")},
        {("lab", "test_amb1"): ("lab.py::test_amb1", "Failed: a"),
         ("lab", "test_amb2"): ("lab.py::test_amb2", "Failed: b")},
        {("lab", "test_erra"): ("lab.py::test_erra", "RuntimeError: x")},
        {("lab", "test_passa"): "lab.py::test_passa"})
    codigo, relatorio = coluna_dupla.veredito(coluna_dupla.PYTEST_FALHA, antes, 0, _verde(antes))
    assert codigo == 0
    assert "prova FORTE" in relatorio
    assert "2 com <failure> sem frame do arquivo do caso" in relatorio
    assert "1 com <error>" in relatorio
    assert "que nem chegaram a rodar" not in relatorio


def test_o_balde_ambiguo_entra_nas_duas_varreduras():
    """O balde novo tem de entrar nos DOIS lugares que varrem vermelho — a varredura
    de orfaos e a guarda de coleta pura —, e esquecer qualquer um dos dois e
    regressao SILENCIOSA: o `_verde` deste arquivo une os tres baldes, entao nenhum
    outro teste fica vermelho.

    (a) verde SEM o par do ambiguo: com o balde na varredura sai o orfao nomeado;
    sem ele o gate cai no ramo da falha fechada, outro rc=1 por outro motivo.
    (b) rc=2 com um ambiguo ao lado do erro de coleta: com o balde na `coleta_pura`
    a guarda dispara; sem ele o XML PARCIAL sai `APROVADO, prova FRACA` (rc=0)."""
    ambiguo = {("lab", "test_amb"): ("lab.py::test_amb", "Failed: forjado")}

    antes = coluna_dupla.Desfechos(2, 0, {}, ambiguo, {},
                                   {("lab", "test_passa"): "lab.py::test_passa"})
    verde_sem_o_par = coluna_dupla.Desfechos(1, 0, {}, {}, {},
                                             {("lab", "test_verde"): "lab.py::test_verde"})
    codigo, relatorio = coluna_dupla.veredito(coluna_dupla.PYTEST_FALHA, antes, 0, verde_sem_o_par)
    assert codigo == 1
    assert "NAO passaram no corrigido" in relatorio
    assert "lab.py::test_amb" in relatorio  # o orfao, pelo nodeid

    interrompido = coluna_dupla.Desfechos(
        2, 0, {}, ambiguo,
        {("", "tests.test_x"): ("tests/test_x.py::tests.test_x", "ImportError: x")}, {})
    codigo, relatorio = coluna_dupla.veredito(coluna_dupla.PYTEST_INTERROMPIDO, interrompido,
                                              0, _verde(interrompido))
    assert codigo == 1
    assert "interrompida" in relatorio
    assert "prova FRACA" not in relatorio


def test_testes_com_cara_de_opcao_nao_libera_a_suite_inteira(tmp_path):
    """Alvo que o pytest nao trata como ALVO. A guarda de nome checa so o SUFIXO,
    entao `--basetemp=<x>.py` termina em `.py`, passa por ela, e o pytest o consome
    como OPCAO — sobra ZERO alvo posicional e a coleta varre a arvore inteira,
    palavra por palavra o bug do `--testes tests/`.

    O laboratorio e o do achado, literal: o teste PEDIDO e tautologico (verde nas
    duas colunas) e o irmao NAO relacionado e vermelho->verde. Medido sem o `--`:
    exit 0 e `APROVADO, prova FORTE` citando `tests/test_irmao.py::test_valor` — o
    gate carimba como provado um conserto que o teste pedido nunca exercitou.

    A segunda metade e o controle positivo, e e obrigatoria: um `--` mal posto
    (antes do `--junitxml`) quebraria TODOS os alvos, os dois casos sairiam rc=4 e
    o negativo acima passaria verde do mesmo jeito."""
    lab = _lab(tmp_path, {"tests/test_irmao.py": _TESTE_DO_FIX})
    _commita(lab, {"lib.py": _LIB_CORRIGIDA,
                   "tests/test_taut.py": "def test_taut():\n    assert True\n"})

    r = _gate(lab, f"--testes=--basetemp={tmp_path / 'base.py'}")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "pytest saiu 4 na coluna antiga" in r.stdout
    assert "prova FORTE" not in r.stdout and "test_irmao" not in r.stdout

    r = _gate(lab, "--testes", "tests/test_taut.py")  # arquivo: chega ao veredito...
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tautologico" in r.stdout and "test_irmao" not in r.stdout

    r = _gate(lab, "--testes", "tests/test_irmao.py::test_valor")  # ...e node id tambem
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FORTE" in r.stdout
    assert "tests/test_irmao.py::test_valor - assert 0 == 42" in r.stdout


def test_addopts_do_ambiente_nao_injeta_alvo(tmp_path):
    """A porta que o `--` NAO alcanca: o pytest PREPENDE o `PYTEST_ADDOPTS`, entao
    um ALVO posto ali entra ANTES do nosso `--` e nada o barra. Mesmo laboratorio
    do teste acima, mesmo alvo pedido — so muda a variavel de ambiente.

    Medido sem o `"PYTEST_ADDOPTS": ""` da `roda_pytest`: exit 0 e `APROVADO, prova
    FORTE` citando `test_irmao`, enquanto a linha `testes = ...` impressa continua
    dizendo `tests/test_taut.py` — o gate mente sobre o que rodou. Nao e regressao
    deste PR; e pre-existente, e o proprio harness deste arquivo zerava a variavel,
    o que escondia o caso de todos os outros testes.

    A segunda metade e o controle positivo: uma OPCAO (nao um alvo) tambem deixa de
    alcancar o gate, e o veredito continua o mesmo. Sem ela, um conserto que
    quebrasse a corrida inteira passaria no negativo."""
    lab = _lab(tmp_path, {"tests/test_irmao.py": _TESTE_DO_FIX})
    _commita(lab, {"lib.py": _LIB_CORRIGIDA,
                   "tests/test_taut.py": "def test_taut():\n    assert True\n"})

    r = _gate(lab, "--testes", "tests/test_taut.py", addopts="tests/test_irmao.py")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tautologico" in r.stdout
    assert "prova FORTE" not in r.stdout and "test_irmao" not in r.stdout

    r = _gate(lab, "--testes", "tests/test_taut.py", addopts="-rN")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tautologico" in r.stdout


def test_citacao_sobrevive_ao_shell(tmp_path):
    """A citacao e feita para ser COLADA depois de um `pytest `, e id de
    parametrizacao carrega virgula e espaco. Sem aspas, o shell parte
    `test_valor[2,5 mil]` em dois argumentos e o alvo vira lixo.

    (a) o lab prova a aspa na citacao de UM vermelho; (b) a `veredito` direto prova
    o SEPARADOR da lista de orfaos: com `', '.join` a virgula gruda no fim de cada
    elemento e a lista inteira deixa de ser colavel, mesmo com cada elemento citado.

    Positivo: os asserts de citacao dos outros testes deste arquivo
    (`tests/test_valor.py::test_valor - assert 0 == 42`) provam que o node id comum
    NAO ganhou aspas — `shlex.quote` nao toca em `[a-zA-Z0-9_@%+=:,./-]`."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_param.py": _TESTE_PARAMETRIZADO})
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "'tests/test_param.py::test_valor[2,5 mil]' - assert 0 == 42" in r.stdout

    antes = coluna_dupla.Desfechos(
        2, 0, {}, {},
        {("lab", "test_a"): ("'lab.py::test_a[2,5 mil]'", "x"),
         ("lab", "test_b"): ("lab.py::test_b", "y")}, {})
    verde = coluna_dupla.Desfechos(1, 0, {}, {}, {},
                                   {("lab", "test_verde"): "lab.py::test_verde"})
    codigo, relatorio = coluna_dupla.veredito(coluna_dupla.PYTEST_FALHA, antes, 0, verde)
    assert codigo == 1
    linha = next(l for l in relatorio.splitlines() if "NAO passaram no corrigido" in l)
    assert linha.endswith("'lab.py::test_a[2,5 mil]' lab.py::test_b")
    assert ", " not in linha  # a virgula do `join` sobrevivia DENTRO da lista citada


def test_testes_com_caminho_absoluto_nao_escapa_das_colunas(tmp_path):
    """Alvo absoluto nao e coletado de coluna nenhuma: o `cwd` do pytest e o
    worktree, mas o caminho aponta para o CHECKOUT ATUAL. Quem decide o veredito
    passa a ser uma revisao POSTERIOR do teste, com os SHAs das duas colunas
    impressos por cima.

    O lab tem TRES commits: em c1 e c2 o `tests/test_x.py` e TAUTOLOGICO (`assert
    True`), e so em c3 ele vira o teste real. O gate roda o par c1 x c2 — o par que
    tem de sair REPROVADO por tautologia.

    Negativo: sem a guarda, o alvo absoluto puxa a revisao c3 do disco, ela fica
    vermelha no worktree c1 e verde no c2, e o par tautologico sai `APROVADO, prova
    FRACA` com exit 0. Positivo: o MESMO par com o alvo RELATIVO — que resolve
    dentro de cada worktree — sai `REPROVADO: tautologico`, e e ele que mostra o
    que o absoluto tinha escapado."""
    lab = _lab(tmp_path, {"tests/test_x.py": "def test_x():\n    assert True\n"})
    _commita(lab, {"lib.py": _LIB_CORRIGIDA})  # c2: nada muda em tests/
    _commita(lab, {"tests/test_x.py": "import lib\n\n\ndef test_x():\n    assert lib.valor() == 42\n"})
    par_taut = ("--antes", "HEAD~2", "--depois", "HEAD~1")  # argparse: a ultima ocorrencia vence

    r = _gate(lab, *par_taut, "--testes", str(lab / "tests" / "test_x.py"))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "nao aponta para um arquivo de teste .py" in r.stderr
    # A outra ponta do par: o prefixo acima e comum aos dois motivos, e sao estes
    # dois asserts que prendem o motivo do ABSOLUTO a este caso.
    assert "Absoluto (ou com `..`)" in r.stderr
    assert "Um DIRETORIO" not in r.stderr
    assert "APROVADO" not in r.stdout

    r = _gate(lab, *par_taut, "--testes", "tests/test_x.py")  # relativo: dentro do worktree
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tautologico" in r.stdout

    r = _gate(lab, *par_taut, "--testes", "../lab/tests/test_x.py")  # sai pelo `..`
    assert r.returncode == 1, r.stdout + r.stderr
    assert "nao aponta para um arquivo de teste .py" in r.stderr


def test_stdout_da_coluna_rejeitada_e_repassado(tmp_path):
    """Quando o grupo falha TAMBEM no codigo corrigido, a linha `(rc=1)` era todo o
    diagnostico que o operador recebia — o traceback das duas colunas morria no
    `capture_output` da `roda_pytest`.

    Vai para o STDERR, e nao para o stdout, porque o stdout e o canal do
    RELATORIO: quatro linhas com os dois SHAs, os alvos, o interpretador e o
    veredito. Despejar ali o texto de duas corridas do pytest — que um unico teste
    barulhento faz passar de 500 linhas por coluna, como mede o
    `test_cauda_do_stdout_corta_o_comeco_e_conta_o_que_omitiu` logo abaixo —
    enterra o veredito no meio do diagnostico. Sao coisas de natureza diferente e ficam em
    canais diferentes: quem quer so o veredito le o stdout, quem quer o traceback
    le o stderr, e um `2>/dev/null` separa os dois sem parser."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA, "tests/test_valor.py": _TESTE_QUE_FALHA_NAS_DUAS})
    r = _gate(lab)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "falha tambem no codigo corrigido" in r.stdout
    assert "assert 0 == 99" in r.stderr   # o traceback da coluna ANTIGA
    assert "assert 42 == 99" in r.stderr  # e o da CORRIGIDA
    assert "assert 0 == 99" not in r.stdout  # o stdout continua so o relatorio


def test_cauda_do_stdout_corta_o_comeco_e_conta_o_que_omitiu(tmp_path):
    """O despejo e so a CAUDA, e o cabecalho diz quantas linhas ficaram de fora.
    Sem o corte, um teste barulhento leva o stderr do gate junto — MEGABYTES com 20
    mil `print` falhando nas duas colunas (o tamanho e do lab, e por isso nao esta
    escrito aqui; CLAUDE.md §2).

    Negativo: com `cauda = 0`, `linhas[-0:]` devolve a lista INTEIRA e os tres
    asserts do laco caem juntos — o bloco vai de 200 linhas para a saida completa,
    o `LINHA_0000` reaparece, e o cabecalho passa a anunciar como "omitidas" as
    linhas que estao ali impressas logo abaixo dele. Positivo: o resumo que NOMEIA
    o teste (`FAILED ... - assert 0 == 99`) sobrevive ao corte nas DUAS colunas —
    e ele o motivo de a cauda ser o pedaco guardado; um corte pela CABECA passaria
    nos dois primeiros asserts e jogaria fora justamente o diagnostico.

    A contagem nao e conferida contra numero escrito aqui (CLAUDE.md §2): o total
    vem de uma SEGUNDA medicao, a mesma corrida feita direto pela `roda_pytest`."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA,
                   "tests/test_valor.py": _TESTE_QUE_IMPRIME_MUITO_E_FALHA_NAS_DUAS})
    r = _gate(lab)
    assert r.returncode == 1, r.stdout + r.stderr

    # `re.split` com UM grupo devolve os separadores: ['', omitidas, bloco, ...].
    partes = re.split(r"\[coluna-dupla\] stdout da coluna \w+ \((\d+) linha\(s\) "
                      r"iniciais omitidas\):\n", r.stderr)
    assert len(partes) == 5 and partes[0] == "", r.stderr
    # O XML sai FORA do lab (ver `_reroda`), e esta chamada vem DEPOIS do `_gate`.
    total = len(coluna_dupla.roda_pytest(str(lab), sys.executable, ["tests/test_valor.py"],
                                         str(tmp_path / "direto.xml"))[2].splitlines())
    for omitidas, bloco in ((partes[1], partes[2]), (partes[3], partes[4])):
        assert len(bloco.splitlines()) == 200, len(bloco.splitlines())
        assert int(omitidas) == total - 200, (omitidas, total)
        assert "LINHA_0000" not in bloco and "LINHA_0499" in bloco
    assert "- assert 0 == 99" in partes[2] and "- assert 42 == 99" in partes[4]


def test_stdout_de_rc3_e_repassado_e_o_da_coluna_verde_nao(tmp_path):
    """As duas direcoes da restricao por rc, no mesmo lab.

    O rc=3 e o unico REPROVADO cujo diagnostico inteiro pode viver no stdout: o
    INTERNALERROR so vai para o stderr enquanto o `TerminalReporter` nao existe
    (`pytest_configure`); de `pytest_collection_modifyitems` em diante quem o
    escreve e o `TerminalReporter.pytest_internalerror`. Medido nos dois, mesmo
    rc=3, e a troca de canal e exata: `pytest_configure` poe o INTERNALERROR no
    stderr e deixa o stdout VAZIO; o hook de coleta daqui poe no stdout e deixa o
    stderr VAZIO. QUAL canal fica vazio nao muda de maquina; o TAMANHO do outro muda
    com o `tmp_path`, e por isso nao esta escrito aqui (CLAUDE.md §2).

    Negativo: com a restricao em `(PYTEST_FALHA, PYTEST_INTERROMPIDO)` — a versao
    que este teste existe para impedir de voltar — a coluna antiga e pulada e o
    operador recebe a linha do veredito e mais nada; o assert do `AttributeError`
    fica vermelho. Positivo/outra direcao: a coluna CORRIGIDA sai rc=0 com stdout
    (`1 passed`) e nao e despejada — sem a restricao (`if not saida:`), o despejo
    volta a sair numa coluna que nao tem vermelho nenhum para mostrar."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA_COM_RESET,
                   "tests/conftest.py": _HOOK_DE_COLETA_QUE_SO_CHAMA_PRODUCAO,
                   "tests/test_valor.py": _TESTE_DO_FIX})
    r = _gate(lab)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "pytest saiu 3 na coluna antiga" in r.stdout
    assert "stdout da coluna antiga" in r.stderr
    assert "INTERNALERROR" in r.stderr
    assert "has no attribute 'reset'" in r.stderr  # o diagnostico, que so o stdout tinha
    assert "stdout da coluna corrigida" not in r.stderr  # ela saiu rc=0: nada a mostrar


def test_veredito_sai_antes_do_despejo_com_os_canais_juntos(tmp_path):
    """Separar os canais so entrega o veredito primeiro se o stdout for DESCARREGADO
    antes do despejo. Todo o resto deste grupo le `r.stdout` e `r.stderr` como pipes
    separados — a unica configuracao em que a ordem entre eles nao existe. Em
    `> log 2>&1`, `| tee` e no CI eles viram um fluxo so, e ai o stdout em pipe e
    bloco-bufferizado (descarrega no `sys.exit`) enquanto o stderr e LINE-buffered e
    sai na quebra de linha.

    Negativo: sem o `flush=True` do `main`, o veredito e as 4 linhas de cabecalho
    saem no FIM, atras do despejo inteiro — a POSICAO exata e o tamanho do despejo,
    que e do lab, e por isso nao fica escrita aqui (CLAUDE.md §2). Positivo: o
    despejo continua saindo (`assert 0 == 99`), ou seja, o `flush` nao trocou um
    problema de ordem por um de conteudo."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA,
                   "tests/test_valor.py": _TESTE_QUE_IMPRIME_MUITO_E_FALHA_NAS_DUAS})
    r = _gate(lab, juntos=True)
    assert r.returncode == 1, r.stdout
    linhas = r.stdout.splitlines()
    # As 4 do cabecalho, o veredito e a primeira linha do despejo, na ordem em que
    # tem de aparecer: comparar com o proprio `sorted` prende as seis de uma vez.
    ordem = [next(i for i, l in enumerate(linhas) if alvo in l)
             for alvo in ("antes  =", "depois =", "testes =", "python =",
                          "falha tambem no codigo corrigido", "stdout da coluna")]
    assert ordem == sorted(ordem), (ordem, len(linhas))
    assert "assert 0 == 99" in r.stdout  # o despejo nao sumiu no caminho


def test_cabecalho_sai_antes_do_stderr_do_pytest_com_os_canais_juntos(tmp_path):
    """A outra metade da ordem, e o teste acima e CEGO a ela: o lab dele nao produz
    stderr de pytest nenhum, entao a unica inversao que ele pode ver e a do
    veredito. O cabecalho e impresso ANTES das duas corridas, e a `roda_pytest`
    repassa o stderr delas LINE-buffered, na primeira quebra de linha — as 4 linhas
    que identificam O QUE foi provado (os dois SHA, os alvos, o interpretador) saem
    debaixo do despejo.

    O lab e o rc=3 de `pytest_configure`: o unico ponto em que o INTERNALERROR
    ainda vai para o stderr, porque o `TerminalReporter` nao existe para captura-lo
    (medido: stdout VAZIO, nem XML sai; o TAMANHO do stderr e do `tmp_path` e por
    isso nao fica escrito aqui).

    Negativo: sem o `flush=True` da linha do `python =`, o cabecalho inteiro fica no
    buffer de bloco do stdout ate o `sys.exit` e as 4 linhas caem DEPOIS do
    INTERNALERROR — os quatro asserts de ordem ficam vermelhos. Positivo: o
    diagnostico repassado continua saindo (`has no attribute 'reset'`), ou seja, o
    `flush` novo nao trocou ordem por conteudo."""
    lab = _lab(tmp_path)
    _commita(lab, {"lib.py": _LIB_CORRIGIDA_COM_RESET,
                   "tests/conftest.py": _HOOK_DE_CONFIGURE_QUE_SO_CHAMA_PRODUCAO,
                   "tests/test_valor.py": _TESTE_DO_FIX})
    r = _gate(lab, juntos=True)
    assert r.returncode == 1, r.stdout
    linhas = r.stdout.splitlines()
    # Sem este par o assert de ordem passaria por AUSENCIA: um lab que deixasse de
    # estourar nao teria stderr nenhum para vir na frente.
    assert "has no attribute 'reset'" in r.stdout, r.stdout
    stderr_repassado = next(i for i, l in enumerate(linhas) if "INTERNALERROR" in l)
    # O assert de ordem sozinho e CEGO AO CANAL: trocado o
    # `_HOOK_DE_CONFIGURE_QUE_SO_CHAMA_PRODUCAO` pelo `_HOOK_DE_COLETA_...` (seis
    # linhas acima dele), o INTERNALERROR chega pelo DESPEJO do stdout, que sai
    # DEPOIS do veredito e portanto sempre depois do cabecalho — o caso ficaria
    # verde com e sem o `flush`. "Repassado pela `roda_pytest`" e exatamente isto:
    # vir ANTES do veredito. Medido: com a troca de constante, este assert e o unico
    # que fica vermelho.
    veredito = next(i for i, l in enumerate(linhas) if "pytest saiu 3" in l)
    assert stderr_repassado < veredito, (stderr_repassado, veredito, r.stdout)
    cabecalho = [next(i for i, l in enumerate(linhas) if alvo in l)
                 for alvo in ("antes  =", "depois =", "testes =", "python =")]
    assert max(cabecalho) < stderr_repassado, (cabecalho, stderr_repassado, r.stdout)


def test_copia_detectada_nao_some_do_overlay(tmp_path):
    """`diff.renames=copies` na config de QUEM RODA faz o `git diff` classificar o
    arquivo de teste novo como `C` — que o `--diff-filter=AMRD` descarta. O teste
    novo entao nao atravessa o overlay E nao entra nos alvos, e sobra so o irmao
    tautologico.

    Negativo: sem o `-c diff.renames=true`, a segunda chamada sai `REPROVADO:
    tautologico` — o gate acusa de tautologico um teste que nunca foi coletado.
    Positivo: a primeira chamada, com a config PADRAO, sai FORTE citando
    `tests/test_novo.py::test_fix`; sem ela, um pino que quebrasse a deteccao de
    rename passaria verde no negativo.

    A config e local do lab (`.git/config`), nao variavel de ambiente: e assim que
    ela chega de verdade — `git config --global diff.renames copies` na maquina de
    quem roda o gate."""
    lab = _lab(tmp_path, {"tests/test_taut.py": _OITO_TAUTOLOGICOS})
    _commita(lab, {"lib.py": _LIB_CORRIGIDA,
                   "tests/test_taut.py": _OITO_TAUTOLOGICOS + "def test_taut_8():\n    assert True\n",
                   "tests/test_novo.py": _COPIA_COM_FIX})

    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FORTE" in r.stdout
    assert "tests/test_novo.py::test_fix" in r.stdout

    _git(lab, "config", "diff.renames", "copies")
    # Sem este assert a segunda metade mede ZERO em silencio: um git que deixasse
    # de classificar como COPIA repetiria o desfecho da primeira e passaria verde.
    status = _git(lab, "diff", "--name-status", "HEAD~1", "HEAD", "--", "tests/").stdout
    assert any(l.startswith("C") for l in status.splitlines()), status  # medido: `C083`
    r = _gate(lab)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "prova FORTE" in r.stdout
    assert "tests/test_novo.py::test_fix" in r.stdout


def test_o_preambulo_conta_as_celulas_que_ele_declara():
    """A lista de limites e a MESMA regra escrita duas vezes: o preambulo diz
    QUANTAS celulas caem em cada categoria, e cada celula diz de qual delas ela e.
    O `CLAUDE.md` §0.7 manda um teste comparar as duas quando a duplicacao e
    inevitavel — e ela e, porque o preambulo existe justamente para nao repetir a
    contagem dentro das celulas (a do `--testes` que EXPANDE diz isso com todas as
    letras).

    Nenhum outro teste deste arquivo olha para o texto, e foi por ai que o defeito
    entrou em tres rodadas seguidas. O marcador e o proprio rotulo da categoria,
    escrito literal na celula; a normalizacao de espaco existe porque o preambulo
    quebra linha no meio da frase. Sem parser e sem heuristica.

    Negativo: a celula do banco compartilhado passou a carimbar `APROVADO falso` e
    o preambulo continuava em DOIS — medido, este teste fica VERMELHO ate o
    preambulo virar TRES. Positivo: a categoria (b) ja esta certa em DOIS e passa
    na mesma rodada, entao o teste nao acusa qualquer contagem.

    TETO, e ele e grande: o que se mede aqui e a DISCIPLINA DE ROTULAR, nao a
    categoria. As duas direcoes ja foram medidas e as duas erram:

    - celula acrescentada que E um APROVADO falso, escrita SEM a frase literal:
      passa em SILENCIO (1 passed), com o preambulo dizendo TRES e a categoria
      tendo QUATRO. Quem nao rotula nao e visto.
    - a frase colada numa celula que NEGA pertencer a categoria: vermelho, pelo
      motivo errado. O criterio e a frase, nao o sentido dela.

    E a categoria (c) — a que fala do que o gate NAO garante — esta descoberta dos
    dois lados: o preambulo nao declara numero para ela e nenhuma celula carrega
    marcador dela, entao o laco abaixo nem chega a olha-la. NAO tente fechar isso
    com heuristica: e a mesma classe dos tres furos de texto que a `le_junit`
    passou o arquivo inteiro fugindo. Quem escreve uma celula escreve o rotulo dela.
    Nao ha contagem escrita neste docstring de proposito (CLAUDE.md §2): quem quiser
    os numeros de hoje le a mensagem do `assert` la embaixo, que os imprime — e nao
    adianta `grep`, porque a celula quebra a frase no meio da linha e so a
    normalizacao de espaco daqui a remonta.

    O que este teste NAO faz e ler o arquivo: ele importa o modulo e le
    `coluna_dupla.__doc__`, entao um `read_text()` + `index()` procurando texto —
    o pecado do CLAUDE.md §3 — nao acontece aqui."""
    palavras = {"UM": 1, "DOIS": 2, "TRES": 3, "QUATRO": 4}
    linhas = coluna_dupla.__doc__.splitlines()
    corte = next(n for n, l in enumerate(linhas) if l.startswith("    - "))
    preambulo = " ".join(" ".join(linhas[:corte]).split())
    celulas = []
    for linha in linhas[corte:]:
        if linha.startswith("    - "):
            celulas.append(linha)
        else:
            celulas[-1] += " " + linha
    celulas = [" ".join(c.split()) for c in celulas]

    for marcador in ("APROVADO falso", "FRACA carimbada FORTE"):
        assert preambulo.count(marcador) == 1, f"{marcador!r} nao e unico no preambulo"
        # O numero declarado vem entre o marcador e os dois-pontos que abrem a
        # enumeracao — `(a) APROVADO falso, DOIS: o alvo ...`.
        entre = preambulo.split(marcador, 1)[1].split(":", 1)[0].split()
        declarado = next(n for p, n in palavras.items() if p in entre)
        real = sum(marcador in c for c in celulas)
        assert real == declarado, (
            f"o preambulo declara {declarado} celula(s) de {marcador!r}, e ha {real} "
            f"entre as {len(celulas)} celulas")
