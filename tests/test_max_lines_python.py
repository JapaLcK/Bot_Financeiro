"""Portão de tamanho para arquivo Python — o par do `quality/max-lines` do JS.

Teto de 350 linhas para arquivo NOVO de PRODUÇÃO. O de JavaScript já existia
(`eslint.config.mjs`, em `error`); este roda no `pytest -q`, que já é bloqueante,
então não há step novo no CI. A dívida herdada mora em `_max_lines_baseline.py`,
e a lista é um ratchet: legado que encolher tem de SAIR de lá.

**Arquivo de TESTE (`tests/`, `harness_tests/`) acima do teto é AVISO, não
reprovação** — o mesmo tratamento que o lado JS já dá a `tests/frontend/**/*.mjs`
(`quality/max-lines` em `warn` para esse padrão). O aviso sai no "warnings
summary" do `pytest`, via `warnings.warn`. Por isso a lista de legado não tem
mais entradas de `tests/`/`harness_tests/`: deixaram de precisar de isenção.

**Uma política, um caminho.** `_varrer()` é o veredito e é a mesma função que as
sondas do fim do arquivo executam; `_lidos()` só monta o universo a partir do
disco, e a sonda ponta a ponta o cobre por um repositório de brinquedo. Duas
implementações da mesma regra é o que o CLAUDE.md §0.7 proíbe, e aqui já custou
duas rodadas de revisão — o registro está em `docs/armadilhas.md`, seção
"Portões de tamanho: o que eles prendem, e o que NÃO prendem".

O QUE ESTE ARQUIVO NÃO PRENDE — leia antes de confiar nele:
  * **Predicado arbitrário.** As sondas nomeiam os predicados perigosos por
    HISTÓRICO (basename e diretório, os do template TypeScript). Um
    `rel.startswith("handlers/")` novo em `_lidos` ou em `_estoura` passa com as
    sondas verdes — medido em 2026-09-07, tabela em `docs/armadilhas.md`. Enumeração
    não cobre arbitrário, e o portão de JS que este espelha tem o mesmo piso
    (`eslint-rules/core-rules.cjs:67-72`). A defesa aí é revisão de diff.
  * **Tamanho não é coesão.** Arquivo de `LEGADOS` pode ir de 400 para 4000
    linhas sem vermelho, e um de 349 linhas ilegível passa limpo.
  * **O universo é o que o git RASTREIA.** Arquivo novo só é medido depois do
    `git add`; no CI isso é vácuo, porque lá tudo está commitado.
  * `_linhas` conta só `b"\n"`: fonte com terminador `\r` PURO escapa inteira
    (CRLF, o caso real, é contado certo). Symlink `.py` para diretório dá
    `IsADirectoryError` e `RAIZ` fora de repo git dá `CalledProcessError` —
    nenhum alcançável aqui, e os dois são erro ruidoso, não veredito silencioso.

Rodar:  .venv/bin/python -m pytest tests/test_max_lines_python.py -q
"""

from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

import pytest

from _max_lines_baseline import LEGADOS

TETO = 350
RAIZ = Path(__file__).resolve().parent.parent


def _linhas(dados: bytes) -> int:
    """Linhas VISÍVEIS. Idêntico ao `wc -l` para quem termina em `\\n`, e é o ponto:
    a mensagem de falha cita o mesmo número do comando de remedição.

    Sem `\\n` final o portão conta a linha visível e o `wc -l` diz um a menos — a
    MESMA divergência do lado JS (`eslint-rules/core-rules.cjs`), de propósito:
    uma definição de linha para os dois portões (CLAUDE.md §0.7). O `and dados`
    faz arquivo vazio valer 0 em vez de 1.
    """
    return dados.count(b"\n") + (1 if dados and not dados.endswith(b"\n") else 0)


def _grande(dados: bytes) -> bool:
    """Acima do teto, ANTES de qualquer isenção. Única implementação do número."""
    return _linhas(dados) > TETO


def _estoura(caminho: str, dados: bytes) -> bool:
    """O veredito por arquivo. Única implementação da ISENÇÃO, que é por CAMINHO
    EXATO: sem `basename` e sem prefixo de diretório, porque foi por causa dessas
    duas que `frontend/index.js` passou com 404 linhas. As sondas prendem as duas.
    """
    return _grande(dados) and caminho not in LEGADOS


