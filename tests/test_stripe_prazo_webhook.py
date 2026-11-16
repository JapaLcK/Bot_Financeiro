"""#1533: o webhook de reembolso e disputa perdida fala com a Stripe pelo `cliente_stripe` (8 s, sem retentativa).

Rota real `/billing/webhook`, assinatura de verdade, SDK real e Stripe LOCAL na rede (os fixtures de
`tests/test_stripe_prazo.py`), Postgres real. As leituras vêm todas antes de `registrar_reembolso`: prazo
estourado é 5xx sem gravar nada (503: `status_do_erro` lê "connection"/"timeout"), e quem tenta de novo é a reentrega da Stripe.

O tempo da Stripe é medido por pedido HTTP (`_na_rede`), sem o banco que a rota grava antes e depois: é o que
deixa o limite apertado (PRAZO + FOLGA, abaixo de 2 × PRAZO) sem oscilar com o Postgres carregado. O total da rota
só tem um teto folgado (`TETO_ROTA`), que pega o padrão do SDK (80 s × 3), não um prazo dobrado.

Controles (rodados, ver o relato do PR):
  · `Charge.retrieve` / `InvoicePayment.list` de volta à API de módulo → T1/T2 vermelhos;
  · `max_network_retries=2` no `cliente_stripe`                       → T1 vermelho (3 conexões);
  · `RequestsClient(timeout=2 × PRAZO)`                                → T1/T2 vermelhos;
  · sem o claim de `reivindicar`                                        → T10 vermelho (2 alertas);
  · `registrar_reembolso` tratando a reentrega como fato novo          → T9 vermelho (2 alertas).
  Positivo: T3 (a disputa perdida estorna) e T8 (a Stripe lenta mas respondendo conclui).
"""
from __future__ import annotations

import threading
import time

import pytest
import stripe

import frontend.finance_bot_websocket_custom as dashboard
import core.services.afiliado_reembolso as modulo_reembolso
from core.services import admin_notify
from db.connection import get_conn
from test_afiliado_reembolso import SEGREDO, _afiliado, _enviar, _logs, _paga, _status  # noqa: F401
from test_stripe_prazo import PRAZO, _intent, _lista, _na_rede, _paga_por, _so, local, pendurada  # noqa: F401

FOLGA = 0.25    # sobre o PRAZO, por pedido: abaixo de 2 × PRAZO, então um prazo dobrado fica vermelho
TETO_ROTA = 5.0  # o total da rota, com o banco: com `-n 4` o T1 chegou a 1,08 s
_FATURAS = "/v1/invoice_payments"


@pytest.fixture
def webhook(monkeypatch):
    """O que o webhook precisa para responder, e os alertas ao admin contados em `alertas`."""
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x")
    alertas = []
    monkeypatch.setattr(admin_notify, "_send", lambda msg: alertas.append(msg) or True)
    return alertas


def _disputa(cobranca="ch_x"):
    return {"object": "dispute", "status": "lost", "charge": cobranca, "amount": 100_000}


def _reembolso(devolvido):
    return {"id": "ch_pi_1", "object": "charge", "amount": 100_000, "amount_refunded": devolvido,
            "payment_intent": "pi_1"}


def _stripe_da_fatura(local, fatura, **cobranca):
    """A cobrança `ch_x` (sem `invoice`, com `payment_intent`) leva à fatura; a fatura, à cobrança `ch_pi_1`."""
    local.rotas |= {
        "/v1/charges/ch_x": {"object": "charge", "id": "ch_x", "amount": 100_000, "payment_intent": "pi_1"},
        _FATURAS: _lista(_FATURAS, {**_paga_por("pi_1"), "invoice": fatura}),  # serve aos dois filtros
        "/v1/payment_intents/pi_1": _intent("pi_1", **cobranca),
        "/v1/disputes": _lista("/v1/disputes", {"object": "dispute", "status": "lost", "amount": 100_000}),
    }


