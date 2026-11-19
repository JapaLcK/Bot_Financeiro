"""#1437, na RECEPÇÃO: o marcador `pix_estornos_recebidos` nasce em `registrar_evento`, na mesma
transação da linha da outbox, para todo `PAYMENT_REFUNDED` com `payment.id` válido
(`db/pix_estornos.marcar_estorno`), com origem (ambiente e conta) e classificação. O bloqueio é só
pelo id. O dreno e a D3 estão em `tests/test_pix_estorno_recebido.py`."""
from __future__ import annotations

import threading
import uuid

import pytest

import core.services.pix_drain as dreno
import db.pix_estornos as pix_estornos
from _billing_grants_helpers import conta
from _dreno_pix_helpers import efeitos, entregar, ler, nova_cobranca
from db.connection import get_conn
from db.webhook_outbox import eventos_pendentes, marcar_processado, registrar_evento
from test_pix_dreno_estorno import _grants_ativos, externo  # noqa: F401


def _evt():
    return f"evt_{uuid.uuid4().hex[:16]}"


def _pid():
    return f"pay_{uuid.uuid4().hex[:12]}"


def _marcador(pid):
    with get_conn() as conn:
        return [(r["origem"], r["classificacao"]) for r in conn.execute(
            "select origem, classificacao from pix_estornos_recebidos where asaas_payment_id = %s order by origem",
            (pid,)).fetchall()]


@pytest.fixture()
def receber():
    """`receber(tipo, pagamento, eid=None)` grava pela outbox SEM drenar; no fim, tira tudo da fila."""
    eids = []

    def _receber(tipo, pagamento, eid=None):
        eids.append(eid or _evt())
        registrar_evento(eids[-1], tipo, {"id": eids[-1], "event": tipo, "payment": pagamento}, 1)
        return eids[-1]

    yield _receber
    for eid in eids:  # nenhuma varredura de outro teste os drena
        marcar_processado(eid)


def test_recepcao_classifica_nosso_alheio_e_nao_identificado(user_id, receber):
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    casos = {
        cobranca["asaas_payment_id"]: ({}, "nosso"),                                  # o id é nosso, sem referência
        _pid(): ({"externalReference": cobranca["external_reference"]}, "nosso"),     # a referência é nossa
        _pid(): ({}, "nao_identificado"),                                             # sem referência
        _pid(): ({"externalReference": "pix:999999999"}, "nao_identificado"),         # nosso formato, sem linha
        _pid(): ({"externalReference": "ext-123"}, "alheio"),                         # Pix avulso da conta
    }
    for pid, (extra, _) in casos.items():
        receber("PAYMENT_REFUNDED", {"id": pid, **extra})
    parcial = _pid()
    receber("PAYMENT_PARTIALLY_REFUNDED", {"id": parcial})
    assert {pid: [c for _, c in _marcador(pid)] for pid in casos} == {pid: [c] for pid, (_, c) in casos.items()}
    assert _marcador(parcial) == []


def test_duplicada_e_event_id_novo_dao_uma_linha_com_a_classe_estavel(receber):
    """A reentrega (mesmo `event_id`, ou um novo) não duplica nem muda a classe; e a de um evento
    recebido ANTES do deploy, sem marcador, marca na duplicata."""
    pid = _pid()
    eid = receber("PAYMENT_REFUNDED", {"id": pid, "externalReference": "ext-1"})
    receber("PAYMENT_REFUNDED", {"id": pid, "externalReference": "ext-1"}, eid)
    receber("PAYMENT_REFUNDED", {"id": pid})
    assert [c for _, c in _marcador(pid)] == ["alheio"]
    with get_conn() as conn:
        conn.execute("delete from pix_estornos_recebidos where asaas_payment_id = %s", (pid,))
        conn.commit()
    receber("PAYMENT_REFUNDED", {"id": pid, "externalReference": "ext-1"}, eid)
    assert [c for _, c in _marcador(pid)] == ["alheio"]


def test_dois_estornos_simultaneos_do_mesmo_id_dao_uma_linha(receber):
    pid, encontro, erros = _pid(), threading.Barrier(2, timeout=10), []

    def um():
        try:
            encontro.wait()
            receber("PAYMENT_REFUNDED", {"id": pid})
        except Exception as exc:  # noqa: BLE001 — o teste afirma que não há nenhuma
            erros.append(exc)

    linhas = [threading.Thread(target=um) for _ in range(2)]
    for t in linhas:
        t.start()
    for t in linhas:
        t.join(15)
    assert erros == [] and len(_marcador(pid)) == 1


