"""#1587: o dreno do Pix tem teto (2 por processo), prazo (45 s por rodada) e backoff (1 h depois da 6ª falha).

Dreno e banco REAIS; só o mundo lá fora é contado (`mundo_externo`). Controles negativos: sem o
semáforo (a), sem o filtro de `eventos_pendentes` (b), sem o `break` do prazo (c) e sem o
`last_attempt_at` em `registrar_falha` (b, d) deixam pelo menos um caso vermelho.
"""
from __future__ import annotations

import threading
import types
import uuid

import pytest

from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import _versao, entregar, evento, mundo_externo, nova_cobranca
from core.services import pix_drain, pix_sweeps
from core.services.pix_drain_effects import EXECUTORES
from core.services.pix_extras import total_cents
from db.connection import get_conn
from db.webhook_outbox import eventos_pendentes, registrar_evento, registrar_falha


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


@pytest.fixture()
def outro_user():
    from conftest import _cleanup_user  # type: ignore[attr-defined]
    from db import ensure_user

    uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid)
    conta(uid, "free", None)
    yield uid
    _cleanup_user(uid)


def _enfileirar(tipo: str, cobranca: dict) -> str:
    """Grava na outbox SEM drenar (o `entregar` dos helpers drena na hora)."""
    eid = f"evt_{uuid.uuid4().hex[:16]}"
    registrar_evento(eid, tipo, {"id": eid, "event": tipo, "payment": {
        "id": cobranca["asaas_payment_id"], "externalReference": cobranca["external_reference"],
        "value": total_cents(cobranca) / 100, "status": tipo.replace("PAYMENT_", "")}}, next(_versao))
    return eid


def _so(monkeypatch, *ids):
    """`drenar_pendentes` só dos eventos DESTE teste, com o filtro REAL de `eventos_pendentes`."""
    import db.webhook_outbox as outbox

    monkeypatch.setattr(outbox, "eventos_pendentes",  # a REAL, importada no topo: `_so` pode rodar 2x
                        lambda limite=100, falhas=5: [e for e in eventos_pendentes(10_000, falhas) if e in ids])


def _recuar(eid: str, minutos: int) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_webhook_events set last_attempt_at = now() - %s * interval '1 minute'"
                    " where event_id = %s", (minutos, eid))
        conn.commit()


# ── (a) teto de drenos simultâneos ───────────────────────────────────────────

def test_semaforo_cheio_recusa_sem_tocar_o_banco(user_id, externo):
    conta(user_id, "free", None)
    eid = _enfileirar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    pix_drain._DRENOS.acquire()
    pix_drain._DRENOS.acquire()
    try:
        assert pix_drain.drenar_evento(eid) is False
    finally:
        pix_drain._DRENOS.release()
        pix_drain._DRENOS.release()
    ev = evento(eid)
    assert (ev["attempts"], ev["last_attempt_at"], ev["processed_at"]) == (0, None, None)
    assert eid in eventos_pendentes(10_000, 6)
    # POSITIVO: com o semáforo livre o mesmo evento é processado.
    assert pix_drain.drenar_evento(eid) is True
    assert evento(eid)["processed_at"] is not None


def test_tres_chamadas_simultaneas_duas_rodam_e_uma_e_recusada(user_id, outro_user, externo, monkeypatch):
    conta(user_id, "free", None)
    cobs = [nova_cobranca(u) for u in (user_id, outro_user)]
    eids = [_enfileirar("PAYMENT_RECEIVED", c) for c in cobs]
    terceiro = _enfileirar("PAYMENT_CONFIRMED", cobs[0])
    dentro, solta, resultados = threading.Semaphore(0), threading.Event(), []
    ga4 = EXECUTORES["ga4"]

    def _ga4(cobranca, evt):
        dentro.release()
        assert solta.wait(20)
        ga4(cobranca, evt)

    monkeypatch.setitem(EXECUTORES, "ga4", _ga4)
    fios = [threading.Thread(target=lambda e=e: resultados.append(pix_drain.drenar_evento(e))) for e in eids]
    try:
        for f in fios:
            f.start()
        assert dentro.acquire(timeout=20) and dentro.acquire(timeout=20)  # os 2 estão DENTRO do efeito
        assert pix_drain.drenar_evento(terceiro) is False
        assert evento(terceiro)["attempts"] == 0
    finally:
        solta.set()
        for f in fios:
            f.join(timeout=30)
    assert resultados == [True, True]
    assert all(evento(e)["processed_at"] is not None for e in eids)
    # os 2 permissos voltaram: 2 aquisições passam, a 3ª não.
    assert pix_drain._DRENOS.acquire(blocking=False) and pix_drain._DRENOS.acquire(blocking=False)
    assert not pix_drain._DRENOS.acquire(blocking=False)
    pix_drain._DRENOS.release()
    pix_drain._DRENOS.release()