def _e_arquivo_de_teste(caminho: str) -> bool:
    """Único predicado teste/não-teste do portão. PRIMEIRO componente do
    caminho, exato — os dois diretórios de teste do repo (`tests/`,
    `harness_tests/`). `tests_x/a.py`, `core/tests/a.py` e `testsuite.py` NÃO
    casam: prefixo frouxo (`startswith("tests")`, `"tests/" in c`, basename
    `test_*`) reabriria a mesma isenção-por-diretório que já custou duas
    rodadas de revisão (ver módulo, e `docs/armadilhas.md`).
    """
    return caminho.split("/", 1)[0] in ("tests", "harness_tests")


def _varrer(arquivos: dict[str, bytes]) -> tuple[list[str], list[str], list[str]]:
    """`{caminho: conteúdo}` -> `(faltando, sobrando, avisos)`. O caminho de
    enforcement, puro sobre bytes para as sondas rodarem ESTA função sem
    escrever no disco (é o que o `lintText` de caminho virtual faz no portão
    de JS).

    `faltando` é só código de PRODUÇÃO estourado; `avisos` é arquivo de TESTE
    estourado — não reprova, espelha o `warn` do lado JS para `tests/frontend/`.

    `sobrando` usa `_grande` e NÃO `_estoura`: `_estoura` é sempre falso para quem
    está em `LEGADOS`, e a pergunta do ratchet é outra — "este legado ainda é
    grande?". Caminho ausente do dicionário (apagado, renomeado, ou escrito como
    glob) vira `b""`, não é grande, e por isso é reportado.
    """
    estourados = [c for c, d in arquivos.items() if _estoura(c, d)]
    faltando = sorted(c for c in estourados if not _e_arquivo_de_teste(c))
    avisos = sorted(c for c in estourados if _e_arquivo_de_teste(c))
    sobrando = sorted(c for c in LEGADOS if not _grande(arquivos.get(c, b"")))
    return faltando, sobrando, avisos


def _rastreados(raiz: Path = RAIZ) -> list[str]:
    """Universo = o que o git rastreia, nunca `rglob` — num worktree
    (`.claude/worktrees/<nome>/`) o `rglob` engole o repo inteiro mais `.venv`
    (armadilha registrada em `tests/test_frontend_assets_e_rotas.py`).

    `-z`: sem ele o git CITA e escapa em octal caminho com acento
    (`"caf\\303\\251.py"`), que não existe em disco e sai do universo em silêncio.
    `surrogateescape`: nome não-UTF-8 é impossível no APFS e possível no ext4 do
    runner; sem ele o portão morre em `UnicodeDecodeError` em vez de veredito.
    """
    saida = subprocess.run(
        ["git", "-C", str(raiz), "ls-files", "-z", "*.py"],
        capture_output=True,
        check=True,
    ).stdout
    return [c for c in saida.decode(errors="surrogateescape").split("\0") if c]


def _lidos(raiz: Path = RAIZ) -> dict[str, bytes]:
    """Universo do disco. O `raiz` existe para a sonda ponta a ponta rodar ESTA
    função contra um repositório de brinquedo."""
    arquivos = {}
    for rel in _rastreados(raiz):
        arq = raiz / rel
        if not arq.exists():
            # Rastreado e ausente do working tree = deleção não estagiada. Sem
            # isto o portão morre em `FileNotFoundError` em vez de dar veredito.
            continue
        arquivos[rel] = arq.read_bytes()
    return arquivos


def test_nenhum_python_novo_passa_de_350_linhas():
    """Portão, ratchet e proibição de glob no mesmo teste.

    `faltando` é o portão: arquivo de PRODUÇÃO novo acima do teto.
    `sobrando` é o ratchet e cobre três coisas de uma vez — legado que encolheu,
    nome morto, e nome que virou glob/prefixo e por isso não casa mais nada.
    `avisos` é arquivo de TESTE acima do teto: não reprova, só aparece no
    warnings summary (mesmo tratamento do lado JS para `tests/frontend/`).
    """
    faltando, sobrando, avisos = _varrer(_lidos())

    if avisos:
        warnings.warn(
            f"{len(avisos)} arquivo(s) de TESTE acima de {TETO} linhas (aviso, "
            f"não reprova): {avisos}."
        )

    assert not faltando, (
        f"{len(faltando)} arquivo(s) Python acima de {TETO} linhas fora da linha "
        f"de base: {faltando}.\n"
        f"QUEBRE O ARQUIVO em módulos por assunto (CLAUDE.md §0.5). Acrescentar o "
        f"nome em LEGADOS (tests/_max_lines_baseline.py) NÃO é a saída: a lista é "
        f"a dívida herdada, não a porta de entrada."
    )
    assert not sobrando, (
        f"{len(sobrando)} nome(s) em LEGADOS que não estão mais acima de {TETO} "
        f"linhas: {sobrando}.\n"
        f"APAGUE cada uma dessas linhas de tests/_max_lines_baseline.py. Um nome "
        f"sobra por três motivos e os três pedem a mesma edição: o arquivo foi "
        f"quebrado (parabéns, tranque o ganho), o arquivo foi apagado/renomeado, "
        f"ou a entrada foi escrita como glob/prefixo e não casa caminho nenhum."
    )


