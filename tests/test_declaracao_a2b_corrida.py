"""PR-A2b-1: clique duplo, duas abas e sync concorrente no trocar/desfazer. O mutex `_lock_user` serializa;
o segundo relê o estado e decide sobre o que leu (CAS no par visto). Retrato completo depois de cada corrida.

`_ordered_pair` só solta o segundo quando o primeiro já segura o mutex e (com `esperar_travado`) o segundo
está parado no lock: é corrida de verdade, não duas chamadas em sequência.

Controles: tirar o `_lock_user` de `undo_bank_movement` -> VERMELHO
`test_clique_duplo_de_desfazer_devolve_uma_sombra` (as duas leem o par e a segunda sombra sai em conflito).
O CAS dos dois lados é o G5/G7 de `tests/test_declaracao_a2b_acoes.py`.
"""
from __future__ import annotations

import pytest

import db
import db.bank_conferencia as conferencia
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import casada, decl, em, retrato, sombra, solta, t, tid
from tests.test_bank_movements_concurrency import _captura, _ordered_pair
from tests.test_declaracao_a2b_acoes import R, T1, T2, ana, confirma
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza

SO_MANUAL_SEM_PAR = [("decl mercado", -50.0, None, None, True, False)]


def test_clique_duplo_de_desfazer_devolve_uma_sombra(user_id, monkeypatch):
    _, d, _ = ana(user_id)
    t1 = tid(user_id, "T1")
    undo = _captura(lambda: conferencia.undo_bank_movement(user_id, d, t1))
    a, b = _ordered_pair(monkeypatch, undo, undo, esperar_travado=True)
    assert sorted([a["changed"], b["changed"]]) == [False, True]
    assert retrato(user_id) == R([decl(0), sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)],
                                 [solta("T1", -50.0, 0), solta("T2", -50.0, 1)], SO_MANUAL_SEM_PAR, 150.0)


def test_duas_abas_trocando_uma_vence(user_id, monkeypatch):
    cid, d, _ = ana(user_id)
    sincroniza(cid, user_id, T1, T2, t("T3", -50, 2, "PIX JOAO"))
    t1, t2, t3 = (tid(user_id, p) for p in ("T1", "T2", "T3"))
    a, b = _ordered_pair(monkeypatch, _captura(lambda: conferencia.confirm_bank_movement(user_id, d, t2, t1)),
                         _captura(lambda: conferencia.confirm_bank_movement(user_id, d, t3, t1)), esperar_travado=True)
    assert a is None and isinstance(b, ValueError) and str(b) == "MOVEMENT_STATE_CHANGED"
    assert retrato(user_id) == R([decl(1), sombra("T1", 50.0, 0), sombra("T3", 50.0, 2)],
                                 [solta("T1", -50.0, 0), casada("T2", -50.0, 1), solta("T3", -50.0, 2)],
                                 [("decl mercado", -50.0, "T2", "manual", True, False)], 150.0)


@pytest.mark.parametrize("desfaz_primeiro", [True, False])
def test_desfazer_e_sync_concorrente_nunca_termina_casada(user_id, monkeypatch, desfaz_primeiro):
    cid, d, _ = ana(user_id)
    t1 = tid(user_id, "T1")
    undo = _captura(lambda: conferencia.undo_bank_movement(user_id, d, t1))
    sync = _captura(lambda: sincroniza(cid, user_id, T1, T2))
    a, b = _ordered_pair(monkeypatch, *((undo, sync) if desfaz_primeiro else (sync, undo)), esperar_travado=True)
    assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
    assert retrato(user_id) == R([decl(0), sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)],
                                 [solta("T1", -50.0, 0), solta("T2", -50.0, 1)], SO_MANUAL_SEM_PAR, 150.0)


def test_retentativa_de_trocar_e_de_escolher(user_id):
    _, d, c = ana(user_id)
    for _ in range(2):
        assert confirma(c, user_id, d, "T2", troca="T1").status_code == 200
    trocada = R([decl(1), sombra("T1", 50.0, 0)], [solta("T1", -50.0, 0), casada("T2", -50.0, 1)],
                [("decl mercado", -50.0, "T2", "manual", True, False)], 100.0)
    assert retrato(user_id) == trocada
    assert conferencia.undo_bank_movement(user_id, d, tid(user_id, "T2"))["changed"]
    for _ in range(2):
        assert confirma(c, user_id, d, "T2").status_code == 200
    assert retrato(user_id) == trocada


