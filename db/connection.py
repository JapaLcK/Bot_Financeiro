"""
db/connection.py — Conexão com o banco de dados PostgreSQL.

Usa psycopg_pool.ConnectionPool (síncrono) pra reusar conns. Sem pool,
cada chamada abria conn nova (~1-2s no Railway), fazendo cada endpoint
síncrono custar 3-6s. Com pool, conn reaproveitada → cada query custa só
o round-trip da query em si.

A interface `with get_conn() as conn:` continua idêntica — callers
existentes não precisam mudar.
"""
import os
import re
import threading
from contextvars import ContextVar

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from core.pg_text import redigir_failing_row
from .cat_icones import icone_da_categoria

# O psycopg monta o texto do erro com o DETAIL do Postgres; num CHECK violado (`finito_*`,
# db/finito_checks.py) ele é `Failing row contains (<a linha inteira do usuário>)`. Redigir no
# `__str__` cobre de uma vez TODO `print(exc)`, `f"{exc}"`, `logger.*(..., exc)` e traceback, de
# qualquer conexão (pool ou `psycopg.connect` direto), sem cada site lembrar. O dado bruto segue
# em `exc.args[0]`/`exc.diag` para quem precisa dele de propósito.
if not getattr(psycopg.Error.__str__, "_sem_failing_row", False):
    _str_do_psycopg = psycopg.Error.__str__

    def _str_sem_linha(self) -> str:
        texto = _str_do_psycopg(self)
        # Estrutural, não pelo idioma: violação de constraint (SQLSTATE classe 23: CHECK, NOT NULL,
        # UNIQUE, FK) leva no DETAIL a linha/chave do usuário, e o Postgres o LOCALIZA (`lc_messages`).
        # `diag.message_detail` é esse trecho em qualquer idioma; a regex em inglês fica de 2ª rede.
        try:
            detalhe = self.diag.message_detail
            if detalhe and (self.sqlstate or "").startswith("23"):
                texto = texto.replace(detalhe, "(omitido)")
        except Exception:  # noqa: BLE001 - redigir nunca pode levantar de dentro de str(exc)
            pass
        return redigir_failing_row(texto)

    _str_sem_linha._sem_failing_row = True
    psycopg.Error.__str__ = _str_sem_linha


_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

# Quantas vezes um `commit()` LEVANTOU nesta thread. Só isso: commit que volta
# sem erro não é ambíguo, e falha antes do commit não gravou nada.
_commits_ambiguos: ContextVar[int] = ContextVar("db_commits_ambiguos", default=0)


def commits_ambiguos() -> int:
    """Contador monotônico de commits que estouraram — leia antes e depois.

    Quando a conexão cai ENQUANTO o Postgres confirma o COMMIT, a chamada
    levanta mas a transação pode ter sido gravada assim mesmo. De fora, isso é
    indistinguível de uma falha ANTES do commit, e a diferença decide se dá para
    repetir o trabalho: repetir um aporte já commitado debita a origem e credita
    o destino DUAS vezes (Codex, PR #144).

    Quem precisa distinguir compara o valor de antes com o de depois — delta 0
    significa "nada chegou a ser confirmado", e só aí repetir é seguro. É o que
    `db.restore_pending_on_error` faz para decidir se devolve a pergunta.

    Contador, e não flag booleana, porque não há onde zerar: um erro engolido
    lá atrás deixaria a flag ligada para sempre.
    """
    return _commits_ambiguos.get()


class _Conn(psycopg.Connection):
    """Conexão que marca o commit ambíguo. Ver `commits_ambiguos`.

    Cobre também o commit implícito do `with get_conn() as conn:` — o
    `Connection.__exit__` do psycopg chama este mesmo `commit()`.
    """

    def commit(self) -> None:
        try:
            super().commit()
        except BaseException:
            _commits_ambiguos.set(_commits_ambiguos.get() + 1)
            raise


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL não está definido.")
        _pool = ConnectionPool(
            database_url,
            connection_class=_Conn,
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX_SYNC", "8")),
            timeout=float(os.getenv("DB_CONNECT_TIMEOUT", "30")),
            kwargs={"row_factory": dict_row},
            reset=_reset_conn,
            open=True,
        )
        return _pool