# 351 linhas: uma acima do teto. É o que prende o 350 — com uma sonda de 500,
# subir TETO para qualquer valor até 499 passaria despercebido.
UMA_ACIMA = b"x\n" * 350 + b"x"
NO_TETO = b"x\n" * 350

# Os nomes que o template TypeScript isentava sozinho: por basename (`index`,
# `constants`, `types`, `*.config.*`) e por diretório (`generated/`, `fixtures/`,
# `migrations/`, `mocks/`). Nenhum deles pode voltar a ser isenção.
SONDAS_SEM_ISENCAO = [
    "__init__.py",
    "constants.py",
    "types.py",
    "settings.py",
    "core/conftest.py",
    "core/generated/g.py",
    "core/migrations/0001_x.py",
    "core/fixtures/f.py",
]


def test_sondas_nao_colidem_com_a_linha_de_base():
    """Higiene: sonda que estivesse em LEGADOS mediria a isenção, não o teto.

    Não é o teste que fecha esse buraco — a colisão já sai vermelha nas sondas
    abaixo, porque a isenção inverte o que elas afirmam. O que este teste entrega
    é a MENSAGEM: `constants.py` está na lista, em vez de "passou silencioso".
    """
    colisao = sorted(set(SONDAS_SEM_ISENCAO) & LEGADOS)
    assert not colisao, (
        f"caminho(s) de sonda dentro de LEGADOS: {colisao}. Troque o nome da "
        f"sonda aqui, ou tire o nome de tests/_max_lines_baseline.py."
    )


def test_legados_nao_tem_entrada_de_arquivo_de_teste():
    """Higiene: reinserir `tests/x.py` em LEGADOS é silencioso — não reprova
    nada (teste acima do teto já é aviso) e só suprime o aviso daquele
    arquivo. Usa `_e_arquivo_de_teste`, a mesma fonte de verdade do
    enforcement (CLAUDE.md §0.7 — uma política, um predicado)."""
    intrusos = sorted(c for c in LEGADOS if _e_arquivo_de_teste(c))
    assert not intrusos, (
        f"entrada(s) de arquivo de TESTE em LEGADOS: {intrusos}. Arquivo de "
        f"teste acima do teto já é aviso, não precisa de isenção — tire a "
        f"linha de tests/_max_lines_baseline.py."
    )


def test_350_linhas_passam_e_351_reprovam():
    assert _varrer({"core/qualquer_coisa.py": NO_TETO})[0] == []
    assert _varrer({"core/qualquer_coisa.py": UMA_ACIMA})[0] == [
        "core/qualquer_coisa.py"
    ]


def test_isencao_por_nome_e_por_diretorio_nao_voltam():
    faltando, _, _ = _varrer({c: UMA_ACIMA for c in SONDAS_SEM_ISENCAO})
    assert faltando == sorted(SONDAS_SEM_ISENCAO), (
        f"passaram silenciosos: {sorted(set(SONDAS_SEM_ISENCAO) - set(faltando))}"
    )


def test_arquivo_de_teste_acima_do_teto_vira_aviso_nao_reprova():
    """Controle positivo do afrouxamento: `tests/` e `harness_tests/` acima do
    teto saem de `faltando` e entram em `avisos` — como o JS trata
    `tests/frontend/**/*.mjs` em `warn`."""
    arquivos = {"tests/test_x.py": UMA_ACIMA, "harness_tests/test_y.py": UMA_ACIMA}
    faltando, _, avisos = _varrer(arquivos)
    assert faltando == []
    assert avisos == sorted(arquivos)


