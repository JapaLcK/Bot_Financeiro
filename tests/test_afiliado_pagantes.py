"""Cadastros, clientes que pagaram e reembolsos do afiliado.

Pagante é fato do CLIENTE: vem de `affiliate_referral_payments`, gravada pelo webhook
da Stripe e pelo dreno do Pix, e nunca da comissão, do percentual ou do status do
afiliado. Postgres real; webhook e dreno pelos caminhos de produção.

Decisões: D6=A (reembolso total segue em pagantes, com rótulo), D7=B (conta excluída
mantém a contagem), D8=A (só fatura de plano), D9 (estorno da comissão) fica no PR 1c,
assim como o reembolso da Stripe: aqui `marcar_reembolso` é chamada direto.

Controles negativos rodados à mão estão no relato do PR; os positivos ficam nos casos
que dizem "positivo".
"""
import itertools
import logging
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import core.admin_dashboard as admin_dashboard
import db
import db.affiliate_payments as pagamentos
import db.affiliates as afiliados
import db.connection as conexao
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf
from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, entregar, evento, ler, mundo_externo, nova_cobranca
from core.services import email_service
from core.services.pix_drain import drenar_evento
from core.services.pix_drain_effects import EXECUTORES
from db.affiliate_payments import marcar_reembolso, registrar_pagamento
from db.affiliates import (
    create_affiliate,
    get_affiliate_by_user,
    record_commission_for_invoice,
    record_referral,
    reverse_commission,
    set_affiliate_status,
)
from db import schema
from db.connection import _Conn, _get_pool, get_conn
from db.plan_grants import list_grants
from db.schema import BACKFILL_AFILIADO_PAGAMENTOS_SQL
from db.users import MergeRefused, merge_users
from test_afiliado_area import ME, SENHA, _afiliado, _conta, _me, _painel_aberto  # noqa: F401
from test_billing_webhook_lifecycle import _FakeStripe, _T_LIFE, _fake_sub, _post

