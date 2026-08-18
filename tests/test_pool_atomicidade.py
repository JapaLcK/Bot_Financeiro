"""O pool não pode devolver conexão em autocommit.

Conexão em autocommit é conexão sem rollback: cada statement já está gravado,
então um erro no meio de uma operação deixa metade da escrita no banco. O
`init_db` precisa de autocommit para rodar a DDL sem segurar locks, e pegava a
conexão do POOL — que depois voltava envenenada para o resto do processo.

O estrago era em caminho de dinheiro. Medido antes da correção: depois de um
`init_db`, 6 de 6 `create_investment_db` que estouraram INSUFFICIENT_ACCOUNT
deixaram o investimento criado no banco.
"""
import pytest

import db
from db.connection import get_conn


def test_conexao_volta_do_pool_sem_autocommit():
    with get_conn() as conn:
        conn.autocommit = True          # simula o que o init_db faz

    # a mesma conexão pode voltar em qualquer um dos próximos checkouts
    for _ in range(8):
        with get_conn() as conn:
            assert conn.autocommit is False


def test_init_db_nao_envenena_o_pool():
    db.init_db()
    for _ in range(8):
        with get_conn() as conn:
            assert conn.autocommit is False


def test_rollback_continua_valendo_depois_do_init_db(user_id):
    """A prova de ponta a ponta: transação que estoura no meio não deixa rastro."""
    db.init_db()
    db.add_launch_and_update_balance(user_id, "receita", 10, None, "seed")

    for i in range(6):
        with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
            db.create_investment_db(
                user_id, f"Inv {i}", 0.14, "yearly", "n", initial_amount=800.0,
            )

    assert db.list_investments(user_id) == []
    assert float(db.get_balance(user_id)) == 10.0
