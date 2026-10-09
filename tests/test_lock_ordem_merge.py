"""`_merge_users` trava origem e destino (`_lock_user`) em ORDEM DE ID, antes das checagens.

Sem o lock, `update launches` → `update accounts` → `delete from accounts` fechava ciclo com o
reset e com outro merge; sem a ordem por id, merge(A→B) × merge(B→A) fecha ciclo entre os dois
mutexes. A pausa é por statement (tests/_pausa_sql.py). O par com o undo comum fica em
tests/test_lock_ordem_undo_comum.py (depende do conserto do undo).
"""
import psycopg
import pytest

import db
import db.privacy as privacy
from db.users import merge_users
from tests._pausa_sql import PausaSql, sem_deadlock


def _semeia(uid, valor=100):
    db.add_launch_and_update_balance(uid, "receita", valor, "salario", "x")


def _lock_de_accounts(q):
    return "from accounts" in q and "for update" in q


@pytest.mark.parametrize("merge_primeiro", [True, False], ids=["merge_primeiro", "reset_primeiro"])
def test_merge_e_reset_da_origem_nao_dao_deadlock(user_id, monkeypatch, merge_primeiro):
    destino = user_id + 1
    db.ensure_user(destino)
    _semeia(user_id)
    monkeypatch.setattr(privacy, "verify_user_password", lambda *a, **k: True)
    fn_merge = lambda: merge_users(user_id, destino)
    fn_reset = lambda: privacy.reset_user_data(user_id, "x")
    if merge_primeiro:  # pausa com os launches movidos (já com o lock, no conserto)
        pausa = PausaSql(monkeypatch, "a", lambda q: "update launches set user_id" in q)
        r_merge, r_reset = pausa.roda(fn_merge, fn_reset)
    else:
        pausa = PausaSql(monkeypatch, "a", lambda q: "update accounts set balance = 0" in q)
        r_reset, r_merge = pausa.roda(fn_reset, fn_merge)
    sem_deadlock(r_merge, r_reset)
    assert pausa.casou and pausa.outro_travou, "a segunda operação devia esperar a primeira"
    assert r_merge is None, r_merge
    # Reset depois do merge pode recusar por FK (a origem deixou de existir): não é deadlock.
    assert not isinstance(r_reset, Exception) or isinstance(r_reset, psycopg.errors.ForeignKeyViolation), r_reset


def test_merge_a_b_e_merge_b_a_travam_na_mesma_ordem(user_id, monkeypatch):
    """Sem o `sorted`, cada merge pega o mutex da sua origem primeiro e eles se cruzam."""
    a, b = user_id, user_id + 1
    db.ensure_user(b)
    _semeia(a)
    pausa = PausaSql(monkeypatch, "a", _lock_de_accounts)  # pausa com SÓ o 1º mutex pego
    r_ab, r_ba = pausa.roda(lambda: merge_users(a, b), lambda: merge_users(b, a))
    sem_deadlock(r_ab, r_ba)
    assert pausa.casou and pausa.outro_travou
    assert r_ab is None, r_ab


def test_merge_legitimo_move_launches_soma_saldo_e_apaga_a_origem(user_id):
    """POSITIVO: o lock não muda o que o merge faz."""
    destino = user_id + 1
    db.ensure_user(destino)
    _semeia(user_id, 100)
    merge_users(user_id, destino)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s", (destino,))
        assert cur.fetchone()["n"] == 1
        cur.execute("select count(*) as n from launches where user_id=%s", (user_id,))
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from accounts where user_id=%s", (user_id,))
        assert cur.fetchone()["n"] == 0
        conn.commit()
    assert db.get_balance(destino) == 100
