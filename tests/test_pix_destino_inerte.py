"""Portão de DESTINO: só a allowlist nomeia o Asaas e as tabelas do Pix.

**É o portão que MENOS mudou no 1b-B, e o que mais continua valendo.** Ele nunca
mediu "ninguém importa" — mede que o gateway não é nomeado fora dos arquivos que
declaram precisar dele. Ligar o Pix acrescentou quatro entradas na allowlist e
não mudou uma linha do mecanismo.

Assunto próprio, e não uma seção de `tests/test_pix_inerte.py`, porque o
UNIVERSO e o MECANISMO são outros: lá são os `.py` lidos com `ast`; aqui é
**todo arquivo rastreado** fora de `tests/`, de qualquer formato, lido como
TEXTO — `git ls-files | grep -vc '^tests/'` conta quantos são hoje.

## Por que existe: os portões de import veem NOME DE MÓDULO

A forma provável de furo neste repositório não é import dinâmico, é **CÓPIA** —
o `CLAUDE.md` §0.1 registra o precedente com nome e sobrenome
(`handlers/credit.py` × `core/handlers/credit.py`). O Tester plantou um router
novo com `httpx.post` para `https://api.asaas.com/v3/payments` e `insert into
pix_charges` em SQL crua, **sem importar nenhum dos módulos inertes**: os portões
de rota e de import ficaram verdes.

## Por que o universo NÃO é só `.py`

Porque este portão é textual e não precisa de `ast`, então limitá-lo a Python
era restrição sem contrapartida — e a contrapartida existia do outro lado:
**há arquivo rastreado que não é `.py`** (`git ls-files | grep -vc '[.]py$'`), e
um deles basta. O Manager plantou `railway.pix-manager-probe.toml` nomeando
`api.asaas.com`, `ASAAS_API_KEY` e as três tabelas: 49 verdes.

**Não é hipótese exótica.** O `Procfile` (`web: python launch.py`) é rastreado,
não é `.py`, e é ele quem dá o start command de produção: o repositório JÁ roda
Python por um caminho que o `ast` não lê. Um arquivo desse tipo chamando
`db.pix_charges` emitiria cobrança sem uma linha vermelha.

O exemplo anterior aqui era `railway.account-deletion.toml`, apagado quando o
`account-deletion-job` migrou para configuração nativa do painel do Railway
(Config as Code descontinuado, arquivos existentes param em 2026-12-01). A troca
de exemplo NÃO reduz a cegueira, muda ela de lado: o start command daquele job
não está mais em arquivo nenhum, o que cai na terceira cegueira abaixo.

CEGUEIRAS DECLARADAS: host montado em partes (`"api." + "asaas" + ".com"`) ou
env de outro nome; tabela com outro nome; arquivo NÃO rastreado — inclusive um
script Python fora do git chamado por um `startCommand`, ou um start command que
só existe no painel do Railway. Para essas o método é revisão de diff.
"""

import pathlib
import subprocess

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# Literais que denunciam o DESTINO, não o nome do módulo.
MARCAS_DO_ASAAS = ("asaas.com", "ASAAS_")
# FONTE ÚNICA da lista (CLAUDE.md §0.7). `tests/test_pix_ddl_sem_escrita.py`
# IMPORTA daqui em vez de manter a própria cópia — as duas listas responderam à
# mesma pergunta ("quais são as tabelas novas do Pix?") e divergiram em silêncio:
# `pix_unmatched_payments` nasceu no 1b-B e ficou de fora das DUAS, deixando a
# tabela nova descoberta pelo portão de escrita no boot e pelo de destino.
# Mora AQUI, e não lá, porque este arquivo não importa `pytest` nem `db` — a
# direção do import é do módulo leve para o pesado.
TABELAS_NOVAS = ("pix_charges", "pix_webhook_events", "pix_payment_effects",
                 "pix_unmatched_payments")


# PROSA: documentação DEVE falar do Asaas, e `.md` não executa. É o ÚNICO falso
# positivo da varredura ampliada, medido: `docs/plano_pix_anual_asaas.md`.
PREFIXOS_DE_PROSA = ("docs/",)

