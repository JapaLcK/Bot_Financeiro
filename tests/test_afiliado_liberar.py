"""#1538, PR 2: Liberar na conferência, "Ver fatura" e o 4xx da fatura no saque (sem confirmação, não 503).

Rota real, Postgres real; a Stripe é a falsa de `test_afiliado_conferencia` (`formas`). Mesmos helpers do PR 1.
"""
import json
import uuid

import pytest
import stripe
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf
from db.affiliate_reviews import registrar_reembolso
from db.affiliates import get_affiliate_stats, request_payout
from db.connection import get_conn
from test_afiliado_area import _afiliado, _comissao  # noqa: F401
from test_afiliado_conferencia import fonte  # noqa: F401
from test_afiliado_conferencia_admin import (
    EVID, _admin, _db, _decide, _em_paralelo, _eventos, _fotografia, _linha, _marca, _tentada, adm,  # noqa: F401
)
from test_afiliado_exclusao import _saque_com_pix
from test_afiliado_fila_conferencia import formas  # noqa: F401
from test_afiliado_reembolso import mundo  # noqa: F401
from test_afiliado_pagantes import AGORA
from test_afiliado_pendencia import OVERVIEW, _paga_e_saca


def _erro(status=404):
    return stripe.InvalidRequestError("No such invoice", "invoice", http_status=status)


def _liberar(adm, com, visto=None, **corpo):
    return _decide(adm, com, "release", visto, **corpo)


@pytest.mark.parametrize("como", ["sem_cobranca", "forma_inesperada", "stripe_404"])
def test_liberar_positivo(adm, formas, monkeypatch, como):
    """Positivo: a pendente presa entra no saldo, sai da lista, o saque novo a inclui e o evento sai uma vez."""
    c = _afiliado()
    com = _marca(_comissao(c, 100))
    if como == "stripe_404":
        formas.falha = _erro()
    _tentada(com, formas, monkeypatch, "sem_cobranca" if como == "stripe_404" else como)
    assert _linha(adm, com)["reason"] == como
    assert get_affiliate_stats(c.aff["id"])["available_cents"] == 0
    r = _liberar(adm, com)
    assert r.status_code == 200, r.text
    assert _linha(adm, com) is None and _db(com)["payment_check_pending"] is False
    assert get_affiliate_stats(c.aff["id"])["available_cents"] == 10_000
    assert request_payout(c.aff["id"])["amount_cents"] == 10_000
    [e] = _eventos(com)
    assert (e["action"], e["before_cents"], e["after_cents"], e["receipt_ref"], e["actor"]) == (
        "check_released", 10_000, 10_000, EVID, admin_dashboard.ADMIN_DASHBOARD_USERNAME)
    assert e["state_before"]["marcada"] is True and e["state_after"]["marcada"] is False
    assert (e["state_after"]["status"], e["state_after"]["payout_id"]) == ("pending", None)


@pytest.mark.parametrize("corpo", [{"justification": ""}, {"justification": "curta"}, {"evidence": ""},
                                   {"evidence": "tem espaço"}])
def test_liberar_422_sem_justificativa_ou_evidencia(adm, formas, monkeypatch, corpo):
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    antes = _fotografia(c, com)
    assert _liberar(adm, com, **corpo).status_code == 422
    assert _fotografia(c, com) == antes and _eventos(com) == [] and _db(com)["payment_check_pending"] is True


def test_liberar_recusas_sob_a_trava(adm, formas, monkeypatch):
    """Cada recusa é 409, sem consultar a Stripe, sem evento e sem mexer em dinheiro."""
    c = _afiliado()
    disputa = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch, "disputa_aberta")
    sem_tentativa = _marca(_comissao(c, 100))
    desconhecido = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_reason = 'motivo_novo' where id = %s",
                     (desconhecido.id,))
    revisao = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    registrar_reembolso(revisao.fatura, "partial", 30_000, AGORA)
    _tentada(revisao, formas, monkeypatch)  # a marca nova do reembolso zerou a tentativa
    paga = _tentada(_marca(_paga_e_saca(_afiliado())[0]), formas, monkeypatch)
    esperado = [
        (disputa, "Disputa aberta: a conferência fecha sozinha quando a disputa terminar."),
        (sem_tentativa, "Aguarde a conferência"),
        (desconhecido, "Motivo sem regra de decisão: confira na Stripe."),
        (revisao, "Em revisão de reembolso: decida a pendência antes."),
        (paga, "Esta ação não vale para esta comissão."),
    ]
    for com, texto in esperado:
        visto = _linha(adm, com)
        consultas = formas.consultas
        r = _liberar(adm, com, visto)
        assert r.status_code == 409 and r.json()["detail"].startswith(texto), (com.id, r.text)
        assert formas.consultas == consultas
        assert _eventos(com) == [] and _db(com)["payment_check_pending"] is True


def test_stripe_403_nao_se_libera_nem_se_conclui(adm, formas, monkeypatch):
    """403 = a chave não tem permissão, não é fato da fatura: Liberar e Concluir recusam; o 404 (controle) libera."""
    c = _afiliado()
    pend = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    paga = _tentada(_marca(_paga_e_saca(_afiliado())[0]), formas, monkeypatch)
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_reason = 'stripe_403' where id in (%s, %s)",
                     (pend.id, paga.id))
    for r in (_liberar(adm, pend), _decide(adm, paga, "conclude")):
        assert r.status_code == 409 and "chave da Stripe" in r.json()["detail"], r.text
    assert _eventos(pend) == [] and _db(pend)["payment_check_pending"] is True
    assert _db(paga)["payment_check_pending"] is True
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_reason = 'stripe_404' where id = %s", (pend.id,))
    formas.falha = _erro()
    r = _liberar(adm, pend)
    assert r.status_code == 200, r.text


