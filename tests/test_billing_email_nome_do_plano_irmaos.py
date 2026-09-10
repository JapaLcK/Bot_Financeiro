"""Os DOIS irmãos do #351: falha de cobrança e cancelamento (`email_service`).

Separado de `test_billing_email_nome_do_plano.py` pelo teto de 350 linhas
(§0.5). Lá estão os três e-mails do caminho FELIZ (boas-vindas, fim de trial,
cobrança confirmada); aqui os dois do caminho em que a cobrança falha ou a
assinatura morre. Os dois grupos compartilham o mapa `PLAN_DISPLAY_NAMES` e o
`plan_display_name`, e isso foi MEDIDO: chavear o mapa no vocabulário público
(`plus`/`pro`) derruba 9 casos nos três arquivos do assunto — 3 lá, 4 aqui e 2
no do Pix.

Uma versão anterior do commit dizia que estes dois ficavam de fora porque
"nenhum dos dois tem o plano em escopo no chamador". **Era falso, e o Tester
provou:** o ramo `invoice.payment_failed` já faz um `Subscription.retrieve`
para decidir se o evento é obsoleto (`finance_bot_websocket_custom.py:5626`), e
no `customer.subscription.deleted` o próprio `event.data.object` É a
Subscription. Medido antes do conserto: assinante Essencial cujo cartão falha
recebia `⚠️ PigBank+ — pagamento falhou, atualize seu cartão`, e quem cancelava
recebia `PigBank+ — assinatura cancelada`.

Os controles do §3, para o GRUPO — RODADOS, com o vermelho que produziram:

  · **negativo, a copy da falha** — volte `nome` para a constante `"PigBank+"`
    em `send_payment_failed_email` → vermelhos `test_falha_..._essencial...`,
    `test_falha_..._pro_max...` e `test_fatura_avulsa...`.
  · **negativo, a copy do cancelamento** — o mesmo em
    `send_subscription_canceled_email` → vermelhos
    `test_cancelamento_..._essencial...` e `test_cancelamento_..._pro_max...`.
  · **negativo, o seam da falha** — tire `_plano_falha` da chamada de
    `_fire_email` → vermelho `test_o_webhook_manda_o_plano_na_falha_e_na_avulsa`
    com `plano inventado numa fatura avulsa: (..., 'http://localhost:8000')` —
    a `DASHBOARD_URL` andando uma casa, que é o que a troca de ordem faz sem
    levantar nada.
  · **negativo, o seam do cancelamento** — tire `_plano_cancelado` → vermelho
    `test_o_webhook_manda_o_plano_no_cancelamento` com um `datetime` no lugar
    do plano.
  · **negativo, a guarda da avulsa** — tire `_sub_agora = None` de antes do
    `if` → vermelho `..._na_falha_e_na_avulsa` com **500** no webhook
    (`UnboundLocalError`), que é a Stripe reentregando por 3 dias.
  · **positivo** — `test_falha_..._plus_continua_pigbank_mais` e
    `test_cancelamento_..._plus_continua_pigbank_mais`: o Plus já lia o nome
    certo com o texto fixo e continua lendo. Sem eles o grupo passaria num
    conserto que trocasse todo mundo pelo genérico.

O que este arquivo NÃO alcança: o envio de verdade (`send_email` é
monkeypatchado) e o e-mail de fatura AVULSA em produção — aqui ele é provado
só até o argumento (`None`) e a copy genérica.
"""
import re
from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    conta as _conta, espiao_email as _espiao, garantir_system_event_logs,
    sub_stripe as _sub,
)
import frontend.finance_bot_websocket_custom as dashboard
from core.services import email_service as es
# A fixture `capturado` e o `_tudo` são os MESMOS dos arquivos irmãos —
# importar em vez de copiar (§0.1).
from test_pix_paid_email_copy import capturado  # noqa: F401
from test_billing_email_nome_do_plano import _tudo
from test_billing_webhook_lifecycle import _post, _setup

PROXIMA = datetime.now(timezone.utc) + timedelta(days=30)


