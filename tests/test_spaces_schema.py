"""Espaços financeiros — invariantes do schema.

A API CRUD de espaços saiu sem chamador; o schema ficou. Estes testes
travam as proteções dele, com as linhas inseridas direto por SQL:
- exatamente 1 espaço default por usuário (índice parcial);
- isolamento entre usuários (FK composta e trigger de open_finance_accounts);
- `on delete set null`: apagar um espaço NÃO apaga o lançamento associado.
"""
from decimal import Decimal

import pytest
from psycopg import errors as pg_errors

import db
from db import get_conn


def _espaco(user_id: int, nome: str, default: bool = False) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into financial_spaces (user_id, name, is_default) "
                "values (%s, %s, %s) returning id",
                (user_id, nome, default),
            )
            space_id = cur.fetchone()["id"]
        conn.commit()
    return space_id


def test_on_delete_set_null_preserva_launch(user_id: int):
    """Apagar o espaço zera o space_id do lançamento, mas não apaga o lançamento."""
    space_id = _espaco(user_id, "Casa")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into launches(user_id, tipo, valor, categoria, space_id)
                values (%s, 'despesa', 10, 'teste', %s)
                returning id
                """,
                (user_id, space_id),
            )
            launch_id = cur.fetchone()["id"]
            conn.commit()

            cur.execute("delete from financial_spaces where id=%s", (space_id,))
            conn.commit()

            cur.execute("select space_id from launches where id=%s", (launch_id,))
            row = cur.fetchone()
    assert row is not None, "lançamento não deve ser apagado junto com o espaço"
    assert row["space_id"] is None


def test_init_db_idempotente_2x():
    """Rodar init_db de novo não pode quebrar (DDL idempotente)."""
    db.init_db()
    db.init_db()


# ── Isolamento por usuário (P1 da revisão) ───────────────────────────────────

def test_banco_bloqueia_dois_defaults(user_id: int):
    """O índice parcial uq_financial_spaces_one_default garante 1 default."""
    _espaco(user_id, "Pessoal", default=True)
    with pytest.raises(pg_errors.UniqueViolation, match='"uq_financial_spaces_one_default"'):
        _espaco(user_id, "Outro Default", default=True)


def test_launch_nao_referencia_espaco_de_outro_usuario(user_id: int):
    """FK composta (user_id, space_id): espaço tem de ser do mesmo dono."""
    other = user_id + 1
    db.ensure_user(other)
    try:
        alheio = _espaco(other, "Fazenda")
        with pytest.raises(pg_errors.ForeignKeyViolation, match='"fk_launches_space"'):
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into launches(user_id, tipo, valor, categoria, space_id)
                        values (%s, 'despesa', 10, 'teste', %s)
                        """,
                        (user_id, alheio),  # espaço de OUTRO usuário
                    )
                    conn.commit()
    finally:
        from tests.conftest import _cleanup_user
        _cleanup_user(other)


def test_of_account_trigger_bloqueia_espaco_de_outro_usuario(user_id: int):
    """open_finance_accounts.space_id só aceita espaço do dono da conexão."""
    other = user_id + 1
    db.ensure_user(other)
    try:
        alheio = _espaco(other, "Empresa")
        connection = db.save_pluggy_open_finance_item(
            user_id,
            {"id": f"item-space-{user_id}", "connector": {"id": 612, "name": "Nubank"}, "status": "UPDATED"},
        )
        db.save_open_finance_sync(
            connection["id"],
            [{
                "provider_account_id": f"acc-space-{user_id}",
                "name": "Nubank Conta",
                "type": "BANK",
                "subtype": "CHECKING_ACCOUNT",
                "currency": "BRL",
                "balance": Decimal("100"),
                "raw": {},
                "transactions": [],
            }],
        )
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select id from open_finance_accounts where connection_id=%s",
                    (connection["id"],),
                )
                acc_id = cur.fetchone()["id"]

        with pytest.raises(pg_errors.RaiseException, match="nao pertence ao dono"):
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "update open_finance_accounts set space_id=%s where id=%s",
                        (alheio, acc_id),  # espaço de OUTRO usuário
                    )
                    conn.commit()
    finally:
        from tests.conftest import _cleanup_user
        _cleanup_user(other)


def test_fks_compostas_presentes():
    """Trava o schema: as FKs de space_id referenciam (user_id, id), não só id."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select conname, pg_get_constraintdef(oid) as def
                from pg_constraint
                where conname in ('fk_launches_space', 'fk_credit_tx_space')
                """
            )
            defs = {r["conname"]: r["def"] for r in cur.fetchall()}
    assert set(defs) == {"fk_launches_space", "fk_credit_tx_space"}
    for d in defs.values():
        assert "financial_spaces(user_id, id)" in d
        assert "SET NULL" in d.upper()
