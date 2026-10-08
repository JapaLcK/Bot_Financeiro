"""`import_ofx_launches_bulk` trava o usuário ANTES do INSERT (ordem do `_lock_user`).

Antes ele inseria em `launches` (a trigger `assign_launch_user_seq` toma o advisory lock do
usuário) e só depois dava `update accounts`: a ordem inversa de todo escritor que atualiza o
saldo — par com `add_launch_and_update_balance`, depósito em caixinha e aporte, nas duas
ordens de chegada. A pausa é por statement (tests/_pausa_sql.py), sem sleep.

`add_launch` com o outro primeiro passa também sem o conserto: o `ensure_user` do import
espera o `update accounts` dele (controle, não discrimina). Os demais pares dão
DeadlockDetected sem o `_lock_user`.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import db
from tests._pausa_sql import PausaSql, sem_deadlock


def _linhas(n, tag):
    return [{"tipo": "despesa", "valor": Decimal("3"), "delta": Decimal("-3"), "categoria": "outros",
             "alvo": None, "nota": "o", "criado_em": datetime.now(timezone.utc),
             "external_id": f"{tag}-{i}"} for i in range(n)]


def _importa(uid, tag="a", n=2):
    return db.import_ofx_launches_bulk(
        uid, _linhas(n, tag), file_hash=f"h-{tag}-{uid}", bank_id="1", acct_id="2",
        acct_type="CHECKING", dt_start=None, dt_end=None)


def _semeia(uid):
    db.add_launch_and_update_balance(uid, "receita", 1000, "salario", "x")
    db.create_pocket(uid, "viagem")
    db.create_investment(uid, "cdb", 0.12, "yearly")


def _trava_accounts(q):  # statement que já tomou o lock de accounts (mutex ou update)
    return "from accounts" in q and "for update" in q or "update accounts" in q


# nome -> (operação do outro lado, statement em que ela já tem o 1º lock)
OUTROS = {
    "add_launch": (lambda u: db.add_launch_and_update_balance(u, "despesa", 5, None, "x", "mercado"),
                   lambda q: "update accounts" in q),
    "pocket_deposit": (lambda u: db.pocket_deposit_from_account(u, "viagem", 50), _trava_accounts),
    "inv_deposit": (lambda u: db.investment_deposit_from_account(u, "cdb", 50), _trava_accounts),
}


@pytest.mark.parametrize("import_primeiro", [True, False], ids=["import_primeiro", "outro_primeiro"])
@pytest.mark.parametrize("outro", list(OUTROS))
def test_import_ofx_e_escritor_do_saldo_nao_dao_deadlock(user_id, monkeypatch, outro, import_primeiro):
    _semeia(user_id)
    op, quando_outro = OUTROS[outro]
    fn_import = lambda: _importa(user_id)
    fn_outro = lambda: op(user_id)
    if import_primeiro:
        pausa = PausaSql(monkeypatch, "a", lambda q: "insert into launches" in q)
        a, b = pausa.roda(fn_import, fn_outro)
        r_import, r_outro = a, b
    else:
        pausa = PausaSql(monkeypatch, "a", quando_outro)
        a, b = pausa.roda(fn_outro, fn_import)
        r_import, r_outro = b, a
    sem_deadlock(a, b)
    assert pausa.casou and pausa.outro_travou, "a segunda operação devia esperar o lock da primeira"
    assert isinstance(r_import, dict) and r_import["inserted"] == 2, r_import
    assert not isinstance(r_outro, Exception), r_outro


def test_import_legitimo_grava_igual_e_reimportar_pula(user_id):
    """POSITIVO: o lock não muda o resultado do import."""
    db.add_launch_and_update_balance(user_id, "receita", 100, "salario", "x")
    r = _importa(user_id, "p", 3)
    assert (r["skipped_same_file"], r["inserted"], r["duplicates"]) == (False, 3, 0)
    assert db.get_balance(user_id) == 100 - 9
    assert _importa(user_id, "p", 3)["skipped_same_file"] is True
    assert db.get_balance(user_id) == 100 - 9
