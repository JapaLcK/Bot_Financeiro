"""O boot cria as tabelas do Pix e NÃO escreve linha nenhuma.

O corte do 1b-A é **ZERO `insert`**, e o motivo tem nome: o
`CardinalityViolation` do PR 1a nasceu de um `insert … on conflict` rodando
dentro do `init_db` — dentro de um passo obrigatório de startup, então o erro
não degradava a migração, derrubava a subida da aplicação. Aqui não há backfill
porque não existe venda Pix anterior ao código que a criou.

## Três medições, e cada uma cobre o que a outra não vê

  1. **captura no CURSOR** (`test_nenhum_statement_escreve_nas_tabelas_novas`) —
     a medição principal;
  2. **delta no banco** — o runtime, contra um `init_db` de verdade;
  3. **as tabelas existem** — o piso: sem ele, (1) e (2) passam num mundo onde o
     DDL não criou nada.

**Por que no cursor e não em `_run_ddl`.** A primeira versão trocava
`db.schema._run_ddl` e inspecionava a LISTA `ddl_statements`. Isso é
estruturalmente cego ao que roda **depois** dela: `init_db` chama
`ensure_plan_trials_user_fk` e `repair_user_fk_cascades` com `cur.execute`
DIRETO, dentro do mesmo `_run_ddl` (`db/schema.py:2268`) — um `insert` posto ali
nunca apareceria na lista. Trocando `db.schema.get_conn` por uma conexão de
mentira que só ANOTA, o que se mede é tudo o que o `init_db` manda ao banco, sem
depender de por qual caminho o statement chegou.

**Por que o delta sozinho não basta** (medido): um `insert … on conflict do
nothing` é IDEMPOTENTE. A primeira subida grava; da segunda em diante o delta é
zero. Com um seed de uma linha no fim de `ddl_statements`, o teste do delta
ficou **verde** — e é exatamente a forma do defeito do 1a.

**Por que o verbo é lido depois de tirar os comentários** (medido): o Tester
plantou `-- reconcilia\\ninsert into pix_charges …`. Um `^\\s*insert` na string
crua não casa a segunda linha, e a linha foi gravada com o portão verde. Também
passavam `/* c */ insert`, `do $$ begin insert … end $$`, `copy` e `merge into`
— os quatro estão na lista de verbos e na tabela de sabotagens abaixo.

CEGUEIRA DECLARADA: escrita que não passe pelo `init_db` (um `insert` no import
de um módulo, um `@app.on_event("startup")`) não é vista aqui — quem cobre esse
alcance é `tests/test_pix_inerte.py`, que prova que ninguém importa os módulos.
"""

import re

import pytest

TABELAS_NOVAS = ("pix_charges", "pix_webhook_events", "pix_payment_effects")

# `with` entra porque uma CTE esconde o `insert` do segundo token; `do` por
# causa do bloco anônimo `do $$ … $$`; `copy` e `merge` porque são escrita que
# ninguém lembra de listar.
VERBOS_DE_ESCRITA = re.compile(r"^\s*(insert|update|delete|with|copy|merge|do)\b", re.I)

# `--` até o fim da linha e `/* … */`. Tirar o comentário ANTES de olhar o verbo
# é o conserto do furo medido: comentário na frente derrubava a âncora `^`.
_COMENTARIO_LINHA = re.compile(r"--[^\n]*")
_COMENTARIO_BLOCO = re.compile(r"/\*.*?\*/", re.S)


def sem_comentarios(sql: str) -> str:
    return _COMENTARIO_BLOCO.sub(" ", _COMENTARIO_LINHA.sub(" ", sql))


def escreve_nas_tabelas_novas(sql: str) -> bool:
    """O statement escreve numa das três tabelas novas?

    Duas condições, e as duas importam: o VERBO (depois de tirar comentário) e o
    NOME da tabela. Só o verbo daria falso positivo no `update plan_trials` dos
    reparos; só o nome daria falso positivo no `create table … on delete set
    null` (o `delete` casa no meio) e no DDL de `plan_grants`, que cita
    `pix_charges.id` num comentário. Os dois falsos positivos foram medidos.
    """
    limpo = sem_comentarios(sql)
    return bool(VERBOS_DE_ESCRITA.match(limpo)) and any(t in limpo for t in TABELAS_NOVAS)