def _reset_conn(conn) -> None:
    """Devolve a conexão ao pool no estado padrão (transacional).

    O psycopg_pool faz rollback sozinho ao devolver, mas NÃO desfaz um
    `conn.autocommit = True` — e uma conexão em autocommit é uma conexão sem
    rollback: cada statement já está commitado, então um erro no meio de uma
    operação deixa metade da escrita gravada. O init_db precisa de autocommit
    para a DDL (locks) e pegava a conexão do pool; sem esta rede, ela voltava
    envenenada e contaminava transações de dinheiro para sempre. Medido: 6 de 6
    `create_investment_db` que estouraram INSUFFICIENT_ACCOUNT depois de um
    init_db deixaram o investimento criado.
    """
    if conn.autocommit:
        conn.autocommit = False


def get_conn(timeout: float | None = None):
    """Retorna um conn do pool. Compatível com `with get_conn() as conn:`.

    `timeout` (segundos) é o teto de espera POR ESTA aquisição; `None` usa o do
    pool (`DB_CONNECT_TIMEOUT`, 30s), que é o comportamento de sempre e o de
    todos os chamadores menos um.

    Existe para quem tem cliente HTTP esperando: a rota de reconexão do Open
    Finance promete um prazo único para a operação inteira, e sem isto a espera
    do pool ficava fora dele — o POST podia estourar o prazo em até 30s por
    conexão, e ainda COMMITAR depois de o cliente ter desistido (Codex #166,
    P2). Quem não passa nada não muda em nada.
    """
    return _get_pool().connection(timeout=timeout)


def close_pool() -> None:
    """Fecha o pool e zera o singleton — a próxima chamada reabre lendo
    DATABASE_URL de novo.

    Existe para a suíte: o conftest cria um database por execução e precisa
    soltar as conexões antes de derrubá-lo no fim. Em produção o pool vive
    enquanto o processo viver, então isto não é chamado.
    """
    global _pool
    with _pool_lock:
        if _pool is None:
            return
        try:
            _pool.close()
        finally:
            _pool = None


# ── comparação de categoria case- E acento-insensível ────────────────────────
# Dados legados guardam a mesma categoria com/sem acento ("alimentacao" vs
# "alimentação"). Pra somar/filtrar por categoria sem perder linhas, comparamos
# ambos os lados normalizados (lower + remove acento) via translate — portável,
# não depende da extensão unaccent.
_CAT_ACCENTS_FROM = "áàâãäéèêëíìîïóòôõöúùûüç"
_CAT_ACCENTS_TO   = "aaaaaeeeeiiiiooooouuuuc"


def cat_norm_sql(expr: str) -> str:
    """Fragmento SQL que normaliza `expr` (coluna ou placeholder %s) pra
    comparação de categoria case- e acento-insensível."""
    return f"translate(lower({expr}), '{_CAT_ACCENTS_FROM}', '{_CAT_ACCENTS_TO}')"


# Rótulo de quem está SEM categoria. NULL e '' são a mesma coisa pro usuário —
# `db/analytics.py` já colapsa os dois assim.
CAT_VAZIA_LABEL = "sem categoria"
# Visual neutro (cinza-ardósia, não o roxo que é o default da coluna) de quem não tem linha no
# catálogo `user_categories`: "sem categoria" e a categoria que só aparece nos lançamentos.
CAT_NEUTRA_EMOJI = "🏷️"
CAT_NEUTRA_COR = "#94A3B8"
# Cores tratadas como "sem cor": `#7c3aed`, o default da coluna `user_categories.color` (WhatsApp, IA e
# cartões criam a categoria assim; o seletor do /app não o oferece, então ninguém o escolheu), e `#64748b`,
# a semente do "outros". `#64748b` TAMBÉM é um swatch do seletor do painel antigo (dashboard.js,
# CATEGORY_COLOR_OPTIONS): quem o escolheu lá passa a ver `CAT_NEUTRA_COR` na API v2, por decisão do dono
# (cinza unificado). Saem neutras, no lugar de pintar de roxo tudo que o usuário não coloriu.
CAT_COR_PADRAO = {"#7c3aed", "#64748b"}