def _sem_tier_solto(parte: str, nome: str) -> None:
    """Nenhum tier SOLTO no corpo, sem a marca — "os recursos Pro", "os limites
    Pro". `assert "PigBank Pro" not in parte` NÃO pega essa forma, e foi assim
    que o assunto passou a dizer "PigBank Essencial" com o corpo ainda dizendo
    o nome de outro tier. Tira o nome legítimo primeiro; o que sobrar é leak.
    """
    resto = parte.replace(nome, "")
    assert not re.search(r"\b(Pro|Plus)\b", resto), f"tier solto no corpo: {parte}"


# ── a copy, por plano ────────────────────────────────────────────────────────
# A copy destes dois NÃO foi tocada além do nome: continua prometendo volta ao
# "Free". Os asserts abaixo afirmam só o NOME de propósito — o dia em que o
# produto decidir o que acontece com quem não paga, a copy muda sem mexer aqui.

def test_falha_de_pagamento_do_essencial_nao_vira_plus(capturado):  # noqa: F811
    """Cartão do Essencial recusado e o assunto dizia "⚠️ PigBank+"."""
    assert es.send_payment_failed_email("a@b.com", "essencial")
    for parte in _tudo(capturado):
        assert "PigBank Essencial" in parte, parte
        assert "PigBank+" not in parte, f"nome de outro plano na falha: {parte}"
        _sem_tier_solto(parte, "PigBank Essencial")


def test_falha_de_pagamento_do_pro_max_e_o_pro(capturado):  # noqa: F811
    assert es.send_payment_failed_email("a@b.com", "pro_max")
    for parte in _tudo(capturado):
        assert "PigBank Pro" in parte, parte
        assert "PigBank+" not in parte, f"nome de outro plano na falha: {parte}"
        _sem_tier_solto(parte, "PigBank Pro")


def test_falha_de_pagamento_do_plus_continua_pigbank_mais(capturado):  # noqa: F811
    """CONTROLE POSITIVO do par: o Plus já lia o nome certo e continua lendo."""
    assert es.send_payment_failed_email("a@b.com", "pro")
    for parte in _tudo(capturado):
        assert "PigBank+" in parte, parte
        assert "PigBank Pro" not in parte, f"nome do plano mais caro: {parte}"
        _sem_tier_solto(parte, "PigBank+")


def test_fatura_avulsa_sem_assinatura_usa_o_generico(capturado):  # noqa: F811
    """`plan=None` é a fatura AVULSA — sem assinatura, não há plano a nomear.

    É o ÚNICO caminho que alcança o fallback genérico (ver
    `plan_display_name`): pelo Stripe o plano sai de `_stored_plan_for_price`,
    que nunca devolve nada fora
    do mapa. O precedente da copy neutra é o `send_payment_reminder_email`, que
    já diz "PigBank" sem sufixo.
    """
    assert es.send_payment_failed_email("a@b.com", None)
    for parte in _tudo(capturado):
        assert "PigBank" in parte, parte
        for outro in ("PigBank+", "PigBank Essencial", "PigBank Pro"):
            assert outro not in parte, f"nomeou plano numa fatura avulsa: {parte}"
        _sem_tier_solto(parte, "PigBank")


def test_cancelamento_do_essencial_nao_vira_plus(capturado):  # noqa: F811
    """Cancelou Essencial e recebia "PigBank+ — assinatura cancelada"."""
    assert es.send_subscription_canceled_email("a@b.com", "essencial", PROXIMA)
    for parte in _tudo(capturado):
        assert "PigBank Essencial" in parte, parte
        assert "PigBank+" not in parte, f"nome de outro plano no cancelamento: {parte}"
        _sem_tier_solto(parte, "PigBank Essencial")


def test_cancelamento_do_pro_max_e_o_pro(capturado):  # noqa: F811
    """Sem grace (`expires_at=None`): o outro ramo da função, mesmo nome."""
    assert es.send_subscription_canceled_email("a@b.com", "pro_max", None)
    for parte in _tudo(capturado):
        assert "PigBank Pro" in parte, parte
        assert "PigBank+" not in parte, f"nome de outro plano no cancelamento: {parte}"
        _sem_tier_solto(parte, "PigBank Pro")


def test_cancelamento_do_plus_continua_pigbank_mais(capturado):  # noqa: F811
    """CONTROLE POSITIVO do par."""
    assert es.send_subscription_canceled_email("a@b.com", "pro", PROXIMA)
    for parte in _tudo(capturado):
        assert "PigBank+" in parte, parte
        assert "PigBank Pro" not in parte, f"nome do plano mais caro: {parte}"
        _sem_tier_solto(parte, "PigBank+")


