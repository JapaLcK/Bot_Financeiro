"""
Testa db/schema_repairs — migrations que corrigem FKs em users(id).
"""
import re
from pathlib import Path

from db import get_conn
from db.schema_repairs import _USER_FK_SET_NULL_TABLES, repair_user_fk_cascades


def _fk_action(cur, table_name: str, constraint_name: str | None = None) -> str | None:
    """Retorna o confdeltype (1 char) da FK que referencia users em `table_name`."""
    if constraint_name:
        cur.execute(
            """
            select confdeltype from pg_constraint
             where contype = 'f' and conrelid = %s::regclass and conname = %s
            """,
            (table_name, constraint_name),
        )
    else:
        cur.execute(
            """
            select confdeltype from pg_constraint
             where contype = 'f' and conrelid = %s::regclass
               and confrelid = 'users'::regclass
             limit 1
            """,
            (table_name,),
        )
    row = cur.fetchone()
    return row["confdeltype"] if row else None


def test_repair_converts_no_action_to_cascade():
    test_table = "_test_fk_repair_no_action"
    with get_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f"drop table if exists {test_table}")
            cur.execute(
                f"""
                create table {test_table} (
                  id bigserial primary key,
                  user_id bigint references users(id) on delete no action
                )
                """
            )
            try:
                assert _fk_action(cur, test_table) == "a"
                changes = repair_user_fk_cascades(cur)
                assert _fk_action(cur, test_table) == "c"
                assert any(c["table"] == test_table for c in changes)
            finally:
                cur.execute(f"drop table if exists {test_table}")


def test_repair_preserves_set_null_for_audit_tables():
    """auth_login_events deve ficar SET NULL — é audit log."""
    cname = "auth_login_events_user_id_fkey"
    with get_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Força pra NO ACTION
            cur.execute(
                f"""
                alter table auth_login_events
                drop constraint {cname},
                add constraint {cname}
                foreign key (user_id) references users(id) on delete no action
                """
            )
            assert _fk_action(cur, "auth_login_events", cname) == "a"

            changes = repair_user_fk_cascades(cur)

            # Repair deve ter voltado pra SET NULL (não CASCADE)
            assert _fk_action(cur, "auth_login_events", cname) == "n"
            event_change = next(
                (c for c in changes if c["table"] == "auth_login_events"), None
            )
            assert event_change is not None
            assert event_change["to"] == "SET NULL"


def test_repair_is_idempotent():
    """Após init_db a base já está correta — segunda rodada não muda nada."""
    with get_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            repair_user_fk_cascades(cur)  # garante estado correto
            second = repair_user_fk_cascades(cur)
            assert second == []


# ─────────────────────────────────────────────────────────────────────────────
# §0.7: DDL × configuração do repair — duas fontes para a MESMA regra.
#
# `create table if not exists` nunca converge tabela que já existe, então o que
# vale em runtime é sempre o `repair_user_fk_cascades`, chamado em todo
# `init_db` (db/schema.py). Um DDL que declare o oposto do alvo do repair é
# documentação que mente: ele é sobrescrito no boot seguinte.
# ─────────────────────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[1]
_DDL_FILES = ("db/schema.py", "core/admin_dashboard.py")

_TABLE_RE = re.compile(
    r"(?:create\s+table\s+(?:if\s+not\s+exists\s+)?|alter\s+table\s+)([a-z_][a-z0-9_]*)",
    re.I,
)
_USER_FK_RE = re.compile(
    r"references\s+users\s*\(\s*id\s*\)\s*(?:on\s+delete\s+(cascade|set\s+null|no\s+action))?",
    re.I,
)


def _declared_user_fk_actions() -> dict[str, set[str]]:
    """Lê o DDL do código: tabela -> {`on delete` declarado} de cada FK em users(id).

    Sem cláusula explícita = `no action` (o default do Postgres).
    """
    declared: dict[str, set[str]] = {}
    for rel in _DDL_FILES:
        text = (_ROOT / rel).read_text(encoding="utf-8")
        marks = [(m.start(), m.group(1).lower()) for m in _TABLE_RE.finditer(text)]
        for fk in _USER_FK_RE.finditer(text):
            table = None
            for pos, name in marks:
                if pos >= fk.start():
                    break
                table = name
            if table is None:
                continue
            action = " ".join((fk.group(1) or "no action").lower().split())
            declared.setdefault(table, set()).add(action)
    return declared


def test_ddl_nao_contradiz_o_alvo_do_repair():
    """Nenhuma FK em users(id) pode declarar no DDL um `on delete` que o
    `repair_user_fk_cascades` vá sobrescrever no boot seguinte."""
    declared = _declared_user_fk_actions()

    # Piso anti-tautologia: se o parser não achar o DDL (regex quebrada, arquivo
    # movido), o teste passaria vazio afirmando nada. Estas tabelas TÊM de estar.
    faltando = (_USER_FK_SET_NULL_TABLES | {"system_event_logs"}) - set(declared)
    assert not faltando, f"parser não achou o DDL de {sorted(faltando)} — regex quebrada?"

    divergentes = {}
    for table, actions in declared.items():
        alvo = "set null" if table in _USER_FK_SET_NULL_TABLES else "cascade"
        if actions != {alvo}:
            divergentes[table] = {"ddl": sorted(actions), "repair": alvo}

    assert not divergentes, (
        "DDL declara um `on delete` que o repair_user_fk_cascades sobrescreve: "
        + repr({t: divergentes[t] for t in sorted(divergentes)})
        + ". Alinhe o DDL com _USER_FK_SET_NULL_TABLES (db/schema_repairs.py) — "
        "quem vale em runtime é o repair, não o `create table if not exists`."
    )


def test_delete_user_preserva_linha_de_pii_access_log(user_id):
    """Efeito da decisão do dono: `pii_access_log` é SET NULL, então apagar o
    titular ANONIMIZA a linha de compliance em vez de destruí-la.

    Controle negativo: tire "pii_access_log" de _USER_FK_SET_NULL_TABLES e o
    repair converte a FK para CASCADE — o delete leva a linha junto e o
    `select` abaixo não acha nada.
    """
    from conftest import _cleanup_user

    with get_conn() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            repair_user_fk_cascades(cur)  # aplica o alvo atual à FK real
            assert _fk_action(cur, "pii_access_log") == "n"

            cur.execute(
                """
                insert into pii_access_log (purpose, actor, subject_user_id, field)
                values ('test_fk', 'system:test', %s, 'email')
                returning id
                """,
                (user_id,),
            )
            row_id = cur.fetchone()["id"]
            try:
                _cleanup_user(user_id)  # o delete real da conta (users + filhos)

                cur.execute(
                    "select subject_user_id, purpose from pii_access_log where id = %s",
                    (row_id,),
                )
                row = cur.fetchone()
                assert row is not None, "CASCADE apagou a trilha de compliance"
                assert row["subject_user_id"] is None
                assert row["purpose"] == "test_fk"  # o resto do registro sobrevive
            finally:
                cur.execute("delete from pii_access_log where id = %s", (row_id,))