def _revisoes(fatura):
    with get_conn() as conn:
        return conn.execute("select status, refunded_cents, notified_at is not null as avisada"
                            " from affiliate_refund_reviews where stripe_invoice_id = %s", (fatura,)).fetchall()


def _marcada(com):
    with get_conn() as conn:
        return conn.execute("select payment_check_pending from affiliate_commissions where id = %s",
                            (com.id,)).fetchone()["payment_check_pending"]


def _cronometrado(*a, **k):
    t = time.monotonic()
    r = _enviar(*a, **k)
    return r, time.monotonic() - t


def _desistiu_no_prazo(duracoes, caminho):
    """Um pedido só, ao `caminho`, que desistiu entre PRAZO e PRAZO + FOLGA."""
    assert [c for c, _ in duracoes] == [caminho], duracoes
    assert PRAZO <= duracoes[0][1] < PRAZO + FOLGA, duracoes


def test_webhook_disputa_le_a_cobranca_no_prazo(pendurada, monkeypatch, webhook):
    """T1. `charge.dispute.closed` com a cobrança só pelo id: a leitura dela desiste no prazo, sem retentativa."""
    duracoes = _na_rede(monkeypatch, pendurada.url)
    r, dur = _cronometrado("charge.dispute.closed", _disputa())
    assert r.status_code == 503 and dur < TETO_ROTA, (r.status_code, dur)
    _desistiu_no_prazo(duracoes, "/v1/charges/ch_x")
    assert len(pendurada.conexoes) == 1


def test_webhook_reembolso_acha_a_fatura_no_prazo(local, monkeypatch, webhook):
    """T2. `charge.refunded` sem `invoice`: a busca da fatura pelo `payment_intent` desiste no prazo."""
    duracoes = _na_rede(monkeypatch, local.url)
    local.rotas[_FATURAS] = "prende"
    r, dur = _cronometrado("charge.refunded", _reembolso(100_000))
    assert r.status_code == 503 and dur < TETO_ROTA, (r.status_code, dur)
    _desistiu_no_prazo(duracoes, _FATURAS)
    assert _so(local.pedidos, _FATURAS) == [
        {"payment[type]": ["payment_intent"], "payment[payment_intent]": ["pi_1"], "limit": ["100"]}]


def test_webhook_disputa_perdida_pela_rede_estorna(local, monkeypatch, webhook):
    """T3, positivo. Cobrança → fatura → cobranças → disputas, com objetos reais do SDK: 200 e comissão estornada.
    A cobrança é lida na versão de API do SDK (a troca de cliente não muda a versão)."""
    _na_rede(monkeypatch, local.url)
    com = _paga(_afiliado())
    _stripe_da_fatura(local, com.fatura, disputed=True)
    r = _enviar("charge.dispute.closed", _disputa())
    assert r.status_code == 200, r.text
    assert _status(com)["status"] == "reversed"
    i = [p for p, _ in local.pedidos].index("/v1/charges/ch_x")
    assert local.versoes[i] == stripe.api_version


def test_webhook_disputa_tempo_total_das_cinco_chamadas(local, monkeypatch, webhook):
    """T8, tempo total: a Stripe lenta mas respondendo (0,4 s por chamada) nas cinco leituras em série. Conclui,
    com exatamente um pedido por chamada, e o tempo somado na Stripe fica entre 5 × 0,4 s e 5 × PRAZO.
    Não mede o prazo (nada estoura, por construção): quem mede é T1/T2. Aqui a prova do teto é a contagem."""
    duracoes = _na_rede(monkeypatch, local.url)
    com = _paga(_afiliado())
    _stripe_da_fatura(local, com.fatura, disputed=True)
    local.atraso = 0.4
    r, dur = _cronometrado("charge.dispute.closed", _disputa())
    assert r.status_code == 200, r.text
    assert sorted(c for c, _ in duracoes) == sorted(
        ["/v1/charges/ch_x", _FATURAS, _FATURAS, "/v1/payment_intents/pi_1", "/v1/disputes"]), duracoes
    assert len(local.pedidos) == 5
    assert 5 * 0.4 <= sum(s for _, s in duracoes) < 5 * PRAZO and dur < TETO_ROTA, (duracoes, dur)
    assert _status(com)["status"] == "reversed"


