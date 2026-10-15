"""CHECK de valor finito em TODA coluna numérica do schema `public`.

`nan <= 0` é False, então `NaN`/`Infinity`/`1e308` passavam pelas guardas por porta
(`utils_text.valor_valido`/`valor_finito`) e entravam em coluna `numeric`. Estas
constraints são o piso no BANCO: `finito_<coluna> CHECK (abs(col) <= VALOR_MAX)`.
`VALOR_MAX` é limite técnico (nunca teto de negócio); NULL passa; NaN e ±Infinity não
(no Postgres NaN é maior que todo numérico finito, então `abs(NaN) <= N` é false).

Não roda no `init_db`: nasce em tarefa de fundo (`ensure_finito_checks_once`), como os
índices de FK (`db/schema_repairs.py`). Coluna numérica nova é coberta no boot seguinte.
O nome é `finito_<coluna>` (único por tabela; `<tabela>_<coluna>_finito` estoura 63 bytes).

Ordem de segurança: pré-scan TUDO-OU-NADA (qualquer coluna suja => não adiciona nada,
ERROR com `tabela.coluna` e nunca o dado) -> `ADD ... NOT VALID` (só lock curto,
`lock_timeout`) -> `VALIDATE` (não bloqueia escrita). Erro vira WARNING só com sqlstate.
"""
from __future__ import annotations

import logging
from collections import defaultdict

import psycopg
from psycopg import sql

from utils_text import VALOR_MAX

from .connection import get_conn

logger = logging.getLogger(__name__)

PREFIXO_FINITO = "finito_"
# Frases ao usuário quando o CHECK barra o valor (fonte única: API/monólito e conversa).
MSG_VALOR_API = "Valor inválido: confira o número."
MSG_VALOR_WHATSAPP = "⚠️ Não consegui registrar esse valor. Confere o número e manda de novo."
FINITO_LOCK = 728_531_006  # lock de SESSÃO próprio (mesmo molde do FK_INDEXES_LOCK)

# Colunas sem o CHECK, com o motivo. Só entra aqui com motivo escrito.
SEM_CHECK_FINITO: dict[tuple[str, str], str] = {
    ("pix_unmatched_payments", "value"): "fila de conciliação do Asaas: NaN prenderia o evento na outbox",
    ("pix_unmatched_payments", "net_value"): "idem; numeric(12,2) já recusa Infinity",
}

_NUMERICOS = ("numeric", "float8", "float4")


def nome_check(coluna: str) -> str:
    return PREFIXO_FINITO + coluna


def expr_finito(coluna: str) -> sql.Composed:
    return sql.SQL("abs({}) <= {}").format(sql.Identifier(coluna), sql.SQL(str(VALOR_MAX)))


def colunas_alvo(cur) -> list[tuple[str, str]]:
    cur.execute(
        "select c.relname as t, a.attname as c from pg_attribute a "
        "join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r' and a.attnum > 0 and not a.attisdropped "
        "and a.atttypid = any(%s::regtype[]) order by 1, 2", (list(_NUMERICOS),))
    return [(r["t"], r["c"]) for r in cur.fetchall() if (r["t"], r["c"]) not in SEM_CHECK_FINITO]


def _constraints(cur) -> dict[tuple[str, str], bool]:
    """{(tabela, conname): validada} das constraints `finito_*` que existem."""
    cur.execute(
        "select c.relname as t, k.conname, k.convalidated as v from pg_constraint k "
        "join pg_class c on c.oid = k.conrelid join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and k.contype = 'c' and k.conname like %s",
        (PREFIXO_FINITO.replace("_", r"\_") + "%",))
    return {(r["t"], r["conname"]): r["v"] for r in cur.fetchall()}


def colunas_fora_do_limite(cur, colunas) -> list[str]:
    """SÓ LEITURA. 'tabela.coluna' das `colunas` com algum valor que o CHECK recusaria
    (NaN, ±Infinity, módulo > VALOR_MAX). Um scan por tabela; nunca devolve o dado."""
    por_tabela: dict[str, list[str]] = defaultdict(list)
    for t, c in colunas:
        por_tabela[t].append(c)
    sujas: list[str] = []
    for t, cols in por_tabela.items():
        itens = sql.SQL(", ").join(
            sql.SQL("bool_or(not ({})) as {}").format(expr_finito(c), sql.Identifier(f"c{i}"))
            for i, c in enumerate(cols))
        cur.execute(sql.SQL("select {} from {}").format(itens, sql.Identifier(t)))
        linha = cur.fetchone()
        sujas += [f"{t}.{c}" for i, c in enumerate(cols) if linha[f"c{i}"]]
    return sujas


