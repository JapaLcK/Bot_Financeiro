"""Apagar a linha ligada ao banco (fundida ou par pendente) × as escritas da reconciliação.

O apagar trava `accounts` → X → transação OF quando a linha está ligada (`_LIGADO_SQL` no
`_precisa_lock`), a mesma ordem do sync e da reconciliação: as corridas serializam. O que se
mede: sem deadlock, no máximo uma sombra, e o saldo cru == soma dos `delta_conta`.

Controle por mutação (relato do PR): sem o `ligado` no `_precisa_lock` →
`DeadlockDetected` em `test_apagar_pendente_enquanto_confirma_nao_deadlocka`; subqueries
`_LIGADO_SQL`/`FUNDIDO_SQL` dentro do `for update` → vermelho em
`test_ligou_enquanto_o_apagar_espera_o_lock_da_linha` (apaga X: `{'delete': None}`).
"""
from __future__ import annotations

import threading
import time

import db
import db.accounts as accounts_mod
import db.reconciliation as rec_mod
from db.accounts import LaunchUnsafeRollback

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, ia_fora, manda, saldo_bruto, sincroniza, soma_delta_conta, tx, uid_pro,
    ultimo_launch,
)
from tests.test_reconciliacao_concorrencia import _corre
from tests.test_reconciliacao_desfazer import _of_tx, _sombras, funde_a
from tests.test_reconciliacao_resolver import _estado, _gasto_do_mes, _q, pendencia
from utils_date import today_tz


def _sem_deadlock(saida):
    assert not any("deadlock" in str(v).lower() for v in saida.values()), saida


def test_apagar_fundida_durante_o_sync(uid_pro, ia_fora):
    conexao = funde_a(uid_pro)
    x = _estado(_of_tx(uid_pro))["imported_launch_id"]
    s = _corre(delete=lambda: db.delete_launch_and_rollback(uid_pro, x),
               sync=lambda: db.import_open_finance_launches(uid_pro, conexao))
    _sem_deadlock(s)
    assert isinstance(s["delete"], str) and isinstance(s["sync"], dict), s
    assert _sombras(uid_pro) == 1
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro)


def test_apagar_fundida_durante_o_desfazer(uid_pro, ia_fora):
    funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    x = _estado(of_tx)["imported_launch_id"]
    s = _corre(delete=lambda: db.delete_launch_and_rollback(uid_pro, x),
               undo=lambda: db.undo_reconciliation(uid_pro, of_tx))
    _sem_deadlock(s)
    assert isinstance(s["undo"], dict), s
    assert _sombras(uid_pro) == 1
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro)
    if isinstance(s["delete"], LaunchUnsafeRollback):  # o desfazer soltou X entre o preview e o lock
        assert s["delete"].motivo == "mudou_durante", s
        assert _q("select 1 from launches where id=%s", (x,))
        assert _gasto_do_mes(uid_pro) == 100.0
    else:
        assert s["delete"] is None or isinstance(s["delete"], str), s
        assert not _q("select 1 from launches where id=%s", (x,))
        assert _gasto_do_mes(uid_pro) == 50.0


def test_apagar_pendente_enquanto_confirma_nao_deadlocka(uid_pro, ia_fora, monkeypatch):
    """Encontro determinístico, sem relógio: o delete trava o que trava e para em
    `_validar_efeitos`; só então o confirmar começa e, se tomar `accounts`, para logo depois.
    Com o lock da linha ligada, o delete já segura `accounts`: o confirmar fica preso nele, a
    barrier estoura (5 s), o delete commita e o confirmar acha X apagado (MATCH_NOT_FOUND).
    Sem o lock, o delete só segura X, os dois se encontram e cruzam: deadlock."""
    _, of_tx, manual, _ = pendencia(uid_pro)
    porta = threading.Barrier(2, timeout=5)
    parou_delete = threading.Event()
    real_validar, real_lock = accounts_mod._validar_efeitos, rec_mod._lock_user

    def espera():
        try:
            porta.wait()
        except threading.BrokenBarrierError:
            pass

    def no_meio(*a, **kw):
        if threading.current_thread().name == "delete" and not parou_delete.is_set():
            parou_delete.set()
            espera()
        return real_validar(*a, **kw)

    def lock_e_espera(*a, **kw):
        r = real_lock(*a, **kw)
        if threading.current_thread().name == "confirm":
            espera()
        return r

    monkeypatch.setattr(accounts_mod, "_validar_efeitos", no_meio)
    monkeypatch.setattr(rec_mod, "_lock_user", lock_e_espera)
    saida = {}

    def roda(nome, fn):
        try:
            saida[nome] = fn()
        except Exception as e:  # o teste decide o que é aceitável
            saida[nome] = e

    d = threading.Thread(target=roda, name="delete",
                         args=("delete", lambda: db.delete_launch_and_rollback(uid_pro, manual)))
    c = threading.Thread(target=roda, name="confirm",
                         args=("confirm", lambda: db.confirm_reconciliation(uid_pro, of_tx)))
    d.start()
    assert parou_delete.wait(30), saida
    c.start()
    for f in (d, c):
        f.join(60)
    assert not any(f.is_alive() for f in (d, c)), f"travou: {saida}"

    _sem_deadlock(saida)
    assert saida["delete"] is None, saida
    assert isinstance(saida["confirm"], ValueError) and str(saida["confirm"]) == "MATCH_NOT_FOUND", saida
    assert _sombras(uid_pro) <= 1
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro)


def test_ligou_enquanto_o_apagar_espera_o_lock_da_linha(uid_pro, ia_fora):
    """Espera REAL no `for update` de X: outra transação liga X (`match_launch_id`; a FK
    toma FOR KEY SHARE em X) e segura aberta. O apagar (carteira pura, sem lock de
    `accounts`) bloqueia; só então a outra commita. O recheck em statement separado vê a
    ligação → `mudou_durante`. Com as subqueries dentro do `for update`, o snapshot é o de
    antes da espera: não vê a ligação e apaga X sem o lock de `accounts`."""
    conexao = conecta_banco(uid_pro, "1000.00")
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    x = ultimo_launch(uid_pro)
    sincroniza(conexao, uid_pro, "950.00", [tx(uid_pro, "-50.00", today_tz(), "MERCADO")])
    of_tx, antes = _of_tx(uid_pro), saldo_bruto(uid_pro)
    saida = {}

    def apaga():
        try:
            saida["delete"] = db.delete_launch_and_rollback(uid_pro, x)
        except Exception as e:  # o teste decide o que é aceitável
            saida["delete"] = e

    with db.connection.get_conn() as liga:
        with liga.cursor() as cur:
            cur.execute("select pg_backend_pid() as pid")
            pid = cur.fetchone()["pid"]
            cur.execute("update open_finance_transactions set match_launch_id=%s where id=%s",
                        (x, of_tx))
            fio = threading.Thread(target=apaga)
            fio.start()
            prazo = time.monotonic() + 30
            while not _q("select 1 from pg_stat_activity where wait_event_type='Lock' "
                         "and %s = any(pg_blocking_pids(pid))", (pid,)):
                assert time.monotonic() < prazo and fio.is_alive(), f"não bloqueou: {saida}"
                time.sleep(0.05)
        liga.commit()
    fio.join(60)
    assert not fio.is_alive(), f"travou: {saida}"

    assert isinstance(saida["delete"], LaunchUnsafeRollback), saida
    assert saida["delete"].motivo == "mudou_durante"
    assert _q("select 1 from launches where id=%s", (x,))
    assert saldo_bruto(uid_pro) == antes
    assert _estado(of_tx)["match_launch_id"] == x
