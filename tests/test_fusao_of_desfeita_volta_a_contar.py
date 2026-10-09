"""Desfeita a fusão, a Carteira volta a contar o gasto — sem nada a restaurar.

Este arquivo nasceu contra uma abordagem por ESCRITA que zerava o `delta_conta`
do manual: sumindo o espelho do banco (desconectar, o provedor apagar a
transação), ninguém mais contava aquele dinheiro e o real sumia para sempre.

A abordagem que ficou não escreve nada, então não há restauração a fazer: o
`delta_conta` nunca saiu de -1 e a correção de leitura deixa de se aplicar
sozinha quando a conta sai de `BANK_ACCOUNTS_SQL`. Os números esperados são os
MESMOS das duas abordagens — é por isso que estes casos continuam valendo.

Medição em `(consolidado, carteira)` do roteiro (conectar 114,88 → "Gastei 1
real com a barbara" → sync propõe o casamento e o usuário confirma → desconectar):

    antes = (113.88, 0.0)   depois = (-1.0, -1.0)

Controle positivo aqui: a reconexão não pode debitar duas vezes. Quem
discrimina o conserto é `tests/test_fusao_of_nao_conta_duas_vezes.py` e o caso
8 de `tests/test_fusao_of_evapora_sozinha.py`.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, delta_conta, ia_fora, manda, of_tx_pendente,
    saldo_bruto, sincroniza, soma_delta_conta, tx, uid_pro, ultimo_launch,
)


def _funde_um_real(uid: int) -> tuple[int, int]:
    """Cenário do relato: banco com 114,88, 1 real gasto à mão, o Pix chega e o
    usuário confirma o casamento (lançamento manual nunca funde em silêncio)."""
    hoje = today_tz()
    conexao = conecta_banco(uid, "114.88")
    manda(uid, "Gastei 1 real com a barbara em dinheiro")
    manual_id = ultimo_launch(uid)
    sincroniza(conexao, uid, "113.88",
               [tx(uid, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    rep = db.import_open_finance_launches(uid, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid, of_tx_pendente(uid))
    assert consolidado(uid) == (113.88, 0.0)
    return conexao, manual_id


# ── caminho 1 do inventário: disconnect_open_finance_connection ─────────────

def test_desconectar_o_banco_devolve_o_gasto(uid_pro, ia_fora):
    conexao, manual_id = _funde_um_real(uid_pro)

    db.disconnect_open_finance_connection(uid_pro, conexao)

    # com o refund destrutivo: (0.0, 0.0) — o gasto tinha sumido para sempre
    assert consolidado(uid_pro) == (-1.0, -1.0)
    assert delta_conta(uid_pro, manual_id) == Decimal("-1")


def test_reconectar_depois_de_desconectar_nao_debita_duas_vezes(uid_pro, ia_fora):
    """POSITIVO: a restauração não pode virar um débito extra no caminho de
    quem troca de banco e volta. O disconnect some com a tx fundida; na conexão
    nova o importador propõe o casamento de novo (candidato manual → pendência,
    nunca fusão silenciosa) e o dono confirma."""
    hoje = today_tz()
    conexao, manual_id = _funde_um_real(uid_pro)
    db.disconnect_open_finance_connection(uid_pro, conexao)
    assert saldo_bruto(uid_pro) == Decimal("-1")

    nova = conecta_banco(uid_pro, "113.88",
                         [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")])
    rep = db.import_open_finance_launches(uid_pro, nova)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid_pro, of_tx_pendente(uid_pro))

    assert saldo_bruto(uid_pro) == Decimal("-1"), "a correção é de LEITURA"
    assert consolidado(uid_pro) == (113.88, 0.0)


# ── caminho 2 do inventário: o provedor apagou a transação ─────────────────

def test_transacao_apagada_no_provedor_devolve_o_gasto(uid_pro, ia_fora):
    """`delete_open_finance_transactions` — o outro chamador de
    `_rollback_imported_of`. Mesma raiz, porta diferente."""
    conexao, manual_id = _funde_um_real(uid_pro)

    apagadas = db.delete_open_finance_transactions(
        f"item-of-{uid_pro}", [f"of-tx-{uid_pro}-1"])

    assert apagadas == 1
    assert delta_conta(uid_pro, manual_id) == Decimal("-1")
    # o espelho do banco ainda vale 113,88; o gasto volta a ser contado no manual
    assert consolidado(uid_pro) == (112.88, -1.0)


# ── achado 2: fusão falsa-positiva é reversível ────────────────────────────

def test_fusao_falsa_positiva_devolve_o_gasto_ao_desfazer(uid_pro, ia_fora):
    """Dois gastos DIFERENTES de R$50 no mesmo dia (um do bolso, um do banco) a
    heurística marca como casamento — não é o escopo consertá-la, mas o dinheiro
    não pode ser perdido de forma irreversível por causa dela. O dono confirma
    a pendência (lançamento manual só funde com confirmação) e a desfaz."""
    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "1000.00")
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    manual_id = ultimo_launch(uid_pro)

    sincroniza(conexao, uid_pro, "950.00",
               [tx(uid_pro, "-50.00", hoje, "MERCADO")])
    rep = db.import_open_finance_launches(uid_pro, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid_pro, of_tx_pendente(uid_pro))
    assert consolidado(uid_pro) == (950.0, 0.0), "a heurística fundiu (fora do escopo)"

    db.disconnect_open_finance_connection(uid_pro, conexao)

    assert consolidado(uid_pro) == (-50.0, -50.0), "o gasto do bolso sumiu"
    assert delta_conta(uid_pro, manual_id) == Decimal("-50")


# ── achado 3: a ordem dos locks é uma só, em todo mundo ────────────────────

def test_apagar_enquanto_o_sync_funde_nao_deadlocka(uid_pro, ia_fora, monkeypatch):
    """Corrida REAL, caminho de produção: o dono apaga o lançamento no dashboard enquanto o sync
    funde a transação do banco. Era `xfail(strict)` por um DEADLOCK pré-existente (delete:
    launches → accounts; sync: accounts → launches); o conserto previsto no marcador era o
    delete tomar `accounts` ANTES de launches, e o undo comum agora o faz (`_lock_user`).

    A pausa é por statement (tests/_pausa_sql.py), sem sleep: o delete para com a linha e o
    mutex tomados, o sync chega e ESPERA o mutex, e as DUAS concluem — o delete apaga o manual
    e o sync, depois dele, traz a transação do banco como sombra, sem perder nem duplicar o gasto.
    """
    from tests._pausa_sql import PausaSql, sem_deadlock

    conexao = conecta_banco(uid_pro, "114.88")
    manda(uid_pro, "Gastei 1 real com a barbara em dinheiro")
    manual_id = ultimo_launch(uid_pro)
    sincroniza(conexao, uid_pro, "113.88",
               [tx(uid_pro, "-1.00", today_tz(), "PIX ENVIADO BARBARA")])

    pausa = PausaSql(monkeypatch, "a", lambda q: "from launches" in q and "for update" in q)
    r_delete, r_sync = pausa.roda(lambda: db.delete_launch_and_rollback(uid_pro, manual_id),
                                  lambda: db.import_open_finance_launches(uid_pro, conexao))
    sem_deadlock(r_delete, r_sync)
    assert pausa.casou and pausa.outro_travou, "o sync devia esperar o mutex do delete"
    assert not isinstance(r_delete, Exception) and not isinstance(r_sync, Exception), (r_delete, r_sync)
    assert saldo_bruto(uid_pro) == soma_delta_conta(uid_pro)


# ── achado B: desconectar CONCORRENTE ao sync ──────────────────────────────

def test_desconectar_durante_o_sync_nao_engole_o_gasto(uid_pro, ia_fora):
    """O caminho sequencial não é o único: o dono clica em "desconectar" com o
    sync da Pluggy em curso.

    Determinismo sem sleep e sem instrumentação: uma `threading.Barrier` larga
    as duas ao mesmo tempo, e o import carrega 41 transações para durar o
    bastante. Com a restauração lendo a lista ANTES do lock (rodada 2), as duas
    chamadas retornavam sucesso e o consolidado fechava em `(0.0, 0.0)` — o real
    sumia calado. A `main` fecha em `(-1.0, -1.0)`.
    """
    import threading

    hoje = today_tz()
    conexao = conecta_banco(uid_pro, "114.88")
    manda(uid_pro, "Gastei 1 real com a barbara em dinheiro")
    manual_id = ultimo_launch(uid_pro)

    # transação longa: o Pix que funde + 40 transações de ruído que não casam
    extras = [tx(uid_pro, f"-{7 + i}.13", hoje, f"COMPRA AVULSA {i}", ident=f"x{i}")
              for i in range(40)]
    sincroniza(conexao, uid_pro, "113.88",
               [tx(uid_pro, "-1.00", hoje, "PIX ENVIADO BARBARA")] + extras)

    porta = threading.Barrier(2, timeout=30)
    saida: dict[str, object] = {}

    def sync():
        porta.wait()
        try:
            saida["sync"] = db.import_open_finance_launches(uid_pro, conexao)
        except Exception as e:
            saida["sync"] = f"{type(e).__name__}: {e}"

    def desconecta():
        porta.wait()
        try:
            saida["disc"] = db.disconnect_open_finance_connection(uid_pro, conexao)
        except Exception as e:
            saida["disc"] = f"{type(e).__name__}: {e}"

    fios = [threading.Thread(target=sync), threading.Thread(target=desconecta)]
    for f in fios:
        f.start()
    for f in fios:
        f.join(timeout=60)
    assert not any(f.is_alive() for f in fios), f"travou: {saida}"

    # O gasto de 1 real continua contado por ALGUÉM. Ou a fusão não chegou a
    # acontecer (delta intacto), ou aconteceu e foi restaurada — nunca sumir.
    assert delta_conta(uid_pro, manual_id) == Decimal("-1"), saida
    assert consolidado(uid_pro) == (-1.0, -1.0), saida