# Allowlist do portão de DESTINO, **por arquivo E por marca**.
#
# A versão anterior era por ARQUIVO, e isso a furava: `db/schema.py` e
# `db/schema_repairs.py` precisam do nome das tabelas (DDL e a linha do
# `_USER_FK_SET_NULL_TABLES`), mas são infraestrutura que o app INTEIRO carrega.
# Allowlistá-los inteiros liberava `asaas.com` e `ASAAS_` dentro deles — e o
# Tester plantou ali a cópia natural, com o mesmo host e a mesma env, zero
# ofuscação: **34 testes verdes**.
#
# Agora cada arquivo declara SÓ as marcas de que precisa, e isso é MEDIDO desde
# 2026-09-09 por `test_a_allowlist_nao_tem_marca_morta` (o gêmeo do
# `test_a_allowlist_nao_tem_caminho_morto` de `tests/test_pix_inerte.py`). Antes
# a frase era só uma intenção: **11 das 16 entradas declaravam marca que o
# arquivo não usava** — `MARCAS_DO_ASAAS + TABELAS_NOVAS` era o default preguiçoso
# e liberava o host e a env dentro de arquivos que só precisavam do nome de uma
# tabela, `db/schema_repairs.py` (que o app INTEIRO carrega) incluído.
#
# Consequência prática, e ela é o ponto: acrescentar uma marca a um arquivo passa
# a exigir editar esta linha. É trabalho de propósito — é a revisão acontecendo.
MARCAS_PERMITIDAS: dict[str, tuple[str, ...]] = {
    # O CLIENTE HTTP é o único que precisa das duas marcas de gateway junto com
    # as tabelas: ele monta a URL, lê `ASAAS_API_KEY` e cita as tabelas na prosa.
    "core/services/asaas.py": MARCAS_DO_ASAAS + ("pix_charges",
                                                 "pix_webhook_events",
                                                 "pix_payment_effects"),
    # Os que leem alguma env `ASAAS_*` — e nenhum deles monta host.
    # `ASAAS_PIX_ANNUAL_ENABLED` mora no checkout de propósito (§5.3 do plano):
    # o monólito chama `pix_annual_available()` em vez de ler a env, justamente
    # para não derrubar este portão. O portão está fazendo trabalho real ali.
    "core/services/pix_checkout.py": ("ASAAS_", "pix_charges"),
    "core/services/pix_pricing.py": ("ASAAS_", "pix_charges"),
    "frontend/routes/billing_pix.py": ("ASAAS_", "pix_charges"),
    # O resto do Pix: só o nome das tabelas que cada um de fato toca. Nenhum
    # deles nomeia o host nem uma env — e agora não pode passar a nomear sem que
    # esta linha mude junto.
    "core/services/asaas_customers.py": ("pix_charges",),
    "core/services/pix_sweeps.py": ("pix_charges",),
    "db/pix_charges.py": ("pix_charges", "pix_webhook_events",
                          "pix_payment_effects"),
    "db/pix_charges_saga.py": ("pix_charges",),
    "db/webhook_outbox.py": ("pix_charges", "pix_webhook_events",
                             "pix_payment_effects"),
    "db/pix_effects.py": ("pix_webhook_events", "pix_payment_effects"),
    # O DRENO (1b-B) — entrada MÍNIMA desde sempre, e agora ela é a regra e não
    # a exceção. Sem `MARCAS_DO_ASAAS`: ler `ASAAS_PIX_ANNUAL_ENABLED` aqui
    # derrubaria o portão, e é isso que mantém a flag dentro do checkout.
    "core/services/pix_drain.py": ("pix_charges", "pix_payment_effects",
                                   "pix_unmatched_payments"),
    "core/services/pix_drain_effects.py": ("pix_charges", "pix_payment_effects"),
    # INFRAESTRUTURA — só o nome das tabelas, nunca o host nem a env.
    "db/schema.py": TABELAS_NOVAS,            # o DDL das quatro tabelas
    "db/schema_repairs.py": ("pix_charges",),  # a linha do _USER_FK_SET_NULL_TABLES
    # `db/privacy.py` fala de UMA tabela e só dela: a exclusão de conta zera o
    # rastreio/QR/customer de `pix_charges` e o export leva a cobrança (§13.2,
    # caso 43). Entrada mínima de propósito — `pix_webhook_events` e
    # `pix_payment_effects` NÃO são alcançáveis por `user_id` e não têm o que
    # fazer aqui, então deixá-las de fora mantém o portão medindo.
    "db/privacy.py": ("pix_charges",),
}


def _rastreados() -> list[str]:
    """`git ls-files` — o universo é o que o git RASTREIA, nunca o working tree
    (que traria `.venv`). Mesmo critério do `test_max_lines_python.py`."""
    saida = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True,
                           text=True, check=True).stdout
    return [linha for linha in saida.splitlines() if linha.strip()]


def _producao_todos_os_formatos() -> list[str]:
    """TODO arquivo rastreado fora de `tests/` — o universo do portão de DESTINO.

    Ele é TEXTUAL e não precisa de `ast`, então limitá-lo a `.py` era restrição
    sem contrapartida: sobrava todo rastreado não-Python, contado por
    `git ls-files | grep -vc '[.]py$'`, e um basta para emitir cobrança (ver
    `startCommand`, no cabeçalho). Entra o que `git ls-files | grep -vc
    '^tests/'` conta; binário é pulado por `UnicodeDecodeError`, sem lista de
    exceções por nome.
    """
    return [rel for rel in _rastreados() if not rel.startswith("tests/")]