def test_estorno_e_compra_simultaneos_nao_deixam_acesso(user_id, externo, receber):
    """O `REFUNDED` chega pelo webhook enquanto o `RECEIVED` drena. Qualquer que seja a ordem, depois
    do dreno do estorno não sobra acesso, e a cobrança não fica `paid`."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    encontro, estorno = threading.Barrier(2, timeout=10), []

    def chega_o_estorno():
        encontro.wait()
        estorno.append(receber("PAYMENT_REFUNDED", {"id": cobranca["asaas_payment_id"],
                                                    "externalReference": cobranca["external_reference"]}))

    t = threading.Thread(target=chega_o_estorno)
    t.start()
    encontro.wait()
    entregar("PAYMENT_RECEIVED", cobranca)
    t.join(15)
    dreno.drenar_evento(estorno[0])
    assert _grants_ativos(user_id) == [] and ler(cobranca["id"])["status"] in ("refunded", "canceled")


def test_estorno_alheio_nao_afeta_os_nossos(user_id, externo, receber):
    alheio = _pid()
    receber("PAYMENT_REFUNDED", {"id": alheio, "externalReference": "ext-123"})
    assert [c for _, c in _marcador(alheio)] == ["alheio"]
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert ler(cobranca["id"])["status"] == "paid" and len(_grants_ativos(user_id)) == 1


def test_origem_sandbox_e_producao_e_qualquer_uma_bloqueia(user_id, externo, receber, monkeypatch):
    """A origem separa sandbox de produção (`ASAAS_BASE_URL`) e a conta (`ASAAS_CONTA`), e não
    restringe o bloqueio: só a linha do sandbox já barra o `RECEIVED` do mesmo id."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    pid = cobranca["asaas_payment_id"]
    monkeypatch.setenv("ASAAS_CONTA", "wallet-teste")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://sandbox.asaas.com/api")
    receber("PAYMENT_REFUNDED", {"id": pid})
    monkeypatch.delenv("ASAAS_BASE_URL")
    receber("PAYMENT_REFUNDED", {"id": pid})
    assert _marcador(pid) == [("producao:wallet-teste", "nosso"), ("sandbox:wallet-teste", "nosso")]
    with get_conn() as conn:
        conn.execute("delete from pix_estornos_recebidos where asaas_payment_id = %s"
                     " and origem like 'producao:%%'", (pid,))
        conn.commit()
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _grants_ativos(user_id) == [] and externo["ga4"] == 0


@pytest.mark.parametrize("pagamento", [
    None, [], "x", {}, {"id": None}, {"id": ""}, {"id": "   "}, {"id": 123}, {"id": ["pay_lista"]},
    {"id": {"id": "pay_objeto"}}, {"id": "pay_x\n"}, {"id": " pay_x"}, {"id": "pay x"},
], ids=["sem_payment", "lista", "texto", "vazio", "id_nulo", "id_vazio", "id_espacos", "id_inteiro",
        "id_lista", "id_objeto", "id_quebra", "id_espaco_inicial", "id_espaco_meio"])
def test_recepcao_nao_marca_id_invalido_e_responde_200(monkeypatch, pagamento):
    from test_pix_rotas_billing import TOKEN, client

    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(dreno, "drenar_evento", lambda e: None)
    eid = _evt()
    try:
        r = client.post("/billing/asaas/webhook", json={"id": eid, "event": "PAYMENT_REFUNDED", "payment": pagamento},
                        headers={"asaas-access-token": TOKEN})
        with get_conn() as conn:
            linhas = conn.execute("select 1 from pix_estornos_recebidos where event_id = %s", (eid,)).fetchall()
        assert r.status_code == 200 and linhas == []
    finally:
        marcar_processado(eid)


def test_marcador_que_falha_derruba_a_linha_da_outbox(monkeypatch):
    """Tudo ou nada: sem o marcador, o evento não entra e o handler não responde 200 (o Asaas
    reentrega). Um marcador fora da transação deixaria o estorno aceito e sem marcador."""
    def quebra(*a, **k):
        raise RuntimeError("marcador falhou")

    monkeypatch.setattr(pix_estornos, "gravar", quebra)
    eid = _evt()
    with pytest.raises(RuntimeError):
        registrar_evento(eid, "PAYMENT_REFUNDED", {"id": eid, "event": "PAYMENT_REFUNDED", "payment": {"id": _pid()}}, 1)
    with get_conn() as conn:
        assert conn.execute("select 1 from pix_webhook_events where event_id = %s", (eid,)).fetchone() is None


def test_fila_poe_o_estorno_antes_da_compra_mais_velha(user_id, receber):
    """Complemento da pausa (#1574), não substituto: a varredura drena o `PAYMENT_REFUNDED` antes."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    pagamento = {"id": cobranca["asaas_payment_id"], "externalReference": cobranca["external_reference"]}
    compra = receber("PAYMENT_RECEIVED", pagamento)
    estorno = receber("PAYMENT_REFUNDED", pagamento)
    fila = [e for e in eventos_pendentes(10_000, 6) if e in (compra, estorno)]
    assert fila == [estorno, compra] and efeitos(cobranca["asaas_payment_id"]) == set()


def test_dreno_promove_nao_identificado_a_nosso(user_id, externo):
    """Marcador gravado quando o id ainda não estava na cobrança (`nao_identificado`); o estorno sem
    `payment.id` (a recepção não marca) é achado pela referência, e o dreno promove a `nosso`."""
    from db.pix_charges_saga import attach_pagamento

    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    pid = _pid()
    with get_conn() as conn:
        with conn.cursor() as cur:
            pix_estornos.gravar(pid, None, "evt_antes", cur)
        conn.execute("update pix_charges set asaas_payment_id = null, status = 'creating' where id = %s",
                     (cobranca["id"],))
        conn.commit()
    assert [c for _, c in _marcador(pid)] == ["nao_identificado"]
    assert attach_pagamento(cobranca["id"], pid)
    entregar("PAYMENT_REFUNDED", cobranca, payment_id="")
    assert [c for _, c in _marcador(pid)] == ["nosso"] and ler(cobranca["id"])["status"] == "canceled"