def _aviso(tabela: str, exc: psycopg.Error) -> None:  # sqlstate, nunca o texto: ele traz dado
    logger.warning("[finito_checks] %s NÃO atualizada (sqlstate=%s); o próximo boot tenta de novo",
                   tabela, getattr(exc, "sqlstate", None))


def ensure_finito_checks(cur) -> dict:
    """Idempotente. Exige AUTOCOMMIT. Devolve {'faltando', 'criadas', 'sujas', 'falhas'}:
    listas de 'tabela.coluna' (as `falhas` são nomes de tabela)."""
    existentes = _constraints(cur)
    faltando = [(t, c) for t, c in colunas_alvo(cur) if (t, nome_check(c)) not in existentes]
    out = {"faltando": [f"{t}.{c}" for t, c in faltando], "criadas": [], "sujas": [], "falhas": []}
    cur.execute("select current_setting('lock_timeout') as v")
    antigo = cur.fetchone()["v"]
    cur.execute("select set_config('lock_timeout', '1s', false)")
    try:
        if faltando:
            out["sujas"] = colunas_fora_do_limite(cur, faltando)
            if out["sujas"]:  # tudo-ou-nada: com dado sujo o VALIDATE falharia e deixaria o resto pela metade
                # ERROR (1x por boot): entra no `backend_errors_24h`; só `tabela.coluna`, nunca o dado
                logger.error("[finito_checks] valor fora do limite em %s; NADA adicionado até "
                             "alguém limpar essas colunas", ", ".join(out["sujas"]))
                return out
            por_tabela: dict[str, list[str]] = defaultdict(list)
            for t, c in faltando:
                por_tabela[t].append(c)
            for t, cols in por_tabela.items():
                adds = sql.SQL(", ").join(
                    sql.SQL("add constraint {} check ({}) not valid").format(
                        sql.Identifier(nome_check(c)), expr_finito(c)) for c in cols)
                try:
                    cur.execute(sql.SQL("alter table {} {}").format(sql.Identifier(t), adds))
                    out["criadas"] += [f"{t}.{c}" for c in cols]
                except psycopg.Error as exc:
                    _aviso(t, exc)
                    out["falhas"].append(t)
        for (t, nome), validada in _constraints(cur).items():
            if validada:
                continue
            try:
                cur.execute(sql.SQL("alter table {} validate constraint {}").format(
                    sql.Identifier(t), sql.Identifier(nome)))
            except psycopg.Error as exc:
                _aviso(t, exc)
                if t not in out["falhas"]:
                    out["falhas"].append(t)
    finally:
        cur.execute("select set_config('lock_timeout', %s, false)", (antigo,))
    return out


def ensure_finito_checks_once() -> dict | None:
    """`ensure_finito_checks` numa conexão em autocommit, fora do `init_db`.
    `None` = outro processo já está rodando."""
    with get_conn() as conn:
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("select pg_try_advisory_lock(%s) as ok", (FINITO_LOCK,))
                if not cur.fetchone()["ok"]:
                    return None
                try:
                    return ensure_finito_checks(cur)
                finally:
                    cur.execute("select pg_advisory_unlock(%s)", (FINITO_LOCK,))
        finally:
            conn.autocommit = False


def violacao_finito(exc: BaseException | None) -> str | None:
    """Nome da constraint `finito_*` se `exc` (ou sua causa/contexto, até 5 níveis) é um
    CheckViolation dela; senão None. Nunca use `str(exc)`: traz 'Failing row contains (...)'."""
    for _ in range(5):
        if exc is None:
            return None
        if isinstance(exc, psycopg.errors.CheckViolation):
            nome = getattr(exc.diag, "constraint_name", None) or ""
            return nome if nome.startswith(PREFIXO_FINITO) else None
        exc = exc.__cause__ or exc.__context__
    return None

