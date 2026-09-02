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


def _pytest_em(monkeypatch, tmp_path, nome, fonte):
    """Roda o pytest de verdade em `tmp_path`, PELA `roda_pytest`: e ela que monta a
    linha de comando do XML, e uma copia dela aqui seria uma segunda fonte de
    verdade. `PYTEST_ADDOPTS` e zerado — herdado do ambiente, mudaria a corrida e o
    teste mediria outra coisa. O XML fica em `tmp_path/relatorio.xml`."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "")
    (tmp_path / nome).write_text(fonte)
    return coluna_dupla.roda_pytest(str(tmp_path), sys.executable, [nome],
                                    str(tmp_path / "relatorio.xml"))


def _verde(antes=None):
    """A coluna CORRIGIDA das chamadas unitarias: um teste que passou, mais tudo o
    que ficou VERMELHO no antigo — falha E erro — agora passando. O criterio de
    verde e PAREADO e varre os dois, entao sem isto o caso sairia REPROVADO por um
    motivo que ele nao quer medir."""
    vermelhos = {**antes.falhados, **antes.errados} if antes else {}
    passados = {("lab", "test_verde"): "lab.py::test_verde",
                **{chave: nodeid for chave, (nodeid, _) in vermelhos.items()}}
    return coluna_dupla.Desfechos(len(passados), 0, {}, {}, passados)


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
# Custo medido: ~1,3 s por caso ponta a ponta (2 worktrees + 2 pytest), ~0,12 s
# quando o gate aborta cedo. O laboratorio e descartavel e nao encosta no
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


def _escreve(raiz, arquivos):
    for rel, conteudo in arquivos.items():
        alvo = raiz / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo)


def _git(lab, *args):
    subprocess.run(["git", *args], cwd=lab, check=True, capture_output=True)


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


def _gate(lab, *args, addopts=""):
    """O gate de verdade, sobre o laboratorio. `PYTEST_ADDOPTS` e fixado pelo mesmo
    motivo do `_pytest_em`: herdado, mudaria a corrida das DUAS colunas. O
    `COLUMNS` que estava aqui saiu junto com a leitura do resumo — era o pytest
    que CORTAVA a linha do resumo na largura do terminal; o XML nao corta nada.

    `addopts` existe para o teste que prova que o gate IGNORA a variavel: zerar
    aqui, sempre, esconderia justamente esse caso de todos os outros testes."""
    return subprocess.run([sys.executable, str(_CAMINHO), "--antes", "HEAD~1", "--depois", "HEAD", *args],
                          cwd=lab, env={**os.environ, "PYTEST_ADDOPTS": addopts},
                          capture_output=True, text=True)


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
    medidas. `os.path.relpath(sys.executable, lab)` nao serve de ataque: ele sai
    com 10 niveis de `..` e o worktree temporario so tem 7 — os 3 sobrando morrem
    em `/` (`/.. == /`) e o caminho errado acerta o alvo por acidente, entao o
    ataque ficaria refem da profundidade do `TMPDIR`. Um relativo para DENTRO do
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


def test_prova_FRACA_nao_afirma_que_ninguem_rodou():
    """A frase da prova FRACA e a linha que o Manager le para decidir se aceita, e
    ela dizia "NENHUM teste chegou a RODAR no codigo antigo" olhando so o contador
    de falhas — falsa sempre que a coluna antiga tinha teste PASSANDO ao lado do
    que errou. Aqui o antigo tem 1 erro e 1 passado, e a frase passa a ser sobre os
    VERMELHOS, nao sobre o grupo."""
    antes = coluna_dupla.Desfechos(
        2, 0, {}, {("lab", "test_erra"): ("lab.py::test_erra", "RuntimeError: x")},
        {("lab", "test_passa"): "lab.py::test_passa"})
    codigo, relatorio = coluna_dupla.veredito(coluna_dupla.PYTEST_FALHA, antes, 0, _verde(antes))
    assert codigo == 0 and "prova FRACA" in relatorio
    assert "NENHUM teste chegou a RODAR" not in relatorio
    assert "1 passado(s) ao lado" in relatorio  # o que a frase antiga escondia


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
