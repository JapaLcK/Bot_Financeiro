"""E-mail pessoal do fundador: 3h depois da PRIMEIRA assinatura, uma vez por conta.

Dois caminhos (checkout do Stripe e efeito `email` do dreno Pix), uma regra só
(`email_service.send_founder_email_once`), duas travas:
  (a) `e_primeira_assinatura` — nenhum grant além deste, fora cortesia do admin;
  (b) marca `founder_email_sent` — reentrega do mesmo evento.
E ele nunca pode derrubar a materialização do pagamento.

Os caminhos são exercitados pela conversa (webhook de verdade e dreno de
verdade, banco real); só o remetente de baixo (`send_founder_email`) é espião.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from _billing_grants_helpers import (
    conta, evt_checkout, garantir_system_event_logs, grant, rodar_resync,
    sub_stripe,
)
from _dreno_pix_helpers import efeitos, entregar, mundo_externo, nova_cobranca
from core.services import email_service as es
from db.connection import get_conn
from test_billing_webhook_lifecycle import _post, _setup

# O `conftest` troca o `send_email` por um stub em todo teste; o real fica
# guardado no import, como em `test_account_deletion_pii_logs.py`.
_SEND_EMAIL_REAL = es.send_email
BANCO = "/login?next=%2Fsettings%3Fview%3Dopen-finance"


# ── o texto ──────────────────────────────────────────────────────────────────

@pytest.fixture
def caixa(monkeypatch):
    enviado = {}

    def _fake(to, subject, html_body, text_body=None, **kw):
        enviado.update(to=to, subject=subject, html=html_body, text=text_body or "", **kw)
        return True

    monkeypatch.setattr(es, "send_email", _fake)
    return enviado


def test_assunto_e_remetente(caixa):
    assert es.send_founder_email("a@b.com")
    assert caixa["subject"] == "oi, aqui é o Lucas do PigBank 🐷"
    assert "lucas@pigbankai.com" in caixa["from_addr"]


def test_agendado_para_tres_horas_depois(caixa):
    es.send_founder_email("a@b.com")
    quando = datetime.fromisoformat(caixa["scheduled_at"])
    alvo = datetime.now(timezone.utc) + timedelta(hours=3)
    assert abs((quando - alvo).total_seconds()) < 60, caixa["scheduled_at"]


def test_links_e_ordem(caixa):
    es.send_founder_email("a@b.com")
    base = es._public_base_url()
    wpp = es._whatsapp_link("Oi, Piggy!")
    for parte in (caixa["html"], caixa["text"]):
        assert base + BANCO in parte
        assert base + "/app" in parte
        assert wpp in parte
    assert parse_qs(urlparse(wpp).query)["text"] == ["Oi, Piggy!"]
    assert caixa["html"].index(base + BANCO) < caixa["html"].index(wpp)


def test_sem_ps_nem_instagram(caixa):
    es.send_founder_email("a@b.com")
    for parte in (caixa["html"], caixa["text"]):
        assert "P.S." not in parte
        assert "instagram" not in parte.lower()


def test_rodape_nao_manda_para_o_bot(caixa):
    """O corpo diz "é só responder este e-mail"; o rodapé padrão diz "use o
    comando ajuda no bot". O do fundador não pode trazer o segundo."""
    es.send_founder_email("a@b.com")
    for parte in (caixa["html"], caixa["text"]):
        assert "comando" not in parte and "ajuda no bot" not in parte
    assert "porque assinou o PigBank" in caixa["html"]
    # controle positivo: os outros e-mails seguem com o rodapé padrão
    es.send_pro_welcome_email("a@b.com", "pro", None)
    assert "Use o comando <strong>ajuda</strong> no bot" in caixa["html"]


def test_send_email_repassa_scheduled_at_so_quando_pedido(monkeypatch):
    params = []

    class _Resend:
        class Emails:
            @staticmethod
            def send(p):
                params.append(p)

    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setattr(es, "send_email", _SEND_EMAIL_REAL)
    monkeypatch.setattr(es, "_get_resend", lambda: _Resend)
    assert es.send_email("a@b.com", "s", "<p>x</p>", scheduled_at="2030-01-01T00:00:00+00:00")
    assert es.send_email("a@b.com", "s", "<p>x</p>")
    assert params[0]["scheduled_at"] == "2030-01-01T00:00:00+00:00"
    assert "scheduled_at" not in params[1]


# ── Stripe, pela conversa ────────────────────────────────────────────────────

@pytest.fixture
def espiao(monkeypatch):
    """`ok` é o retorno (o Resend devolve False sem levantar); `erro` levanta."""
    estado = {"chamadas": [], "ok": True, "erro": None}

    def _fn(to):
        estado["chamadas"].append(to)
        if estado["erro"]:
            raise estado["erro"]
        return estado["ok"]

    monkeypatch.setattr(es, "send_founder_email", _fn)
    garantir_system_event_logs()
    return estado


def _checkout(client, fake, uid, sub_id, created):
    r = _post(client, fake, evt_checkout(uid, sub_id, created, f"cs_{sub_id}"),
              subs={sub_id: sub_stripe("active", "price_x", 30)})
    assert r.status_code == 200, r.text


def test_primeiro_checkout_manda_um(user_id, monkeypatch, espiao):
    uid, client, fake = _setup(monkeypatch, f"fund1-{user_id}")
    _checkout(client, fake, uid, "sub_f1", 1_800_000_000)
    assert espiao["chamadas"] == [f"wh-fund1-{user_id}@t.com"]


def test_reentrega_do_mesmo_checkout_nao_repete(user_id, monkeypatch, espiao):
    uid, client, fake = _setup(monkeypatch, f"fund2-{user_id}")
    _checkout(client, fake, uid, "sub_f2", 1_800_000_000)
    _checkout(client, fake, uid, "sub_f2", 1_800_000_000)
    assert len(espiao["chamadas"]) == 1


