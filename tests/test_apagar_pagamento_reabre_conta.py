"""Apagar o pagamento de uma conta a pagar reabre a conta (#793).

`bill_instances.launch_id` é `on delete set null`: sem o `update` em
`delete_launch_and_rollback`, a conta ficava 'paga' sem pagamento e o saldo devolvido.
`mark_bill_paid` (carteira) é uma transação só: reserva + débito + ligação.

Controles: sem o `update` do passo 1 os testes de reabertura ficam vermelhos; com
`mark_bill_paid` em duas transações, `test_janela_*` fica vermelho.
"""
from __future__ import annotations

import threading

import pytest

import db
import db.accounts as A
import db.bills as B
from conftest import usuario_pagante
from tests._patrimonio_helpers import q
from tests._fusao_of_helpers import conecta_banco, consolidado, ia_fora, of_tx_pendente, sincroniza, tx, uid_pro  # noqa: F401 (fixtures)
from tests.test_api_v2_lancamentos_escrita import post
from tests.test_api_v2_perfil import cliente
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_pending_rollback import _diga
from utils_date import today_tz


def conta_luz(uid, nome="Luz", valor=120):
    return B.create_boleto(uid, nome, valor, today_tz(), category="moradia")


def bill(uid, bid):
    return q("select status, paid_at, paid_amount, launch_id from bill_instances "
             "where id=%s and user_id=%s", (bid, uid))


def saldo(uid) -> float:
    return float(db.get_balance(uid))


def paga(uid, c=None):
    c = c or conta_luz(uid)
    B.mark_bill_paid(uid, c["id"], metodo="carteira")
    return c["id"], int(B.get_bill(uid, c["id"])["launch_id"])


def reaberta(uid, bid):
    b = bill(uid, bid)
    assert (b["status"], b["paid_at"], b["paid_amount"], b["launch_id"]) == ("pending", None, None, None), b


def seq(uid, lid):
    return db.get_launch_user_seq(uid, lid)


def test_conversa_apagar_pagamento_reabre_e_repagar_debita_uma_vez():
    uid = usuario_pagante()
    db.add_launch_and_update_balance(uid, "receita", 1000, None, "seed")
    c = conta_luz(uid)
    assert "mercado" in _diga(uid, "gastei 50 no mercado").lower()
    _diga(uid, "paguei a luz")
    lid = int(B.get_bill(uid, c["id"])["launch_id"])
    assert bill(uid, c["id"])["status"] == "paid" and saldo(uid) == 830
    assert "confirma" in _diga(uid, f"apagar #{seq(uid, lid)}").lower()
    _diga(uid, "sim")
    reaberta(uid, c["id"])
    assert saldo(uid) == 950
    _diga(uid, "paguei a luz")
    assert bill(uid, c["id"])["status"] == "paid" and saldo(uid) == 830
    assert q("select count(*) n from launches where user_id=%s and alvo='conta:Luz'", (uid,))["n"] == 1


def test_rota_delete_launches_reabre():
    uid = usuario_pagante()
    bid, lid = paga(uid)
    c, h = cliente(uid)
    assert c.delete(f"/launches/{uid}/{lid}", headers=h).status_code == 200
    reaberta(uid, bid)


def test_lote_misto_reabre_so_a_conta():
    uid = usuario_pagante()
    _diga(uid, "gastei 50 no mercado")
    m = int(db.list_launches(uid, limit=1)[0]["id"])
    bid, lid = paga(uid)
    _diga(uid, f"apagar #{seq(uid, m)} e #{seq(uid, lid)}")
    assert "Apagados" in _diga(uid, "sim")
    reaberta(uid, bid)


def test_apagar_tudo_reabre_todas():
    uid = usuario_pagante()
    b1, _ = paga(uid, conta_luz(uid, "Luz"))
    b2, _ = paga(uid, conta_luz(uid, "Agua", 80))
    assert db.delete_all_launches_and_rollback(uid)["remaining"] == 0
    reaberta(uid, b1)
    reaberta(uid, b2)


def test_positivos_comum_banco_e_recusa_nao_mexem():
    uid = usuario_pagante()
    db.add_launch_and_update_balance(uid, "despesa", 10, "cafe", "cafe")
    comum = int(db.list_launches(uid, limit=1)[0]["id"])
    banco = conta_luz(uid, "Gas")
    B.mark_bill_paid(uid, banco["id"], None, metodo="banco")
    bid, lid = paga(uid)
    db.delete_launch_and_rollback(uid, comum)
    assert bill(uid, banco["id"])["status"] == "paid" and bill(uid, bid)["status"] == "paid"
    # recusa: sem `efeitos` não reverte, e conta + saldo ficam como estavam
    q("update launches set efeitos = null where id=%s", (lid,))
    antes = saldo(uid)
    with pytest.raises(A.LaunchNoEffects):
        db.delete_launch_and_rollback(uid, lid)
    assert bill(uid, bid)["status"] == "paid" and bill(uid, bid)["launch_id"] == lid
    assert saldo(uid) == antes