AGORA = datetime(2026, 11, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _logs():
    garantir_system_event_logs()


@pytest.fixture
def cobrar(monkeypatch):
    """Devolve `cobrar(uid, ...)`, que entrega uma fatura pelo `/billing/webhook` real."""
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(email_service, "send_pro_charged_email", lambda *a, **k: True)
    fake = _FakeStripe()
    fake.subs["sub_afil"] = _fake_sub("active")
    monkeypatch.setitem(sys.modules, "stripe", fake)
    client = TestClient(dashboard.app)
    relogio = itertools.count(_T_LIFE)

    def _cobrar(uid, fatura=None, valor=60_000, tipo="invoice.paid", linhas=()):
        fatura = fatura or f"in_{uuid.uuid4().hex[:12]}"
        objeto = {"metadata": {"finbot_user_id": str(uid)}, "subscription": "sub_afil",
                  "amount_paid": valor, "id": fatura, "lines": {"data": list(linhas), "has_more": False}}
        r = _post(client, fake, {"type": tipo, "id": f"evt_{uuid.uuid4().hex}",
                                 "created": next(relogio), "data": {"object": objeto}})
        assert r.status_code == 200, r.text
        return fatura

    _cobrar.fake = fake
    return _cobrar


@pytest.fixture
def externo(monkeypatch):
    return mundo_externo(monkeypatch)


def _indicado(c):
    ind = _conta(plano=False)
    assert record_referral(c.aff["code"], ind.uid)
    return ind


def _pix(ind):
    conta(ind.uid, "free", None)
    return nova_cobranca(ind.uid)


def _linhas(c):
    with get_conn() as conn:
        return conn.execute(
            "select p.source, p.payment_ref, p.amount_cents, p.refund_status from affiliate_referral_payments p"
            " join affiliate_referrals r on r.id = p.referral_id where r.affiliate_id = %s order by p.id",
            (c.aff["id"],)).fetchall()


def _numeros(c):
    s = _me(c)["stats"]
    return (s["referrals"], s["paying_clients"], s["refunded_clients"],
            s["refunds"]["total"], s["refunds"]["partial"])


# ── Pagante não depende de comissão ──────────────────────────────────────────

def test_afiliado_zero_por_cento_tem_pagante_sem_comissao(cobrar):
    c = _conta()
    c.aff = create_affiliate(c.uid, commission_bps=0)
    cobrar(_indicado(c).uid)
    m = _me(c)
    assert m["commission_percent"] == 0 and m["commissions"] == []
    assert (m["stats"]["paying_clients"], m["stats"]["generated"]) == (1, 0)


def _admin(monkeypatch):
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    admin = TestClient(dashboard.app, base_url="https://testserver")
    assert admin.post("/admin/auth/login", headers=csrf(admin), json={
        "username": admin_dashboard.ADMIN_DASHBOARD_USERNAME, "password": "secret-admin",
    }).status_code == 200
    return admin


@pytest.mark.parametrize("corpo,esperado", [
    ({"commission_bps": 0}, 0), ({"commission_bps": "0"}, 0),
    ({}, 10_000), ({"commission_bps": None}, 10_000), ({"commission_bps": ""}, 10_000),
    ({"commission_bps": False}, 10_000), ({"commission_bps": 2500}, 2500),
], ids=["zero", "zero_texto", "ausente", "null", "vazio", "false", "positivo_normal"])
def test_admin_grava_zero_como_zero_e_ausente_como_padrao(monkeypatch, corpo, esperado):
    c = _conta(plano=False)
    admin = _admin(monkeypatch)
    r = admin.post("/admin/api/affiliates", headers=csrf(admin), json={"email": c.email, **corpo})
    assert r.status_code == 200, r.text
    assert get_affiliate_by_user(c.uid)["commission_bps"] == esperado


@pytest.mark.parametrize("bps", [0.5, 0.99, -0.5, 1.5, 2500.5])
def test_admin_recusa_percentual_nao_inteiro(monkeypatch, bps):
    c = _conta(plano=False)
    admin = _admin(monkeypatch)
    r = admin.post("/admin/api/affiliates", headers=csrf(admin), json={"email": c.email, "commission_bps": bps})
    assert r.status_code == 400, r.text
    assert get_affiliate_by_user(c.uid) is None


@pytest.mark.parametrize("bps", [True, "1_000", "+5", " 5 ", "٣", "-1", "0x10", "1.5"],
                         ids=["true", "sublinhado", "mais", "espacos", "arabe", "negativo", "hexa", "decimal"])
def test_admin_recusa_o_que_o_int_aceitaria_sozinho(monkeypatch, bps):
    c = _conta(plano=False)
    admin = _admin(monkeypatch)
    r = admin.post("/admin/api/affiliates", headers=csrf(admin), json={"email": c.email, "commission_bps": bps})
    assert r.status_code == 422, r.text
    assert get_affiliate_by_user(c.uid) is None


def test_admin_aceita_inteiro_escrito_como_decimal(monkeypatch):
    c = _conta(plano=False)
    admin = _admin(monkeypatch)
    r = admin.post("/admin/api/affiliates", headers=csrf(admin), json={"email": c.email, "commission_bps": 2500.0})
    assert r.status_code == 200, r.text
    assert get_affiliate_by_user(c.uid)["commission_bps"] == 2500


@pytest.mark.parametrize("bps", [-1, 10_001])
def test_percentual_fora_da_faixa_segue_recusado(bps):
    with pytest.raises(ValueError, match="entre 0 e 10000"):
        create_affiliate(_conta(plano=False).uid, commission_bps=bps)


def test_pix_conta_pagante_e_gera_comissao_de_um_doze_avos(externo):
    c = _afiliado()  # 10%
    cobranca = _pix(_indicado(c))
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _numeros(c) == (1, 1, 0, 0, 0)
    # 49900 × 10% ÷ 12 = 415,83 → 416 (tests/test_afiliado_comissao_pix.py mede a regra)
    assert [(x["amount"], x["invoice_amount"], x["paid_months"]) for x in _me(c)["commissions"]] == [(4.16, 499.0, 12)]
    assert [(x["source"], x["payment_ref"], x["amount_cents"]) for x in _linhas(c)] == [
        ("pix", str(cobranca["id"]), cobranca["amount_cents"])]
    assert "afiliado" in efeitos(cobranca["asaas_payment_id"])


def test_cliente_conta_uma_vez_com_varios_pagamentos(cobrar, externo):
    c = _afiliado()
    ind = _indicado(c)
    entregar("PAYMENT_RECEIVED", _pix(ind))
    fatura = cobrar(ind.uid)
    cobrar(ind.uid, fatura, tipo="invoice.payment_succeeded")  # a mesma fatura nos dois eventos
    cobrar(ind.uid)
    assert len(_linhas(c)) == 3
    assert _numeros(c)[:2] == (1, 1)


def test_registrar_e_idempotente_e_nao_faz_nada_sem_referral_ou_sem_valor():
    c = _afiliado()
    ind, sem_afiliado = _indicado(c), _conta(plano=False)
    assert registrar_pagamento(ind.uid, "stripe", "in_1", 100, AGORA) is True
    assert registrar_pagamento(ind.uid, "stripe", "in_1", 100, AGORA) is False
    assert registrar_pagamento(sem_afiliado.uid, "stripe", "in_2", 100, AGORA) is False
    assert registrar_pagamento(ind.uid, "stripe", "in_3", 0, AGORA) is False
    assert len(_linhas(c)) == 1


def test_fatura_zerada_nao_e_pagante(cobrar):
    c = _afiliado()
    cobrar(_indicado(c).uid, valor=0)
    assert _numeros(c)[:2] == (1, 0)


def test_caderno_em_trial_nao_conta_e_a_fatura_do_plano_conta(cobrar):
    """D8: a fatura do trial cobra só o caderno; o valor do plano nela dá 0."""
    c = _afiliado()
    ind = _indicado(c)
    cobrar.fake.subs["sub_afil"]["metadata"] = {"ebook_price": "price_caderno"}
    caderno = {"pricing": {"price_details": {"price": "price_caderno"}}, "amount": 9_900}
    cobrar(ind.uid, valor=9_900, linhas=[caderno])
    assert _numeros(c)[:2] == (1, 0)
    cobrar(ind.uid, valor=69_900, linhas=[caderno])  # positivo: a fatura do plano entra, sem o caderno
    assert [x["amount_cents"] for x in _linhas(c)] == [60_000]


def test_estorno_de_comissao_nao_apaga_a_conversao(cobrar):
    c = _afiliado()
    cobrar(_indicado(c).uid)
    comissao = _me(c)["commissions"][0]
    assert _me(c)["stats"]["generated"] == 60
    assert reverse_commission(comissao["id"])
    s = _me(c)["stats"]
    assert (s["paying_clients"], s["generated"], s["refunds"]) == (1, 0, {"total": 0, "partial": 0})


def test_desativado_segue_contando_pagante_sem_comissao(cobrar):
    c = _afiliado()
    ind = _indicado(c)
    assert set_affiliate_status(c.aff["id"], "disabled")
    cobrar(ind.uid)
    m = _me(c)
    assert m["stats"]["paying_clients"] == 1 and m["commissions"] == []


# ── Reembolso ────────────────────────────────────────────────────────────────

def test_reembolso_pix_total_fica_em_pagantes_com_rotulo(externo):
    c = _afiliado()
    cobranca = _pix(_indicado(c))
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)
    assert _numeros(c) == (1, 1, 1, 1, 0)


