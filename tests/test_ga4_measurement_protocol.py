"""GA4 server-side: a receita que nenhum navegador vê.

O `purchase` do GA4 sai do WEBHOOK do Stripe, não do site — só o servidor sabe o
valor real cobrado, e a cobrança do fim do trial e as renovações acontecem com o
usuário longe do navegador. Este arquivo alimenta eventos sintéticos da Stripe
pelo `POST /billing/webhook` (mesmo harness do `test_billing_webhook_lifecycle`)
e verifica **o corpo que iria pro Google** — não que uma função foi chamada.

Os três cortes que decidem se o número final está certo, cada um com teste:
  1. checkout em TRIAL não manda purchase (o dinheiro entra semanas depois);
  2. a primeira fatura da compra imediata (`subscription_create`) não manda de
     novo — ela já foi o purchase do checkout;
  3. sem `GA4_API_SECRET` nada sai (é o controle negativo do arquivo inteiro).
"""
from __future__ import annotations

from types import SimpleNamespace

from core.services import ga4_mp
from tests.test_billing_webhook_lifecycle import (
    _cleanup_trial,
    _fake_sub,
    _post,
    _setup,
)

_CID_REAL = "1234567890.1712345678"


class _Captura:
    """Substitui o `requests` do ga4_mp: guarda o que seria enviado e responde 204
    (que é o que o endpoint do GA4 responde de verdade)."""

    def __init__(self):
        self.chamadas: list[dict] = []

    def post(self, url, params=None, json=None, timeout=None):
        self.chamadas.append({"url": url, "params": params or {}, "json": json or {}})
        return SimpleNamespace(status_code=204, text="")

    # ── leitura ──────────────────────────────────────────────────────────────
    @property
    def eventos(self) -> list[dict]:
        return [c["json"]["events"][0] for c in self.chamadas]

    def unico(self) -> tuple[dict, dict]:
        assert len(self.chamadas) == 1, f"esperava 1 envio, veio {len(self.chamadas)}"
        corpo = self.chamadas[0]["json"]
        return corpo, corpo["events"][0]


def _ligar_ga4(monkeypatch, *, com_segredo: bool = True) -> _Captura:
    monkeypatch.setenv("GA4_MEASUREMENT_ID", "G-TESTE00000")
    if com_segredo:
        monkeypatch.setenv("GA4_API_SECRET", "segredo-de-teste")
    else:
        monkeypatch.delenv("GA4_API_SECRET", raising=False)
    captura = _Captura()
    monkeypatch.setattr(ga4_mp, "requests", captura)
    return captura


def _sub_pago(status="active", *, unit_amount=1990, metadata=None, days=30) -> dict:
    """`_fake_sub` + o preço (que é de onde sai o valor da compra) e o metadata
    (que é de onde sai o client_id)."""
    sub = _fake_sub(status, "price_ga4", days)
    sub["items"]["data"][0]["price"].update({"unit_amount": unit_amount, "currency": "brl"})
    sub["metadata"] = metadata or {}
    return sub


def _evento_checkout(uid: int, *, session_id="cs_ga4_1", metadata=None,
                     amount_total=None, currency="brl") -> dict:
    obj = {
        "id": session_id,
        "metadata": {"finbot_user_id": str(uid), **(metadata or {})},
        "subscription": "sub_ga4",
    }
    if amount_total is not None:
        obj["amount_total"] = amount_total
        obj["currency"] = currency
    return {"type": "checkout.session.completed", "data": {"object": obj}}


def _evento_fatura(uid: int, *, invoice_id, billing_reason, amount_paid=1990) -> dict:
    return {
        "type": "invoice.paid",
        "data": {"object": {
            "id": invoice_id,
            "metadata": {"finbot_user_id": str(uid)},
            "subscription": "sub_ga4",
            "amount_paid": amount_paid,
            "currency": "brl",
            "billing_reason": billing_reason,
        }},
    }


# ── compra imediata (checkout sem trial) ─────────────────────────────────────

def test_compra_imediata_manda_purchase_com_valor_e_client_id_do_navegador(user_id, monkeypatch):
    """O caso que paga a conta: valor real, id da transação e a MESMA pessoa que
    navegou o site (client_id que veio do cookie `_ga` e viajou no metadata)."""
    uid, client, fake = _setup(monkeypatch, f"ga4-ok-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        r = _post(
            client, fake,
            _evento_checkout(uid, metadata={"ga_client_id": _CID_REAL}),
            subs={"sub_ga4": _sub_pago("active", metadata={"ga_client_id": _CID_REAL})},
        )
        assert r.status_code == 200, r.text

        corpo, evento = captura.unico()
        assert corpo["client_id"] == _CID_REAL
        assert corpo["user_id"] == str(uid)
        assert evento["name"] == "purchase"
        assert evento["params"]["value"] == 19.90
        assert evento["params"]["currency"] == "BRL"
        assert evento["params"]["transaction_id"] == "cs_ga4_1"
        # `items` é o que liga os relatórios de e-commerce; sem ele o GA4 mostra
        # o evento e não mostra a receita por produto.
        assert evento["params"]["items"][0]["price"] == 19.90
        # A chave de escrita vai na query, não no corpo.
        assert captura.chamadas[0]["params"]["measurement_id"] == "G-TESTE00000"
    finally:
        _cleanup_trial(uid)