def test_quem_ja_tinha_legacy_nao_recebe(user_id, monkeypatch, espiao):
    uid, client, fake = _setup(monkeypatch, f"fund3-{user_id}")
    conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=20))
    rodar_resync()
    assert grant(uid, "legacy")
    _checkout(client, fake, uid, "sub_f3", 1_800_000_000)
    assert espiao["chamadas"] == []


def test_quem_cancelou_e_volta_nao_recebe(user_id, monkeypatch, espiao):
    uid, client, fake = _setup(monkeypatch, f"fund4-{user_id}")
    espiao["erro"] = RuntimeError("fora")   # 1ª assinatura sem e-mail: sem marca
    _checkout(client, fake, uid, "sub_velha", 1_800_000_000)
    espiao["erro"] = None
    _checkout(client, fake, uid, "sub_nova", 1_800_000_100)
    assert len(espiao["chamadas"]) == 1   # só a tentativa da 1ª, que levantou


def test_cortesia_do_admin_nao_conta(user_id, monkeypatch, espiao):
    from core.admin_dashboard import _gravar_grant_do_admin
    uid, client, fake = _setup(monkeypatch, f"fund5-{user_id}")
    with get_conn() as conn, conn.cursor() as cur:
        _gravar_grant_do_admin(cur, uid, "pro", 1)
        conn.commit()
    assert grant(uid, "admin")
    _checkout(client, fake, uid, "sub_f5", 1_800_000_000)
    assert len(espiao["chamadas"]) == 1


@pytest.mark.parametrize("onde", ["remetente", "trava"])
def test_falha_no_fundador_nao_derruba_o_webhook(user_id, monkeypatch, espiao, onde):
    import db.plan_grants as pg
    uid, client, fake = _setup(monkeypatch, f"fund6{onde}-{user_id}")
    if onde == "remetente":
        espiao["erro"] = RuntimeError("resend explodiu")
    else:
        def _explode(*a):
            raise RuntimeError("banco fora")
        monkeypatch.setattr(pg, "e_primeira_assinatura", _explode)
    _checkout(client, fake, uid, "sub_f6", 1_800_000_000)
    assert grant(uid, "stripe")["external_ref"] == "sub_f6"


def test_resend_recusou_nao_marca_e_reentrega_tenta(user_id, monkeypatch, espiao):
    uid, client, fake = _setup(monkeypatch, f"fund7-{user_id}")
    espiao["ok"] = False
    _checkout(client, fake, uid, "sub_f7", 1_800_000_000)
    espiao["ok"] = True
    _checkout(client, fake, uid, "sub_f7", 1_800_000_000)
    _checkout(client, fake, uid, "sub_f7", 1_800_000_000)
    assert len(espiao["chamadas"]) == 2


# ── Pix, pela conversa ───────────────────────────────────────────────────────

@pytest.fixture
def externo(monkeypatch):
    garantir_system_event_logs()
    return mundo_externo(monkeypatch)


def test_primeira_compra_pix_manda_um(user_id, externo):
    conta(user_id, "free", None)
    entregar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    assert externo["fundador"] == 1


def test_renovacao_pix_nao_repete(user_id, externo):
    conta(user_id, "free", None)
    entregar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    entregar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    assert externo["fundador"] == 1


def test_renovacao_pix_sem_marca_tambem_nao_manda(user_id, externo):
    """Só a trava (a): a 1ª compra não deixou marca (o fundador levantou)."""
    conta(user_id, "free", None)
    externo["fundador_erro"] = RuntimeError("fora")
    entregar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    externo["fundador_erro"] = None
    entregar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    assert externo["fundador"] == 0


def test_confirmacao_ja_enviada_nao_pula_o_fundador(user_id, externo):
    """Queda entre registrar a confirmação e registrar o efeito: a retentativa
    acha `pix_paid_email_sent` e não reenvia a confirmação — mas o fundador,
    que vem depois, ainda sai."""
    from core.observability import log_system_event_sync
    conta(user_id, "free", None)
    log_system_event_sync("info", "pix_paid_email_sent", "x", source="pix", user_id=user_id)
    entregar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    assert (externo["email"], externo["fundador"]) == (0, 1)


def test_reentrega_do_pagamento_pix_nao_repete(user_id, externo):
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert externo["fundador"] == 1


def test_fundador_que_levanta_nao_segura_o_pix(user_id, externo):
    conta(user_id, "free", None)
    externo["fundador_erro"] = RuntimeError("resend explodiu")
    cobranca = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert "email" in efeitos(cobranca["asaas_payment_id"])
    assert externo["email"] == 1
    assert grant(user_id, "pix")


# ── cruzado ──────────────────────────────────────────────────────────────────

def test_stripe_e_depois_pix_manda_um(user_id, monkeypatch, externo):
    uid, client, fake = _setup(monkeypatch, f"fund8-{user_id}")
    _checkout(client, fake, uid, "sub_f8", 1_800_000_000)
    entregar("PAYMENT_RECEIVED", nova_cobranca(uid))
    assert externo["fundador"] == 1


def test_stripe_sem_marca_e_depois_pix_nao_manda(user_id, monkeypatch, externo):
    """Só a trava (a), entre gateways: o grant Stripe conta para o Pix."""
    uid, client, fake = _setup(monkeypatch, f"fund9-{user_id}")
    externo["fundador_erro"] = RuntimeError("fora")
    _checkout(client, fake, uid, "sub_f9", 1_800_000_000)
    externo["fundador_erro"] = None
    entregar("PAYMENT_RECEIVED", nova_cobranca(uid))
    assert externo["fundador"] == 0