class _CursorEspiao:
    """Anota o SQL e devolve vazio. `fetchone`/`fetchall` existem porque os
    reparos do fim do `init_db` os chamam — `repair_user_fk_cascades` faz
    `cur.fetchall() or []` e `ensure_plan_trials_user_fk` faz `cur.fetchone()`.
    Devolver vazio leva os dois pelo caminho mais LONGO, que é o que se quer
    capturar."""

    def __init__(self, anotados: list[str]):
        self._anotados = anotados

    def execute(self, sql, params=None):
        self._anotados.append(str(sql))

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _ConnEspiao:
    autocommit = False

    def __init__(self, anotados: list[str]):
        self._anotados = anotados

    def cursor(self):
        return _CursorEspiao(self._anotados)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _PoolEspiao:
    def __init__(self, anotados: list[str]):
        self._anotados = anotados

    def connection(self, timeout=None):
        return _ConnEspiao(self._anotados)


@pytest.fixture()
def statements_do_boot(monkeypatch) -> list[str]:
    """Tudo o que `init_db()` manda ao banco — sem tocar no banco.

    **O ponto de captura é `db.connection._get_pool`, e a escolha é o conserto.**

    A versão anterior trocava `db.schema.get_conn`, e isso é troca de uma CÓPIA
    do nome: `db/schema.py` faz `from .connection import get_conn` no topo, que
    copia a referência para dentro do módulo. Qualquer helper que faça o MESMO
    import — noutro módulo, chamado pelo `init_db` — abre a própria conexão e
    passa limpo pelo espião. O Tester plantou um "reparo idempotente" em
    `db/schema_repairs.py`, que é onde este repositório põe exatamente isso:
    34 verdes, 1 linha gravada na primeira subida, delta 0 na segunda — **a
    mesma forma do defeito do 1a**, num módulo vizinho.

    `get_conn` faz `return _get_pool().connection(timeout=timeout)`, e o
    `_get_pool` é resolvido em `db.connection` na HORA DA CHAMADA. Então trocar
    o pool alcança todas as cópias de `get_conn` de uma vez, existentes e
    futuras — é a diferença entre consertar a instância e fechar a classe.

    CEGUEIRA DECLARADA: código que chame `psycopg.connect()` direto, sem passar
    pelo pool. Nenhum módulo de `db/` faz isso hoje; se um dia fizer, o sintoma
    é este portão continuar verde, e o que alcança é revisão de diff.
    """
    anotados: list[str] = []
    import db.connection as conexao
    import db.schema as schema

    monkeypatch.setattr(conexao, "_get_pool", lambda: _PoolEspiao(anotados))
    schema.init_db()
    return anotados


def test_a_captura_ve_o_ddl_e_tambem_o_que_roda_depois_dele(statements_do_boot):
    """Piso anti-tautologia, e ele mede as DUAS origens.

    Captura vazia — assinatura mudou, `get_conn` deixou de ser usado — faria o
    portão abaixo passar afirmando nada. E capturar só a lista `ddl_statements`
    seria a cegueira que motivou trocar `_run_ddl` por `get_conn`: o `alter
    table plan_trials` vem de `ensure_plan_trials_user_fk`, que roda com
    `cur.execute` direto, FORA da lista.
    """
    faltando = [t for t in TABELAS_NOVAS
                if not any(f"create table if not exists {t}" in s
                           for s in statements_do_boot)]
    assert not faltando, f"a captura não achou o create de {faltando}"

    # O marcador do segundo caminho é `pg_advisory_lock`: ele é executado pelo
    # `_run_ddl` com `cur.execute` DIRETO, antes do laço, e **nunca** aparece na
    # lista `ddl_statements`. Um `alter table plan_trials` não serve — a lista
    # também tem um (`db/schema.py:1727`), então o marcador existia nos DOIS
    # caminhos e a sabotagem "volte a capturar só a lista" saía VERDE. Medido.
    assert any("pg_advisory_lock" in s for s in statements_do_boot), (
        "a captura não enxerga o que roda FORA da lista `ddl_statements` — ela "
        "voltou a trocar `_run_ddl`, e a escrita posta depois do laço "
        "(db/schema.py:2268) fica invisível"
    )
    # E que ela alcança conexão aberta por OUTRO módulo: este `alter table` sai
    # de `db/schema_repairs.py`, não de `db/schema.py`. Trocar `schema.get_conn`
    # (a versão anterior) via este statement só por acidente — porque hoje o
    # reparo recebe o cursor de fora. Um helper vizinho que abrisse a própria
    # conexão passava limpo, e foi ali que a plantação do Tester coube.
    assert any("plan_trials_user_id_fkey" in s for s in statements_do_boot), (
        "a captura não enxerga o que os módulos VIZINHOS mandam ao banco — o "
        "ponto de troca voltou a ser uma cópia de `get_conn` em vez do pool"
    )


