"""Corridas das escritas de reconciliação: duas threads soltas por uma Barrier.

Toda escrita trava `accounts` antes da transação OF, a mesma ordem do sync; o
que se mede aqui é o efeito disso — uma decisão só vale uma vez, e o saldo cru
continua sendo a soma dos `delta_conta`.
"""
from __future__ import annotations

import threading

import db
from db.reconciliation import ReconciliationConflict

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    consolidado, ia_fora, saldo_bruto, soma_delta_conta, uid_pro,
)
from tests.test_reconciliacao_desfazer import _of_tx, _sombras, funde_a
from tests.test_reconciliacao_resolver import _estado, pendencia


def _corre(**alvos):
    """Roda cada função numa thread, soltas juntas. Devolve resultado ou exceção."""
    porta = threading.Barrier(len(alvos), timeout=30)
    saida = {}

    def roda(nome, fn):
        porta.wait()
        try:
            saida[nome] = fn()
        except Exception as e:  # o teste decide o que é aceitável
            saida[nome] = e

    fios = [threading.Thread(target=roda, args=(n, f)) for n, f in alvos.items()]
    for f in fios:
        f.start()
    for f in fios:
        f.join(timeout=60)
    assert not any(f.is_alive() for f in fios), f"travou: {saida}"
    return saida


def test_desfazer_durante_o_sync(uid_pro, ia_fora):
    conexao = funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    s = _corre(undo=lambda: db.undo_reconciliation(uid_pro, of_tx),
               sync=lambda: db.import_open_finance_launches(uid_pro, conexao))
    assert s["undo"]["changed"] is True, s
    assert isinstance(s["sync"], dict), s
    assert consolidado(uid_pro) == (900.0, -50.0)
    assert _sombras(uid_pro) == 1


def test_confirmar_durante_o_sync(uid_pro, ia_fora):
    conexao, of_tx, manual, _ = pendencia(uid_pro)
    s = _corre(confirm=lambda: db.confirm_reconciliation(uid_pro, of_tx),
               sync=lambda: db.import_open_finance_launches(uid_pro, conexao))
    assert s["confirm"]["changed"] is True, s
    assert isinstance(s["sync"], dict), s
    assert _estado(of_tx)["reconciliation_status"] == "confirmed"
    assert consolidado(uid_pro) == (113.88, 0.0)


def test_duplo_clique_em_confirmar(uid_pro, ia_fora):
    _, of_tx, _, _ = pendencia(uid_pro)
    s = _corre(a=lambda: db.confirm_reconciliation(uid_pro, of_tx),
               b=lambda: db.confirm_reconciliation(uid_pro, of_tx))
    assert sorted(r["changed"] for r in s.values()) == [False, True], s
    assert consolidado(uid_pro) == (113.88, 0.0)


def test_duplo_clique_em_desfazer(uid_pro, ia_fora):
    funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    s = _corre(a=lambda: db.undo_reconciliation(uid_pro, of_tx),
               b=lambda: db.undo_reconciliation(uid_pro, of_tx))
    assert sorted(r["changed"] for r in s.values()) == [False, True], s
    assert _sombras(uid_pro) == 1
    assert consolidado(uid_pro) == (900.0, -50.0)


def test_confirmar_enquanto_apaga_x_preserva_o_saldo_cru(uid_pro, ia_fora):
    """Prova SÓ o invariante: saldo cru == soma dos `delta_conta`, com qualquer
    vencedor. NÃO prova o 409 por `ForeignKeyViolation`: com a transação OF
    travada pelo confirmar, o `on delete set null` do delete de X espera por ela,
    então a FK violada não tem caminho determinístico. O resultado do confirmar é
    só filtrado para não ser exceção inesperada (medido: o Postgres costuma
    escolher o confirmar como vítima do deadlock → `ReconciliationConflict`)."""
    _, of_tx, manual, _ = pendencia(uid_pro)
    s = _corre(confirm=lambda: db.confirm_reconciliation(uid_pro, of_tx),
               delete=lambda: db.delete_launch_and_rollback(uid_pro, manual))
    c = s["confirm"]
    assert (isinstance(c, dict) and c["ok"]) or isinstance(c, (ValueError, ReconciliationConflict)), s
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro), s
