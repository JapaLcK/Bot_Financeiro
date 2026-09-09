"""O PR 1b-A é a fundação INERTE do Pix anual (docs/plano_pix_anual_asaas.md).

A propriedade que este arquivo existe para provar, e que é o critério de
aceitação do dono:

> **nenhuma versão intermediária deve conseguir emitir uma cobrança sem já
> possuir o caminho que recebe o pagamento e concede o acesso.**

Checkout e webhook não se separam — os dois são o 1b-B. Enquanto isso, os quatro
módulos novos existem e ninguém os chama.

**"Ninguém os chama" vale para os `.py` de PRODUÇÃO — não para o repositório
inteiro.** A versão anterior dizia "repositório INTEIRO" e era falso: o universo
deste portão é `git ls-files '*.py' | grep -vc '^tests/'`, e o repositório
rastreia bem mais que isso (`git ls-files | wc -l`). Foi por essa frase que o
Manager entrou (ver a cegueira do `startCommand`). Alcance se mede rodando a
varredura na hora, nunca de memória — nem de número escrito aqui, que envelhece
em silêncio (§2).

## Quatro caminhos de alcance, um arquivo cada

  1. **rota** — `tests/test_pix_rota_inerte.py` (fechá-lo exigiu TRÊS
     introspecções);
  2. **import** — AQUI: nenhum módulo de produção importa os módulos novos. É
     como o próprio app chegaria, inclusive por `background_tasks` e pelo loop
     de 60 s — os dois precisam do import para chamar;
  3. **destino** — `tests/test_pix_destino_inerte.py`, que vê o HOST e não o
     nome do módulo, e cujo universo é todo rastreado fora de `tests/`
     (`git ls-files | grep -vc '^tests/'`), não só `.py`;
  4. **boot** — `tests/test_pix_ddl_sem_escrita.py`.

Arquivos separados porque os universos e os mecanismos são diferentes: este lê
os `.py` com `ast`; o de destino lê como texto todo rastreado, de qualquer
formato.

**A varredura de import se AUTOVALIDA por dois caminhos**, os dois necessários:
`_quem_importa` sobre `tests/` (tem de vir NÃO-VAZIA) e `_importa_algum` sobre
uma TABELA de fontes sintéticas, forma a forma. O segundo existe porque o
primeiro é cego ao que os testes não escrevem — os arquivos desta leva importam
só na forma ABSOLUTA, então apagar o ramo do import RELATIVO deixava as duas
metades verdes com um chamador de produção real em `db/`. Medido pelo Tester.

`CHAMADORES_PERMITIDOS` nasce como allowlist VAZIA de propósito: o 1b-B
acrescenta uma linha em vez de apagar o teste, e o portão passa a medir "só
estes chamam", que continua sendo uma propriedade.

## CEGUEIRAS DECLARADAS — o que sobra descoberto depois dos três portões

  * **import dinâmico** (`importlib.import_module("db." + nome)`): `ast` vê a
    árvore, não o valor. Nenhum existe hoje.
  * **NÃO-`.py` rastreado**: este portão precisa de `ast`, então todo arquivo
    rastreado que não é Python (`git ls-files | grep -vc '[.]py$'`) fica fora
    dele. Não é teórico — o Manager plantou `railway.pix-manager-probe.toml`
    nomeando `api.asaas.com` e as três tabelas, com 49 verdes, e o `Procfile`
    (`web: python launch.py`) já dá o start command de produção num arquivo que
    o `ast` não lê (o repositório JÁ roda Python por um caminho não-`.py`). Quem
    fecha esse caso é o portão de DESTINO, que varre todo formato; aqui fica
    declarado porque é ESTE portão que a frase "ninguém importa" pode fazer
    parecer completo.
  * **arquivo NÃO rastreado**: o universo é `git ls-files`, então um script
    Python fora do git chamado por um `startCommand` escapa dos dois. Esta
    cegueira CRESCEU: o `account-deletion-job` migrou para configuração nativa
    do painel do Railway (Config as Code descontinuado), então o start command
    dele não está mais em arquivo nenhum — só no painel.

Para todas, o método é revisão de diff, não mais varredura.
"""

import ast
import pathlib
import subprocess

import pytest

