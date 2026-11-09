"""Oferta de campanha (40% em Plus e Pro, 23 a 30/11/2026, Brasília): janela, cartão, Pix e plans-config.

Controles: negativos = desligar `pct_da_oferta` no checkout do cartão ou no Pix, voltar `<=` para `==` na
substituição do QR e voltar o comparador de `rm_desc` deixa o grupo vermelho; positivo = fora da janela tudo
como antes (`allow_promotion_codes`, 19900, 49900, 9900). Stripe e Asaas falsos; banco de verdade.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import frontend.finance_bot_websocket_custom as dashboard
from _pix_checkout_helpers import _comprar, _linhas, _marcar_paga, asaas_falso, vendavel  # noqa: F401 - fixtures
from core.services import oferta_campanha as camp
from core.services.pix_pricing import PRECOS_ANUAIS_CENTS
from frontend.routes.retomada import campanha_config
from test_billing_checkout import _CSRF_HEADERS, _FakeStripe
from test_retomada_backend import _checkout, _cliente, _conta, _pix

BRT = ZoneInfo("America/Sao_Paulo")
DENTRO = datetime(2026, 11, 25, 12, tzinfo=BRT)
ANTES = datetime(2026, 11, 22, 23, 59, 59, tzinfo=BRT)
DEPOIS = datetime(2026, 12, 1, tzinfo=BRT)


def _relogio(monkeypatch, quando):
    monkeypatch.setattr(camp, "_agora", lambda: quando)


def _cartao(monkeypatch):
    for nome, v in (("PRO_MENSAL", "price_pm"), ("PRO_ANUAL", "price_pa"), ("PROMAX_MENSAL", "price_xm"),
                    ("PROMAX_ANUAL", "price_xa"), ("ESSENCIAL_MENSAL", "price_em"), ("ESSENCIAL_ANUAL", "price_ea")):
        monkeypatch.setattr(dashboard, f"STRIPE_PRICE_ID_{nome}", v)
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    fake = _FakeStripe()
    import sys
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return fake


# ── a janela e o maior-entre ────────────────────────────────────────────────

def test_janela_em_brasilia_fim_exclusivo():
    z = lambda *a: datetime(*a, tzinfo=timezone.utc)   # noqa: E731
    assert camp.campanha_pct(z(2026, 11, 23, 2, 59, 59)) is None
    assert camp.campanha_pct(z(2026, 11, 23, 3, 0, 0)) == 40
    assert camp.campanha_pct(datetime(2026, 11, 30, 23, 59, 59, tzinfo=BRT)) == 40
    assert camp.campanha_pct(datetime(2026, 12, 1, 0, 0, 0, tzinfo=BRT)) is None


def test_maior_entre_link_e_campanha_so_em_plus_e_pro():
    assert camp.pct_da_oferta("pro", 20, DENTRO) == 40 and camp.pct_da_oferta("pro_max", None, DENTRO) == 40
    assert camp.pct_da_oferta("pro", 45, DENTRO) == 45          # nunca a soma
    assert camp.pct_da_oferta("essencial", None, DENTRO) is None
    assert camp.pct_da_oferta("essencial", 20, DENTRO) == 20
    assert camp.pct_da_oferta("plus", None, DENTRO) is None     # vocabulário público não casa
    assert camp.pct_da_oferta("pro", 20, DEPOIS) == 20 and camp.pct_da_oferta("pro", None, ANTES) is None


def test_campanha_config(monkeypatch):
    assert campanha_config() is None
    _relogio(monkeypatch, DENTRO)
    c = campanha_config()
    assert (c["precos"]["plus"]["annual"], c["precos"]["pro"]["annual"]) == (11940, 29940)
    assert (c["precos"]["plus"]["monthly"], c["precos"]["pro"]["monthly"]) == (1194, 2994)
    assert set(c["textos"]) == {"plus", "pro"} and set(c["precos"]) == {"plus", "pro"}   # Essencial fora
    assert "30 de novembro" in c["frase"] and "30 de novembro" in c["frase_assinante"]
    assert c["textos"]["plus"]["linha_anual"] == "R$ 119,40 no 1º ano, depois R$ 199 por ano"
    assert c["textos"]["pro"]["linha_mensal"] == "1º mês por R$ 29,94, depois R$ 49,90 por mês"
    assert c["textos"]["plus"]["anual_mes"] == "R$ 9,95" and c["trial"] is False
    assert all("—" not in t for x in c["textos"].values() for t in x.values())
    _relogio(monkeypatch, DEPOIS)
    assert campanha_config() is None


def test_plans_config_leva_a_campanha_so_na_janela(monkeypatch):
    assert _cliente().get("/billing/plans-config").json()["campanha"] is None
    _relogio(monkeypatch, DENTRO)
    assert _cliente().get("/billing/plans-config").json()["campanha"]["desconto_pct"] == 40


# ── cartão ──────────────────────────────────────────────────────────────────

def test_cupom_da_campanha_nasce_com_o_nome_do_percentual(monkeypatch):
    a, _ = _conta("c-nome")
    fake, _x = _cartao(monkeypatch), _relogio(monkeypatch, DENTRO)
    nomes, orig = [], fake.Coupon.create
    monkeypatch.setattr(fake.Coupon, "create", lambda **kw: (nomes.append(kw["name"]), orig(**kw))[1])
    assert _checkout(_cliente(auth=a), plan="plus", interval="monthly").status_code == 200
    assert nomes == ["40% OFF"]


@pytest.mark.parametrize("plan", ["plus", "pro"])
@pytest.mark.parametrize("interval", ["monthly", "annual"])
def test_cartao_plus_e_pro_levam_o_cupom_da_campanha(monkeypatch, plan, interval):
    a, _ = _conta(f"c-{plan}-{interval}")
    fake, _x = _cartao(monkeypatch), _relogio(monkeypatch, DENTRO)
    import db.plans as plans
    monkeypatch.setattr(plans, "is_trial_eligible_for_user", lambda uid: True)
    assert _checkout(_cliente(auth=a), plan=plan, interval=interval).status_code == 200
    k = fake.last_session_kwargs
    assert k["discounts"] == [{"coupon": "rm_e0_40_prod_x"}] and "allow_promotion_codes" not in k
    assert k["metadata"]["rm_desc"] == "40" and "rm_etapa" not in k["metadata"]
    if interval == "annual":   # o trial de 7 dias segue; o desconto cai na 1ª cobrança real
        assert k["subscription_data"]["trial_period_days"] == 7 and k["metadata"]["td"] == "7"
    assert fake.coupons["rm_e0_40_prod_x"]["duration"] == "repeating"


def test_cartao_essencial_fica_sem_cupom(monkeypatch):
    a, _ = _conta("c-ess")
    fake, _x = _cartao(monkeypatch), _relogio(monkeypatch, DENTRO)
    for interval in ("monthly", "annual"):
        assert _checkout(_cliente(auth=a), plan="essencial", interval=interval).status_code == 200
        assert fake.last_session_kwargs["allow_promotion_codes"] is True
        assert "discounts" not in fake.last_session_kwargs
    assert fake.coupon_create_calls == 0


def test_cartao_fora_da_janela_tudo_como_antes(monkeypatch):
    a, _ = _conta("c-fora")
    fake = _cartao(monkeypatch)
    for quando in (ANTES, DEPOIS):
        _relogio(monkeypatch, quando)
        assert _checkout(_cliente(auth=a), plan="pro", interval="annual").status_code == 200
        k = fake.last_session_kwargs
        assert k["allow_promotion_codes"] is True and "discounts" not in k and "rm_desc" not in k["metadata"]


def test_sessao_cheia_de_antes_da_janela_expira_e_nasce_uma_com_cupom(monkeypatch):
    a, _ = _conta("c-antes")
    fake = _cartao(monkeypatch)
    _relogio(monkeypatch, ANTES)
    velha = _checkout(_cliente(auth=a), plan="plus").json()["checkout_url"]
    _relogio(monkeypatch, DENTRO)
    nova = _checkout(_cliente(auth=a), plan="plus").json()["checkout_url"]
    assert nova != velha and fake.session_expire_calls == 1
    assert fake.last_session_kwargs["discounts"] == [{"coupon": "rm_e0_40_prod_x"}]
    assert _checkout(_cliente(auth=a), plan="plus").json()["checkout_url"] == nova   # igual reaproveita


def test_sessao_de_retomada_de_20_de_antes_da_janela_nao_e_reusada_dentro_dela(monkeypatch):
    a, _ = _conta("c-antes20")
    fake = _cartao(monkeypatch)
    _relogio(monkeypatch, ANTES)
    velha = _checkout(_cliente(a, 3), plan="plus").json()["checkout_url"]
    assert fake.last_session_kwargs["metadata"]["rm_desc"] == "20" and fake.last_session_kwargs["metadata"]["rm_etapa"] == "3"
    _relogio(monkeypatch, DENTRO)
    nova = _checkout(_cliente(a, 3), plan="plus").json()["checkout_url"]
    assert nova != velha and fake.session_expire_calls == 1
    assert fake.last_session_kwargs["metadata"]["rm_desc"] == "40"
    assert fake.last_session_kwargs["discounts"] == [{"coupon": "rm_e3_40_prod_x"}]


def test_sessao_de_40_depois_do_corte_nao_e_reusada(monkeypatch):
    a, _ = _conta("c-corte")
    fake = _cartao(monkeypatch)
    _relogio(monkeypatch, DENTRO)
    velha = _checkout(_cliente(auth=a), plan="pro", interval="annual").json()["checkout_url"]
    _relogio(monkeypatch, DEPOIS)
    nova = _checkout(_cliente(auth=a), plan="pro", interval="annual").json()["checkout_url"]
    assert nova != velha and fake.session_expire_calls == 1
    assert fake.last_session_kwargs["allow_promotion_codes"] is True


def test_link_na_janela_vale_o_maior_e_assinante_ativo_leva_409(monkeypatch):
    a, _ = _conta("c-link")
    fake, _x = _cartao(monkeypatch), _relogio(monkeypatch, DENTRO)
    assert _checkout(_cliente(a, 3), plan="plus").status_code == 200
    k = fake.last_session_kwargs
    assert k["metadata"]["rm_desc"] == "40" and k["metadata"]["rm_etapa"] == "3"   # 20 do link < 40
    assert _checkout(_cliente(a, 3), plan="essencial").status_code == 200
    assert fake.last_session_kwargs["metadata"]["rm_desc"] == "20"                   # Essencial mantém o do link

    b, _ = _conta("c-ativo")
    import db
    db.set_stripe_customer(b, "cus_ja_assina")
    fake.Subscription = type("S", (), {"list": staticmethod(lambda **kw: {"data": [{"id": "sub", "schedule": None}]})})
    n = fake.session_create_calls
    r = _checkout(_cliente(auth=b), plan="plus")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "already_subscribed"
    assert fake.session_create_calls == n


# ── Pix ─────────────────────────────────────────────────────────────────────

def test_pix_precos_na_janela_e_cadernos_cheios(monkeypatch, vendavel, asaas_falso):
    _relogio(monkeypatch, DENTRO)
    for plano, esperado in (("pro", 11940), ("pro_max", 29940), ("essencial", 9900)):
        uid, _ = _conta(f"p-{plano}")
        assert _comprar(uid, plano)["amount_cents"] == esperado
    assert PRECOS_ANUAIS_CENTS["pro"] * 6 // 10 == 11940


def test_pix_qr_cheio_de_antes_da_janela_troca_no_clique_novo(monkeypatch, vendavel, asaas_falso):
    a, _ = _conta("p-troca")
    _relogio(monkeypatch, ANTES)
    cheio = _comprar(a, "pro")
    assert cheio["amount_cents"] == 19900
    _relogio(monkeypatch, DENTRO)
    novo = _comprar(a, "pro")
    assert novo["amount_cents"] == 11940 and novo["public_token"] != cheio["public_token"]
    assert [(l["amount_cents"], l["status"]) for l in _linhas(a)] == [(19900, "canceled"), (11940, "pending")]


def test_pix_qr_da_campanha_sobrevive_ao_corte_e_o_pagamento_concede_acesso(monkeypatch, vendavel, asaas_falso):
    a, _ = _conta("p-corte")
    _relogio(monkeypatch, DENTRO)
    qr = _comprar(a, "pro")
    _relogio(monkeypatch, DEPOIS)
    assert _comprar(a, "pro")["public_token"] == qr["public_token"]   # QR mais barato não volta a custar cheio
    _marcar_paga(a, qr["public_token"])
    from db.plan_grants import list_grants  # noqa: PLC0415
    assert [(g["source"], g["plan_stored"]) for g in list_grants(a)] == [("pix", "pro")]


def test_pix_qr_vencido_da_campanha_nasce_cheio_depois_do_corte(monkeypatch, vendavel, asaas_falso):
    a, _ = _conta("p-vencido")
    _relogio(monkeypatch, DENTRO)
    qr = _comprar(a, "pro")
    from core.services import pix_checkout
    monkeypatch.setattr(pix_checkout, "qr_vivo", lambda ativa: False)   # vencido
    _relogio(monkeypatch, DEPOIS)
    novo = _comprar(a, "pro")
    assert novo["amount_cents"] == 19900 and novo["public_token"] != qr["public_token"]


def test_pix_upgrade_com_credito_sobre_o_valor_pago(monkeypatch, vendavel, asaas_falso):
    from _billing_grants_helpers import conta
    a, _ = _conta("p-up")
    conta(a, "free", None)
    _relogio(monkeypatch, DENTRO)
    plus = _comprar(a, "pro")
    _marcar_paga(a, plus["public_token"], dias=-1)
    up = _comprar(a, "pro_max")
    linha = _linhas(a)[-1]
    assert linha["credit_cents"] > 0 and linha["amount_cents"] == 29940 - linha["credit_cents"] == up["amount_cents"]


def test_pix_rota_com_retomada_na_janela_vale_o_maior(monkeypatch, vendavel, asaas_falso):
    a, _ = _conta("p-link")
    _relogio(monkeypatch, DENTRO)
    r = _pix(_cliente(a, 3), plan="plus")
    assert r.status_code == 200 and r.json()["amount_cents"] == 11940
    assert _linhas(a)[0]["amount_cents"] == 11940
