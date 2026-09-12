"""O portão de IMPORT do Pix anual (docs/plano_pix_anual_asaas.md).

A propriedade que este arquivo existe para provar, e que é o critério de
aceitação do dono:

> **nenhuma versão intermediária deve conseguir emitir uma cobrança sem já
> possuir o caminho que recebe o pagamento e concede o acesso.**

Checkout e webhook não se separam — os dois são o 1b-B, e entraram juntos.
**A allowlist deixou de ser vazia neste PR**, e com isso o portão passou de
"ninguém importa" para "SÓ ESTES importam" — que continua sendo propriedade, e
mede mais: um import novo em `db/`, num handler ou num scheduler reprova pelo
nome do arquivo. O nome do módulo não mudou junto porque o assunto não mudou;
o que ele afirma está em `CHAMADORES_PERMITIDOS`, não no título.

**"Ninguém os chama" vale para os `.py` de PRODUÇÃO — não para o repositório
inteiro.** A versão anterior dizia "repositório INTEIRO" e era falso: o universo
deste portão é `git ls-files '*.py' | grep -vc '^tests/'`, e o repositório
rastreia bem mais que isso (`git ls-files | wc -l`). Foi por essa frase que o
Manager entrou (ver a cegueira do `startCommand`). Alcance se mede rodando a
varredura na hora, nunca de memória — nem de número escrito aqui, que envelhece
em silêncio (§2).

## Quatro caminhos de alcance, um arquivo cada

  1. **rota** — `tests/test_pix_rota_registrada.py` (fechá-lo exigiu TRÊS
     introspecções; no 1b-B ele INVERTEU e passou a exigir as três rotas);
  2. **import** — AQUI: só a allowlist importa os módulos do Pix. É como o
     próprio app chegaria, inclusive por `background_tasks` e pelo loop de
     60 s — os dois precisam do import para chamar;
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

`CHAMADORES_PERMITIDOS` nasceu VAZIA no 1b-A de propósito, e o 1b-B a ABRIU em
vez de apagar o teste — é a diferença entre um portão que mudou de pergunta e um
portão que foi desligado. `test_a_allowlist_nao_tem_caminho_morto` é o par: sem
ele, a lista cresceria com entradas que não permitem nada e ninguém reconfere.

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

# Os módulos cujo alcance é medido por este portão. `pix_pricing` fica de fora
# da lista de propósito: ele é função pura, não emite cobrança nem concede
# acesso, então importá-lo não violaria o critério do dono — os chamadores dele
# são medidos separado, mais abaixo.
#
# `db.pix_effects` nasceu da divisão de `db.webhook_outbox` (1b-B) e entra AQUI
# no mesmo commit: módulo novo do Pix fora desta lista é buraco no dia 1. Vale
# igual para `db.pix_charges_saga` e `core.services.asaas_customers`, que
# nasceram das divisões de tamanho do C6/C7 — o segundo é por onde o CPF passa.
MODULOS_INERTES = ("core.services.asaas", "core.services.asaas_customers",
                   "db.pix_charges", "db.pix_charges_saga",
                   "db.webhook_outbox", "db.pix_effects")

# Allowlist do portão de IMPORT: caminhos (POSIX) que PODEM importar os módulos
# acima. É um conjunto de caminhos, não um mapa — quem importa o quê fica no
# portão, e a granularidade por módulo só vale a pena quando houver mais de um
# chamador. Vazia no 1b-A; o 1b-B a abre, e com isso o portão passa de "ninguém
# importa" para "só estes importam" — que continua sendo propriedade.
#
# **Só entra caminho que faz o import DE VERDADE hoje.** Entrada de allowlist
# que não permite nada é linha que ninguém reconfere; o portão vale porque cada
# uma foi olhada no commit que precisou dela.
#
# **Os próprios módulos do Pix estão na lista, e é decisão de desenho.** A
# varredura trata inerte→inerte como violação porque o universo inclui os
# `MODULOS_INERTES` — e `db/webhook_outbox.py` PRECISA importar
# `core/services/asaas.py` (o `_erro_seguro` deixou de ser cópia). Pô-los aqui,
# em vez de excluí-los do universo em silêncio, mantém a lista legível e a
# propriedade intacta: **o primeiro salto, produção → Pix, continua sendo o que
# este portão mede.**
CHAMADORES_PERMITIDOS: frozenset[str] = frozenset({
    # O caminho que RECEBE o pagamento.
    "core/services/pix_drain.py",          # reserva, roteia, transiciona
    "core/services/pix_drain_effects.py",  # grant, ga4, capi, e-mail, revoke
    # O caminho que EMITE a cobrança — entrou no MESMO PR, que é o corte do dono.
    "core/services/pix_checkout.py",       # a saga, a flag, o preço, o 409/503
    "core/services/pix_sweeps.py",         # reconciliação da saga (§10.1)
    "frontend/routes/billing_pix.py",      # as três rotas, finas
    # Módulos do Pix importando módulos do Pix (ver o parágrafo acima).
    "core/services/asaas_customers.py",
    "db/pix_charges_saga.py",
    "db/webhook_outbox.py",
})


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
        "módulo de produção fora da allowlist importando o Pix: "
        + repr(dict(sorted(fora.items())))
        + ". Acrescentar o caminho a CHAMADORES_PERMITIDOS é uma decisão, não "
          "um conserto: quem emite cobrança tem de trazer junto o caminho que "
          "recebe o pagamento — é o que o corte do dono proíbe separar."
    )


def test_a_allowlist_nao_tem_caminho_morto():
    """POSITIVO da allowlist, e ele impede o modo de falha oposto.

    Uma entrada que não importa mais nada é linha que "permite" algo inexistente
    — e no dia em que um arquivo com aquele nome voltar, ele nasce autorizado
    sem revisão. Cada caminho aqui tem de existir E importar um módulo do Pix.

    Sem este teste, a allowlist poderia crescer até virar "qualquer coisa em
    `core/services/`" sem uma linha vermelha.
    """
    achados = _quem_importa(MODULOS_INERTES, raiz="")
    mortos = sorted(CHAMADORES_PERMITIDOS - set(achados))
    assert not mortos, (
        f"caminhos na allowlist que não importam módulo nenhum do Pix: {mortos}"
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

    Um a um, cada módulo inerte — e isto também valida os NOMES em
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


def test_pix_pricing_so_e_importado_pelo_checkout_e_pela_rota():
    """`pix_pricing` INVERTEU junto com o resto (§5.2 do plano).

    Ele fica fora de `MODULOS_INERTES` porque importá-lo não emite cobrança — é
    aritmética pura —, mas medir o chamador separado continua valendo: é o que
    impede alguém de ligar o preço a outra tela achando que "pricing não conta".

    **O que este portão afirma, desde o consumidor não-Pix:** preço LIDO pode
    sair do módulo; caminho de COBRANÇA não. Ele continua reprovando pelo nome
    do arquivo um import novo em `db/`, num handler ou num scheduler — não virou
    "qualquer um importa". O critério do dono (topo deste arquivo) é sobre EMITIR
    cobrança sem o caminho que recebe o pagamento; ler um `dict[str, int]` não
    emite nada, e o preço ANUAL não é do Pix (o cartão vende anual por
    `STRIPE_PRICE_ID_*_ANUAL` desde antes, e o Pix anual ainda está atrás de
    `ASAAS_PIX_ANNUAL_ENABLED`). Abrir o esperado, em vez de apagar o teste, é a
    mesma escolha que o 1b-B fez com a allowlist.

    São TRÊS chamadores: o checkout chama `plano_da_cobranca`, a rota importa
    `CoberturaJaPaga` para traduzir a recusa em 409 sem reconsultar o banco (é o
    contrato escrito na docstring da própria exceção), e o e-mail só LÊ o preço.

    **Gatilho para o futuro:** um consumidor não-Pix é exceção justificada; no
    dia em que aparecer o SEGUNDO, a constante está no módulo errado e o
    conserto é extraí-la para um módulo neutro — PR próprio, porque mexe no
    caminho de emissão de cobrança e arrisca ciclo de import.
    """
    esperado = {
        "core/services/pix_checkout.py",      # chama `plano_da_cobranca`
        "frontend/routes/billing_pix.py",     # traduz `CoberturaJaPaga` em 409
        # o aviso de fim do Grátis lê só `PRECOS_ANUAIS_CENTS` para a copy;
        # não emite cobrança e não fala com o Asaas
        "core/services/email_service.py",
    }
    achados = set(_quem_importa(("core.services.pix_pricing",), raiz=""))
    assert achados == esperado, (
        f"chamadores de pix_pricing mudaram — sobrando: {sorted(achados - esperado)}; "
        f"faltando: {sorted(esperado - achados)}"
    )


def test_db_init_nao_reexporta_os_modulos_novos():
    """Precedente do 1a. Re-export é ALCANCE: `from db import *` traria o
    módulo para dentro de qualquer arquivo."""
    fonte = (RAIZ / "db" / "__init__.py").read_text(encoding="utf-8")
    for nome in ("pix_charges", "pix_charges_saga", "webhook_outbox", "pix_effects"):
        assert nome not in fonte, f"db/__init__.py re-exporta {nome}"
