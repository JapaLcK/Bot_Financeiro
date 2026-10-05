"""Ordem de lock por usuário entre launches, caixinhas, investimentos e lotes (#622, #263).

Cada par abaixo era um deadlock de verdade (o Postgres matava uma das duas
transações). A regra que fecha a classe está na docstring de
`db.bank_movements._lock_user`.

Cada operação PAUSA no ponto em que já tem o 1º lock e ainda não pediu o 2º
(`_Corrida.pausa`) e só segue quando a outra chegou ao ponto dela ou quando algum
backend está esperando lock. Sem o `_lock_user` as duas chegam e o ciclo fecha;
com ele a segunda trava na entrada e a primeira segue sozinha. Sem `sleep`.

Nas corridas contra o reset, só "a outra operação primeiro" é perigosa: se o
reset chega antes, a outra espera no `ensure_user` (insert em `accounts`, que
bloqueia sob o UPDATE do reset) e nunca fecha ciclo. A ordem inversa fica aqui
mesmo assim, como controle de que nada trava.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

import db
import db.accounts as accounts
import db.investments as investments
import db.pockets as pockets
import db.privacy as privacy
from tests._espera_lock import _esperar_backend_travado


class _Corrida:
    def __init__(self, monkeypatch, primeiro, segundo, reset_antes_de=None):
        self.chegou = {primeiro: threading.Event(), segundo: threading.Event()}
        self.outro = {primeiro: segundo, segundo: primeiro}
        self.papel = threading.local()
        orig = {n: getattr(m, n) for m, n in (
            (accounts, "_validar_efeitos"), (pockets, "_renomear_no_historico"),
            (privacy, "_table_exists"), (investments, "accrue_investment_db"),
            (pockets, "accrue_pocket_db"))}

        def antes(papel, nome, quando=None):  # pausa e chama o original
            def f(*a, **k):
                if quando is None or quando(*a):
                    self.pausa(papel)
                return orig[nome](*a, **k)
            return f

        def depois(a, **k):  # undo: o 1º lock (launch) já foi pego aqui
            r = orig["_validar_efeitos"](a, **k)
            self.pausa("undo")
            return r

        monkeypatch.setattr(accounts, "_validar_efeitos", depois)
        monkeypatch.setattr(pockets, "_renomear_no_historico", antes("renome", "_renomear_no_historico"))
        monkeypatch.setattr(privacy, "_table_exists",
                            antes("reset", "_table_exists", lambda cur, t: t == reset_antes_de))
        monkeypatch.setattr(investments, "accrue_investment_db", antes("accrual", "accrue_investment_db"))
        monkeypatch.setattr(pockets, "accrue_pocket_db", antes("accrual_p", "accrue_pocket_db"))
        monkeypatch.setattr(privacy, "verify_user_password", lambda *a, **k: True)

        class _Tipos(tuple):  # delete_pocket: pockets já travada, launches ainda não
            def __iter__(s):
                self.pausa("delete")
                return super().__iter__()
        monkeypatch.setattr(pockets, "TIPOS_HISTORICO_CAIXINHA", _Tipos(pockets.TIPOS_HISTORICO_CAIXINHA))

    def pausa(self, papel):
        if getattr(self.papel, "v", None) != papel or self.chegou[papel].is_set():
            return
        self.chegou[papel].set()
        fim = time.monotonic() + 3
        while time.monotonic() < fim and not self.chegou[self.outro[papel]].is_set():
            if _esperar_backend_travado(0.1):
                return

    def roda(self, fns):
        """`fns`: papel -> callable. Devolve papel -> resultado OU exceção."""
        primeiro, segundo = self.chegou

        def run(papel, espera):
            self.papel.v = papel
            if espera:
                assert espera.wait(5), "o primeiro nunca chegou ao ponto de pausa"
            try:
                return fns[papel]()
            except Exception as exc:  # noqa: BLE001 — quem julga é o teste
                return exc
        with ThreadPoolExecutor(2) as pool:
            a, b = pool.submit(run, primeiro, None), pool.submit(run, segundo, self.chegou[primeiro])
            res = {primeiro: a.result(20), segundo: b.result(20)}
        assert not [r for r in res.values() if isinstance(r, psycopg.errors.DeadlockDetected)], res
        return res


def _nomes(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select name from pockets where user_id=%s", (uid,))
        return [r["name"] for r in cur.fetchall()]


def _n(uid, tabela):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select count(*) as n from {tabela} where user_id=%s", (uid,))
        return cur.fetchone()["n"]


def _launch_existe(uid, lid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from launches where user_id=%s and id=%s", (uid, lid))
        return cur.fetchone() is not None


def _ordem(undo_primeiro, outro):
    return ("undo", outro) if undo_primeiro else (outro, "undo")


@pytest.mark.parametrize("como_string", [False, True], ids=["jsonb_objeto", "jsonb_string"])
@pytest.mark.parametrize("undo_primeiro", [True, False])
def test_desfazer_criacao_e_renome_nao_dao_deadlock(user_id, monkeypatch, undo_primeiro, como_string):
    lid, pid, _ = db.create_pocket(user_id, "viagem")
    if como_string:  # `efeitos` gravado como string JSON (legado): `_precisa_lock` tem de ler igual
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("update launches set efeitos = to_jsonb(efeitos::text) where id=%s", (lid,))
            cur.execute("select jsonb_typeof(efeitos) as t from launches where id=%s", (lid,))
            assert cur.fetchone()["t"] == "string"
            conn.commit()
    res = _Corrida(monkeypatch, *_ordem(undo_primeiro, "renome")).roda({
        "undo": lambda: db.delete_launch_and_rollback(user_id, lid),
        "renome": lambda: pockets.update_pocket_meta(user_id, pid, name="viagem2"),
    })
    assert res["undo"] is None, res
    # Sequencial válido nas duas ordens: renome depois do undo não acha a caixinha
    # (None); undo depois do renome a acha pelo nome novo (o renome reescreve o
    # `efeitos.create_pocket.nome`) e a apaga. Com `efeitos` string o renome não
    # alcança o `nome` (jsonb_set) e o undo, procurando o nome velho, deixa a caixinha.
    assert res["renome"] is None or res["renome"]["name"] == "viagem2", res
    # Com `efeitos` string e renome primeiro sobra "viagem2": comportamento atual, não
    # desejado (acompanhamento em issue separada); se corrigir, atualize esta asserção.
    assert _nomes(user_id) == (["viagem2"] if como_string and res["renome"] else [])
    assert not _launch_existe(user_id, lid)


@pytest.mark.parametrize("undo_primeiro", [True, False])
def test_desfazer_criacao_e_delete_pocket_nao_dao_deadlock(user_id, monkeypatch, undo_primeiro):
    lid, _, _ = db.create_pocket(user_id, "viagem")
    res = _Corrida(monkeypatch, *_ordem(undo_primeiro, "delete")).roda({
        "undo": lambda: db.delete_launch_and_rollback(user_id, lid),
        "delete": lambda: db.delete_pocket(user_id, "viagem"),
    })
    # Um conclui; o outro já não acha o que apagar (LookupError), sem erro de banco.
    erros = [r for r in res.values() if isinstance(r, Exception)]
    assert len(erros) == 1 and isinstance(erros[0], LookupError), res
    assert _nomes(user_id) == [] and not _launch_existe(user_id, lid)


@pytest.mark.parametrize("undo_primeiro", [True, False])
def test_reset_e_desfazer_criacao_nao_dao_deadlock(user_id, monkeypatch, undo_primeiro):
    lid, _, _ = db.create_pocket(user_id, "viagem")
    res = _Corrida(monkeypatch, *_ordem(undo_primeiro, "reset"), reset_antes_de="launches").roda({
        "undo": lambda: db.delete_launch_and_rollback(user_id, lid),
        "reset": lambda: privacy.reset_user_data(user_id, "x"),
    })
    assert not isinstance(res["reset"], Exception), res
    assert res["undo"] is None or isinstance(res["undo"], LookupError), res
    assert _nomes(user_id) == [] and not _launch_existe(user_id, lid)


@pytest.mark.parametrize("accrual_primeiro", [True, False])
def test_reset_e_accrual_de_investimentos_nao_dao_deadlock(user_id, monkeypatch, accrual_primeiro):
    db.add_launch_and_update_balance(user_id, "receita", 1000, "salario", "x")
    db.create_investment(user_id, "cdb", 0.12, "yearly")
    db.investment_deposit_from_account(user_id, "cdb", 500)
    with db.get_conn() as conn, conn.cursor() as cur:  # há o que render: o accrual trava os lotes
        cur.execute("update investments set last_date = current_date - 30 where user_id=%s", (user_id,))
        cur.execute("update investment_lots set last_date = current_date - 30 where user_id=%s", (user_id,))
        cur.execute("select count(*) as n from investment_lots where user_id=%s", (user_id,))
        assert cur.fetchone()["n"] == 1
        conn.commit()
    ordem = ("accrual", "reset") if accrual_primeiro else ("reset", "accrual")
    res = _Corrida(monkeypatch, *ordem, reset_antes_de="investments").roda({
        "accrual": lambda: investments.accrue_all_investments(user_id),
        "reset": lambda: privacy.reset_user_data(user_id, "x"),
    })
    assert not any(isinstance(r, Exception) for r in res.values()), res
    assert _n(user_id, "investments") == 0 and _n(user_id, "investment_lots") == 0


@pytest.mark.parametrize("accrual_primeiro", [True, False])
def test_reset_e_accrual_de_caixinhas_nao_dao_deadlock(user_id, monkeypatch, accrual_primeiro):
    db.add_launch_and_update_balance(user_id, "receita", 1000, "salario", "x")
    db.create_pocket(user_id, "viagem")
    db.pocket_deposit_from_account(user_id, "viagem", 500)
    with db.get_conn() as conn, conn.cursor() as cur:  # caixinha antiga, ainda não congelada: o accrual mexe nos lotes
        cur.execute("update pockets set interest_frozen_at = null, interest_enabled = true where user_id=%s", (user_id,))
        assert _n(user_id, "pocket_lots") == 1
        conn.commit()
    ordem = ("accrual_p", "reset") if accrual_primeiro else ("reset", "accrual_p")
    res = _Corrida(monkeypatch, *ordem, reset_antes_de="launches").roda({
        "accrual_p": lambda: pockets.accrue_all_pockets(user_id),
        "reset": lambda: privacy.reset_user_data(user_id, "x"),
    })
    assert not any(isinstance(r, Exception) for r in res.values()), res
    assert _n(user_id, "pockets") == 0 and _n(user_id, "pocket_lots") == 0


@pytest.mark.parametrize("como_string", [False, True], ids=["jsonb_objeto", "jsonb_string"])
@pytest.mark.parametrize("undo_primeiro", [True, False])
def test_reset_e_desfazer_aporte_nao_dao_deadlock(user_id, monkeypatch, undo_primeiro, como_string):
    """O mesmo `efeitos` string também esconderia o aporte (`touches_investment`) do `_precisa_lock`."""
    db.add_launch_and_update_balance(user_id, "receita", 1000, "salario", "x")
    db.create_investment(user_id, "cdb", 0.12, "yearly")
    lid = db.investment_deposit_from_account(user_id, "cdb", 300)[0]
    if como_string:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("update launches set efeitos = to_jsonb(efeitos::text) where id=%s", (lid,))
            conn.commit()
    res = _Corrida(monkeypatch, *_ordem(undo_primeiro, "reset"), reset_antes_de="launches").roda({
        "undo": lambda: db.delete_launch_and_rollback(user_id, lid),
        "reset": lambda: privacy.reset_user_data(user_id, "x"),
    })
    assert not isinstance(res["reset"], Exception), res
    assert res["undo"] is None or isinstance(res["undo"], LookupError), res
    assert _n(user_id, "investments") == 0 and not _launch_existe(user_id, lid)


@pytest.mark.parametrize("como_string", [False, True], ids=["jsonb_objeto", "jsonb_string"])
@pytest.mark.parametrize("undo_primeiro", [True, False])
def test_reset_e_desfazer_despesa_com_origem_banco_nao_dao_deadlock(user_id, monkeypatch, undo_primeiro, como_string):
    """Cobre o `uses_bank_movement_lock` do `_precisa_lock` com `efeitos` string."""
    ef = {"delta_conta": -5, "funding_source": {"kind": "bank", "id": 1}}
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into launches(user_id, tipo, valor, source, efeitos) values "
                    f"(%s, 'despesa', 5, 'manual', {'to_jsonb(%s::text)' if como_string else '%s::jsonb'}) returning id",
                    (user_id, json.dumps(ef)))
        lid = cur.fetchone()["id"]
        conn.commit()
    res = _Corrida(monkeypatch, *_ordem(undo_primeiro, "reset"), reset_antes_de="launches").roda({
        "undo": lambda: db.delete_launch_and_rollback(user_id, lid),
        "reset": lambda: privacy.reset_user_data(user_id, "x"),
    })
    assert not isinstance(res["reset"], Exception), res
    assert res["undo"] is None or isinstance(res["undo"], LookupError), res
    assert not _launch_existe(user_id, lid)


@pytest.mark.parametrize("tipo", ["caixinha", "investimento"])
def test_reset_conta_lote_que_um_accrue_concorrente_ainda_nao_commitou(user_id, monkeypatch, tipo):
    """Saldo legado sem lote: o accrue o materializa (insert do lote) segurando o pai, sem commitar.
    O reset tem de esperar o pai ANTES de contar os lotes, senão conta 0 e o CASCADE leva 1."""
    monkeypatch.setattr(privacy, "verify_user_password", lambda *a, **k: True)
    modulo, nome, tabela, lotes, accrue_db, accrue_all = (
        (pockets, "viagem", "pockets", "pocket_lots", "accrue_pocket_db", pockets.accrue_all_pockets)
        if tipo == "caixinha" else
        (investments, "cdb", "investments", "investment_lots", "accrue_investment_db",
         investments.accrue_all_investments))
    if tipo == "caixinha":
        db.create_pocket(user_id, nome)
    else:
        db.create_investment(user_id, nome, 0.12, "yearly")
    with db.get_conn() as conn, conn.cursor() as cur:  # saldo sem lote, como o legado
        cur.execute(f"update {tabela} set balance = 300 where user_id=%s", (user_id,))
        cur.execute(f"delete from {lotes} where user_id=%s", (user_id,))
        conn.commit()
    original, materializou = getattr(modulo, accrue_db), threading.Event()

    def materializa_e_segura(cur, uid, pid, *a, **k):
        r = original(cur, uid, pid, *a, **k)  # lote inserido, nada commitado, pai travado
        materializou.set()
        assert _esperar_backend_travado(), "o reset nunca chegou a esperar lock"  # teto padrão: 15 s
        return r
    monkeypatch.setattr(modulo, accrue_db, materializa_e_segura)

    def reset():
        assert materializou.wait(5)
        return privacy.reset_user_data(user_id, "x")
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(accrue_all, user_id)
        r = pool.submit(reset)
        a.result(30)
        deleted = r.result(30)["deleted"]
    assert deleted[lotes] == 1, deleted
    assert _n(user_id, tabela) == 0 and _n(user_id, lotes) == 0
