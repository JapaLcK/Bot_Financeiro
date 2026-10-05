"""Funil v3, PR 2 de 4 — N produtos extras na fatura e no rastreio.

Pela rota `POST /billing/webhook`, com banco real e os helpers de
`tests/test_ebook_webhook.py`. A origem (`assinar`/`precos`) não muda nada (P2).

Y1  trial + 3 extras: a 1ª fatura (plano 0) não cobra, não comissiona, não rastreia.
Y2  sem trial + 3 extras + cupom num deles: e-mail e comissão = plano líquido exato.
Y3  checkout com 3 extras = UMA compra com a soma (com trial: `ebook_<sid>`).
Y4  fatura de ciclo com extra nas linhas: o rastreio leva só o plano.
Y5  mais de 10 linhas (`has_more`): busca todas pela API; sem `has_more`, não busca.
Y6  `has_more` sem extras na foto: não busca (nada a subtrair).
Y7  falha ao buscar as linhas: 5xx, sem e-mail, comissão nem rastreio.

Controles (rodados, ver o relato do PR):
  · subtraindo só o slot 1                      → Y1, Y2 vermelhos;
  · conjunto lido da env, não da foto           → C1–C5, C7 (test_ebook_webhook), Y1, Y2;
  · sem o Purchase dos extras no trial          → Y3 (trial) vermelho;
  · `amount_paid` no rastreio da fatura         → Y4 vermelho;
  · item do GA4 "ebook" → "xxx"                 → Y3 (trial) vermelho;
  · sem a paginação do `has_more`               → Y5 vermelho.
  · buscar mesmo sem extras na foto             → Y6 vermelho.
  · try/except em volta do `list_lines`         → Y7 vermelho.
  Positivos: C6 (sem extras, igual a hoje) e Y5 sem `has_more` (a API não é chamada).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_billing_webhook_lifecycle import _T_LIFE, _fake_sub, _post
from test_ebook_webhook import (  # noqa: F401 — `indicado` e `_event_logs` são fixtures
    _PRECO, _SUB, _TIPOS, _URL, _cobrado, _comissoes, _event_logs, _fatura, _linha, indicado,
)

_P3 = [_PRECO, "price_extra_2", "price_extra_3"]
_ORIGENS = pytest.mark.parametrize("origem", ["assinar", "precos"])


def _meta(origem, precos=_P3):
    meta = {"origem": origem}
    for n, preco in enumerate(precos, 1):
        pre = "ebook_" if n == 1 else f"ebook_{n}_"
        meta.update({f"{pre}price": preco, f"{pre}url": _URL})
    return meta


def _sub(origem, status="active", precos=_P3):
    sub = _fake_sub(status, "price_plano")
    sub["items"]["data"][0]["price"].update({"unit_amount": 1990, "currency": "brl"})
    sub["metadata"] = _meta(origem, precos)
    return sub


def _api_linhas(fake, linhas) -> list:
    """`stripe.Invoice.list_lines` falso; devolve a lista das chamadas."""
    chamadas: list = []
    fake.Invoice = SimpleNamespace(list_lines=lambda inv, **kw: chamadas.append((inv, kw))
                                   or {"data": linhas, "has_more": False})
    return chamadas


def _rastreio(envios):
    """(canal, nome, id, valor[, item]) de cada evento que iria pro Meta e pro GA4."""
    out = []
    for k in envios:
        for e in k["json"].get("data", []):
            out.append(("capi", e["event_name"], e["event_id"], (e.get("custom_data") or {}).get("value")))
        for e in k["json"].get("events", []):
            p = e["params"]
            out.append(("ga4", e["name"], p["transaction_id"], p["value"], p["items"][0]["item_id"]))
    return sorted(out)


@_TIPOS
@_ORIGENS
def test_y1_trial_com_3_extras_a_1a_fatura_nao_cobra_nem_comissiona(indicado, tipo, origem):
    uid, client, fake, vistos, envios = indicado
    r = _post(client, fake, _fatura(tipo, uid, "in_y1", 990 + 1490 + 2990, "subscription_create",
                                    [_linha(_P3[0], 990), _linha(_P3[1], 1490),
                                     _linha(_P3[2], 2990), _linha("price_plano", 0)]),
              subs={_SUB: _sub(origem)})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid), envios) == ([], [], [])


@_TIPOS
@_ORIGENS
def test_y2_p2_sem_trial_3_extras_e_cupom_num_deles_cobra_so_o_plano(indicado, tipo, origem):
    """Cupom de 3,00 no extra 2 (o `amount` da linha é bruto) e o extra 3 com o
    preço expandido: sobra exatamente o plano. Sem `has_more`, a API não é chamada."""
    uid, client, fake, vistos, _ = indicado
    api = _api_linhas(fake, [])
    linhas = [_linha(_P3[0], 990), _linha(_P3[1], 1490, 300),
              _linha({"id": _P3[2]}, 2990), _linha("price_plano", 1990)]
    r = _post(client, fake, _fatura(tipo, uid, "in_y2", 990 + 1190 + 2990 + 1990,
                                    "subscription_create", linhas),
              subs={_SUB: _sub(origem)})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid), api) == ([19.9], [1990], [])


_EXTRAS = 990 + 1490 + 2990   # 54,70


@_ORIGENS
@pytest.mark.parametrize("status,total,esperado", [
    ("trialing", _EXTRAS, [("capi", "Purchase", "ebook_cs_y3", 54.7),
                           ("capi", "StartTrial", "trial_cs_y3", 19.9),
                           ("ga4", "purchase", "ebook_cs_y3", 54.7, "ebook")]),
    ("active", 1990 + _EXTRAS, [("capi", "Purchase", "purchase_cs_y3", 74.6),
                                # item = o plano (price desconhecido → `plan_value`), nunca "ebook"
                                ("ga4", "purchase", "cs_y3", 74.6, "pro")]),
])
def test_y3_p4_checkout_com_3_extras_vira_uma_compra_com_a_soma(indicado, origem, status, total, esperado):
    uid, client, fake, _, envios = indicado
    evento = {"type": "checkout.session.completed", "id": "evt_y3", "created": _T_LIFE,
              "data": {"object": {"id": "cs_y3", "subscription": _SUB, "ui_mode": "hosted_page",
                                  "metadata": {"finbot_user_id": str(uid), **_meta(origem)},
                                  "amount_total": total, "currency": "brl"}}}
    r = _post(client, fake, evento, subs={_SUB: _sub(origem, status)})
    assert r.status_code == 200, r.text
    assert _rastreio(envios) == esperado


@_TIPOS
def test_y4_fatura_de_ciclo_com_extra_rastreia_so_o_plano(indicado, tipo):
    uid, client, fake, vistos, envios = indicado
    r = _post(client, fake, _fatura(tipo, uid, "in_y4", 990 + 1990, "subscription_cycle",
                                    [_linha(_P3[0], 990), _linha("price_plano", 1990)]),
              subs={_SUB: _sub("assinar")})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid)) == ([19.9], [1990])
    assert [t[:4] for t in _rastreio(envios)] == [("capi", "Purchase", "purchase_in_y4", 19.9),
                                                  ("ga4", "purchase", "in_y4", 19.9)]


@_TIPOS
def test_y5_mais_de_10_linhas_busca_todas_pela_api(indicado, tipo):
    """Plano + 10 extras = 11 linhas; a fatura embute só 10 e marca `has_more`."""
    uid, client, fake, vistos, _ = indicado
    precos = [f"price_extra_{n}" for n in range(1, 11)]
    todas = [_linha("price_plano", 1990)] + [_linha(p, 100) for p in precos]
    api = _api_linhas(fake, todas)
    evento = _fatura(tipo, uid, "in_y5", 1990 + 1000, "subscription_create", todas[:10])
    evento["data"]["object"]["lines"]["has_more"] = True
    r = _post(client, fake, evento, subs={_SUB: _sub("assinar", precos=precos)})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid)) == ([19.9], [1990])
    assert api == [("in_y5", {"limit": 100})]


@_TIPOS
def test_y6_has_more_sem_extras_na_foto_nao_busca(indicado, tipo):
    """POSITIVO: sem extras na foto não há o que subtrair — nem chamada à API."""
    uid, client, fake, vistos, _ = indicado
    api = _api_linhas(fake, [])
    evento = _fatura(tipo, uid, "in_y6", 1990, "subscription_create", [_linha("price_plano", 1990)])
    evento["data"]["object"]["lines"]["has_more"] = True
    sub = _sub("precos")
    sub["metadata"] = {}
    r = _post(client, fake, evento, subs={_SUB: sub})
    assert r.status_code == 200, r.text
    assert (_cobrado(vistos), _comissoes(uid), api) == ([19.9], [1990], [])


@_TIPOS
def test_y7_falha_ao_buscar_as_linhas_vira_5xx_sem_efeito(indicado, tipo):
    """Sem as 11 linhas não dá para saber o plano: 5xx (o Stripe reentrega) e
    nada de e-mail, comissão ou rastreio com o extra cortado dentro."""
    uid, client, fake, vistos, envios = indicado
    precos = [f"price_extra_{n}" for n in range(1, 11)]

    def _explode(inv, **kw):
        raise RuntimeError("stripe fora do ar")
    fake.Invoice = SimpleNamespace(list_lines=_explode)
    todas = [_linha("price_plano", 1990)] + [_linha(p, 100) for p in precos]
    evento = _fatura(tipo, uid, "in_y7", 1990 + 1000, "subscription_cycle", todas[:10])
    evento["data"]["object"]["lines"]["has_more"] = True
    r = _post(client, fake, evento, subs={_SUB: _sub("assinar", precos=precos)})
    assert r.status_code >= 500
    assert (_cobrado(vistos), _comissoes(uid), envios) == ([], [], [])
