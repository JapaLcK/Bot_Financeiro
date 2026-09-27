"""PR-E — a ferramenta do operador lê ENTRE usuários, então nenhum módulo de
produção pode importá-la (CLAUDE.md §0, isolamento por usuário).

Controles negativos medidos (cada um vermelho, revertido depois):
  (a) `from .open_finance_diagnostico import classifica_item` em `db/__init__.py`
      (o reexport do repo: exporia o módulo a toda rota como `db.X`);
  (b) `from db.open_finance_diagnostico import listar_sem_conexao` num arquivo
      novo, não rastreado, em `api/`;
  (c) `importlib.import_module("db.open_finance_diagnostico")` num arquivo novo
      em `core/`.
CLASSE CEGA: import montado em tempo de execução (`"db." + nome`) não é literal
e passa. Revisão de diff cobre isso.
"""
from __future__ import annotations


def _importa_alvo(arq, rel: str, alvos: set[str]) -> list[int]:
    """Linhas de `arq` que importam um dos `alvos`: `import X`, `from X import y`,
    `from pacote import modulo`, o RELATIVO resolvido pelo pacote do arquivo
    (`db/__init__.py` + `.open_finance_diagnostico`), e string literal igual ao
    alvo (`importlib.import_module("db.open_finance_diagnostico")`, `__import__`)."""
    import ast

    partes = rel[:-3].split("/")
    pacote = partes[:-1]            # `db/__init__.py` e `db/x.py` → pacote `db`
    achados = []
    for no in ast.walk(ast.parse(arq.read_text(encoding="utf-8"))):
        nomes: list[str] = []
        if isinstance(no, ast.Import):
            nomes = [a.name for a in no.names]
        elif isinstance(no, ast.ImportFrom):
            base = pacote[:len(pacote) - (no.level - 1)] if no.level else []
            mod = ".".join(base + ([no.module] if no.module else []))
            nomes = [mod] + [f"{mod}.{a.name}" for a in no.names]
        elif isinstance(no, ast.Constant) and isinstance(no.value, str):
            nomes = [no.value]
        if alvos & set(nomes):
            achados.append(no.lineno)
    return achados


def test_nenhum_modulo_de_producao_importa_a_ferramenta_do_operador():
    """Leitura ENTRE usuários só pelo terminal: nenhum módulo de produção importa
    o diagnóstico nem o script — nem por reexport em `db/__init__.py`, nem por
    `importlib`. Varre o disco (pega arquivo ainda não rastreado), fora de
    `tests/`, `scripts/`, diretórios ocultos (`.venv`, `.claude`, `.time-dev`,
    worktrees) e `node_modules`."""
    import os
    from pathlib import Path

    alvos = {"db.open_finance_diagnostico", "scripts.of_itens_operador"}
    raiz = Path(__file__).resolve().parent.parent
    fora = {"tests", "scripts", "node_modules", "__pycache__"}
    achados = []
    for pasta, dirs, arqs in os.walk(raiz):
        dirs[:] = [d for d in dirs if d not in fora and not d.startswith(".")]
        for nome in arqs:
            arq = Path(pasta) / nome
            rel = arq.relative_to(raiz).as_posix()
            if nome.endswith(".py") and rel != "db/open_finance_diagnostico.py":
                achados += [f"{rel}:{n}" for n in _importa_alvo(arq, rel, alvos)]
    assert achados == [], achados