TROCADA = R([decl(1), sombra("T1", 50.0, 0)], [solta("T1", -50.0, 0), casada("T2", -50.0, 1)],
            [("decl mercado", -50.0, "T2", "manual", True, False)], 100.0)


@pytest.mark.parametrize("troca_primeiro", [True, False])
def test_trocar_e_sync_concorrentes(user_id, monkeypatch, troca_primeiro):
    cid, d, _ = ana(user_id)
    t1, t2 = tid(user_id, "T1"), tid(user_id, "T2")
    troca = _captura(lambda: conferencia.confirm_bank_movement(user_id, d, t2, t1))
    sync = _captura(lambda: sincroniza(cid, user_id, T1, T2))
    a, b = _ordered_pair(monkeypatch, *((troca, sync) if troca_primeiro else (sync, troca)), esperar_travado=True)
    assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
    assert retrato(user_id) == TROCADA


@pytest.mark.parametrize("troca_primeiro", [True, False])
def test_trocar_e_desfazer_concorrentes(user_id, monkeypatch, troca_primeiro):
    cid, d, _ = ana(user_id)
    t1, t2 = tid(user_id, "T1"), tid(user_id, "T2")
    troca = _captura(lambda: conferencia.confirm_bank_movement(user_id, d, t2, t1))
    undo = _captura(lambda: conferencia.undo_bank_movement(user_id, d, t1))
    a, b = _ordered_pair(monkeypatch, *((troca, undo) if troca_primeiro else (undo, troca)), esperar_travado=True)
    assert isinstance(b, ValueError) and str(b) == "MOVEMENT_STATE_CHANGED", (a, b)
    r = retrato(user_id)
    assert len(r["D"]) == 1 and r["D"][0][4] is True
    assert r["saiu"] == (100.0 if troca_primeiro else 150.0), r


def test_duas_declaracoes_disputam_a_mesma_candidata(user_id, monkeypatch):
    """D1 (casada com T1) troca para T3 enquanto D2 (só manual, sem par) escolhe T3: só uma fica com ela."""
    cid, d1, _ = ana(user_id)
    d2 = declara(user_id, criado_em=em(0), alvo="farmacia", categoria="saude")
    q("update bank_movement_declarations set manual_only=true where launch_id=%s returning 1", (d2,))
    sincroniza(cid, user_id, T1, T2, t("T3", -50, 2, "PIX JOAO"))
    t1, t3 = tid(user_id, "T1"), tid(user_id, "T3")
    a, b = _ordered_pair(monkeypatch, _captura(lambda: conferencia.confirm_bank_movement(user_id, d1, t3, t1)),
                         _captura(lambda: conferencia.confirm_bank_movement(user_id, d2, t3)), esperar_travado=True)
    assert a is None and isinstance(b, ValueError), (a, b)
    assert q("select count(*) n from bank_movement_declarations where matched_transaction_id=%s", (t3,))["n"] == 1


@pytest.mark.parametrize("troca_primeiro", [True, False])
def test_trocar_e_apagar_concorrentes(user_id, monkeypatch, troca_primeiro):
    cid, d, _ = ana(user_id)
    t1, t2 = tid(user_id, "T1"), tid(user_id, "T2")
    troca = _captura(lambda: conferencia.confirm_bank_movement(user_id, d, t2, t1))
    apaga = _captura(lambda: db.delete_launch_and_rollback(user_id, d))
    a, b = _ordered_pair(monkeypatch, *((troca, apaga) if troca_primeiro else (apaga, troca)), esperar_travado=True)
    r = retrato(user_id)
    assert r["D"] == [] and sorted(x[0] for x in r["L"]) == ["sombra T1", "sombra T2"], r
    assert [x[5] for x in r["T"]] == ["imported", "imported"], r
    assert troca_primeiro or isinstance(b, LookupError), (a, b)