# Os três módulos que NÃO podem ser alcançáveis nesta fatia. `pix_pricing` fica
# de fora da lista de propósito: ele é função pura, não emite cobrança nem
# concede acesso, então importá-lo não violaria o critério do dono — mas
# ninguém o importa hoje, e há teste próprio para isso mais abaixo.
MODULOS_INERTES = ("core.services.asaas", "db.pix_charges", "db.webhook_outbox")

# Allowlist do portão de IMPORT: caminhos (POSIX) que PODEM importar os módulos
# acima. É um conjunto de caminhos, não um mapa — quem importa o quê fica no
# portão, e a granularidade por módulo só vale a pena quando houver mais de um
# chamador. Vazia no 1b-A; o 1b-B acrescenta o router do checkout e o do webhook
# JUNTOS (emitir cobrança sem o caminho que recebe o pagamento é o que o corte
# proíbe).
CHAMADORES_PERMITIDOS: frozenset[str] = frozenset()


RAIZ = pathlib.Path(__file__).resolve().parent.parent


def _rastreados(padrao: str | None = None) -> list[str]:
    """`git ls-files` — o universo é o que o git RASTREIA (nunca o working tree,
    que traria `.venv`). Mesmo critério do `test_max_lines_python.py`."""
    cmd = ["git", "ls-files"] + ([padrao] if padrao else [])
    saida = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True,
                           check=True).stdout
    return [linha for linha in saida.splitlines() if linha.strip()]


def _arquivos_rastreados() -> list[str]:
    """Só `.py`: é o universo do portão de IMPORT, que precisa de `ast`."""
    return _rastreados("*.py")


def _producao() -> list[str]:
    return [rel for rel in _arquivos_rastreados() if not rel.startswith("tests/")]


def _importa_algum(arvore: ast.AST, modulos: tuple[str, ...]) -> set[str]:
    """Módulos de `modulos` importados nesta árvore, por QUALQUER das formas.

    As quatro que este repositório escreve, e a tabela
    `test_cada_forma_de_import_e_reconhecida` exercita uma a uma:

        import db.pix_charges
        from db.pix_charges import x
        from db import pix_charges
        from .pix_charges import x      # vizinho de pacote, dentro de db/
    """
    achados: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            for alias in no.names:
                achados.update(m for m in modulos
                               if alias.name == m or alias.name.startswith(m + "."))
        elif isinstance(no, ast.ImportFrom):
            base = no.module or ""
            for m in modulos:
                pacote, _, folha = m.rpartition(".")
                if base == m or base.startswith(m + "."):
                    achados.add(m)
                elif base == pacote and any(a.name == folha for a in no.names):
                    achados.add(m)
                elif no.level and (base == folha
                                   or any(a.name == folha for a in no.names)):
                    achados.add(m)
    return achados