def cat_visual(emoji, cor) -> dict:
    """`{emoji, icone, cor}` de uma linha do catálogo, nunca vazio nem inválido: o writer só faz `strip()`.
    `icone` é o nome Phosphor do emoji (`db/cat_icones.py`); o emoji segue no payload (app instalado não atualiza)."""
    emoji = (emoji or "").strip() or CAT_NEUTRA_EMOJI
    valida = bool(cor) and re.fullmatch(r"#[0-9a-fA-F]{6}", cor) and cor.lower() not in CAT_COR_PADRAO
    return {"emoji": emoji, "icone": icone_da_categoria(emoji), "cor": cor if valida else CAT_NEUTRA_COR}


def cat_key_sql(expr: str) -> str:
    """Chave de AGRUPAMENTO e de CASAMENTO de categoria: `cat_norm_sql` com o
    vazio colapsado em `CAT_VAZIA_LABEL`.

    Existe porque as duas pontas tinham que ser a MESMA expressão e não eram: o
    donut do dashboard agrupava por `COALESCE(categoria,'sem categoria')` e a
    lista de lançamentos casava contra a coluna CRUA. Resultado: a barra "sem
    categoria" dizia R$ 100 e a lista abria vazia — `norm(NULL)` não é
    'sem categoria'. Chega em produção pela importação de cartão do Open Finance
    sem categoria do provedor (`db/open_finance.py` → `add_imported_credit_purchase`,
    que grava `credit_transactions.categoria` NULO).
    """
    return cat_norm_sql(f"coalesce(nullif({expr}, ''), '{CAT_VAZIA_LABEL}')")


_CAT_TABELA = str.maketrans(_CAT_ACCENTS_FROM, _CAT_ACCENTS_TO)


def cat_key(valor: str | None) -> str:
    """`cat_key_sql` em Python: a mesma chave de casamento (minúsculas, sem acento, vazio = `CAT_VAZIA_LABEL`)."""
    return (valor or CAT_VAZIA_LABEL).lower().translate(_CAT_TABELA)


# ── `tipo` de `launches`: a forma legada conta junto ──────────────────────────
# Nenhum escritor de hoje grava 'saida'/'entrada' (ver o comentário de
# `_TIPO_ALIASES`, db/accounts.py), mas muito read path ainda trata esses valores
# como tipo. Fonte ÚNICA em SQL das duas formas — `tests/test_tipo_legado_no_dashboard.py`
# compara esta tabela com o `_TIPO_ALIASES` do Python, que é a mesma regra do
# outro lado (§0.7: duplicação inevitável exige teste que compare as duas).
#
# Por que importa: as barras de categoria do dashboard contam
# `tipo IN ('despesa','saida')` e o "Gastos do mês" lia só `despesa`. Com uma
# linha legada na base, a soma das barras e o gráfico diário passavam do total
# exibido, e o "sobrou este mês" saía maior do que é.
TIPO_DESPESA_SQL = "tipo IN ('despesa', 'saida')"
TIPO_RECEITA_SQL = "tipo IN ('receita', 'entrada')"

# Colapsa a forma legada na moderna. Para quem AGRUPA por tipo (o "Gastos do
# mês", a evolução por mês); quem só FILTRA usa os dois de cima.
TIPO_CANON_SQL = (
    f"CASE WHEN {TIPO_DESPESA_SQL} THEN 'despesa' "
    f"WHEN {TIPO_RECEITA_SQL} THEN 'receita' ELSE tipo END"
)