def test_reembolso_pix_parcial_rotula_e_nao_revoga(externo):
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    externo["refunds"] = [{"value": 10.0, "status": "DONE"}]  # com comissão o parcial lê o acumulado
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert _numeros(c) == (1, 1, 0, 0, 1)
    assert [g["status"] for g in list_grants(ind.uid) if g["source"] == "pix"] == ["active"]


def test_pix_parcial_depois_total_vira_total_e_nao_volta(externo):
    """Efeitos distintos de propósito: o dreno deduplica por (pagamento, efeito)."""
    c = _afiliado()
    cobranca = _pix(_indicado(c))
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)
    assert _numeros(c) == (1, 1, 1, 1, 0)
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)  # atrasado
    assert _numeros(c) == (1, 1, 1, 1, 0)


def test_pix_parcial_com_o_afiliado_pendente_rotula_na_retentativa(externo, monkeypatch):
    c = _afiliado()
    cobranca = _pix(_indicado(c))
    with monkeypatch.context() as m:
        m.setattr(pagamentos, "registrar_pagamento", _quebra)
        eid = entregar("PAYMENT_RECEIVED", cobranca)
    externo["refunds"] = [{"value": 10.0, "status": "DONE"}]
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)  # ainda sem linha para marcar
    drenar_evento(eid)
    assert _numeros(c) == (1, 1, 0, 0, 1)


def _grants_pix(ind):
    return [g["status"] for g in list_grants(ind.uid) if g["source"] == "pix"]


@pytest.mark.parametrize("depois", [("PAYMENT_REFUNDED",),
                                    ("PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_REFUNDED"),
                                    ("PAYMENT_REFUNDED", "PAYMENT_CONFIRMED")],
                         ids=["direto", "parcial_antes", "confirmed_depois"])
@pytest.mark.parametrize("causa", ["registro", "email"])
def test_pix_total_com_o_afiliado_pendente_grava_pagante_e_nao_reconcede(externo, monkeypatch, causa, depois):
    """A retentativa da compra morre em D3: o pagante tem de nascer no estorno, e nada de compra
    pode rodar. `grant` já está registrado nestas sequências, então é `afiliado` fora dos efeitos
    e o e-mail sem repetir que ficam vermelhos se a guarda sair; o status do grant, não."""
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    with monkeypatch.context() as m:
        if causa == "registro":
            m.setattr(pagamentos, "registrar_pagamento", _quebra)
        externo["email_ok"] = causa != "email"
        eid = entregar("PAYMENT_RECEIVED", cobranca)
    externo["email_ok"] = True
    assert evento(eid)["processed_at"] is None
    for tipo in depois:
        entregar(tipo, cobranca)
    assert _numeros(c) == (1, 1, 1, 1, 0)  # antes de boot e de retentativa
    drenar_evento(eid)
    assert evento(eid)["processed_at"] is not None
    assert _numeros(c) == (1, 1, 1, 1, 0)
    assert _grants_pix(ind) == ["revoked"]
    assert (externo["ga4"], externo["capi"], externo["email"]) == (1, 1, 1)
    feito = efeitos(cobranca["asaas_payment_id"])
    assert "grant" in feito and "afiliado" not in feito


