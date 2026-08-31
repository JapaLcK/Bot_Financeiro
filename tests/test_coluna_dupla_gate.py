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


def _gate(lab, *args):
    """O gate de verdade, sobre o laboratorio. `PYTEST_ADDOPTS` e fixado pelo mesmo
    motivo do `_pytest_em`: herdado, mudaria a saida das DUAS colunas. `COLUMNS`
    porque o pytest CORTA a linha do resumo na largura do terminal — medido: sem
    ele, o padrao de 80 devolvia `- AssertionError: assert 0 =...` e o assert sobre
    o motivo do vermelho falhava por formatacao, nao por comportamento."""
    return subprocess.run([sys.executable, str(_CAMINHO), "--antes", "HEAD~1", "--depois", "HEAD", *args],
                          cwd=lab, env={**os.environ, "PYTEST_ADDOPTS": "", "COLUMNS": "200"},
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
