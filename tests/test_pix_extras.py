"""Cadernos extras no Pix anual, PR A (receber e entregar) — banco real, pelo dreno.

D. o efeito `ebook` do dreno grava uma pendência por caderno da foto
   (`session_id` = `pix:<id>`), herda D3 e órfão, e é idempotente.
V. o total (plano + cadernos) é o que `alertar_valor`, GA4/CAPI e o poll usam; o
   crédito de upgrade continua só sobre o plano.
J. o job entrega o caderno Pix conferindo a cobrança do dono e o Asaas, ANTES de
   qualquer chamada ao Stripe.
M. a coluna `extras` e o check (DDL idempotente).

Controles negativos (rodados — ver o relato do PR):
  · tirar `ebook` de `EFEITOS_DE_COMPRA`                  → D1 vermelho;
  · `alertar_valor` comparando com `amount_cents`          → V1 vermelho;
  · `conferir_entrega` sem as checagens de estorno         → J2/J3 vermelhos;
  · `cobranca_do_dono` sem `user_id` no where              → J5 vermelho.
  Positivos: D2 (sem cadernos o grant sai e nada é gravado), J1 (entrega), V1
  (o plano sozinho ainda alerta).
"""
from __future__ import annotations

import secrets
import uuid

import pytest
from psycopg import errors

import core.services.asaas as asaas
from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, evento, ler, mundo_externo, nova_cobranca
from core.services.ebook_entrega import entregar_pendentes
from db.connection import get_conn
from db.plan_grants import list_grants
from test_ebook_entrega import mundo  # noqa: F401

_EXTRAS = [
    {"price": "price_cad_a", "url": "https://drive.test/a?x=1&y=2", "nome": "Caderno A", "valor_cents": 1990},
    {"price": "price_cad_b", "url": "https://drive.test/b", "nome": "Caderno <B>", "valor_cents": 2990},
]


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        r = cur.fetchall() if cur.description else None
        conn.commit()
    return r


def _pendencias(uid):
    return {r["ebook_price"]: r for r in _sql(
        "select session_id, ebook_price, ebook_url, resultado from ebook_entregas"
        " where user_id = %s", (uid,))}


def _paga(uid, extras=_EXTRAS):
    conta(uid, "free", None)
    cob = nova_cobranca(uid, extras=extras)
    entregar("PAYMENT_RECEIVED", cob, valor=(cob["amount_cents"] + sum(
        e["valor_cents"] for e in extras)) / 100)
    return cob


# ── D. o efeito `ebook` ──────────────────────────────────────────────────────

def test_d1_received_com_dois_cadernos_grava_duas_pendencias(user_id, externo, monkeypatch):
    import core.services.email_service as es

    pedidos = []
    monkeypatch.setattr(es, "send_pix_paid_email", lambda *a, **kw: pedidos.append((a, kw)) or True)
    cob = _paga(user_id)
    linhas = _pendencias(user_id)
    assert {p: (r["session_id"], r["ebook_url"]) for p, r in linhas.items()} == {
        e["price"]: (cob["external_reference"], e["url"]) for e in _EXTRAS}
    assert "ebook" in efeitos(cob["asaas_payment_id"])
    assert ler(cob["id"])["status"] == "paid"
    assert externo["alerta"] == [], "o total pago não pode alertar valor divergente"
    assert externo["ga4_kw"][0]["value"] == (49900 + 1990 + 2990) / 100
    (args, kw), = pedidos
    assert args[2] == 499.0, "o e-mail recebe o plano em `amount_brl`"
    assert kw["extras"] == [("Caderno A", 1990), ("Caderno <B>", 2990)]


def test_d2_sem_cadernos_e_no_op_registrado_e_o_grant_sai(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cob)
    assert _pendencias(user_id) == {}
    assert efeitos(cob["asaas_payment_id"]) >= {"grant", "ebook"}
    assert [g for g in list_grants(user_id) if g["source"] == "pix" and g["status"] == "active"]


def test_d3_reentrega_e_evento_novo_do_mesmo_pagamento_nao_duplicam(user_id, externo):
    cob = _paga(user_id)
    entregar("PAYMENT_RECEIVED", cob)       # event_id novo, mesmo pagamento
    entregar("PAYMENT_CONFIRMED", cob)
    assert len(_pendencias(user_id)) == 2