def test_pix_estorno_antes_do_pagamento_nao_cria_pagante(externo):
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)

    def sem_pagante():
        assert _linhas(c) == [] and _grants_pix(ind) == []
        assert ler(cobranca["id"])["status"] == "pending"

    entregar("PAYMENT_REFUNDED", cobranca)
    sem_pagante()
    entregar("PAYMENT_RECEIVED", cobranca)
    sem_pagante()
    db.init_db()
    sem_pagante()


def test_pix_falha_no_estorno_retenta_sem_reconceder(externo, monkeypatch):
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    with monkeypatch.context() as m:
        m.setattr(pagamentos, "registrar_pagamento", _quebra)
        compra = entregar("PAYMENT_RECEIVED", cobranca)
        estorno = entregar("PAYMENT_REFUNDED", cobranca)
    assert evento(estorno)["processed_at"] is None and evento(estorno)["attempts"] == 1
    feito = efeitos(cobranca["asaas_payment_id"])
    assert "revoke" in feito and "afiliado_estorno" not in feito
    assert _numeros(c) == (1, 0, 0, 0, 0)
    drenar_evento(estorno)
    assert evento(estorno)["processed_at"] is not None
    assert "afiliado_estorno" in efeitos(cobranca["asaas_payment_id"])
    drenar_evento(compra)
    assert _numeros(c) == (1, 1, 1, 1, 0)
    assert _grants_pix(ind) == ["revoked"]


def test_pix_grant_que_falhou_nao_nasce_na_retentativa_depois_do_estorno(externo, monkeypatch):
    """O único caso em que a D3 segura uma concessão de verdade: o `grant` ainda não rodou, e
    a retentativa da compra reconcederia acesso a dinheiro devolvido."""
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    with monkeypatch.context() as m:
        m.setitem(EXECUTORES, "grant", _quebra)
        compra = entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_REFUNDED", cobranca)
    drenar_evento(compra)
    assert evento(compra)["processed_at"] is not None and _grants_pix(ind) == []
    assert "grant" not in efeitos(cobranca["asaas_payment_id"])
    assert (externo["ga4"], externo["capi"], externo["email"]) == (0, 0, 0)


def test_pix_estorno_em_chargeback_rotula_a_linha_que_ja_existe(externo):
    """`chargeback` (reparo manual) não é origem do `PAYMENT_REFUNDED`: a cobrança chega ao
    efeito sem virar `refunded`, e a linha gravada na compra ainda tem de ganhar o rótulo."""
    c = _afiliado()
    cobranca = _pix(_indicado(c))
    entregar("PAYMENT_RECEIVED", cobranca)
    with get_conn() as conn:
        conn.execute("update pix_charges set status = 'chargeback' where id = %s", (cobranca["id"],))
        conn.commit()
    entregar("PAYMENT_REFUNDED", cobranca)
    assert ler(cobranca["id"])["status"] == "chargeback"
    assert _numeros(c) == (1, 1, 1, 1, 0)


@pytest.mark.parametrize("tipo,esperado", [("PAYMENT_REFUNDED", (1, 1, 1, 1, 0)),
                                           ("PAYMENT_PARTIALLY_REFUNDED", (1, 1, 0, 0, 1))],
                         ids=["total", "parcial"])
def test_pix_reembolso_depois_da_exclusao_do_indicado_e_rotulado(externo, tipo, esperado):
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    entregar("PAYMENT_RECEIVED", cobranca)
    db.delete_user_data(ind.uid)
    externo["refunds"] = [{"value": 10.0, "status": "DONE"}]  # a comissão segue: o parcial lê o acumulado
    eid = entregar(tipo, cobranca)
    assert evento(eid)["processed_at"] is not None
    assert _numeros(c) == esperado


def _sem_id_remoto(cobranca):
    with get_conn() as conn:
        conn.execute("update pix_charges set asaas_payment_id = null where id = %s", (cobranca["id"],))
        conn.commit()
    return {**cobranca, "asaas_payment_id": None}


def test_pix_parcial_sem_nenhum_id_fecha_sem_retentativa(externo):
    """Como na base: o rótulo do afiliado não transforma evento sem id em evento travado."""
    c = _afiliado()
    eid = entregar("PAYMENT_PARTIALLY_REFUNDED", _sem_id_remoto(_pix(_indicado(c))), payment_id="")
    assert (evento(eid)["processed_at"] is not None, evento(eid)["attempts"]) == (True, 0)
    assert efeitos("") == set()


def test_pix_parcial_sem_id_em_cobranca_paga_nao_grava_efeito_de_chave_vazia(externo):
    """Aqui a transição acontece: só a falta de id segura o efeito, cuja chave seria `""`
    e valeria para todas as cobranças."""
    c = _afiliado()
    cobranca = _pix(_indicado(c))
    entregar("PAYMENT_RECEIVED", cobranca)
    eid = entregar("PAYMENT_PARTIALLY_REFUNDED", _sem_id_remoto(cobranca), payment_id="")
    assert ler(cobranca["id"])["status"] == "refunded_partial"
    assert (evento(eid)["processed_at"] is not None, evento(eid)["attempts"]) == (True, 0)
    assert efeitos("") == set()


