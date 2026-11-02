"""PR 6b da retomada: desconto escalonado (E1 5%, E2 10%, E3 20%) no 1º pagamento.

Controles: negativos = desligar cada ramo novo deixa o grupo vermelho (o `discounts` no
`_new_session`, o `rm_desc` no `_checkout_session_matches`, o `ja_comprou` sob o lock, o
`desconto_pct` no `criar_checkout`, a comparação de `price_cents` na substituição do Pix);
positivos = sem retomada nada muda (`allow_promotion_codes`, 9900) e o cupom igual passa.
O Stripe e o Asaas são falsos; o banco é de verdade.
"""
import re
from pathlib import Path

import pytest

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as billing_pix
from _pix_checkout_helpers import _comprar, _linhas, asaas_falso, vendavel  # noqa: F401 - fixtures
from core.services import retomada
from core.services.retomada_cupom import CupomIndisponivel, cupom_id
from test_billing_checkout import _CSRF_HEADERS, _FakeStripe, _reset_rate_limiter  # noqa: F401
from test_pix_extras_checkout import loja  # noqa: F401 - fixture
from test_retomada_backend import _checkout, _cliente, _conta, _pix, _stripe

ETAPAS = [(1, 5), (2, 10), (3, 20)]


def _id(n, pct):
    return f"rm_e{n}_{pct}_prod_x"


def _trial(monkeypatch, valor):
    import db.plans as plans
    monkeypatch.setattr(plans, "is_trial_eligible_for_user", lambda uid: valor)