def test_nenhum_statement_escreve_nas_tabelas_novas(statements_do_boot):
    """A medição principal."""
    escritas = [" ".join(s.split())[:200] for s in statements_do_boot
                if escreve_nas_tabelas_novas(s)]
    assert not escritas, (
        "o boot escreve nas tabelas do Pix — o corte do 1b-A é ZERO insert: "
        + repr(escritas)
    )


SABOTAGENS = [
    ("insert nu", "insert into pix_charges (id) values (1)"),
    ("comentário de linha antes",
     "-- reconcilia o que ficou\ninsert into pix_charges (id) values (1)"),
    ("comentário de bloco antes", "/* seed */ insert into pix_webhook_events values (1)"),
    ("bloco anônimo", "do $$ begin insert into pix_charges values (1); end $$"),
    ("copy", "copy pix_charges from stdin"),
    ("merge", "merge into pix_payment_effects t using x on true"),
    ("CTE", "with x as (select 1) insert into pix_charges select * from x"),
    ("update", "update pix_charges set status = 'paid'"),
    ("delete", "delete from pix_webhook_events"),
    ("indentado e em CAIXA ALTA", "\n    INSERT INTO pix_charges VALUES (1)"),
]


@pytest.mark.parametrize("rotulo,sql", SABOTAGENS, ids=[r for r, _ in SABOTAGENS])
def test_cada_forma_de_escrita_e_reconhecida(rotulo, sql):
    """AUTOVALIDAÇÃO do detector, forma a forma.

    Sem esta tabela, o portão acima só prova que o DDL de HOJE passa — e um
    detector que nunca casasse nada passaria igual. Cada linha aqui é uma forma
    que já derrubou (ou derrubaria) a versão anterior da regex.
    """
    assert escreve_nas_tabelas_novas(sql), f"o detector não vê {rotulo!r}: {sql!r}"


INOCENTES = [
    ("create com on delete", "create table x (u bigint references users(id) on delete set null)"),
    ("comentário citando a tabela", "create table plan_grants (\n  r text -- ver pix_charges.id\n)"),
    ("escrita em OUTRA tabela", "update plan_trials set user_id = null"),
    ("índice parcial", "create unique index uniq_pix_charge_ativa on pix_charges (user_id)"),
    ("insert do resync do 1a", "insert into plan_grants (user_id) select 1"),
]


@pytest.mark.parametrize("rotulo,sql", INOCENTES, ids=[r for r, _ in INOCENTES])
def test_o_detector_nao_grita_a_toa(rotulo, sql):
    """POSITIVO do par, e não é cerimônia: as duas primeiras linhas SÃO os falsos
    positivos que a primeira versão produziu (`on delete set null` casando
    `delete` no meio; `pix_charges.id` citado num comentário do DDL de
    `plan_grants`). Portão que grita sem motivo é portão que alguém desliga."""
    assert not escreve_nas_tabelas_novas(sql), f"falso positivo em {rotulo!r}: {sql!r}"


def _contagens() -> dict[str, int]:
    from db.connection import get_conn

    with get_conn() as conn, conn.cursor() as cur:
        saida = {}
        for tabela in TABELAS_NOVAS:
            cur.execute(f"select count(*) as n from {tabela}")
            saida[tabela] = cur.fetchone()["n"]
        return saida


def test_init_db_de_verdade_nao_muda_a_contagem():
    """O lado RUNTIME: um `init_db()` real, contra o banco, não pode mover a
    contagem. Cobre a escrita que a captura não veria por não ser um `execute`
    reconhecível — um `copy_from`, um `executemany`.

    Mede o DELTA, não o total, porque o total é dependente de ordem:
    `tests/test_pix_charges.py` cria cobranças na mesma sessão e a FK `set null`
    faz a linha SOBREVIVER à limpeza do usuário, de propósito (é a
    pseudonimização). E o delta sozinho não basta — ver o cabeçalho.
    """
    from db.schema import init_db

    antes = _contagens()
    init_db()
    depois = _contagens()
    assert depois == antes, (
        "init_db escreveu linha: "
        f"{ {t: (antes[t], depois[t]) for t in TABELAS_NOVAS if antes[t] != depois[t]} }"
    )


def test_as_tres_tabelas_existem_depois_do_boot():
    """O piso dos dois acima: sem ele, ambos passam verdes num mundo onde o DDL
    não criou tabela nenhuma."""
    from db.connection import get_conn

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select table_name from information_schema.tables"
            " where table_schema = current_schema() and table_name = any(%s)",
            (list(TABELAS_NOVAS),),
        )
        achadas = {r["table_name"] for r in cur.fetchall()}
    assert achadas == set(TABELAS_NOVAS), f"faltou criar: {set(TABELAS_NOVAS) - achadas}"
