"""O criterio de prova FORTE do gate de coluna dupla: `FAILED` x `ERROR`.

Vale um teste porque o modo de falha e SILENCIOSO e ja aconteceu: um grupo em que
NENHUM corpo de teste chegou a rodar (21 erros de fixture, zero asserções) saia
`APROVADO, prova FORTE`. O exit code nao separa os dois casos — erro de fixture e
asserção que falha saem os DOIS com rc=1 —, entao a regra le o resumo do pytest.

Testa a `veredito`, que e onde mora TODA a decisao do gate: o `main()` so imprime
o que ela devolve. Sem isso o teste passaria por fora da linha que decide.

O pytest roda DE VERDADE, num diretorio fora do repo (sem conftest, sem banco),
em vez de inventar strings: o que sustenta a regra e o formato real da saida, e
uma string inventada so provaria que `startswith` funciona. O script nao e
importavel como modulo do pacote, entao vem por caminho.
"""
import importlib.util
import os
import pathlib
import subprocess
import sys

_CAMINHO = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "coluna_dupla.py"
_spec = importlib.util.spec_from_file_location("coluna_dupla", _CAMINHO)
coluna_dupla = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(coluna_dupla)

# O `print` na fixture e o ataque: ele cai em `Captured stdout setup`, na coluna 0,
# com o formato exato de uma linha de resumo. Sem fatiar pelo cabecalho do resumo,
# o gate le esse print como "um teste rodou e falhou" — no caso EXATO em que zero
# corpos de teste executaram, que e o que a regra existe para pegar.
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

# Para o `-rs` do teste de cabecalho-sem-desfecho: o `-r` PARCIAL so imprime o
# cabecalho se sobrar algo a reportar na letra pedida — medido, `-rs` num arquivo
# so com a asserção que falha nao imprime cabecalho nenhum e cai no outro ramo. O
# skip da ao `-rs` o que reportar, e a asserção e o teste que RODOU e falhou sem
# aparecer no resumo: rc=1, cabecalho de pe, zero FAILED e zero ERROR.
_SKIP_MAIS_ASSERCAO_QUE_FALHA = '''
import pytest

@pytest.mark.skip(reason="so para o -rs ter o que reportar")
def test_pulado():
    pass

def test_roda_e_falha():
    assert 1 == 2
'''

# O mesmo ataque pelo OUTRO canal, e pior: o stderr do pytest sai INTEIRO depois
# do stdout, logo sempre depois do ultimo cabecalho de resumo. Um cabecalho falso
# escrito la MOVE o corte do `rpartition`, o resumo verdadeiro (com a linha ERROR)
# some junto, e o gate aprovaria como FORTE citando um teste que nao existe. O
# `atexit` escreve depois de a captura do pytest ja ter sido desmontada.
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


def _pytest_em(tmp_path, nome, fonte, addopts=""):
    """Roda o pytest de verdade em `tmp_path`. `PYTEST_ADDOPTS` e fixado sempre —
    herdado do ambiente, ele mudaria a saida e o teste mediria outra coisa."""
    (tmp_path / nome).write_text(fonte)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", nome], cwd=tmp_path,
                       env={**os.environ, "PYTEST_ADDOPTS": addopts},
                       capture_output=True, text=True)
    return r.returncode, r.stdout  # so o stdout, igual ao que a `roda_pytest` entrega


def test_erro_de_fixture_e_prova_FRACA_mesmo_imprimindo_FAILED(tmp_path):
    """rc=1 igual ao da asserção, e um `FAILED ` na coluna 0 vindo do stdout da
    fixture. Ainda assim: nenhum corpo de teste rodou, entao prova FRACA."""
    rc, saida = _pytest_em(tmp_path, "test_engana.py", _FIXTURE_QUE_ESTOURA_E_ENGANA)
    assert rc == coluna_dupla.PYTEST_FALHA  # o exit code NAO delata que nada rodou
    assert "\nFAILED " in saida  # o print enganoso esta mesmo la, na coluna 0
    codigo, relatorio = coluna_dupla.veredito(rc, saida, 0)
    assert codigo == 0
    assert "prova FRACA" in relatorio


def test_assercao_que_falha_e_prova_FORTE(tmp_path):
    """Controle positivo: sem ele, um criterio que devolvesse [] sempre passaria no
    teste acima e o gate classificaria TODA prova como fraca — pior que o bug."""
    rc, saida = _pytest_em(tmp_path, "test_assercao_falha.py", _ASSERCAO_QUE_FALHA)
    assert rc == coluna_dupla.PYTEST_FALHA  # o mesmo rc do caso acima; o resumo e que difere
    codigo, relatorio = coluna_dupla.veredito(rc, saida, 0)
    assert codigo == 0
    assert "prova FORTE" in relatorio
    assert "test_roda_e_falha" in relatorio