def _quem_importa(modulos: tuple[str, ...], raiz: str) -> dict[str, set[str]]:
    """caminho relativo → módulos inertes importados, dentro de `raiz`.

    `raiz` é prefixo: `""` (menos `tests/`) para produção, `"tests/"` para a
    autovalidação. UMA função para os dois lados — duas, e a que varre produção
    quebraria sozinha com a outra verde.
    """
    achados: dict[str, set[str]] = {}
    alvos = _producao() if not raiz else [r for r in _arquivos_rastreados()
                                          if r.startswith(raiz)]
    for rel in alvos:
        try:
            arvore = ast.parse((RAIZ / rel).read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        importados = _importa_algum(arvore, modulos)
        if importados:
            achados[rel] = importados
    return achados


# ── 1. import, e as DUAS autovalidações ──────────────────────────────────────

def test_nenhum_modulo_de_producao_importa_os_modulos_inertes():
    """A propriedade central: os módulos novos não são alcançáveis a partir de
    NADA que roda em produção — nem por rota, nem por `background_tasks`, nem
    pelo loop de 60 s, nem por um import de conveniência no topo de um router.
    """
    achados = _quem_importa(MODULOS_INERTES, raiz="")
    fora = {rel: sorted(m) for rel, m in achados.items()
            if rel not in CHAMADORES_PERMITIDOS}
    assert not fora, (
        "1b-A deixou de ser inerte — módulo de produção importando o Pix: "
        + repr(dict(sorted(fora.items())))
        + ". Se isto é o 1b-B, acrescente o caminho a CHAMADORES_PERMITIDOS "
          "(e traga o webhook junto: emitir cobrança sem o caminho que recebe o "
          "pagamento é o que o corte proíbe)."
    )


FORMAS_DE_IMPORT = [
    ("import absoluto", "import db.pix_charges"),
    ("import absoluto com as", "import db.pix_charges as pc"),
    ("from módulo", "from db.pix_charges import criar_cobranca"),
    ("from pacote", "from db import pix_charges"),
    ("relativo com folha", "from .pix_charges import criar_cobranca"),
    ("relativo pelo pacote", "from . import pix_charges"),
]


@pytest.mark.parametrize("rotulo,fonte", FORMAS_DE_IMPORT,
                         ids=[r for r, _ in FORMAS_DE_IMPORT])
def test_cada_forma_de_import_e_reconhecida(rotulo, fonte):
    """AUTOVALIDAÇÃO 1 — a forma, uma a uma, sem depender do estilo dos testes.

    A varredura sobre `tests/` (abaixo) é cega ao que os testes não escrevem: os
    cinco arquivos desta leva importam só na forma ABSOLUTA. Apagando o ramo do
    import RELATIVO, aquela metade continuava verde — com um chamador de
    produção real em `db/leitor_pix.py` e o portão passando. Medido pelo Tester.

    O relativo não é hipótese: `db/pix_charges.py` já faz
    `from .connection import get_conn`.
    """
    assert _importa_algum(ast.parse(fonte), MODULOS_INERTES) == {"db.pix_charges"}, (
        f"a varredura não reconhece {rotulo!r}: {fonte!r}"
    )


def test_import_de_modulo_parecido_nao_e_confundido():
    """POSITIVO do par: a varredura não pode casar por substring. Sem ele, um
    `_importa_algum` que devolvesse tudo passaria na tabela acima, e o portão de
    produção viveria vermelho por ruído até alguém desligá-lo."""
    for inocente in ("from db.pix_pricing import x", "import db.charges",
                     "from db import connection", "import core.services.asaas_docs"):
        assert _importa_algum(ast.parse(inocente), MODULOS_INERTES) == set(), inocente


def test_a_varredura_acha_os_imports_que_os_testes_fazem():
    """AUTOVALIDAÇÃO 2 — a mesma `_quem_importa` do portão, sobre `tests/`, onde
    os imports existem de verdade.

    Regex/AST quebrada, arquivo movido, `git ls-files` sem saída: qualquer uma
    faz este teste ficar vermelho ANTES de o portão de produção passar por vácuo.

    Os três módulos, um a um — e isto também valida os NOMES em
    `MODULOS_INERTES`: um typo (`db.pix_chargez`) nunca seria encontrado e cai
    aqui, em vez de deixar o portão verde para sempre.
    """
    achados = _quem_importa(MODULOS_INERTES, raiz="tests/")
    assert achados, (
        "a varredura não achou import nenhum em tests/ — ela está quebrada, e o "
        "portão de produção está passando por vácuo, não por inércia"
    )
    encontrados = set().union(*achados.values())
    assert encontrados == set(MODULOS_INERTES), (
        f"a varredura só enxergou {sorted(encontrados)}; sem cobertura: "
        f"{sorted(set(MODULOS_INERTES) - encontrados)}"
    )


def test_pix_pricing_tambem_nao_tem_chamador():
    """`pix_pricing` fica fora de `MODULOS_INERTES` porque importá-lo não
    emitiria cobrança — é aritmética pura. Mas também não tem chamador nesta
    fatia, e medir isso separado impede alguém de ligar o preço à tela achando
    que "pricing não conta"."""
    achados = _quem_importa(("core.services.pix_pricing",), raiz="")
    assert not achados, f"pix_pricing ganhou chamador em produção: {sorted(achados)}"


def test_db_init_nao_reexporta_os_modulos_novos():
    """Precedente do 1a. Re-export é ALCANCE: `from db import *` traria o
    módulo para dentro de qualquer arquivo."""
    fonte = (RAIZ / "db" / "__init__.py").read_text(encoding="utf-8")
    for nome in ("pix_charges", "webhook_outbox"):
        assert nome not in fonte, f"db/__init__.py re-exporta {nome}"