def test_v2_apagar_pagamento_de_conta_segue_409(libera):
    uid = usuario_pagante()
    bid, lid = paga(uid)
    libera(uid)
    assert post(uid, "apagar", {"id": f"l{lid}"}).status_code == 409
    assert bill(uid, bid)["status"] == "paid"


def test_isolamento_conta_de_outro_usuario_forjada_nao_reabre():
    a, b = usuario_pagante(), usuario_pagante()
    _, lid = paga(a)
    alheia = conta_luz(b, "Alheia")
    q("update bill_instances set status='paid', launch_id=%s where id=%s", (lid, alheia["id"]))
    db.delete_launch_and_rollback(a, lid)
    assert bill(b, alheia["id"])["status"] == "paid"


def _pausa_dentro(monkeypatch):
    dentro, solta = threading.Event(), threading.Event()
    dentro.id = None
    real = A.add_launch_and_update_balance

    def pausa(*a, **k):
        r = real(*a, **k)
        dentro.id = r[0]  # o lançamento em voo, ainda sem commit
        dentro.set()
        solta.wait(15)
        return r

    monkeypatch.setattr(A, "add_launch_and_update_balance", pausa)
    return dentro, solta


def test_janela_delete_espera_o_pagamento_e_depois_reabre(monkeypatch):
    """Pausado DENTRO do pagamento (lançamento criado, sem commit), o apagar do mesmo usuário
    espera o mutex; commitado, a conta já está ligada e ele a reabre. Em duas transações o
    apagar passava na pausa e a conta ficava paga sem lançamento."""
    uid = usuario_pagante()
    c = conta_luz(uid)
    dentro, solta = _pausa_dentro(monkeypatch)
    pagou = threading.Thread(target=B.mark_bill_paid, args=(uid, c["id"]), kwargs={"metodo": "carteira"})
    erros = []

    def apaga():
        try:
            db.delete_launch_and_rollback(uid, dentro.id)
        except Exception as e:  # noqa: BLE001
            erros.append(e)

    apagou = threading.Thread(target=apaga)
    pagou.start()
    try:
        assert dentro.wait(15)
        apagou.start()
        apagou.join(1.5)
        assert apagou.is_alive(), "apagou durante a janela do pagamento"
    finally:
        solta.set()
        pagou.join(15)
        apagou.join(15)
    assert not erros and not pagou.is_alive() and not apagou.is_alive(), erros
    reaberta(uid, c["id"])
    assert saldo(uid) == 0
    assert q("select count(*) n from launches where user_id=%s", (uid,))["n"] == 0


def test_pagamento_x_apagar_em_threads_nao_trava_nem_duplica():
    uid = usuario_pagante()
    bid, lid = paga(uid)
    erros = []

    def roda(f, *a, **k):
        try:
            f(*a, **k)
        except Exception as e:  # noqa: BLE001
            erros.append(e)

    ts = [threading.Thread(target=roda, args=(B.mark_bill_paid, uid, bid), kwargs={"metodo": "carteira"}),
          threading.Thread(target=roda, args=(db.delete_launch_and_rollback, uid, lid))]
    [t.start() for t in ts]
    [t.join(30) for t in ts]
    assert not any(t.is_alive() for t in ts), "deadlock"
    assert not erros, erros
    n = q("select count(*) n from launches where user_id=%s and alvo='conta:Luz'", (uid,))["n"]
    b = bill(uid, bid)
    assert (b["status"] == "paid") == (n == 1) and n <= 1, (b, n)
    assert saldo(uid) == -120.0 * n
    if n:
        assert b["launch_id"] == q("select id from launches where user_id=%s and alvo='conta:Luz'", (uid,))["id"]


def test_agenda_reaberta_nao_duplica_e_lembra():
    import db.recurring as R
    from core.services.recurring_charger import sync_manual_bills_once
    uid = usuario_pagante()
    R.create_recurring_expense(uid, "Luz", 120, "moradia", today_tz().day, "account", payment_mode="manual")
    sync_manual_bills_once(user_id=uid)
    (b,) = B.list_bills(uid, include_paid=True)
    B.mark_bill_paid(uid, b["id"], metodo="carteira")
    db.delete_launch_and_rollback(uid, int(B.get_bill(uid, b["id"])["launch_id"]))
    sync_manual_bills_once(user_id=uid)
    assert len(B.list_bills(uid, include_paid=True)) == 1
    assert [x["id"] for x in B.list_due_bill_reminders(uid, today_tz())] == [b["id"]]


