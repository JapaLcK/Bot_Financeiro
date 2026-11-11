"""#1499: cada chamada à Stripe da conferência do afiliado e do e-book desiste no prazo, sem retentativa.

SDK real (`stripe.StripeClient`) e Stripe LOCAL na rede: só o endereço é trocado (`base_addresses`); prazo e
retentativa são os da produção, com `PRAZO_STRIPE_S` em 0,5 s. Sem o fixture `mundo`, que troca a API de
módulo pelo adaptador: aqui quem fala é o cliente de verdade.

Controles (rodados, ver o relato do PR):
  · `cliente_stripe` sem `http_client=` (80 s do SDK)        → as threads seguem vivas: desiste/QR/e-book vermelhos;
  · `max_network_retries=2`                                  → 3 pedidos por chamada: desiste/e-book vermelhos;
  · `Dispute.list` de volta à API de módulo                   → disputa vermelho;
  · `list_line_items` de volta à API de módulo                → e-book vermelho;
  · `VAGAS_CONFERENCIA = 5`                                   → vagas vermelho.
  Positivo: a Stripe que responde devolve o mesmo resultado de hoje, e o e-book entrega.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import stripe

import core.admin_dashboard as admin_dashboard
import core.services.ebook_entrega as ebook_entrega
from core.services import email_service as es
from core.services.afiliado_reembolso import devolvido_na_stripe
from db.ebook_entregas import registrar
from test_afiliado_exclusao import _saque_com_pix
from test_afiliado_pagantes import _admin
from test_afiliado_reembolso import _afiliado, _logs, _paga  # noqa: F401
from test_ebook_entrega import _PRECO, _conta, _sql, _url

PRAZO = 0.5
# Capturado na coleta, antes do `_block_outbound_network` autouse, que troca `requests.Session.request`.
_SESSAO_REAL = requests.Session.request


def _so_local(self, method, url, *a, **k):
    """Libera a sessão só para a Stripe local: qualquer outro destino segue bloqueado."""
    if not url.startswith("http://127.0.0.1:"):
        raise RuntimeError(f"fora da Stripe local: {url.split('?')[0]}")
    return _SESSAO_REAL(self, method, url, *a, **k)


@pytest.fixture
def pendurada():
    """Aceita a conexão e nunca responde. `conexoes` conta as tentativas."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(16)
    s.settimeout(0.1)
    p = SimpleNamespace(url=f"http://127.0.0.1:{s.getsockname()[1]}", conexoes=[], fim=threading.Event())

    def aceita():
        while not p.fim.is_set():
            try:
                p.conexoes.append(s.accept()[0])
            except OSError:
                continue

    threading.Thread(target=aceita, daemon=True).start()
    yield p
    p.fim.set()
    for c in p.conexoes:
        c.close()
    s.close()