def test_nenhum_arquivo_de_producao_fala_com_o_asaas():
    """A CÓPIA, que é a forma provável de furo neste repositório (§0.1).

    O Tester plantou `frontend/routes/upgrade_anual.py` com rota própria,
    `include_router`, `httpx.post` para `https://api.asaas.com/v3/payments` e
    `insert into pix_charges` em SQL crua — **sem importar nenhum dos três
    módulos**. Cobrança emitida, linha gravada, e os portões 1 e 2 verdes.

    Este portão olha o DESTINO: host do Asaas, env `ASAAS_*` e o nome das
    tabelas novas. Mencionar qualquer um deles fora do que o arquivo declara em
    `MARCAS_PERMITIDAS` — mesmo em comentário — é motivo para alguém olhar.

    **A allowlist é por arquivo E por marca**, porque por arquivo ela furava:
    `db/schema.py` e `db/schema_repairs.py` precisam do nome das tabelas e são
    carregados pelo app inteiro; liberá-los por completo liberava o host e a env
    dentro deles, e foi ali que a cópia natural passou com 34 verdes.

    O QUE SOBRA DESCOBERTO: uma cópia dentro de `db/schema.py` que use o nome das
    tabelas (permitido lá) e alcance o gateway por host montado em partes ou env
    de outro nome. Contra isso valem os outros portões — o de DDL prova que o
    boot não escreve nessas tabelas por conexão NENHUMA, o de import prova que
    `schema.py` não chama os módulos do Pix — mais revisão de diff.
    """
    marcas = MARCAS_DO_ASAAS + TABELAS_NOVAS
    achados: dict[str, list[str]] = {}
    for rel in _producao_todos_os_formatos():
        if rel.startswith(PREFIXOS_DE_PROSA):
            continue
        permitidas = MARCAS_PERMITIDAS.get(rel, ())
        try:
            texto = (RAIZ / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binário (fonte, imagem): não executa e não tem texto
        vistas = [m for m in marcas if m in texto and m not in permitidas]
        if vistas:
            achados[rel] = vistas

    assert not achados, (
        "arquivo de produção fora da allowlist falando com o Pix/Asaas — é a "
        "CÓPIA que os portões de import não veem: "
        + repr(dict(sorted(achados.items())))
    )


def test_a_allowlist_nao_tem_marca_morta():
    """POSITIVO da allowlist, gêmeo do `test_a_allowlist_nao_tem_caminho_morto`
    de `tests/test_pix_inerte.py:229` — e ele faltava aqui.

    Marca declarada que o arquivo não usa é linha que "permite" algo inexistente:
    no dia em que o `db/schema_repairs.py` — carregado pelo app INTEIRO — passar a
    montar `https://api.asaas.com`, ele nasce autorizado, sem revisão. Não é
    hipótese: a versão anterior deste dict dava `MARCAS_DO_ASAAS + TABELAS_NOVAS`
    a 11 dos 16 arquivos, incluindo esse, e o docstring acima já afirmava que
    "cada arquivo declara SÓ as marcas de que precisa". A afirmação era falsa e
    nada no arquivo notava.

    Sem este teste a allowlist volta a crescer até virar "qualquer coisa em
    `core/services/`" sem uma linha vermelha — que é o modo de falha oposto ao
    que `test_nenhum_arquivo_de_producao_fala_com_o_asaas` pega, e nenhum dos
    dois enxerga o do outro.

    **A folga que ele deixa de propósito:** um arquivo que perde a última
    ocorrência de uma marca fica vermelho AQUI, não lá. É a direção certa —
    obriga a apagar a linha em vez de deixá-la de herança.
    """
    mortas = {}
    for rel, marcas in MARCAS_PERMITIDAS.items():
        caminho = RAIZ / rel
        if not caminho.exists():
            mortas[rel] = "ARQUIVO NÃO EXISTE"
            continue
        texto = caminho.read_text(encoding="utf-8")
        sobrando = [m for m in marcas if m not in texto]
        if sobrando:
            mortas[rel] = sobrando

    assert not mortas, (
        "entradas de MARCAS_PERMITIDAS liberando marca que o arquivo NÃO usa — "
        "cada uma é uma autorização pré-concedida a código que ainda não existe: "
        + repr(dict(sorted(mortas.items())))
    )


def test_a_varredura_de_destino_enxerga_as_marcas():
    """AUTOVALIDAÇÃO do portão 3: as marcas TÊM de ser encontradas onde elas de
    fato estão. Uma constante com typo (`asas.com`) deixaria o portão acima
    verde para sempre, e nada mais no arquivo notaria.
    """
    asaas = (RAIZ / "core/services/asaas.py").read_text(encoding="utf-8")
    for marca in MARCAS_DO_ASAAS:
        assert marca in asaas, f"{marca!r} não existe em core/services/asaas.py"
    schema = (RAIZ / "db/schema.py").read_text(encoding="utf-8")
    for tabela in TABELAS_NOVAS:
        assert tabela in schema, f"{tabela!r} não existe no DDL — nome errado?"