def test_webhook_reentrega_depois_do_prazo_nao_duplica(local, monkeypatch, webhook):
    """T9, recuperação. Reembolso parcial com a Stripe presa na 3ª leitura: 503 e nada gravado. A reentrega do
    MESMO evento, com a Stripe de volta, abre 1 pendência e 1 alerta; uma 3ª entrega não muda nada."""
    _na_rede(monkeypatch, local.url)
    com = _paga(_afiliado())
    _stripe_da_fatura(local, com.fatura, amount_refunded=30_000)
    local.rotas["/v1/payment_intents/pi_1"] = "prende"
    evento = f"evt_t9_{com.id}"
    assert _enviar("charge.refunded", _reembolso(30_000), evento=evento).status_code == 503
    assert (_revisoes(com.fatura), _marcada(com), webhook) == ([], False, [])

    local.rotas["/v1/payment_intents/pi_1"] = _intent("pi_1", amount_refunded=30_000)
    for _ in range(2):
        assert _enviar("charge.refunded", _reembolso(30_000), evento=evento).status_code == 200
        assert _revisoes(com.fatura) == [{"status": "open", "refunded_cents": 30_000, "avisada": True}]
        assert len(webhook) == 1 and "reembolso parcial" in webhook[0]
    assert _status(com)["status"] == "pending"


def test_webhook_reentrega_concorrente_nao_duplica(local, monkeypatch, webhook):
    """T10, recuperação. A Stripe desiste de esperar e reentrega enquanto a 1ª entrega ainda roda (a mesma
    Stripe lenta do T8): as duas respondem 200, com 1 pendência e 1 alerta.
    O alerta espera as DUAS entregas passarem por `reivindicar` (até 10 s): a 1ª não marca o aviso antes de a 2ª
    tentar o claim, então só o claim segura o 2º alerta. Sem isso, a janela dependia do relógio (medido: com uma
    espera que só olhava `registrar_reembolso`, a mutação "sem claim" passava)."""
    _na_rede(monkeypatch, local.url)
    com = _paga(_afiliado())
    _stripe_da_fatura(local, com.fatura, amount_refunded=30_000)
    local.atraso = 0.4
    claims, as_duas = [], threading.Event()
    real = modulo_reembolso.reivindicar

    def reivindicar(*a, **k):
        r = real(*a, **k)
        claims.append(r is not None)
        if len(claims) == 2:
            as_duas.set()
        return r

    def envia(msg):
        as_duas.wait(10)
        webhook.append(msg)
        return True

    monkeypatch.setattr(modulo_reembolso, "reivindicar", reivindicar)
    monkeypatch.setattr(admin_notify, "_send", envia)
    evento, respostas = f"evt_t10_{com.id}", {}

    def entrega(n):
        try:
            respostas[n] = _enviar("charge.refunded", _reembolso(30_000), evento=evento).status_code
        except Exception as exc:  # noqa: BLE001 — vira a mensagem da asserção
            respostas[n] = repr(exc)

    linhas = [threading.Thread(target=entrega, args=(n,), daemon=True) for n in range(2)]
    for t in linhas:
        t.start()
    for t in linhas:
        t.join(20)
    assert not any(t.is_alive() for t in linhas), f"entrega presa depois de 20 s: {respostas}"
    assert respostas == {0: 200, 1: 200}, respostas
    assert _revisoes(com.fatura) == [{"status": "open", "refunded_cents": 30_000, "avisada": True}]
    assert len(webhook) == 1, webhook
    assert sorted(claims) == [False, True], f"as duas entregas tinham de tentar o claim: {claims}"
