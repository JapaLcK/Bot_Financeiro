"""Reconquista de trial vencido sem pagamento: agendamento pelo webhook real, tick, e-mail.
Postgres real; só o Stripe e o envio são falsos.
Controle negativo: voltar a condição do webhook para `_te - 3600 <= _fim <= _te + 3600` deixa
`test_webhook_agenda_encerrada_depois_do_trial` vermelho (ver relato do PR)."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from stripe.params._promotion_code_create_params import PromotionCodeCreateParams  # antes do fake em sys.modules

import db
from _billing_grants_helpers import conta as _conta, set_customer
from conftest import _cleanup_user
from core.services import email_service, reconquista
from db.connection import get_conn
from db.reconquista import agendar
from test_billing_webhook_lifecycle import _post, _setup

AGORA = datetime.now(timezone.utc)
DESDE = AGORA - timedelta(days=30)
TE = int((AGORA - timedelta(days=2)).timestamp())   # trial acabou há 2 dias


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        r = cur.fetchall() if cur.description else None
        conn.commit()
    return r


def _linha(uid):
    r = _sql("select * from reconquista_trial where user_id=%s", (uid,))
    return r[0] if r else None


def _evt(uid, trial_end, ended_at, comment=None):
    obj = {"id": f"sub_{uid}", "metadata": {"finbot_user_id": str(uid)},
           "trial_end": trial_end, "ended_at": ended_at}
    if comment:
        obj["cancellation_details"] = {"comment": comment}
    return {"type": "customer.subscription.deleted", "id": f"evt_{uuid.uuid4().hex}",
            "created": TE, "data": {"object": obj}}


@pytest.fixture()
def uids():
    lista = []
    yield lista
    for u in lista:
        _cleanup_user(u)


@pytest.fixture()
def ligada(monkeypatch):
    monkeypatch.setenv("RECONQUISTA_DESDE", DESDE.isoformat())


def _webhook(monkeypatch, uids, evt_fn):
    uid, client, fake = _setup(monkeypatch, uuid.uuid4().hex[:10])
    uids.append(uid)
    r = _post(client, fake, evt_fn(uid))
    assert r.status_code == 200, r.text
    return uid


# ── webhook: quem entra na fila ──────────────────────────────────────────────

def test_webhook_trial_sem_pagamento_agenda_uma_vez(monkeypatch, uids, ligada):
    uid, client, fake = _setup(monkeypatch, uuid.uuid4().hex[:10])
    uids.append(uid)
    for _ in range(2):   # entrega + reentrega
        assert _post(client, fake, _evt(uid, TE, TE)).status_code == 200
    rows = _sql("select * from reconquista_trial where user_id=%s", (uid,))
    assert len(rows) == 1 and rows[0]["status"] == "pendente"
    assert rows[0]["enviar_em"] - rows[0]["trial_end"] == timedelta(days=2)


@pytest.mark.parametrize("quando,evt", [
    ("marca_exclusao", lambda u: _evt(u, TE, TE, "exclusao_de_conta")),
    ("antes_do_desde", lambda u: _evt(u, int((DESDE - timedelta(days=1)).timestamp()),
                                      int((DESDE - timedelta(days=1)).timestamp()))),
    ("sem_trial", lambda u: _evt(u, None, TE)),
    ("cancelou_no_meio", lambda u: _evt(u, int((AGORA + timedelta(days=20)).timestamp()),
                                        int(AGORA.timestamp()))),
])
def test_webhook_nao_agenda(monkeypatch, uids, ligada, quando, evt):
    if quando == "marca_exclusao":
        from core.services.stripe_exclusao import MARCA_EXCLUSAO
        evt = lambda u: _evt(u, TE, TE, MARCA_EXCLUSAO)   # noqa: E731
    uid = _webhook(monkeypatch, uids, evt)
    assert _linha(uid) is None, quando


def test_webhook_agenda_encerrada_depois_do_trial(monkeypatch, uids, ligada):
    """Cartão recusado no fim do trial: past_due e `deleted` dias depois (fora de trial_end +-1h)."""
    uid = _webhook(monkeypatch, uids, lambda u: _evt(u, TE, TE + 5 * 86400))
    assert _linha(uid)["status"] == "pendente"


@pytest.mark.parametrize("valor", [None, "", "lixo", "2026-01-01T00:00:00"])   # ausente, vazia, inválida, sem fuso
def test_webhook_desligado_nao_agenda(monkeypatch, uids, valor):
    if valor is None:
        monkeypatch.delenv("RECONQUISTA_DESDE", raising=False)
    else:
        monkeypatch.setenv("RECONQUISTA_DESDE", valor)
    uid = _webhook(monkeypatch, uids, lambda u: _evt(u, TE, TE))
    assert _linha(uid) is None


# ── tick ─────────────────────────────────────────────────────────────────────

class _Err(Exception):
    pass


class _InvalidReq(_Err):
    code = "resource_missing"


class FakeStripe:
    def __init__(self, faturas=(), falha=False):
        self.promos, self.coupons, self.faturas, self.falha = [], {}, list(faturas), falha
        outer = self
        self.error = SimpleNamespace(StripeError=_Err, InvalidRequestError=_InvalidReq)

        class Price:
            retrieve = staticmethod(lambda pid: {"product": "prod_" + pid})

        class Coupon:
            @staticmethod
            def retrieve(cid, expand=None):
                if cid not in outer.coupons:
                    raise _InvalidReq(cid)
                return outer.coupons[cid]

            @staticmethod
            def create(id, name, percent_off, duration, duration_in_months, applies_to, expand=None):
                outer.coupons[id] = {"id": id, "valid": True, "percent_off": percent_off,
                                     "duration": duration, "duration_in_months": duration_in_months,
                                     "applies_to": applies_to}
                return outer.coupons[id]

        class PromotionCode:
            @staticmethod
            def create(**kw):
                # Valida contra o TypedDict REAL do SDK (stripe 15.x): chave fora dele ou
                # obrigatória ausente = o Stripe recusaria.
                P = PromotionCodeCreateParams
                assert set(kw) <= set(P.__annotations__), set(kw) - set(P.__annotations__)
                assert P.__required_keys__ <= set(kw), P.__required_keys__ - set(kw)
                if outer.falha:
                    raise _Err("stripe fora")
                outer.promos.append(kw)
                return {"code": "VOLTA10XYZ"}

        class Invoice:
            @staticmethod
            def list(**kw):
                return {"data": outer.faturas}

        self.Price, self.Coupon, self.PromotionCode, self.Invoice = Price, Coupon, PromotionCode, Invoice


@pytest.fixture()
def mundo(monkeypatch, uids, ligada):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO_MENSAL", "price_plus")
    monkeypatch.setenv("STRIPE_PRICE_ID_PROMAX_MENSAL", "price_pro")
    emails = []   # só os da reconquista: o cadastro de teste também manda e-mail de boas-vindas
    monkeypatch.setattr(email_service, "send_email",
                        lambda to, subject, html_body, **kw: subject.startswith("Seu Piggy") and emails.append(
                            {"to": to, "subject": subject, "html": html_body, **kw}) or True)

    def novo(stripe=None, vencida=True, **cols):
        stripe = stripe or FakeStripe()
        monkeypatch.setitem(sys.modules, "stripe", stripe)
        u = db.register_auth_user(f"rc-{uuid.uuid4().hex[:10]}@t.com", "senha-forte-123")
        uid = int(u["user_id"])
        uids.append(uid)
        set_customer(uid, f"cus_{uid}")
        for col, val in cols.items():
            _sql(f"update auth_accounts set {col}=%s where user_id=%s", (val, uid))
        from db_support import invalidate_auth_user_cache
        invalidate_auth_user_cache(uid)
        agendar(uid, AGORA - timedelta(days=2) + (timedelta(0) if vencida else timedelta(days=3)))
        return uid, stripe

    return SimpleNamespace(novo=novo, emails=emails)


def test_tick_nao_envia_antes_de_enviar_em(mundo):
    uid, st = mundo.novo(vencida=False)
    reconquista.rodar_uma_vez()
    assert _linha(uid)["status"] == "pendente" and not mundo.emails and not st.promos


def test_tick_envia_uma_vez_com_promo_exclusivo(mundo):
    uid, st = mundo.novo(stripe=FakeStripe(faturas=[{"amount_paid": 0}]))   # trial: fatura de R$ 0
    reconquista.rodar_uma_vez()
    reconquista.rodar_uma_vez()   # 2º tick não reenvia
    assert len(mundo.emails) == 1 and len(st.promos) == 1
    p = st.promos[0]
    assert p["customer"] == f"cus_{uid}" and p["max_redemptions"] == 1
    assert p["promotion"] == {"type": "coupon", "coupon": "rm_e9_10_prod_price_plus_prod_price_pro"}   # Plus e Pro
    assert abs(p["expires_at"] - (datetime.now(timezone.utc) + timedelta(days=7)).timestamp()) < 60
    assert "VOLTA10XYZ" in mundo.emails[0]["html"]
    assert mundo.emails[0]["headers"]["List-Unsubscribe"]
    assert _linha(uid)["status"] == "enviado" and _linha(uid)["enviado_em"]


@pytest.mark.parametrize("motivo,kw,faturas", [
    ("ja_pagou", {}, [{"amount_paid": 0}, {"amount_paid": 1990}]),
    ("opt_out", {"engagement_opt_out": True}, []),
    ("assinou", {"plan": "pro", "plan_expires_at": AGORA + timedelta(days=20)}, []),
])
def test_tick_pula_sem_enviar(mundo, motivo, kw, faturas):
    uid, st = mundo.novo(stripe=FakeStripe(faturas=faturas), **kw)
    reconquista.rodar_uma_vez()
    assert _linha(uid)["status"] == "pulado" and _linha(uid)["motivo"] == motivo
    assert not mundo.emails and not st.promos


def test_tick_encerrada_depois_com_fatura_paga_agenda_mas_pula(monkeypatch, uids, ligada, mundo):
    """Assinante antigo (pagou meses, cancelou anos depois): o webhook agenda, o tick pula `ja_pagou`."""
    uid = _webhook(monkeypatch, uids, lambda u: _evt(u, TE, TE + 400 * 86400))
    assert _linha(uid)["status"] == "pendente"
    monkeypatch.setitem(sys.modules, "stripe", FakeStripe(faturas=[{"amount_paid": 0}, {"amount_paid": 1990}]))
    set_customer(uid, f"cus_{uid}")
    _sql("update reconquista_trial set enviar_em = now() - interval '1 hour' where user_id=%s", (uid,))
    reconquista.rodar_uma_vez()
    assert _linha(uid)["status"] == "pulado" and _linha(uid)["motivo"] == "ja_pagou"
    assert not mundo.emails


def test_tick_falha_do_stripe_pula_sem_email_e_nao_volta(mundo):
    uid, st = mundo.novo(stripe=FakeStripe(falha=True))
    reconquista.rodar_uma_vez()
    reconquista.rodar_uma_vez()
    assert _linha(uid)["status"] == "pulado" and _linha(uid)["motivo"] == "erro:_Err"
    assert not mundo.emails


def test_tick_uma_linha_por_vez_morte_no_meio_nao_prende_as_outras(mundo, monkeypatch):
    st = FakeStripe(faturas=[{"amount_paid": 0}])
    ids = [mundo.novo(stripe=st)[0] for _ in range(3)]
    def morre(*a, **k):
        raise SystemExit   # deploy matando o processo (BaseException: o except do tick não pega)
    monkeypatch.setattr(reconquista, "_processar", morre)
    with pytest.raises(SystemExit):
        reconquista.rodar_uma_vez()
    assert sorted(_linha(u)["status"] for u in ids) == ["enviando", "pendente", "pendente"]


def test_tick_isolamento_entre_usuarios(mundo):
    a, st = mundo.novo(stripe=FakeStripe(faturas=[{"amount_paid": 0}]), engagement_opt_out=True)
    b, _ = mundo.novo(stripe=st)
    reconquista.rodar_uma_vez()
    assert _linha(a)["status"] == "pulado" and _linha(b)["status"] == "enviado"
    assert [p["customer"] for p in st.promos] == [f"cus_{b}"] and len(mundo.emails) == 1


# ── e-mail ───────────────────────────────────────────────────────────────────

TEXTO = """Oi, Ana!