def test_pagamento_nao_abre_2a_conexao_com_a_transacao_aberta(monkeypatch):
    """`add_launch_and_update_balance(cur=)` chamava `ensure_user` (2ª conexão) dentro da
    transação: N pagamentos simultâneos esgotavam o pool (PoolTimeout)."""
    from contextlib import contextmanager
    from psycopg_pool import ConnectionPool
    uid = usuario_pagante()
    c = conta_luz(uid)
    real, nivel, aninhadas = ConnectionPool.connection, [0], []

    @contextmanager
    def conta(self, *a, **k):
        aninhadas.append(nivel[0]) if nivel[0] else None
        nivel[0] += 1
        try:
            with real(self, *a, **k) as conn:
                yield conn
        finally:
            nivel[0] -= 1

    monkeypatch.setattr(ConnectionPool, "connection", conta)
    B.mark_bill_paid(uid, c["id"], metodo="carteira")
    assert not aninhadas, "get_conn com a transação do pagamento aberta"


def test_lock_do_usuario_segura_o_pagamento_pausado(monkeypatch):
    """Antes do débito, só o `_lock_user` de mark_bill_paid segura um 2º escritor do mesmo usuário."""
    from db.bank_movements import _lock_user
    uid = usuario_pagante()
    c = conta_luz(uid)
    dentro, solta, real = threading.Event(), threading.Event(), A.add_launch_and_update_balance

    def pausa(*a, **k):
        dentro.set()
        solta.wait(15)
        return real(*a, **k)

    monkeypatch.setattr(A, "add_launch_and_update_balance", pausa)
    pagou = threading.Thread(target=B.mark_bill_paid, args=(uid, c["id"]), kwargs={"metodo": "carteira"})
    tomou = threading.Event()

    def outro():
        with db.get_conn() as conn, conn.cursor() as cur:
            _lock_user(cur, uid)
            tomou.set()

    t = threading.Thread(target=outro)
    pagou.start()
    try:
        assert dentro.wait(15)
        t.start()
        assert not tomou.wait(1.5), "2º escritor passou com o pagamento em voo"
    finally:
        solta.set()
        pagou.join(15)
        t.join(15)
    assert tomou.is_set()


def test_recorrente_apagado_depois_do_pagamento_deixa_apagar_o_lancamento():
    uid = usuario_pagante()
    rid = q("insert into recurring_expenses (user_id, name, amount, category, due_day, payment_type) "
            "values (%s, 'Luz', 10, 'contas', 5, 'account') returning id", (uid,))["id"]
    B.ensure_bill_instance(rid, uid, today_tz(), 10)
    bid = q("select id from bill_instances where recurring_id=%s", (rid,))["id"]
    B.mark_bill_paid(uid, bid, metodo="carteira")
    lid = int(B.get_bill(uid, bid)["launch_id"])
    q("delete from recurring_expenses where id=%s", (rid,))  # instância some por cascade
    assert q("select count(*) n from bill_instances where id=%s", (bid,))["n"] == 0
    db.delete_launch_and_rollback(uid, lid)
    assert q("select count(*) n from launches where user_id=%s", (uid,))["n"] == 0


def _paga_e_funde(uid):
    cx = conecta_banco(uid, "1000.00")
    c = B.create_boleto(uid, "Luz", 50, today_tz(), category="moradia")
    B.mark_bill_paid(uid, c["id"], metodo="carteira")
    lid = int(B.get_bill(uid, c["id"])["launch_id"])
    sincroniza(cx, uid, "950.00", [tx(uid, "-50.00", today_tz(), "LUZ")])
    db.import_open_finance_launches(uid, cx)
    db.confirm_reconciliation(uid, of_tx_pendente(uid))
    return c["id"], lid


def test_fundida_com_o_banco_reabre(uid_pro, ia_fora):
    bid, lid = _paga_e_funde(uid_pro)
    antes = consolidado(uid_pro)
    assert db.delete_launch_and_rollback(uid_pro, lid)
    reaberta(uid_pro, bid)
    assert consolidado(uid_pro) == antes


def test_fundida_no_apagar_tudo_reabre(uid_pro, ia_fora):
    bid, _ = _paga_e_funde(uid_pro)
    db.delete_all_launches_and_rollback(uid_pro)
    reaberta(uid_pro, bid)