def test_sem_client_id_a_venda_nao_se_perde(user_id, monkeypatch):
    """Cookie bloqueado / checkout criado antes desta versão: a receita entra do
    mesmo jeito, com um client_id sintético que NUNCA colide com um real."""
    uid, client, fake = _setup(monkeypatch, f"ga4-fb-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        _post(client, fake, _evento_checkout(uid), subs={"sub_ga4": _sub_pago("active")})

        corpo, evento = captura.unico()
        assert corpo["client_id"] == f"pb-{uid}"
        assert evento["params"]["value"] == 19.90
    finally:
        _cleanup_trial(uid)


def test_cupom_manda_o_valor_pago_e_nao_o_de_tabela(user_id, monkeypatch):
    """Este checkout aceita cupom (`allow_promotion_codes=True`). O `unit_amount`
    do plano ignora o desconto; o `amount_total` da sessão não. Mandar o de
    tabela infla a receita — e o relatório não avisa (Codex, #244)."""
    uid, client, fake = _setup(monkeypatch, f"ga4-cup-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        _post(
            client, fake,
            # plano de R$ 19,90 com cupom: pagou R$ 9,90
            _evento_checkout(uid, amount_total=990),
            subs={"sub_ga4": _sub_pago("active", unit_amount=1990)},
        )
        _, evento = captura.unico()
        assert evento["params"]["value"] == 9.90
        assert evento["params"]["items"][0]["price"] == 9.90
    finally:
        _cleanup_trial(uid)


def test_cupom_de_cem_por_cento_registra_zero_e_nao_o_preco_cheio(user_id, monkeypatch):
    """Zero é resposta legítima, não campo ausente — por isso o código testa
    `is None` e não falsy. Com o teste errado, a compra de graça viraria receita
    de preço cheio."""
    uid, client, fake = _setup(monkeypatch, f"ga4-cup0-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        _post(
            client, fake,
            _evento_checkout(uid, amount_total=0),
            subs={"sub_ga4": _sub_pago("active", unit_amount=1990)},
        )
        _, evento = captura.unico()
        assert evento["params"]["value"] == 0
    finally:
        _cleanup_trial(uid)


def test_item_usa_o_nome_publico_do_plano(user_id, monkeypatch):
    """O `begin_checkout` do navegador manda 'plus'; o banco chama esse tier de
    'pro' por legado (e o Pro de 'pro_max'). Mandar o nome do banco aqui faria o
    GA4 tratar checkout e compra como produtos diferentes — e colidiria Plus com
    Pro no mesmo id 'pro' (Codex, #244)."""
    uid, client, fake = _setup(monkeypatch, f"ga4-pl-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        _post(
            client, fake,
            _evento_checkout(uid, metadata={"plan": "plus"}, amount_total=1990),
            subs={"sub_ga4": _sub_pago("active", metadata={"plan": "plus"})},
        )
        _, evento = captura.unico()
        # price_ga4 é desconhecido → _stored_plan_for_price devolve 'pro'. O item
        # tem de sair 'plus' mesmo assim.
        assert evento["params"]["items"][0]["item_id"] == "plus"
    finally:
        _cleanup_trial(uid)


def test_client_id_pode_vir_so_da_sessao(user_id, monkeypatch):
    """Assinatura que já existia antes desta versão (metadata SEM o campo) numa
    compra nova, que tem. Os dois metadata são consultados; a primeira versão
    olhava só o da assinatura quando ele era não-vazio, e perdia o da sessão."""
    uid, client, fake = _setup(monkeypatch, f"ga4-ses-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        _post(
            client, fake,
            _evento_checkout(uid, metadata={"ga_client_id": _CID_REAL}),
            # metadata NÃO-VAZIO e sem o ga_client_id: é o que quebrava.
            subs={"sub_ga4": _sub_pago("active", metadata={"finbot_user_id": str(uid)})},
        )
        corpo, _ = captura.unico()
        assert corpo["client_id"] == _CID_REAL
    finally:
        _cleanup_trial(uid)


def test_trial_nao_manda_purchase(user_id, monkeypatch):
    """Corte 1: assinatura que nasce em trial não movimentou dinheiro nenhum. O
    purchase dela sai semanas depois, no invoice.paid da primeira cobrança."""
    uid, client, fake = _setup(monkeypatch, f"ga4-tr-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        r = _post(
            client, fake,
            _evento_checkout(uid, metadata={"ga_client_id": _CID_REAL}),
            subs={"sub_ga4": _sub_pago("trialing", metadata={"ga_client_id": _CID_REAL})},
        )
        assert r.status_code == 200, r.text
        assert captura.chamadas == []
    finally:
        _cleanup_trial(uid)


# ── cobrança do fim do trial e renovações ────────────────────────────────────

def test_renovacao_manda_purchase_com_o_valor_pago_e_o_id_da_fatura(user_id, monkeypatch):
    """O dinheiro que nenhum navegador vê. `transaction_id` é a FATURA — se fosse
    a assinatura, a renovação do mês seguinte seria descartada como duplicada."""
    uid, client, fake = _setup(monkeypatch, f"ga4-rn-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        r = _post(
            client, fake,
            _evento_fatura(uid, invoice_id="in_ga4_9", billing_reason="subscription_cycle",
                           amount_paid=4990),
            subs={"sub_ga4": _sub_pago("active", metadata={"ga_client_id": _CID_REAL})},
        )
        assert r.status_code == 200, r.text

        corpo, evento = captura.unico()
        assert evento["params"]["transaction_id"] == "in_ga4_9"
        assert evento["params"]["value"] == 49.90     # o PAGO, não o de tabela
        assert corpo["client_id"] == _CID_REAL        # atribuição sobrevive à renovação
    finally:
        _cleanup_trial(uid)


def test_renovacao_depois_de_trocar_de_plano_usa_o_preco_atual(user_id, monkeypatch):
    """A troca agendada (`/billing/change-plan`) reescreve só o PRICE das fases do
    SubscriptionSchedule — o `metadata.plan` continua com o plano antigo pra
    sempre. Sem olhar o preço, toda renovação depois da troca entraria no item
    errado, e a receita do plano novo apareceria no antigo (Codex, #244)."""
    import frontend.finance_bot_websocket_custom as dashboard
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PROMAX_MENSAL", "price_promax_m")

    uid, client, fake = _setup(monkeypatch, f"ga4-troca-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        sub = _sub_pago("active", metadata={"plan": "plus"})   # metadata: o VELHO
        sub["items"]["data"][0]["price"]["id"] = "price_promax_m"  # preço: o NOVO
        _post(
            client, fake,
            _evento_fatura(uid, invoice_id="in_troca_1",
                           billing_reason="subscription_cycle", amount_paid=4990),
            subs={"sub_ga4": sub},
        )
        _, evento = captura.unico()
        assert evento["params"]["items"][0]["item_id"] == "pro"
    finally:
        _cleanup_trial(uid)


def test_primeira_fatura_da_compra_imediata_nao_conta_duas_vezes(user_id, monkeypatch):
    """Corte 2: `subscription_create` é a fatura da compra que o checkout já
    contou. Sem este corte, toda compra imediata viraria duas no relatório."""
    uid, client, fake = _setup(monkeypatch, f"ga4-dup-{user_id}")
    captura = _ligar_ga4(monkeypatch)
    try:
        r = _post(
            client, fake,
            _evento_fatura(uid, invoice_id="in_ga4_1", billing_reason="subscription_create"),
            subs={"sub_ga4": _sub_pago("active")},
        )
        assert r.status_code == 200, r.text
        assert captura.chamadas == []
    finally:
        _cleanup_trial(uid)


# ── configuração e fronteira de confiança ────────────────────────────────────

def test_sem_api_secret_nada_e_enviado(user_id, monkeypatch):
    """Controle negativo do arquivo: sem o segredo, o mesmo evento que produziu
    um envio nos testes acima não produz nenhum. É também o contrato de
    dev/staging — nada de dado de teste na propriedade de produção."""
    uid, client, fake = _setup(monkeypatch, f"ga4-off-{user_id}")
    captura = _ligar_ga4(monkeypatch, com_segredo=False)
    try:
        r = _post(
            client, fake,
            _evento_checkout(uid, metadata={"ga_client_id": _CID_REAL}),
            subs={"sub_ga4": _sub_pago("active", metadata={"ga_client_id": _CID_REAL})},
        )
        assert r.status_code == 200, r.text
        assert captura.chamadas == []
    finally:
        _cleanup_trial(uid)


def test_client_id_do_navegador_e_validado_antes_de_viajar():
    """Fronteira de confiança: o client_id chega do CLIENTE e vai parar no
    metadata do Stripe. Só o formato do GA passa; o resto é descartado em vez de
    virar lixo no relatório (ou no metadata de uma assinatura)."""
    assert ga4_mp.sanitize_client_id(_CID_REAL) == _CID_REAL
    assert ga4_mp.sanitize_client_id(f"  {_CID_REAL}  ") == _CID_REAL

    for lixo in ["", "GA1.1.123.456", "abc.def", "12345678", None, 42,
                 "1.1; drop table", "9" * 25 + ".1", {"a": 1}]:
        assert ga4_mp.sanitize_client_id(lixo) is None, lixo


def test_fallback_nunca_colide_com_client_id_real():
    """O sintético tem de ser impossível de confundir com um de verdade — colisão
    fundiria duas pessoas diferentes num usuário só."""
    sintetico = ga4_mp.fallback_client_id(42)
    assert sintetico == "pb-42"
    assert ga4_mp.sanitize_client_id(sintetico) is None