def test_d4_registrar_efeito_falhando_uma_vez_e_o_retry_nao_duplicam(user_id, externo, monkeypatch):
    import core.services.pix_drain as dreno
    from core.services.pix_drain import drenar_evento

    real, falhou = dreno.registrar_efeito, []

    def _uma_falha(pid, efeito, evt):
        if efeito == "ebook" and not falhou:
            falhou.append(1)
            raise errors.OperationalError("caiu")
        return real(pid, efeito, evt)

    monkeypatch.setattr(dreno, "registrar_efeito", _uma_falha)
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id, extras=_EXTRAS)
    event_id = entregar("PAYMENT_RECEIVED", cob)
    assert falhou and "ebook" not in efeitos(cob["asaas_payment_id"])
    assert evento(event_id)["processed_at"] is None, "a falha tem de deixar o evento aberto"
    drenar_evento(event_id)                 # a retentativa refaz o efeito
    assert len(_pendencias(user_id)) == 2
    assert "ebook" in efeitos(cob["asaas_payment_id"])


def test_d5_estorno_antes_do_received_nao_grava(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id, extras=_EXTRAS)
    entregar("PAYMENT_REFUNDED", cob)
    entregar("PAYMENT_RECEIVED", cob)
    assert _pendencias(user_id) == {}


def test_d6_orfao_nao_grava(user_id, externo):
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id, extras=_EXTRAS)
    _sql("update pix_charges set user_id = null where id = %s", (cob["id"],))
    entregar("PAYMENT_RECEIVED", cob)
    assert ler(cob["id"])["status"] == "paid_orphan"
    assert _sql("select count(*) as n from ebook_entregas where session_id = %s",
                (cob["external_reference"],))[0]["n"] == 0


def test_e1_email_discrimina_plano_cadernos_e_total(monkeypatch):
    import core.services.email_service as es

    enviados = []
    monkeypatch.setattr(es, "send_email", lambda **kw: enviados.append(kw) or True)
    from datetime import datetime, timezone
    agora = datetime.now(timezone.utc)
    es.send_pix_paid_email("a@t.com", "pro_max", 499.0, agora, agora,
                           extras=[("Caderno A", 1990), ("Caderno <B>", 2990)])
    es.send_pix_paid_email("a@t.com", "pro_max", 499.0, agora, agora)
    com, sem = enviados
    nome = es.plan_display_name("pro_max")
    for linha in (f"Plano {nome}: R$ 499,00", "Caderno A: R$ 19,90", "Total pago: R$ 548,80"):
        assert linha in com["text_body"], linha
    assert "Caderno &lt;B&gt;:</strong> R$ 29,90" in com["html_body"]
    assert "Total pago:</strong> R$ 548,80" in com["html_body"]
    assert "(R$ 548,80)" in com["subject"]
    assert "Valor pago: R$ 499,00" in sem["text_body"] and "Total pago" not in sem["html_body"]
    assert "Caderno" not in sem["html_body"] and f"Plano {nome}:" not in sem["text_body"]


# ── V. o total ───────────────────────────────────────────────────────────────

def test_v1_alertar_valor_compara_com_o_total(externo):
    from core.services.pix_drain_effects import alertar_valor

    cob = {"id": 1, "amount_cents": 49900, "extras": _EXTRAS}
    alertar_valor(cob, (49900 + 1990 + 2990) / 100)
    assert externo["alerta"] == []
    alertar_valor(cob, 499.00)              # só o plano: faltou dinheiro
    assert len(externo["alerta"]) == 1


def test_v2_credito_de_upgrade_e_o_poll(user_id, externo):
    from db.pix_charges_saga import valores_por_cobranca

    cob = _paga(user_id)
    assert valores_por_cobranca(user_id)[str(cob["id"])] == 49900, "crédito só sobre o plano"
    from core.services.pix_extras import total_cents
    assert total_cents(ler(cob["id"])) == 49900 + 1990 + 2990
    assert total_cents({"amount_cents": 100}) == 100


def test_v3_poll_devolve_o_total(user_id, monkeypatch):
    from fastapi.testclient import TestClient

    import frontend.finance_bot_websocket_custom as dashboard
    import frontend.routes.billing_pix as rotas
    from db.pix_charges import criar_cobranca

    linha = criar_cobranca(user_id, public_token=secrets.token_urlsafe(16), plan="pro_max",
                           plan_stored="pro_max", price_cents=49900, credit_cents=0,
                           amount_cents=49900, duration_days=365, extras=_EXTRAS)
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)
    corpo = TestClient(dashboard.app).get(f"/billing/pix/{linha['public_token']}").json()
    assert (corpo["amount_cents"], corpo["total_cents"]) == (49900, 49900 + 1990 + 2990)


# ── J. o job ────────────────────────────────────────────────────────────────