@pytest.fixture
def local():
    """Stripe local: `rotas[caminho]` é o JSON da resposta, ou "prende" (segura sem responder)."""
    rotas, pedidos, solta = {}, [], threading.Event()

    class Stripe(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            pedidos.append((u.path, parse_qs(u.query)))
            corpo = rotas.get(u.path)
            if corpo == "prende":
                solta.wait(30)
                return
            status = 200 if corpo is not None else 404
            dados = json.dumps(corpo or {"error": {"type": "invalid_request_error", "message": "?"}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stripe)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{srv.server_port}", rotas=rotas, pedidos=pedidos)
    solta.set()
    srv.shutdown()
    srv.server_close()


def _na_rede(monkeypatch, url):
    """Só o endereço muda: o resto dos argumentos é o que a produção passa."""
    real = getattr(stripe.StripeClient, "_real", stripe.StripeClient)

    def cliente(*a, **k):
        return real(*a, **k, base_addresses={"api": url})

    cliente._real = real
    monkeypatch.setattr(stripe, "StripeClient", cliente)
    monkeypatch.setattr(requests.Session, "request", _so_local)
    monkeypatch.setattr(ebook_entrega, "PRAZO_STRIPE_S", PRAZO, raising=False)


def _em_thread(funcao, limite=5):
    """Roda `funcao` numa thread e espera `limite` s: a que segue viva é a que ficou presa na Stripe."""
    fim = {}

    def roda():
        t = time.monotonic()
        try:
            fim["valor"] = funcao()
        except Exception as exc:  # noqa: BLE001
            fim["erro"] = exc
        fim["s"] = time.monotonic() - t

    th = threading.Thread(target=roda, daemon=True)
    th.start()
    th.join(limite)
    assert not th.is_alive(), f"presa na Stripe por mais de {limite} s"
    return fim


def _lista(url, *itens):
    return {"object": "list", "url": url, "has_more": False, "data": list(itens)}


def _paga_por(pi):
    return {"object": "invoice_payment", "status": "paid", "payment": {"type": "payment_intent", "payment_intent": pi}}


def _intent(pi, **cobranca):
    return {"object": "payment_intent", "id": pi,
            "latest_charge": {"object": "charge", "id": f"ch_{pi}", "amount": 100_000, "amount_refunded": 0,
                              "disputed": False, **cobranca}}


def _so(pedidos, caminho):
    return [q for p, q in pedidos if p == caminho]


# ── Conferência ──────────────────────────────────────────────────────────────

def test_cobrancas_da_fatura_desiste_no_prazo(pendurada, monkeypatch):
    _na_rede(monkeypatch, pendurada.url)
    fim = _em_thread(lambda: list(ebook_entrega.cobrancas_da_fatura("in_x", "sk_test_x")))
    assert isinstance(fim.get("erro"), stripe.APIConnectionError), fim
    assert fim["s"] < 2, fim["s"]
    assert len(pendurada.conexoes) == 1, str(fim["erro"])[-200:]  # sem retentativa


def test_disputa_desiste_no_prazo(local, monkeypatch):
    _na_rede(monkeypatch, local.url)
    local.rotas |= {"/v1/invoice_payments": _lista("/v1/invoice_payments", _paga_por("pi_1")),
                    "/v1/payment_intents/pi_1": _intent("pi_1", disputed=True),
                    "/v1/disputes": "prende"}
    fim = _em_thread(lambda: devolvido_na_stripe("in_x", "sk_test_x"))
    assert isinstance(fim.get("erro"), stripe.APIConnectionError), fim
    assert fim["s"] < 2, fim["s"]
    assert _so(local.pedidos, "/v1/disputes") == [{"charge": ["ch_pi_1"], "limit": ["10"]}]


def test_stripe_que_responde_funciona(local, monkeypatch):
    """Positivo, com objetos reais do SDK: o resultado é o de hoje, e a paginação e o `expand` saem na query."""
    _na_rede(monkeypatch, local.url)
    local.rotas |= {
        "/v1/invoice_payments": _lista("/v1/invoice_payments", _paga_por("pi_1"), _paga_por("pi_2")),
        "/v1/payment_intents/pi_1": _intent("pi_1", amount_refunded=30_000),
        "/v1/payment_intents/pi_2": _intent("pi_2", amount=50_000, disputed=True),
        "/v1/disputes": _lista("/v1/disputes", {"object": "dispute", "status": "lost", "amount": 50_000}),
    }
    fim = _em_thread(lambda: devolvido_na_stripe("in_x", "sk_test_x"))
    tipo, devolvido, cobrancas = fim["valor"]
    assert (tipo, devolvido, [c["id"] for c in cobrancas]) == ("partial", 80_000, ["ch_pi_1", "ch_pi_2"])
    assert _so(local.pedidos, "/v1/invoice_payments") == [{"invoice": ["in_x"], "limit": ["100"]}]
    assert _so(local.pedidos, "/v1/payment_intents/pi_1") == [{"expand[0]": ["latest_charge"]}]


def test_qr_solta_a_vaga_com_stripe_pendurada(pendurada, monkeypatch, _logs):  # noqa: F811
    """Sem prazo por chamada, o 503 só sairia no prazo total (3 s), e a vaga seguiria presa por minutos."""
    _na_rede(monkeypatch, pendurada.url)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(admin_dashboard, "PRAZO_CONFERENCIA_S", 3)
    adm = _admin(monkeypatch)
    c = _afiliado()
    _paga(c), _paga(c, 70)
    saque = _saque_com_pix(c)
    t0 = time.monotonic()
    r = adm.get(f"/admin/api/affiliates/payouts/{saque}/pix")
    dur = time.monotonic() - t0
    assert r.status_code == 503 and r.json()["detail"].startswith(admin_dashboard._STRIPE_FORA), r.text
    assert dur < 2, dur
    fim = time.monotonic() + 2
    while admin_dashboard._VAGAS_CONFERENCIA._value < admin_dashboard.VAGAS_CONFERENCIA and time.monotonic() < fim:
        time.sleep(0.02)
    assert admin_dashboard._VAGAS_CONFERENCIA._value == admin_dashboard.VAGAS_CONFERENCIA


def test_vagas_conferencia_fixadas():
    """A mutação viva da #1507: o teste de rajada lia a constante e acompanhava a mudança."""
    assert admin_dashboard.VAGAS_CONFERENCIA == 4
    assert admin_dashboard._VAGAS_CONFERENCIA._initial_value == 4
    assert admin_dashboard._EXECUTOR_CONFERENCIA._max_workers == 4


# ── E-book (decisão do dono: as duas chamadas próprias dele também) ──────────

def test_ebook_desiste_no_prazo_por_linha_nao_envia_e_tenta_de_novo(local, monkeypatch, _logs):  # noqa: F811
    """Stripe que não responde: cada linha desiste no prazo, nada sai, a linha segue aberta com o claim, e o
    próximo ciclo depois do claim vencido entrega com a Stripe de volta (positivo)."""
    _na_rede(monkeypatch, local.url)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_job")
    enviados = []
    monkeypatch.setattr(es, "send_email", lambda to, *a, **k: enviados.append(to) or True)
    contas = {}
    for n in range(2):
        uid, email = _conta(senha="hash")
        sid = f"cs_prazo_{uid}"
        registrar(uid, sid, [(_PRECO, _url(uid))])
        contas[sid] = (uid, email)
        local.rotas[f"/v1/checkout/sessions/{sid}/line_items"] = "prende"

    fim = _em_thread(ebook_entrega.entregar_pendentes)
    assert fim.get("valor") == 0 and fim["s"] < 3, fim
    for sid, (uid, email) in contas.items():
        assert len(_so(local.pedidos, f"/v1/checkout/sessions/{sid}/line_items")) == 1  # sem retentativa
        (linha,) = _sql("select fechada_em, reivindicada_ate > now() as presa, tentativas from ebook_entregas"
                        " where user_id = %s", (uid,))
        assert (linha["fechada_em"], linha["presa"], linha["tentativas"]) == (None, True, 1)
        (log,) = _sql("select details->>'erro' as erro from system_event_logs"
                      " where event_type = 'ebook_entrega_falhou' and user_id = %s", (uid,))
        assert log["erro"] == "APIConnectionError"
    assert [e for e in enviados if e in {email for _, email in contas.values()}] == []

    for sid, (uid, _) in contas.items():  # a Stripe volta, o claim vence
        local.rotas |= {
            f"/v1/checkout/sessions/{sid}/line_items": _lista(f"/v1/checkout/sessions/{sid}/line_items", {
                "object": "item", "id": "li_1", "description": "Caderno", "price": {"object": "price", "id": _PRECO}}),
            f"/v1/checkout/sessions/{sid}": {"object": "checkout.session", "id": sid, "invoice": f"in_{sid}"},
            "/v1/invoice_payments": _lista("/v1/invoice_payments", _paga_por(f"pi_{sid}")),
            f"/v1/payment_intents/pi_{sid}": _intent(f"pi_{sid}"),
        }
        _sql("update ebook_entregas set reivindicada_ate = now() - interval '1 minute' where user_id = %s", (uid,))
    _em_thread(ebook_entrega.entregar_pendentes)
    assert sorted(e for e in enviados if e in {email for _, email in contas.values()}) == sorted(
        email for _, email in contas.values())
    assert _so(local.pedidos, f"/v1/checkout/sessions/{next(iter(contas))}/line_items")[-1] == {"limit": ["100"]}
