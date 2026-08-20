"""Espaços financeiros — Fase 1: schema, default lazy e CRUD básico.

Cobre os invariantes do modelo antes de qualquer leitura/escrita por espaço:
- exatamente 1 espaço default por usuário (idempotente);
- unicidade de nome por usuário;
- isolamento entre usuários;
- `on delete set null`: apagar um espaço NÃO apaga o lançamento associado.
"""
import db
from db import get_conn


def test_ensure_default_space_idempotente(user_id: int):
    a = db.ensure_default_space(user_id)
    b = db.ensure_default_space(user_id)
    assert a == b

    spaces = db.list_spaces(user_id)
    defaults = [s for s in spaces if s["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == a
    assert defaults[0]["name"] == db.spaces.DEFAULT_SPACE_NAME


def test_create_space_unico_por_nome(user_id: int):
    db.ensure_default_space(user_id)
    s1 = db.create_space(user_id, "Fazenda", emoji="🚜")
    s2 = db.create_space(user_id, "Fazenda")  # mesmo nome → mesma linha
    assert s1["id"] == s2["id"]
    assert not s1["is_default"]

    names = {s["name"] for s in db.list_spaces(user_id)}
    assert {"Fazenda", db.spaces.DEFAULT_SPACE_NAME} <= names


def test_espacos_isolados_por_usuario(user_id: int):
    other = user_id + 1
    db.ensure_user(other)
    try:
        db.create_space(user_id, "Empresa")
        mine = {s["name"] for s in db.list_spaces(user_id)}
        theirs = {s["name"] for s in db.list_spaces(other)}
        assert "Empresa" in mine
        assert "Empresa" not in theirs
    finally:
        from tests.conftest import _cleanup_user
        _cleanup_user(other)


def test_on_delete_set_null_preserva_launch(user_id: int):
    """Apagar o espaço zera o space_id do lançamento, mas não apaga o lançamento."""
    space = db.create_space(user_id, "Casa")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into launches(user_id, tipo, valor, categoria, space_id)
                values (%s, 'despesa', 10, 'teste', %s)
                returning id
                """,
                (user_id, space["id"]),
            )
            launch_id = cur.fetchone()["id"]
            conn.commit()

            cur.execute("delete from financial_spaces where id=%s", (space["id"],))
            conn.commit()

            cur.execute("select space_id from launches where id=%s", (launch_id,))
            row = cur.fetchone()
    assert row is not None, "lançamento não deve ser apagado junto com o espaço"
    assert row["space_id"] is None


def test_init_db_idempotente_2x():
    """Rodar init_db de novo não pode quebrar (DDL idempotente)."""
    db.init_db()
    db.init_db()