@pytest.fixture()
def asaas_falso(monkeypatch):
    estado = {"resposta": {"status": "RECEIVED", "refunds": None}, "chamadas": []}

    def _buscar(pid):
        estado["chamadas"].append(pid)
        if isinstance(estado["resposta"], Exception):
            raise estado["resposta"]
        return estado["resposta"]

    monkeypatch.setattr(asaas, "buscar_pagamento", _buscar)
    return estado


def _emails(mundo, uid):
    return [e for e in mundo.enviados if e[0] == f"gr-{uid}@t.local" and e[1].startswith("📘 Chegou")]


def test_j1_received_entrega_os_dois_sem_tocar_no_stripe(user_id, externo, mundo, asaas_falso):
    _paga(user_id)
    entregar_pendentes()
    assert {r["resultado"] for r in _pendencias(user_id).values()} == {"enviado"}
    envios = _emails(mundo, user_id)
    assert len(envios) == 2 and any("Caderno &lt;B&gt;" in e[2] for e in envios)
    assert mundo.chamadas == [] and mundo.consultas == [], "a compra Pix chamou o Stripe"


@pytest.mark.parametrize("evento", ["PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED"])
def test_j2_estorno_local_fecha_estornado_sem_asaas(user_id, externo, mundo, asaas_falso, evento):
    cob = _paga(user_id)
    entregar(evento, cob)
    asaas_falso["chamadas"].clear()
    entregar_pendentes()
    assert {r["resultado"] for r in _pendencias(user_id).values()} == {"estornado"}
    assert asaas_falso["chamadas"] == [] and _emails(mundo, user_id) == []


@pytest.mark.parametrize("resposta", [
    {"status": "CHARGEBACK_REQUESTED", "refunds": None},
    {"status": "RECEIVED", "refunds": [{"value": 10.0}]},
    {"status": "REFUND_IN_PROGRESS"},
], ids=["contestacao", "refunds", "estorno-em-andamento"])
def test_j3_estorno_no_asaas_fecha_estornado(user_id, externo, mundo, asaas_falso, resposta):
    _paga(user_id)
    asaas_falso["resposta"] = resposta
    entregar_pendentes()
    assert {r["resultado"] for r in _pendencias(user_id).values()} == {"estornado"}
    assert _emails(mundo, user_id) == []


@pytest.mark.parametrize("resposta", [
    asaas.AsaasApiError("fora", status_code=503),
    {"status": "STATUS_NOVO"},
    {"status": "RECEIVED", "refunds": "x"},
], ids=["asaas-fora", "status-desconhecido", "refunds-forma"])
def test_j4_falha_do_asaas_nao_envia_nem_fecha(user_id, externo, mundo, asaas_falso, resposta):
    _paga(user_id)
    asaas_falso["resposta"] = resposta
    entregar_pendentes()
    assert {r["resultado"] for r in _pendencias(user_id).values()} == {None}
    assert _emails(mundo, user_id) == []


def test_j5_linha_de_a_com_a_cobranca_de_b_e_nao_comprou(user_id, externo, mundo, asaas_falso):
    from db import ensure_user
    from db.ebook_entregas import registrar

    cob_b = _paga(user_id)
    a = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(a)
    conta(a, "free", None)
    registrar(a, cob_b["external_reference"], [("price_cad_a", "https://drive.test/a")])
    asaas_falso["chamadas"].clear()
    from core.services.ebook_entrega import _entregar
    _entregar(a, cob_b["external_reference"], "price_cad_a", "sk_test_job")
    assert _pendencias(a)["price_cad_a"]["resultado"] == "nao_comprou"
    from db.pix_extras import cobranca_do_dono
    ref = cob_b["external_reference"]
    assert cobranca_do_dono(a, ref) is None and cobranca_do_dono(user_id, ref)["status"] == "paid"
    assert asaas_falso["chamadas"] == [] and _emails(mundo, a) == []


def test_j6_compra_stripe_nunca_chama_o_asaas(mundo, asaas_falso):
    from test_ebook_entrega import _conta

    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_j6")
    entregar_pendentes()
    assert len(mundo.para(email)) == 1 and asaas_falso["chamadas"] == []


# ── M. DDL ───────────────────────────────────────────────────────────────────

def test_m1_init_db_duas_vezes_e_check_recusa_nao_array(user_id):
    from db.pix_charges import criar_cobranca
    from db.schema import init_db

    init_db()
    init_db()
    linha = criar_cobranca(user_id, public_token=secrets.token_urlsafe(16), plan="pro_max",
                           plan_stored="pro_max", price_cents=100, credit_cents=0,
                           amount_cents=100, duration_days=365)
    assert linha["extras"] == []
    with pytest.raises(errors.CheckViolation):
        _sql("update pix_charges set extras = '{}'::jsonb where id = %s", (linha["id"],))