# ── (b) backoff depois da 6ª falha ───────────────────────────────────────────

def test_estorno_que_falha_sempre_sai_da_fila_por_uma_hora_e_nao_segura_a_compra(
        user_id, outro_user, externo, monkeypatch):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob)  # a cobrança vira `paid`; agora o estorno
    monkeypatch.setitem(EXECUTORES, "revoke", lambda c, e: (_ for _ in ()).throw(RuntimeError("x")))
    estorno = _enfileirar("PAYMENT_REFUNDED", cob)
    _so(monkeypatch, estorno)
    for rodada in range(1, 7):  # POSITIVO: até a 6ª passada ele volta a CADA rodada, sem esperar 1 h
        assert externo["alerta"] == [] or rodada == 6  # o alerta "travado" (`tentativas > 5`) é DA 6ª
        assert pix_sweeps.drenar_pendentes() == 1
        assert evento(estorno)["attempts"] == rodada
    assert len(externo["alerta"]) == 1 and "travado" in externo["alerta"][0]
    assert evento(estorno)["last_attempt_at"] is not None

    compra = _enfileirar("PAYMENT_RECEIVED", nova_cobranca(outro_user))
    _so(monkeypatch, estorno, compra)
    assert pix_sweeps.drenar_pendentes() == 1  # só a compra: o estorno está em backoff
    assert evento(estorno)["attempts"] == 6
    assert evento(compra)["processed_at"] is not None

    _recuar(estorno, 61)
    assert pix_sweeps.drenar_pendentes() == 1
    assert evento(estorno)["attempts"] == 7


# ── (c) prazo da rodada ──────────────────────────────────────────────────────

def test_prazo_de_45s_para_a_rodada_e_deixa_o_resto_intacto(user_id, outro_user, externo, monkeypatch):
    conta(user_id, "free", None)
    cobs = [nova_cobranca(u) for u in (user_id, outro_user)]
    eids = [_enfileirar("PAYMENT_RECEIVED", c) for c in cobs]
    eids.append(_enfileirar("PAYMENT_CONFIRMED", cobs[0]))
    _so(monkeypatch, *eids)
    valores = iter((0, 0, 46))  # início; 1º evento ainda no prazo; o 2º já estourou
    monkeypatch.setattr(pix_sweeps, "time", types.SimpleNamespace(monotonic=lambda: next(valores, 46)))
    assert pix_sweeps.drenar_pendentes() == 1
    ev = [evento(e) for e in eids]
    assert ev[0]["processed_at"] is not None
    assert [(x["attempts"], x["processed_at"]) for x in ev[1:]] == [(0, None), (0, None)]


def test_dentro_do_prazo_a_rodada_drena_todos(user_id, outro_user, externo, monkeypatch):
    """POSITIVO do (c): com o relógio real, nada é cortado."""
    conta(user_id, "free", None)
    eids = [_enfileirar("PAYMENT_RECEIVED", nova_cobranca(u)) for u in (user_id, outro_user)]
    _so(monkeypatch, *eids)
    assert pix_sweeps.drenar_pendentes() == 2
    assert all(evento(e)["processed_at"] is not None for e in eids)


# ── (d) `registrar_falha` carimba a hora ─────────────────────────────────────

def test_registrar_falha_grava_last_attempt_at(user_id, externo):
    eid = _enfileirar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    assert evento(eid)["last_attempt_at"] is None
    assert registrar_falha(eid, "RuntimeError") == 1
    assert evento(eid)["last_attempt_at"] is not None