def test_pix_total_sem_nenhum_id_segue_recusado(externo):
    """Positivo do par: quem tem efeito de verdade continua precisando de id."""
    c = _afiliado()
    eid = entregar("PAYMENT_REFUNDED", _sem_id_remoto(_pix(_indicado(c))), payment_id="")
    assert evento(eid)["processed_at"] is None and "PagamentoSemId" in str(evento(eid)["last_error"])


def test_pix_parcial_fora_de_ordem_nao_registra_efeito(externo):
    """Cobrança ainda em `pending`: zero transição e zero efeito, como a matriz do dreno."""
    c = _afiliado()
    cobranca = _pix(_indicado(c))
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    assert efeitos(cobranca["asaas_payment_id"]) == set()
    entregar("PAYMENT_RECEIVED", cobranca)  # positivo: o pagamento entra normalmente
    assert _numeros(c) == (1, 1, 0, 0, 0)


def test_marcar_reembolso_nunca_rebaixa_e_ignora_pagamento_fora_do_registro():
    c = _afiliado()
    ind = _indicado(c)
    registrar_pagamento(ind.uid, "stripe", "in_r", 100, AGORA)
    assert marcar_reembolso("stripe", "in_nao_existe", "total", AGORA) is False
    assert marcar_reembolso("stripe", "in_r", "partial", AGORA) is True
    assert marcar_reembolso("stripe", "in_r", "partial", AGORA) is False
    assert marcar_reembolso("stripe", "in_r", "total", AGORA) is True
    assert marcar_reembolso("stripe", "in_r", "partial", AGORA) is False
    assert [x["refund_status"] for x in _linhas(c)] == ["total"]
    with pytest.raises(ValueError):
        marcar_reembolso("stripe", "in_r", "none", AGORA)


def test_dois_pagamentos_com_o_primeiro_reembolsado_nao_e_cliente_reembolsado(cobrar):
    c = _afiliado()
    ind = _indicado(c)
    primeira = cobrar(ind.uid)
    cobrar(ind.uid)
    assert marcar_reembolso("stripe", primeira, "total", AGORA)
    assert _numeros(c) == (1, 1, 0, 1, 0)
    assert _me(c)["commissions"][0]["payment_refund"] == "total"


# ── Isolamento e LGPD ────────────────────────────────────────────────────────

def test_cada_afiliado_ve_so_os_seus_numeros(cobrar):
    a, b = _afiliado(), _afiliado()
    ind_a, ind_b = _indicado(a), _indicado(b)
    _indicado(b)
    fatura_a, fatura_b = cobrar(ind_a.uid), cobrar(ind_b.uid)
    marcar_reembolso("stripe", fatura_a, "total", AGORA)
    assert _numeros(a) == (1, 1, 1, 1, 0)
    assert _numeros(b) == (2, 1, 0, 0, 0)
    assert _me(b)["commissions"][0]["payment_refund"] == "none"
    forjado = _me(a, affiliate_id=b.aff["id"], user_id=b.uid)
    assert forjado["stats"]["referrals"] == 1 and forjado["code"] == a.aff["code"]
    assert fatura_b not in str(_me(a))


def test_mesma_referencia_em_fontes_diferentes_nao_colide():
    a, b = _afiliado(), _afiliado()
    registrar_pagamento(_indicado(a).uid, "pix", "123", 100, AGORA)
    registrar_pagamento(_indicado(b).uid, "stripe", "123", 100, AGORA)
    assert marcar_reembolso("pix", "123", "total", AGORA)
    assert [x["refund_status"] for x in _linhas(a)] == ["total"]
    assert [x["refund_status"] for x in _linhas(b)] == ["none"]


def test_resposta_so_leva_contagens_e_rotulo(cobrar):
    c = _afiliado()
    ind = _indicado(c)
    fatura = cobrar(ind.uid)
    marcar_reembolso("stripe", fatura, "partial", AGORA)
    r = TestClient(dashboard.app).get(ME, headers=c.h)
    m = r.json()
    assert set(m["stats"]) == {"referrals", "held", "available", "review", "requested", "paid", "generated",
                               "paying_clients", "refunded_clients", "refunds"}
    assert set(m["stats"]["refunds"]) == {"total", "partial"}
    assert set(m["commissions"][0]) == {"id", "invoice_amount", "amount", "original_amount", "in_review", "checking", "status",
                                        "available_at", "created_at", "payout_id", "payment_refund", "paid_months"}
    assert [p for p in (fatura, ind.email, str(ind.uid), "referral_id", "refunded_at",
                        "stripe", "source") if p in r.text] == []


# ── Conexões ─────────────────────────────────────────────────────────────────