# ── os seams dos dois irmãos ────────────────────────────────────────────────

def test_o_webhook_manda_o_plano_na_falha_e_na_avulsa(user_id, monkeypatch):
    """`invoice.payment_failed` de Essencial → `"essencial"`; avulsa → `None`.

    O plano sai do `Subscription.retrieve` que o ramo JÁ fazia para decidir se o
    evento é obsoleto — o mesmo objeto, sem uma chamada de API a mais.

    A metade AVULSA é o caminho que ESTOURA se `_sub_agora` não for inicializado
    antes do `if`: `UnboundLocalError` dentro do handler, 500 no webhook e a
    Stripe reentregando por 3 dias. Tire o `_sub_agora = None` e esta metade
    fica vermelha em `status_code == 200`.
    """
    uid, client, fake = _setup(monkeypatch, f"pf-{user_id}")
    _conta(uid, "essencial", None, "active")
    garantir_system_event_logs()
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_ESSENCIAL_MENSAL", "price_ess")

    vistos: dict[str, tuple] = {}
    monkeypatch.setattr(es, "send_payment_failed_email",
                        _espiao(vistos, "send_payment_failed_email"))

    # A AVULSA vem PRIMEIRO, e a ordem é obrigatória, não estética: ela não
    # abre ciclo (`_abriu_ciclo` depende de `_sub_id`), então cai na dedupe de
    # `DUNNING_GRACE_DAYS` e ficaria MUDA depois da fatura de assinatura —
    # medido, o teste acusava "a avulsa não mandou e-mail nenhum". A de
    # assinatura abre ciclo e manda com `dedup_days=0.0`, que é "não suprima",
    # então nesta ordem as duas saem.
    avulsa = {"type": "invoice.payment_failed", "id": "evt_nome_pf_avulsa",
              "created": 1_800_000_200,
              "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                  "attempt_count": 1}}}
    r = _post(client, fake, avulsa)
    assert r.status_code == 200, r.text
    args = vistos.get("send_payment_failed_email")
    assert args is not None, "a avulsa não mandou e-mail nenhum"
    assert args[1] is None, f"plano inventado numa fatura avulsa: {args}"

    vistos.clear()
    falha = {"type": "invoice.payment_failed", "id": "evt_nome_pf",
             "created": 1_800_000_300,
             "data": {"object": {"metadata": {"finbot_user_id": str(uid)},
                                 "subscription": "sub_pf", "attempt_count": 1}}}
    r = _post(client, fake, falha, subs={"sub_pf": _sub("past_due", "price_ess", 5)})
    assert r.status_code == 200, r.text
    args = vistos.get("send_payment_failed_email")
    assert args is not None, "o e-mail de falha não saiu"
    assert args[1] == "essencial", f"send_payment_failed_email recebeu {args[1]!r}: {args}"


def test_o_webhook_manda_o_plano_no_cancelamento(user_id, monkeypatch):
    """`customer.subscription.deleted` de Essencial → `"essencial"`.

    O `obj` do evento É a Subscription, então o price está ali — e tem de ser
    ele, não a conta: o `update_user_plan(..., "free", None)` do mesmo ramo roda
    ANTES do e-mail, e ler a conta devolveria "free" para todo mundo.
    """
    uid, client, fake = _setup(monkeypatch, f"cx-{user_id}")
    _conta(uid, "essencial", PROXIMA, "active")
    garantir_system_event_logs()
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_ESSENCIAL_MENSAL", "price_ess")

    vistos: dict[str, tuple] = {}
    monkeypatch.setattr(es, "send_subscription_canceled_email",
                        _espiao(vistos, "send_subscription_canceled_email"))

    morta = _sub("canceled", "price_ess", 5)
    morta["id"] = "sub_cx"
    morta["metadata"] = {"finbot_user_id": str(uid)}
    r = _post(client, fake, {"type": "customer.subscription.deleted",
                             "id": "evt_nome_del", "created": 1_800_000_400,
                             "data": {"object": morta}})
    assert r.status_code == 200, r.text
    args = vistos.get("send_subscription_canceled_email")
    assert args is not None, "o e-mail de cancelamento não saiu"
    assert args[1] == "essencial", (
        f"send_subscription_canceled_email recebeu {args[1]!r}: {args}")