# `has_time`: dá pra confiar na HORA do lançamento ou só na data?
# Decide se quem lê escreve "10/03, 00:30" ou só "10/03". QUAIS linhas caem em
# cada galho está no docstring de `launch_day` (utils_date), que é o dono dessa
# enumeração — aqui não se repete a lista, porque as cópias divergiram (§0.7).
#
# Quem PRODUZ `has_time` — 3 queries, 5 sites, e só 3 deles passam por este CASE:
#   - Visão Geral do dashboard (query 4): a perna de `launches` usa o CASE
#     (frontend/finance_bot_websocket_custom.py:520); a de CRÉDITO fixa `true` à
#     mão, com `posted_at` NULO e `criado_em` = `credit_transactions.created_at`
#     (:461, dentro do `credit_union_sql`);
#   - `list_launches` (db/accounts.py:128), que lê só `launches`;
#   - `list_launches_by_category`: a perna de `launches` usa o CASE
#     (db/accounts.py:836); a de CRÉDITO fixa `false`, com
#     `posted_at` = `purchased_at` (:791).
# As duas pernas de CRÉDITO respondem coisas DIFERENTES sobre a mesma compra
# (instante em que a linha foi gravada × dia em que a compra aconteceu), então o
# dia pode divergir entre as duas telas — medido, e descrito onde cada uma mora
# (docstring de `list_launches_by_category`; frontend/dashboard.js).
# Há um 6º site, morto: `true AS has_time` dentro do `COUNT(*)` da query 3
# (fbwc:498) — a coluna nunca sai da subquery.
# Quem LÊ — 4 sites em 3 arquivos, e um deles NÃO é tela: `fmtLaunchWhen` mais o
# editor do dashboard (frontend/dashboard.js:483 e :8353), o `fmtLaunchTimeHome`
# do home.html (frontend/home.html:774), e o texto do WhatsApp
# (core/handlers/launches.py:620).
#
# O dia continua saindo de `launch_day`, em Python, e não de um `::date`: o
# `has_time`/`posted_at` decide QUAL campo manda (data × instante), coisa que
# nenhum cast resolve. O que mudou é o motivo — desde
# `utils_date.align_process_tz` a sessão do Postgres roda no fuso do app (via
# `PGTZ`, sem uma linha aqui), então um `::date` já não devolveria o dia errado;
# ele só continuaria respondendo a pergunta errada.
LAUNCH_HAS_TIME_SQL = """
        CASE
          WHEN efeitos ? 'of_original'
            THEN COALESCE((efeitos->>'time_known')::boolean, false)
          WHEN source = 'ofx' THEN false
          WHEN source = 'open_finance'
            THEN COALESCE((efeitos->>'time_known')::boolean, false)
          ELSE true
        END"""


# Catálogo deduplicado por nome normalizado, pronto pra virar CTE de join. `name` é a
# grafia que vence (o `nome` de `GET /api/v2/categorias`; `cat` é a `chave`).
# `user_categories` é única só no par EXATO (user_id, name), então 'cafe' e
# 'café' coexistem: um join por valor normalizado contra a tabela crua devolve
# a mesma linha de orçamento/gasto duas vezes e dobra dinheiro na tela.
# Desempate idêntico ao de `user_category_display_map` (db/categories.py):
# vence a do seed (is_system) e, entre iguais, o menor nome alfabético — não
# pode depender de `id`, que muda com quem foi criado/apagado antes.
CAT_META_SQL = (
    f"select distinct on ({cat_norm_sql('name')}) "
    f"       {cat_norm_sql('name')} as cat, emoji, color, name "
    "  from user_categories where user_id = %s "
    f" order by {cat_norm_sql('name')}, is_system desc, name asc"
)


# Desempate canônico entre GÊMEAS de `category_budgets` (única só no par EXATO
# (user_id, categoria): 'cafe' e 'café' coexistem em dado legado). Mesma regra
# do `CAT_META_SQL` acima, sem o `is_system` — que essa tabela não tem: vence o
# menor nome alfabético. Fonte única de quem ganha; sem isto cada leitor
# desempata pela ordem de inserção e o donut mostra um limite e o bot outro.
CAT_CANON_ORDER = " order by categoria limit 1"
