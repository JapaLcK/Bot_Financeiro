"""
Testa db/schema_repairs — migrations que corrigem FKs em users(id).
"""
from db import get_conn
from db.schema_repairs import repair_user_fk_cascades


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