def test_liberar_estornada_recusa(adm, formas, monkeypatch):
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    visto = _linha(adm, com)
    assert _decide(adm, com, "reverse", visto).status_code == 200
    assert _liberar(adm, com, visto).status_code == 409
    assert [e["action"] for e in _eventos(com)] == ["check_reversed"]


def test_liberar_no_saque_aberto_recusa_com_o_texto_do_estornar(adm, formas, monkeypatch):
    c = _afiliado()
    com = _comissao(c, 100)
    saque = _saque_com_pix(c)
    _tentada(_marca(com), formas, monkeypatch)
    antes = _fotografia(c, com)
    r = _liberar(adm, com)
    assert r.status_code == 409 and r.json()["detail"].startswith(f"A comissão está no saque {saque}, ainda aberto."), r.text
    assert _fotografia(c, com) == antes and _eventos(com) == []


def test_liberar_versao_velha_e_dois_admins(adm, formas, monkeypatch):
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    visto = _linha(adm, com)
    _marca(com)  # o webhook remarcou depois da lista
    r = _liberar(adm, com, visto)
    assert r.status_code == 409 and r.json()["detail"] == "A comissão mudou: recarregue antes de decidir.", r.text
    assert _eventos(com) == []
    _tentada(com, formas, monkeypatch)
    visto = _linha(adm, com)
    outro = _admin(monkeypatch)
    codigos = _em_paralelo(lambda: _liberar(adm, com, visto), lambda: _liberar(outro, com, visto))
    assert sorted(codigos) == [200, 409]
    assert [e["action"] for e in _eventos(com)] == ["check_released"]
    assert get_affiliate_stats(c.aff["id"])["available_cents"] == 10_000


def test_liberar_com_a_stripe_fora_e_confirmando_na_hora(adm, formas, monkeypatch):
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    antes = _db(com)
    formas.falha = stripe.APIConnectionError("fora")
    r = _liberar(adm, com)
    assert r.status_code == 503 and r.json()["detail"] == admin_dashboard._STRIPE_FORA
    assert _db(com) == antes and _eventos(com) == []
    formas.falha = None
    formas.cobra(com.fatura)  # a Stripe confirma agora: a regra normal decide
    r = _liberar(adm, com)
    assert r.status_code == 409 and r.json()["detail"].startswith("A Stripe respondeu agora"), r.text
    assert _db(com)["payment_check_pending"] is False and _eventos(com) == []


def test_admin_so_e_sem_pii(adm, formas, monkeypatch):
    c = _afiliado()
    com = _tentada(_marca(_comissao(c, 100)), formas, monkeypatch)
    anonimo = TestClient(dashboard.app, base_url="https://testserver")
    r1 = anonimo.post(f"/admin/api/affiliates/commissions/{com.id}/check-release", headers=csrf(anonimo), json={})
    r2 = anonimo.get(f"/admin/api/affiliates/commissions/{com.id}/stripe-invoice", follow_redirects=False)
    assert r1.status_code in (401, 403) and r2.status_code in (401, 403)
    assert com.fatura not in adm.get(OVERVIEW).text
    assert _liberar(adm, com).status_code == 200
    assert com.fatura not in json.dumps(_eventos(com), default=str)


def test_ver_fatura_302_chave_de_teste_e_viva_e_404(adm, formas, monkeypatch):
    c = _afiliado()
    com = _marca(_comissao(c, 100))
    url = f"/admin/api/affiliates/commissions/{com.id}/stripe-invoice"
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    r = adm.get(url, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"https://dashboard.stripe.com/test/invoices/{com.fatura}"
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "rk_test_xxx")  # chave restrita de teste também
    assert "/test/invoices/" in adm.get(url, follow_redirects=False).headers["location"]
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "sk_live_xxx")
    assert adm.get(url, follow_redirects=False).headers["location"] == f"https://dashboard.stripe.com/invoices/{com.fatura}"
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set stripe_invoice_id = %s where id = %s",
                     (f"pix:{uuid.uuid4().hex[:8]}", com.id))
    assert adm.get(url, follow_redirects=False).status_code == 404
    assert adm.get("/admin/api/affiliates/commissions/999999999/stripe-invoice", follow_redirects=False).status_code == 404


def test_ver_fatura_quota_o_id(adm, formas):
    """O id da fatura vai quotado: `/?#` não troca o host nem o caminho do redirecionamento."""
    com = _marca(_comissao(_afiliado(), 100))
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set stripe_invoice_id = %s where id = %s", ("in_a/b?c#d", com.id))
    loc = adm.get(f"/admin/api/affiliates/commissions/{com.id}/stripe-invoice", follow_redirects=False).headers["location"]
    assert loc.startswith("https://dashboard.stripe.com/") and loc.endswith("/invoices/in_a%2Fb%3Fc%23d"), loc


def test_lista_so_oferece_ver_fatura_da_stripe(adm, formas):
    c = _afiliado()
    stripe_, pix = _marca(_comissao(c, 100)), _marca(_comissao(c, 100))
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set stripe_invoice_id = 'pix:1' where id = %s", (pix.id,))
    assert (_linha(adm, stripe_)["has_stripe_invoice"], _linha(adm, pix)["has_stripe_invoice"]) == (True, False)