Seu teste do PigBank acabou, mas seus gastos, categorias e metas continuam aqui, do jeito que você deixou.

Nesses dias, o Piggy começou a entender para onde vai o seu dinheiro. Parar agora é perder isso de vista justo quando começou a fazer sentido.

Com o Plus, por R$ 19,90 por mês, você:
• sabe quanto vai sobrar antes de o mês acabar
• registra tudo pelo WhatsApp, sem planilha
• recebe um alerta antes de estourar o orçamento

Continuar com o Piggy: {link}

E como você já começou, o primeiro mês sai com 10% de desconto. Use o código ABC123 até 20/11.

Abraço,
Equipe PigBank"""


def test_email_texto_literal_e_escape(monkeypatch):
    vistos = []
    monkeypatch.setattr(email_service, "send_email", lambda **kw: vistos.append(kw) or True)
    ate = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    email_service.send_reconquista_email("a@b.com", "Ana", "ABC123", ate, "https://x/u")
    v = vistos[0]
    assert v["subject"] == "Seu Piggy ainda está guardando tudo"
    assert v["text_body"] == TEXTO.format(link=f"{email_service._public_base_url()}/precos")
    assert v["log_recipient"] is False and "List-Unsubscribe" in v["headers"]
    assert "Continuar com o Piggy" in v["html_body"] and "<b>ABC123</b> até 20/11" in v["html_body"]

    email_service.send_reconquista_email("a@b.com", "<script>x</script>", "A<b>", ate, "https://x/u")
    h = vistos[1]["html_body"]
    assert "<script>x</script>" not in h and "&lt;script&gt;" in h and "A<b>" not in h

    email_service.send_reconquista_email("a@b.com", "", "ABC123", ate, "https://x/u")
    assert vistos[2]["text_body"].startswith("Oi!\n\n")