def test_tabela_e_helper():
    assert retomada.DESCONTO_POR_ETAPA == {1: 5, 2: 10, 3: 20}
    assert [retomada.desconto_da_etapa(x) for x in (1, 3, 0, 4, None, True, "1")] == [5, 20, None, None, None, None, None]
    assert retomada.com_desconto(7920 * 100 // 80, 20) == 7920 and retomada.com_desconto(9900, 5) == 9405


def test_precos_mensais_batem_com_o_html():
    html = Path("frontend/precos.html").read_text(encoding="utf-8")
    achados = re.findall(r"data-price-monthly[^>]*>R\$&nbsp;([\d,]+)<", html)
    assert [int(a.replace(",", "")) for a in achados] == list(retomada.PRECOS_MENSAIS_CENTS.values())


# ── Stripe ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("etapa,pct", ETAPAS)
def test_stripe_desconto_por_etapa(monkeypatch, etapa, pct):
    a, _ = _conta(f"d-st{etapa}")
    fake = _stripe(monkeypatch)
    assert _checkout(_cliente(a, etapa)).status_code == 200
    k = fake.last_session_kwargs
    assert k["discounts"] == [{"coupon": _id(etapa, pct)}]
    assert "allow_promotion_codes" not in k
    assert k["metadata"]["rm_desc"] == str(pct) == k["subscription_data"]["metadata"]["rm_desc"]
    assert fake.coupons[_id(etapa, pct)]["duration"] == "repeating"


def test_stripe_sem_retomada_nada_muda(monkeypatch):
    a, _ = _conta("d-st-sem")
    fake = _stripe(monkeypatch)
    assert _checkout(_cliente(auth=a)).status_code == 200
    k = fake.last_session_kwargs
    assert k["allow_promotion_codes"] is True and "discounts" not in k
    assert "rm_desc" not in k["metadata"] and fake.coupon_create_calls == 0


def test_stripe_trial_continua_e_leva_o_desconto(monkeypatch):
    a, _ = _conta("d-trial")
    fake = _stripe(monkeypatch)
    _trial(monkeypatch, True)
    assert _checkout(_cliente(a, 3)).status_code == 200
    k = fake.last_session_kwargs
    assert k["subscription_data"]["trial_period_days"] == 7
    assert k["discounts"] == [{"coupon": _id(3, 20)}]


def test_cupom_criado_uma_vez_com_repeating_e_applies_to():
    fake = _FakeStripe()
    assert cupom_id(fake, 2, 10, "price_x") == _id(2, 10)
    assert cupom_id(fake, 2, 10, "price_x") == _id(2, 10)   # o igual não é recriado
    assert fake.coupon_create_calls == 1
    c = fake.coupons[_id(2, 10)]
    assert (c["percent_off"], c["duration"], c["duration_in_months"], c["applies_to"]) == (
        10.0, "repeating", 1, {"products": ["prod_x"]})


def test_cupom_corrida_already_exists_passa():
    fake = _FakeStripe()
    fake.coupon_race = True
    assert cupom_id(fake, 1, 5, "price_x") == _id(1, 5)


@pytest.mark.parametrize("mudanca", [
    {"percent_off": 15.0}, {"duration": "once"}, {"duration_in_months": 3},
    {"applies_to": {"products": ["prod_outro"]}}, {"valid": False},
])
def test_cupom_divergente_e_503_e_nao_e_corrigido(monkeypatch, mudanca):
    a, _ = _conta("d-div")
    fake = _stripe(monkeypatch)
    cupom_id(fake, 3, 20, "p")   # nasce correto
    fake.coupons[_id(3, 20)].update(mudanca)
    antes = dict(fake.coupons[_id(3, 20)])
    r = _checkout(_cliente(a, 3))
    assert r.status_code == 503 and fake.session_create_calls == 0
    assert fake.coupons[_id(3, 20)] == antes and fake.coupon_create_calls == 1
    with pytest.raises(CupomIndisponivel):
        cupom_id(fake, 3, 20, "p")


def test_reaproveita_so_com_rm_etapa_e_rm_desc_iguais(monkeypatch):
    a, _ = _conta("d-reuso")
    fake = _stripe(monkeypatch)
    assert _checkout(_cliente(a, 1)).status_code == 200
    assert _checkout(_cliente(a, 1)).status_code == 200
    assert (fake.session_create_calls, fake.session_expire_calls) == (1, 0)       # igual reaproveita
    fake.open_sessions[0]["metadata"]["rm_desc"] = "99"                           # tabela mudou / sessão velha
    assert _checkout(_cliente(a, 1)).status_code == 200
    assert (fake.session_create_calls, fake.session_expire_calls) == (2, 1)
    del fake.open_sessions[1]["metadata"]["rm_desc"]                              # sessão de antes do 6b
    assert _checkout(_cliente(a, 1)).status_code == 200
    assert (fake.session_create_calls, fake.session_expire_calls) == (3, 2)
    assert _checkout(_cliente(a, 2)).status_code == 200                           # outra etapa
    assert (fake.session_create_calls, fake.session_expire_calls) == (4, 3)


def test_ja_comprou_e_409_nos_dois_meios_e_quem_nao_comprou_passa(monkeypatch, vendavel, asaas_falso):
    a, _ = _conta("d-comprou")
    fake = _stripe(monkeypatch)
    monkeypatch.setattr(dashboard, "ja_comprou", lambda uid: True)
    monkeypatch.setattr(billing_pix, "ja_comprou", lambda uid: True)
    assert _checkout(_cliente(a, 1)).status_code == 409
    assert fake.session_create_calls == 0 and fake.customer_create_calls == 0
    assert _pix(_cliente(a, 1), plan="essencial").status_code == 409 and _linhas(a) == []
    monkeypatch.setattr(dashboard, "ja_comprou", lambda uid: False)
    monkeypatch.setattr(billing_pix, "ja_comprou", lambda uid: False)
    assert _checkout(_cliente(a, 1)).status_code == 200
    assert _pix(_cliente(a, 1), plan="essencial").status_code == 200


def test_etapa_forjada_no_corpo_ou_na_query_nao_da_desconto(monkeypatch, vendavel, asaas_falso):
    b, _ = _conta("d-forja")
    fake = _stripe(monkeypatch)
    c = _cliente(auth=b, painel=b)
    r = c.post("/billing/create-checkout?etapa=3&rm_etapa=3&desconto=20", headers=_CSRF_HEADERS,
               json={"plan": "plus", "etapa": 3, "rm_etapa": 3, "desconto_pct": 20})
    assert r.status_code == 200, r.text
    assert "discounts" not in fake.last_session_kwargs and fake.coupon_create_calls == 0
    r = c.post("/billing/pix/checkout?etapa=3", headers=_CSRF_HEADERS,
               json={"plan": "essencial", "cpf_cnpj": "52998224725", "etapa": 3, "desconto_pct": 20})
    assert r.status_code == 200, r.text
    assert _linhas(b)[0]["amount_cents"] == 9900


# ── Pix ─────────────────────────────────────────────────────────────────────

def test_pix_e3_essencial_7920_e_sem_retomada_9900(vendavel, asaas_falso):
    a, _ = _conta("d-pix3")
    b, _ = _conta("d-pix0")
    assert _pix(_cliente(a, 3), plan="essencial").status_code == 200
    assert _pix(_cliente(painel=b), plan="essencial").status_code == 200
    assert _linhas(a)[0]["amount_cents"] == 7920 and _linhas(b)[0]["amount_cents"] == 9900


def test_pix_pending_cheia_mais_clique_da_e1_gera_cobranca_nova_de_9405(vendavel, asaas_falso):
    a, _ = _conta("d-pix-troca")
    assert _pix(_cliente(painel=a), plan="essencial").status_code == 200
    r = _pix(_cliente(a, 1), plan="essencial")
    assert r.status_code == 200 and r.json()["amount_cents"] == 9405
    linhas = _linhas(a)
    assert [(l["amount_cents"], l["status"]) for l in linhas] == [(9900, "canceled"), (9405, "pending")]
    assert _pix(_cliente(a, 1), plan="essencial").json()["public_token"] == r.json()["public_token"]   # igual reaproveita


def test_pix_extras_ficam_a_preco_cheio(vendavel, asaas_falso, loja):
    a, _ = _conta("d-pix-extras")
    r = _comprar(a, plano="essencial", desconto_pct=20, extras=["price_ca"])
    assert r["amount_cents"] == 7920 and r["total_cents"] == 7920 + 1990


# ── e-mails ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("etapa,pct", ETAPAS)
def test_emails_citam_o_desconto_e_nao_prometem_nos_cadernos(etapa, pct):
    from core.services.remarketing_emails import montar
    _, html, texto = montar(etapa, "https://x/r", "https://x/u", "https://x")
    assert f"{pct}% OFF" in html and f"{pct}% OFF" in texto
    assert "caderno" not in html.lower() and "caderno" not in texto.lower()


# ── plans-config ────────────────────────────────────────────────────────────

def test_plans_config_devolve_precos_ja_com_desconto(monkeypatch):
    a, _ = _conta("d-cfg")
    _trial(monkeypatch, True)
    r = _cliente(a, 3).get("/billing/plans-config").json()["retomada"]
    assert r["desconto_pct"] == 20 and r["trial"] is True
    assert r["precos"]["essencial"] == {"monthly": 792, "annual": 7920, "pix": 7920}
    assert r["precos"]["pro"]["annual"] == 39920 and r["precos"]["plus"]["monthly"] == 1592
    assert _cliente().get("/billing/plans-config").json()["retomada"] is None
    forjado = _cliente().get("/billing/plans-config?etapa=3").json()["retomada"]
    assert forjado is None