def test_predicado_de_teste_e_prefixo_exato_de_diretorio_de_topo():
    """Controle negativo: nada que só PARECE `tests/` vira aviso. Um predicado
    frouxo (`startswith("tests")`, `"tests/" in c`, basename `test_*`)
    deixaria estes quatro passarem despercebidos para `avisos`; aqui continuam
    em `faltando`. `scripts/test_z.py` prende o basename especificamente —
    `scripts/test_email.py` é real neste repo e precisa continuar bloqueado."""
    arquivos = {
        "tests_x/a.py": UMA_ACIMA,
        "core/tests/a.py": UMA_ACIMA,
        "testsuite.py": UMA_ACIMA,
        "scripts/test_z.py": UMA_ACIMA,
    }
    faltando, _, avisos = _varrer(arquivos)
    assert faltando == sorted(arquivos)
    assert avisos == []


def test_legado_de_5000_linhas_nao_e_reportado():
    """Controle positivo: o portão não reprova tudo.

    Sem este caso, o grupo inteiro passaria num `_varrer` que reportasse todo
    arquivo — que é pior que não ter portão.
    """
    if not LEGADOS:
        pytest.skip("dívida quitada: sem legado não há isenção para exercitar")
    legado = sorted(LEGADOS)[0]
    assert _varrer({legado: b"x\n" * 5000})[0] == []


def test_ratchet_reporta_legado_que_encolheu_ou_sumiu():
    """Os DOIS ramos do `sobrando`, porque eles pegam coisas diferentes.

    Presente-e-pequeno é o legado que alguém quebrou. Ausente é o `arquivos.get(c,
    b"")` do `_varrer`, e é o único que pega as outras três: arquivo apagado,
    arquivo renomeado, e entrada escrita como GLOB (`tests/*`), que não casa
    caminho nenhum e por isso nunca apareceria no universo.
    """
    if not LEGADOS:
        pytest.skip("dívida quitada: sem legado não há ratchet para exercitar")
    legado = sorted(LEGADOS)[0]
    assert legado in _varrer({legado: NO_TETO})[1], "ramo presente-e-pequeno"
    assert legado in _varrer({})[1], "ramo ausente (apagado/renomeado/glob)"


def _repo_de_brinquedo(tmp_path: Path, arquivos: dict[str, bytes]) -> Path:
    """`git add` sem commit — o `git ls-files` lê o índice. Sonda o caminho real
    sem tocar no índice DESTE repositório."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for nome, dados in arquivos.items():
        alvo = tmp_path / nome
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(dados)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def test_ponta_a_ponta_pelo_disco_e_pelo_git(tmp_path):
    """Cobre `_lidos` pelo caminho real: git -> `_rastreados` -> `_lidos` -> `_varrer`.

    As outras sondas montam o dicionário à mão e por isso não veem `_lidos`; uma
    isenção por basename ou por diretório escondida LÁ deixava as outras verdes.
    Esta prende os dois predicados, e SÓ eles — predicado arbitrário
    (`rel.startswith("handlers/")`) continua passando, medido e registrado em
    `docs/armadilhas.md`.
    """
    raiz = _repo_de_brinquedo(
        tmp_path,
        {
            "constants.py": UMA_ACIMA,      # isenção por BASENAME não volta
            "generated/g.py": UMA_ACIMA,    # isenção por DIRETÓRIO não volta
            "pequeno.py": NO_TETO,          # e o portão não reprova tudo
        },
    )
    assert _varrer(_lidos(raiz))[0] == ["constants.py", "generated/g.py"]


def test_nome_com_acento_sobrevive_a_leitura(tmp_path):
    """O que o `-z` compra. Repo de brinquedo: nome DESTE repo não tem acento.

    Dois tetos, medidos: (1) o vermelho dela depende do gitconfig GLOBAL — com
    `core.quotepath = false` (comum em dotfiles) apagar o `-z` a deixa VERDE; no
    CI, que usa o default, o controle vale. (2) o `-z` compra duas coisas e esta
    prova uma; a outra é nome com `\\n` embutido, patológico demais para virar sonda.
    """
    raiz = _repo_de_brinquedo(tmp_path, {"café da manhã.py": UMA_ACIMA})

    (nome,) = _rastreados(raiz)
    assert '"' not in nome and "\\" not in nome, f"caminho citado/escapado: {nome!r}"
    assert (raiz / nome).exists(), f"não existe em disco: {nome!r}"
