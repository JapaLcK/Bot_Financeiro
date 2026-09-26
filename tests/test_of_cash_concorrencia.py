"""Q41 grupo 7: dois reconciliadores ao mesmo tempo (dois syncs do mesmo
usuário) creditam o saque uma vez só."""
import threading
from decimal import Decimal

from conftest import usuario_pagante
from db.connection import get_conn
from db.open_finance_cash import reconcile_cash_transfers
from tests._of_cash_helpers import caixa, carteira, conecta, dia, links, sync, tx  # noqa: F401


def test_dois_reconciliadores_creditam_uma_vez(caixa, monkeypatch):
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    monkeypatch.setenv("OF_CASH_ENABLED", "0")  # espelho gravado sem reconciliar
    sync(c, uid, [tx("t1", -200, dia(10))])
    monkeypatch.setenv("OF_CASH_ENABLED", "1")

    barreira, erros = threading.Barrier(2), []

    def roda():
        try:
            with get_conn() as conn, conn.cursor() as cur:
                barreira.wait(timeout=10)
                reconcile_cash_transfers(cur, uid)
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — o teste lê a lista
            erros.append(repr(exc))

    ts = [threading.Thread(target=roda) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)

    assert erros == []
    assert carteira(uid) == Decimal("200")
    assert [r["status"] for r in links(uid)] == ["ativo"]


def test_apagar_o_par_durante_o_reconciliador_nao_deadlocka(caixa, monkeypatch):
    """"apagar #N" do par × sync que corrige o mesmo saque (200→250).

    O reconciliador trava conta → lançamento. O apagar travava lançamento →
    conta (o par não é `source='open_finance'`, então não pegava `_lock_user`):
    DeadlockDetected. Encontro sem relógio: o apagar para DEPOIS de travar o
    lançamento (`_validar_efeitos`) até o reconciliador ter pego a conta — ou
    estar esperando por ela (o conserto: o apagar já a segura)."""
    import time

    import db
    import db.accounts as accounts_mod
    import db.bank_movements as bank_mod
    from tests._of_cash_helpers import q

    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10))])
    (link,) = links(uid)
    q("update open_finance_transactions set amount=-250 where id=%s", (link["of_transaction_id"],))

    apagou, pegou_conta, pid, erros = threading.Event(), threading.Event(), [], {}
    real_validar, real_lock = accounts_mod._validar_efeitos, bank_mod._lock_user

    def esperando_lock(p):
        return q("select wait_event_type as w from pg_stat_activity where pid=%s", (p,), True)[0]["w"] == "Lock"

    def validar(*a, **k):
        if threading.current_thread().name == "apaga" and not apagou.is_set():
            apagou.set()
            fim = time.monotonic() + 30
            while not (pegou_conta.is_set() or pid and esperando_lock(pid[0])) and time.monotonic() < fim:
                time.sleep(0.02)
        return real_validar(*a, **k)

    def lock(cur, user_id):
        real_lock(cur, user_id)
        if threading.current_thread().name == "sync":
            pegou_conta.set()

    monkeypatch.setattr(accounts_mod, "_validar_efeitos", validar)
    monkeypatch.setattr(bank_mod, "_lock_user", lock)

    def apaga():
        try:
            db.delete_launch_and_rollback(uid, link["launch_id"])
        except Exception as exc:  # noqa: BLE001
            erros["apaga"] = repr(exc)
        finally:
            apagou.set()

    def reconcilia():
        try:
            apagou.wait(timeout=30)
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("select pg_backend_pid() as p")
                pid.append(cur.fetchone()["p"])
                reconcile_cash_transfers(cur, uid)
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            erros["sync"] = repr(exc)
        finally:
            pegou_conta.set()

    ts = [threading.Thread(target=apaga, name="apaga"), threading.Thread(target=reconcilia, name="sync")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=60)

    assert not any(t.is_alive() for t in ts), f"travou: {erros}"
    assert erros == {}
    assert carteira(uid) == 0, "apagar o par tem de devolver a Carteira"
    assert [(r["status"], r["launch_id"], Decimal(str(r["amount"]))) for r in links(uid)] == [
        ("ativo", None, Decimal("250"))]