@pytest.fixture
def pool_de(monkeypatch):
    """Troca o pool por um pequeno, com timeout curto: conexão aninhada vira `PoolTimeout`."""
    abertos = []
    conninfo = _get_pool().conninfo

    def _pool(tamanho):
        pool = ConnectionPool(conninfo, connection_class=_Conn, min_size=1, max_size=tamanho,
                              timeout=2, kwargs={"row_factory": dict_row}, open=True)
        abertos.append(pool)
        monkeypatch.setattr(conexao, "_get_pool", lambda: pool)
        return pool

    yield _pool
    monkeypatch.undo()
    for pool in abertos:
        pool.close()


def test_stats_simultaneos_nao_esgotam_pool_pequeno(pool_de, monkeypatch):
    """As duas chamadas chegam juntas ao resumo: com a 1ª conexão na mão, ninguém acharia a 2ª."""
    c = _afiliado()
    encontro = threading.Barrier(2, timeout=10)
    real = afiliados.resumo_conversao

    def resumo(affiliate_id):
        encontro.wait()
        return real(affiliate_id)

    monkeypatch.setattr(afiliados, "resumo_conversao", resumo)
    pool_de(2)
    with ThreadPoolExecutor(2) as threads:
        lidos = [threads.submit(afiliados.get_affiliate_stats, c.aff["id"]) for _ in range(2)]
        assert [f.result(timeout=20)["paying_clients"] for f in lidos] == [0, 0]


@pytest.mark.parametrize("chamada", [
    lambda c, ind: afiliados.get_affiliate_stats(c.aff["id"]),
    lambda c, ind: afiliados.list_affiliate_commissions(c.aff["id"]),
    lambda c, ind: pagamentos.resumo_conversao(c.aff["id"]),
    lambda c, ind: registrar_pagamento(ind.uid, "stripe", "in_pool", 100, AGORA),
    lambda c, ind: marcar_reembolso("stripe", "in_pool", "total", AGORA),
], ids=["stats", "comissoes", "resumo", "registrar", "marcar"])
def test_leitura_e_escrita_usam_uma_conexao_por_vez(pool_de, chamada):
    c = _afiliado()
    ind = _indicado(c)
    pool_de(1)
    chamada(c, ind)


# ── Conta excluída ───────────────────────────────────────────────────────────

def _fk_e_nulo():
    with get_conn() as conn:
        fk = conn.execute("select confdeltype from pg_constraint where contype = 'f'"
                          " and conrelid = 'affiliate_referrals'::regclass"
                          " and confrelid = 'users'::regclass").fetchone()["confdeltype"]
        nulo = conn.execute("select is_nullable from information_schema.columns"
                            " where table_schema = current_schema() and table_name = 'affiliate_referrals'"
                            " and column_name = 'referred_user_id'").fetchone()["is_nullable"]
    return fk, nulo


def test_conta_excluida_mantem_cadastro_e_pagante_sem_a_pessoa(cobrar):
    c = _afiliado()
    ind = _indicado(c)
    cobrar(ind.uid)
    assert _numeros(c)[:2] == (1, 1)
    db.delete_user_data(ind.uid)
    assert _numeros(c)[:2] == (1, 1)
    with get_conn() as conn:
        linhas = conn.execute("select referred_user_id from affiliate_referrals where affiliate_id = %s",
                              (c.aff["id"],)).fetchall()
    assert [x["referred_user_id"] for x in linhas] == [None]


def test_dois_excluidos_nao_colidem_no_unique():
    c = _afiliado()
    um, outro = _indicado(c), _indicado(c)
    db.delete_user_data(um.uid)
    db.delete_user_data(outro.uid)
    assert _numeros(c)[0] == 2


def test_excluir_o_afiliado_leva_os_pagamentos():
    c = _afiliado()
    registrar_pagamento(_indicado(c).uid, "stripe", "in_af", 100, AGORA)
    db.delete_user_data(c.uid)
    assert _linhas(c) == []
    with get_conn() as conn:
        assert conn.execute("select 1 from affiliate_referral_payments where payment_ref = 'in_af'").fetchone() is None


def test_init_db_duas_vezes_mantem_set_null():
    db.init_db()
    db.init_db()
    assert _fk_e_nulo() == ("n", "YES")


def test_init_db_migra_banco_antigo_com_cascade_e_not_null():
    with get_conn() as conn:
        conn.execute("delete from affiliate_referrals where referred_user_id is null")
        nome = conn.execute("select conname from pg_constraint where contype = 'f'"
                            " and conrelid = 'affiliate_referrals'::regclass"
                            " and confrelid = 'users'::regclass").fetchone()["conname"]
        conn.execute(f'alter table affiliate_referrals drop constraint "{nome}", add constraint "{nome}"'
                     " foreign key (referred_user_id) references users(id) on delete cascade")
        conn.execute("alter table affiliate_referrals alter column referred_user_id set not null")
        conn.commit()
    assert _fk_e_nulo() == ("c", "NO")
    db.init_db()
    assert _fk_e_nulo() == ("n", "YES")