def test_sem_resumo_o_gate_reprova_em_vez_de_carimbar_FRACA(tmp_path):
    """`-rN` herdado do ambiente apaga o resumo inteiro. Sem ramo proprio, o gate
    degradaria para sempre-FRACA em silencio: um APROVADO que nao mediu nada."""
    rc, saida = _pytest_em(tmp_path, "test_assercao_falha.py", _ASSERCAO_QUE_FALHA,
                           addopts="-rN")
    assert rc == coluna_dupla.PYTEST_FALHA
    assert coluna_dupla.CABECALHO_RESUMO not in saida  # o resumo sumiu mesmo
    codigo, relatorio = coluna_dupla.veredito(rc, saida, 0)
    assert codigo == 1
    assert "REPROVADO" in relatorio and "PYTEST_ADDOPTS" in relatorio


def test_sem_resumo_a_isca_da_fixture_nao_vira_desfecho(tmp_path):
    """A subguarda `if achou` do `rpartition`: sem cabecalho, a cauda e a saida
    INTEIRA e o `print("FAILED ...")` da fixture passaria a valer por desfecho.

    E o unico caso do grupo em que ela decide — o `-rN` do teste acima reprova com
    e sem a guarda, porque aquela saida nao tem nenhuma linha `FAILED ` na coluna
    0. Medido: sem o `if achou`, aqui sai `APROVADO, prova FORTE` (exit 0) citando
    um teste que nunca chegou a rodar."""
    rc, saida = _pytest_em(tmp_path, "test_engana.py", _FIXTURE_QUE_ESTOURA_E_ENGANA,
                           addopts="-rN")
    assert rc == coluna_dupla.PYTEST_FALHA
    assert coluna_dupla.CABECALHO_RESUMO not in saida  # sem cabecalho...
    assert "\nFAILED " in saida  # ...e com a isca na coluna 0, vinda do stdout da fixture
    codigo, relatorio = coluna_dupla.veredito(rc, saida, 0)
    assert codigo == 1
    assert "REPROVADO" in relatorio


def test_cabecalho_sem_desfecho_reprova(tmp_path):
    """`-rs` e um `-r` PARCIAL: mantem o cabecalho e tira as linhas de desfecho.
    O caso acima (cabecalho ausente) nao cobre este — aqui o cabecalho esta de pe,
    e mesmo assim zero FAILED e zero ERROR. Um teste RODOU e falhou; carimbar
    FRACA ("nenhum chegou a rodar") seria mentira, e APROVADO sem ter medido."""
    rc, saida = _pytest_em(tmp_path, "test_skip_e_falha.py", _SKIP_MAIS_ASSERCAO_QUE_FALHA,
                           addopts="-rs")
    assert rc == coluna_dupla.PYTEST_FALHA
    assert coluna_dupla.CABECALHO_RESUMO in saida  # o cabecalho continua la...
    resumo = saida.rpartition(coluna_dupla.CABECALHO_RESUMO)[2]
    assert "FAILED " not in resumo and "ERROR " not in resumo  # ...sem desfecho nenhum
    codigo, relatorio = coluna_dupla.veredito(rc, saida, 0)
    assert codigo == 1
    assert "REPROVADO" in relatorio


def test_stderr_nao_alimenta_o_veredito(monkeypatch, tmp_path):
    """Exercita a `roda_pytest`, nao so a `veredito`: o furo estava na linha que
    junta os streams, e um teste que monta a string a mao nunca o veria.

    O payload escreve pelo `atexit` um cabecalho de resumo FALSO no stderr. Com
    `stdout + stderr` esse cabecalho vira o ultimo, o corte do `rpartition` anda
    para depois dele e o gate aprova FORTE citando `test_que_nunca_existiu`."""
    monkeypatch.setenv("PYTEST_ADDOPTS", "")  # a `roda_pytest` herda o ambiente
    (tmp_path / "test_hdr.py").write_text(_ATEXIT_QUE_ENGANA_PELO_STDERR)
    rc, saida = coluna_dupla.roda_pytest(str(tmp_path), sys.executable, ["test_hdr.py"])
    assert rc == coluna_dupla.PYTEST_FALHA
    assert "test_que_nunca_existiu" not in saida  # o stderr ficou fora do que decide
    codigo, relatorio = coluna_dupla.veredito(rc, saida, 0)
    assert codigo == 0
    assert "prova FRACA" in relatorio
    assert "test_que_nunca_existiu" not in relatorio