# ── Backfill e junção de contas ──────────────────────────────────────────────

def test_backfill_le_comissao_e_pix_e_e_idempotente(externo):
    c = _afiliado()
    comum, estornado = _indicado(c), _indicado(c)
    record_commission_for_invoice(comum.uid, "in_bf_1", 10_000)
    linha = record_commission_for_invoice(estornado.uid, "in_bf_2", 20_000)
    assert reverse_commission(linha["id"])
    pago, total, parcial, aberto = (_pix(_indicado(c)) for _ in range(4))
    for cobranca in (pago, total, parcial):
        entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_REFUNDED", total)
    entregar("PAYMENT_PARTIALLY_REFUNDED", parcial)
    with get_conn() as conn:
        conn.execute("delete from affiliate_referral_payments where referral_id in"
                     " (select id from affiliate_referrals where affiliate_id = %s)", (c.aff["id"],))
        conn.commit()

    db.init_db()  # quem roda é o boot
    achado = {(x["source"], x["payment_ref"]): (x["amount_cents"], x["refund_status"]) for x in _linhas(c)}
    db.init_db()
    assert len(_linhas(c)) == len(achado) == 5  # o boot seguinte não duplica
    assert achado == {
        ("stripe", "in_bf_1"): (10_000, "none"),
        ("stripe", "in_bf_2"): (20_000, "none"),  # comissão estornada: a fatura foi paga, e não vira reembolso
        ("pix", str(pago["id"])): (pago["amount_cents"], "none"),
        ("pix", str(total["id"])): (total["amount_cents"], "total"),
        ("pix", str(parcial["id"])): (parcial["amount_cents"], "partial"),
    }
    assert str(aberto["id"]) not in {x["payment_ref"] for x in _linhas(c)}


def test_falha_no_backfill_nao_impede_a_subida_e_vira_log_de_erro(monkeypatch, caplog):
    monkeypatch.setattr(schema, "BACKFILL_AFILIADO_PAGAMENTOS_SQL", "insert into tabela_que_nao_existe values (1)")
    with caplog.at_level(logging.ERROR, logger="db.schema"):
        db.init_db()
    linhas = [r for r in caplog.records if r.name == "db.schema"]
    assert [r.levelname for r in linhas] == ["ERROR"]
    assert "UndefinedTable" in linhas[0].getMessage() and "tabela_que_nao_existe" not in linhas[0].getMessage()
    with get_conn() as conn:  # a conexão do boot volta sã ao pool, sem o prazo de lock
        assert conn.execute("select 1 as ok").fetchone()["ok"] == 1
        assert conn.execute("select current_setting('lock_timeout') as v").fetchone()["v"] == "0"


def _em_outra_conexao():
    return psycopg.connect(_get_pool().conninfo)


def test_backfill_nao_prende_o_boot_atras_de_transacao_aberta(monkeypatch, caplog):
    """Transação aberta com a chave que o backfill vai gravar: o boot sai pelo prazo de lock."""
    c = _afiliado()
    ind = _indicado(c)
    record_commission_for_invoice(ind.uid, "in_trava", 100_000)
    with get_conn() as conn:
        rid = conn.execute("select id from affiliate_referrals where referred_user_id = %s", (ind.uid,)).fetchone()["id"]
    segura = _em_outra_conexao()
    original = psycopg.Cursor.execute

    def executa(cur, query, *a, **k):
        if str(query) == BACKFILL_AFILIADO_PAGAMENTOS_SQL:  # depois da DDL, que também pediria lock
            original(segura.cursor(), "insert into affiliate_referral_payments(referral_id, source, payment_ref,"
                     " amount_cents, paid_at) values (%s, 'stripe', 'in_trava', 1, now())", (rid,))
        return original(cur, query, *a, **k)

    monkeypatch.setattr(psycopg.Cursor, "execute", executa)
    boot = threading.Thread(target=db.init_db, daemon=True)
    try:
        with caplog.at_level(logging.ERROR, logger="db.schema"):
            boot.start()
            boot.join(25)
        assert not boot.is_alive(), "o boot ficou preso atrás de uma transação alheia"
        assert "LockNotAvailable" in caplog.text
    finally:
        segura.rollback()
        segura.close()
        boot.join(30)
    monkeypatch.undo()
    db.init_db()  # positivo: sem a trava, o boot seguinte conclui
    assert [x["payment_ref"] for x in _linhas(c)] == ["in_trava"]


@pytest.mark.parametrize("tabela", ["affiliate_referrals", "affiliate_commissions", "affiliate_payouts"])
def test_boot_nao_pede_lock_exclusivo_quando_a_coluna_ja_aceita_nulo(tabela):
    leitor = _em_outra_conexao()
    leitor.execute(f"select count(*) from {tabela}")  # fica ocioso na transação, como um relatório
    boot = threading.Thread(target=db.init_db, daemon=True)
    try:
        boot.start()
        boot.join(25)
        assert not boot.is_alive(), "o boot fez fila atrás de uma leitura"
    finally:
        leitor.rollback()
        leitor.close()
        boot.join(30)


def test_merge_leva_os_pagamentos_e_o_cliente_segue_um():
    c = _afiliado()
    origem = _indicado(c)
    destino = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(destino)
    registrar_pagamento(origem.uid, "stripe", "in_m1", 100, AGORA)
    merge_users(origem.uid, destino)
    assert registrar_pagamento(destino, "stripe", "in_m2", 100, AGORA) is True
    assert _numeros(c)[:2] == (1, 1)
    assert len(_linhas(c)) == 2


def test_merge_com_referral_nos_dois_lados_e_recusado_sem_mexer_nos_numeros():
    a, b = _afiliado(), _afiliado()
    origem, destino = (int(uuid.uuid4().int % 10_000_000_000) for _ in range(2))
    for uid, c in ((origem, a), (destino, b)):
        db.ensure_user(uid)
        assert record_referral(c.aff["code"], uid)
        registrar_pagamento(uid, "stripe", f"in_{uid}", 100, AGORA)
    with pytest.raises(MergeRefused):
        merge_users(origem, destino)
    assert _numeros(a)[:2] == (1, 1) and _numeros(b)[:2] == (1, 1)


# ── Falha do registro ────────────────────────────────────────────────────────

def _quebra(*a, **k):
    raise RuntimeError("banco fora: indicado@example.com")


def test_falha_no_registro_stripe_nao_derruba_webhook_grant_nem_comissao(cobrar, monkeypatch):
    c = _afiliado()
    ind = _indicado(c)
    with monkeypatch.context() as m:
        m.setattr(pagamentos, "registrar_pagamento", _quebra)
        fatura = cobrar(ind.uid)
    assert len(_me(c)["commissions"]) == 1
    assert db.get_auth_user(ind.uid)["plan"] != "free"
    assert _numeros(c)[:2] == (1, 0)
    with get_conn() as conn:
        log = conn.execute("select level, message, details from system_event_logs"
                           " where event_type = 'affiliate_payment_record_failed'").fetchone()
    assert log["level"] == "error" and "indicado@example.com" not in str(log)
    cobrar(ind.uid, fatura, tipo="invoice.payment_succeeded")  # positivo: o evento irmão da mesma fatura grava
    assert _numeros(c)[:2] == (1, 1)
    assert len(_me(c)["commissions"]) == 1


def test_falha_no_registro_pix_nao_segura_grant_e_a_retentativa_conserta(externo, monkeypatch):
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    with monkeypatch.context() as m:
        m.setattr(pagamentos, "registrar_pagamento", _quebra)
        eid = entregar("PAYMENT_RECEIVED", cobranca)
    feito = efeitos(cobranca["asaas_payment_id"])
    assert {"grant", "email"} <= feito and "afiliado" not in feito
    assert [g["status"] for g in list_grants(ind.uid) if g["source"] == "pix"] == ["active"]
    assert evento(eid)["processed_at"] is None and evento(eid)["attempts"] == 1
    drenar_evento(eid)
    assert evento(eid)["processed_at"] is not None
    assert _numeros(c)[:2] == (1, 1)
    assert externo["email"] == 1


# ── A conversa inteira ───────────────────────────────────────────────────────

def test_conversa_do_link_ate_a_exclusao(cobrar, externo, monkeypatch):
    c = _afiliado()
    codigos = {}
    monkeypatch.setattr(email_service, "send_verification_email",
                        lambda to, code, *a, **k: codigos.update({to: code}) or True)
    monkeypatch.setattr(email_service, "send_welcome_email", lambda *a, **k: True)
    visitante = TestClient(dashboard.app)
    assert visitante.get(f"/r/{c.aff['code']}", follow_redirects=False).status_code == 302
    email = f"afil-pag-{uuid.uuid4().hex[:10]}@example.com"
    r = visitante.post("/auth/register", headers=csrf(visitante), json={
        "email": email, "password": SENHA, "phone": f"55119{uuid.uuid4().int % 100_000_000:08d}", "name": "Bia"})
    assert r.status_code == 200, r.text
    r = visitante.post("/auth/verify-email", headers=csrf(visitante), json={"email": email, "code": codigos[email]})
    assert r.status_code == 200, r.text
    novo = int(r.json()["user_id"])
    assert _numeros(c)[:2] == (1, 0)

    fatura = cobrar(novo)
    cobrar(novo, fatura, tipo="invoice.payment_succeeded")
    assert _numeros(c)[:2] == (1, 1)
    marcar_reembolso("stripe", fatura, "total", AGORA)
    assert _numeros(c) == (1, 1, 1, 1, 0)

    db.delete_user_data(novo)
    assert _numeros(c) == (1, 1, 1, 1, 0)
