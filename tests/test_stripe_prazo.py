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
  #1533 (a passada do e-book):
  · sem o `continue` da Stripe fora                          → T5 (e `test_j8` do Pix) vermelhos;
  · sem o orçamento `PRAZO_PASSADA_S`                        → T11 vermelho;
  · qualquer erro conta como Stripe fora / 403 conta como queda → T6 vermelho;
  · só `APIConnectionError` conta como queda                  → T5 por status (401, 429, 500, 409) vermelho;
  · `stripe_fora = (...)` sem o `or`                          → `test_j9` do Pix vermelho;
  · `PRAZO_STRIPE_S`/`PRAZO_PASSADA_S` trocados               → constantes vermelho.
  Positivo: a Stripe que responde devolve o mesmo resultado de hoje, e o e-book entrega.
  #1533 (o script de backfill e o resumo de cobrança do admin):
  · `_datas_de_reembolso` de volta a `stripe.Refund.list` (módulo)      → script/reembolso vermelho;
  · `rodar` de volta a `stripe.Invoice.list` (módulo)                  → script/faturas vermelho;
  · `_fetch_stripe_billing_sync` de volta a `stripe.Subscription.list`  → admin/desiste vermelho (e o positivo).
  · `_valor_do_plano` de volta a `stripe.Subscription.retrieve`          → script/assinatura vermelho;
  · `linhas_da_fatura(fatura, cliente)` sem o `cliente=` (global, sem chave) → script/has_more vermelhos;
  Positivo: a Stripe que responde devolve os mesmos dados nos três.
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
    """Stripe local: `rotas[caminho]` é o JSON da resposta, "prende" (segura sem responder) ou um status HTTP de
    erro (int, com o corpo de erro da Stripe: o SDK escolhe a classe pelo status). `atraso` (s)
    atrasa só as rotas configuradas: o 404 das sobras de outros testes na tabela compartilhada segue imediato.
    `versoes` guarda o `Stripe-Version` de cada pedido, na ordem de `pedidos`."""
    rotas, pedidos, versoes, solta = {}, [], [], threading.Event()
    s = SimpleNamespace(rotas=rotas, pedidos=pedidos, versoes=versoes, atraso=0.0)

    class Stripe(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            pedidos.append((u.path, parse_qs(u.query)))
            versoes.append(self.headers.get("Stripe-Version"))
            corpo = rotas.get(u.path)
            if corpo == "prende":
                solta.wait(30)
                return
            if corpo is not None and s.atraso:
                time.sleep(s.atraso)
            status = corpo if isinstance(corpo, int) else 200 if corpo is not None else 404
            if status != 200:
                corpo = {"error": {"type": "invalid_request_error", "message": "?"}}
            dados = json.dumps(corpo).encode()
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
    s.url = f"http://127.0.0.1:{srv.server_port}"
    yield s
    solta.set()
    srv.shutdown()
    srv.server_close()


def _na_rede(monkeypatch, url):
    """Só o endereço muda: o resto dos argumentos é o que a produção passa. Devolve a lista de (caminho, segundos)
    de cada pedido HTTP do cliente: o tempo da chamada à Stripe, sem o banco da rota em volta (#1533)."""
    real = getattr(stripe.StripeClient, "_real", stripe.StripeClient)

    def cliente(*a, **k):
        return real(*a, **k, base_addresses={"api": url})

    cliente._real = real
    duracoes = []

    def medido(self, method, url, *a, **k):
        t = time.monotonic()
        try:
            return _so_local(self, method, url, *a, **k)
        finally:
            duracoes.append((urlparse(url).path, time.monotonic() - t))

    monkeypatch.setattr(stripe, "StripeClient", cliente)
    monkeypatch.setattr(requests.Session, "request", medido)
    monkeypatch.setattr(ebook_entrega, "PRAZO_STRIPE_S", PRAZO, raising=False)
    return duracoes


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
# #1533: a passada inteira tem tempo limitado. Stripe fora → as linhas de cartão seguintes nem são reivindicadas;
# Stripe lenta → o orçamento `PRAZO_PASSADA_S` para a passada, e o resto vai para o próximo ciclo sem claim.

def _ebook(local, monkeypatch, n):
    """`n` linhas de cartão, na ordem de `abertas()`, e os e-mails espiados. Devolve [(sid, uid, email)]."""
    _na_rede(monkeypatch, local.url)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_job")
    enviados = []
    monkeypatch.setattr(es, "send_email", lambda to, *a, **k: enviados.append(to) or True)
    contas = []
    for _ in range(n):
        uid, email = _conta(senha="hash")
        sid = f"cs_prazo_{uid}"
        registrar(uid, sid, [(_PRECO, _url(uid))])
        contas.append((sid, uid, email))
    return contas, enviados


def _responde(local, sid):
    """A Stripe que responde para a compra `sid`: as 4 chamadas da linha de cartão."""
    local.rotas |= {
        f"/v1/checkout/sessions/{sid}/line_items": _lista(f"/v1/checkout/sessions/{sid}/line_items", {
            "object": "item", "id": "li_1", "description": "Caderno", "price": {"object": "price", "id": _PRECO}}),
        f"/v1/checkout/sessions/{sid}": {"object": "checkout.session", "id": sid, "invoice": "in_eb"},
        "/v1/invoice_payments": _lista("/v1/invoice_payments", _paga_por("pi_eb")),
        "/v1/payment_intents/pi_eb": _intent("pi_eb"),
    }


def _linha(uid):
    (linha,) = _sql("select fechada_em, reivindicada_ate, reivindicada_ate > now() as presa, tentativas"
                    " from ebook_entregas where user_id = %s", (uid,))
    return linha


def _falhas(uid):
    return _sql("select details->>'erro' as erro from system_event_logs"
                " where event_type = 'ebook_entrega_falhou' and user_id = %s", (uid,))


def _para(enviados, contas):
    return sorted(e for e in enviados if e in {email for _, _, email in contas})


def test_ebook_para_o_cartao_na_queda_e_tenta_de_novo(local, monkeypatch, _logs):  # noqa: F811
    """T5. Stripe que não responde: a 1ª linha desiste no prazo e as outras de cartão nem são consultadas (a
    passada fica em 1 × PRAZO, qualquer que seja o número de linhas). Recuperação: com a Stripe de volta e o
    claim vencido, um e-mail por conta, e uma passada a mais não repete nenhum."""
    contas, enviados = _ebook(local, monkeypatch, 3)
    (sid1, uid1, _), *resto = contas
    local.rotas[f"/v1/checkout/sessions/{sid1}/line_items"] = "prende"

    fim = _em_thread(ebook_entrega.entregar_pendentes)
    assert fim.get("valor") == 0 and fim["s"] < PRAZO + 0.5, fim
    assert len(_so(local.pedidos, f"/v1/checkout/sessions/{sid1}/line_items")) == 1  # sem retentativa
    linha = _linha(uid1)
    assert (linha["fechada_em"], linha["presa"], linha["tentativas"]) == (None, True, 1)
    assert [x["erro"] for x in _falhas(uid1)] == ["APIConnectionError"]
    for sid, uid, _ in resto:
        assert [p for p, _ in local.pedidos if sid in p] == []
        linha = _linha(uid)
        assert (linha["reivindicada_ate"], linha["tentativas"], _falhas(uid)) == (None, 0, [])
    assert _para(enviados, contas) == []

    for sid, _, _ in contas:  # a Stripe volta, o claim da 1ª vence
        _responde(local, sid)
    _sql("update ebook_entregas set reivindicada_ate = now() - interval '1 minute' where user_id = %s", (uid1,))
    _em_thread(ebook_entrega.entregar_pendentes)
    _em_thread(ebook_entrega.entregar_pendentes)
    assert _para(enviados, contas) == sorted(email for _, _, email in contas)
    assert _so(local.pedidos, f"/v1/checkout/sessions/{sid1}/line_items")[-1] == {"limit": ["100"]}


@pytest.mark.parametrize("status", [400, 403, 404])
def test_ebook_erro_da_linha_nao_para_a_passada(local, monkeypatch, _logs, status):  # noqa: F811
    """T6, positivo: 400/403/404 são da linha (`InvalidRequestError`/`PermissionError`: a compra, não a Stripe).
    A 2ª linha sai na mesma passada."""
    contas, enviados = _ebook(local, monkeypatch, 2)
    (sid1, uid1, _), (sid2, _, email2) = contas
    local.rotas[f"/v1/checkout/sessions/{sid1}/line_items"] = status
    _responde(local, sid2)
    _em_thread(ebook_entrega.entregar_pendentes)
    assert [x["erro"] for x in _falhas(uid1)] == ["PermissionError" if status == 403 else "InvalidRequestError"]
    assert _para(enviados, contas) == [email2]


@pytest.mark.parametrize("status,erro", [(401, "AuthenticationError"), (429, "RateLimitError"),
                                         (500, "APIError"), (409, "APIError")])
def test_ebook_queda_da_stripe_para_o_cartao(local, monkeypatch, _logs, status, erro):  # noqa: F811
    """T5 por status: chave recusada, limite, 5xx e o resto que o SDK chama de `APIError` (409 incluso) são queda.
    A 2ª linha de cartão nem é consultada nem reivindicada."""
    contas, enviados = _ebook(local, monkeypatch, 2)
    (sid1, uid1, _), (sid2, uid2, _) = contas
    local.rotas[f"/v1/checkout/sessions/{sid1}/line_items"] = status
    _responde(local, sid2)
    _em_thread(ebook_entrega.entregar_pendentes)
    assert [x["erro"] for x in _falhas(uid1)] == [erro]
    assert [p for p, _ in local.pedidos if sid2 in p] == []
    assert (_linha(uid2)["reivindicada_ate"], _para(enviados, contas)) == (None, [])


def test_ebook_constantes_fixadas():
    """As mutações que sobreviviam: 16 s por chamada, 600 s por passada."""
    assert (ebook_entrega.PRAZO_STRIPE_S, ebook_entrega.PRAZO_PASSADA_S) == (8, 60)


def test_ebook_orcamento_da_passada(local, monkeypatch, _logs):  # noqa: F811
    """T11, tempo total com a Stripe lenta mas respondendo: 4 chamadas de 0,4 s por linha. Passado o orçamento,
    a linha em curso termina e as seguintes ficam sem pedido nem claim; a passada seguinte as entrega."""
    monkeypatch.setattr(ebook_entrega, "PRAZO_PASSADA_S", 1)
    contas, enviados = _ebook(local, monkeypatch, 3)
    for sid, _, _ in contas:
        _responde(local, sid)
    local.atraso = 0.4
    (sid1, _, email1), *resto = contas

    fim = _em_thread(ebook_entrega.entregar_pendentes)
    assert fim["s"] < 1 + 4 * PRAZO + 0.5, fim
    assert _para(enviados, contas) == [email1]
    for sid, uid, _ in resto:
        assert [p for p, _ in local.pedidos if sid in p] == []
        assert (_linha(uid)["reivindicada_ate"], _linha(uid)["tentativas"]) == (None, 0)

    local.atraso = 0.0
    _em_thread(ebook_entrega.entregar_pendentes)
    assert _para(enviados, contas) == sorted(email for _, _, email in contas)  # um e-mail cada


# ── #1533: script de backfill e resumo de cobrança do admin ──────────────────

def _script(monkeypatch):
    import backfill_afiliado_pagamentos as modulo
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(modulo, "_passar_comissoes", lambda *a: None)
    monkeypatch.setattr(modulo, "_clientes", lambda: [{"cliente": "cus_x", "uid": 1}])
    return modulo


def test_script_reembolso_desiste_no_prazo(pendurada, monkeypatch):
    modulo = _script(monkeypatch)
    _na_rede(monkeypatch, pendurada.url)
    fim = _em_thread(lambda: modulo._datas_de_reembolso("sk_test_x", {"id": "ch_1"}))
    assert isinstance(fim.get("erro"), stripe.APIConnectionError), fim
    assert fim["s"] < 2, fim["s"]
    assert len(pendurada.conexoes) == 1  # sem retentativa


def test_script_faturas_desistem_no_prazo_e_a_rodada_acusa_falha(pendurada, monkeypatch):
    modulo = _script(monkeypatch)
    _na_rede(monkeypatch, pendurada.url)
    fim = _em_thread(modulo.rodar)
    n = fim["valor"]
    assert (n["clientes_com_falha"], n["erro_APIConnectionError"]) == (1, 1), fim  # main() sai com 1
    assert fim["s"] < 2, fim["s"]
    assert len(pendurada.conexoes) == 1


def test_script_assinatura_desiste_no_prazo(pendurada, monkeypatch):
    modulo = _script(monkeypatch)
    _na_rede(monkeypatch, pendurada.url)
    fatura = {"id": "in_1", "subscription": "sub_1"}
    fim = _em_thread(lambda: modulo._valor_do_plano(
        ebook_entrega.cliente_stripe("sk_test_x"), fatura))
    assert isinstance(fim.get("erro"), stripe.APIConnectionError), fim
    assert fim["s"] < 2, fim["s"]
    assert len(pendurada.conexoes) == 1


def test_script_com_stripe_que_responde_le_o_mesmo(local, monkeypatch):
    modulo = _script(monkeypatch)
    _na_rede(monkeypatch, local.url)
    local.rotas |= {"/v1/refunds": _lista("/v1/refunds", {"object": "refund", "created": 1_790_000_001}),
                    "/v1/invoices": _lista("/v1/invoices", {"object": "invoice", "id": "in_1"})}
    lidas = []
    monkeypatch.setattr(modulo, "_fatura", lambda s, f, *a: lidas.append(f["id"]))
    assert modulo._datas_de_reembolso("sk_test_x", {"id": "ch_1"}) == [1_790_000_001]
    n = modulo.rodar()
    assert (lidas, n["clientes"], n["clientes_com_falha"]) == (["in_1"], 1, 0)
    assert _so(local.pedidos, "/v1/refunds") == [{"charge": ["ch_1"], "limit": ["100"]}]
    assert _so(local.pedidos, "/v1/invoices") == [{"customer": ["cus_x"], "status": ["paid"], "limit": ["100"]}]


def test_resumo_de_cobranca_do_admin_desiste_no_prazo(pendurada, monkeypatch):
    _na_rede(monkeypatch, pendurada.url)
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "sk_test_x")
    fim = _em_thread(admin_dashboard._fetch_stripe_billing_sync)
    assert fim["valor"] == {"available": False, "reason": "APIConnectionError"}, fim
    assert fim["s"] < 2, fim["s"]
    assert len(pendurada.conexoes) == 1


def test_resumo_de_cobranca_do_admin_com_stripe_que_responde(local, monkeypatch):
    _na_rede(monkeypatch, local.url)
    monkeypatch.setattr(admin_dashboard, "STRIPE_SECRET_KEY", "sk_test_x")
    preco = {"object": "price", "unit_amount": 1990, "recurring": {"interval": "month", "interval_count": 1}}
    sub = {"object": "subscription", "status": "active", "items": {"object": "list", "data": [{"price": preco}]}}
    local.rotas["/v1/subscriptions"] = _lista("/v1/subscriptions", sub)
    r = admin_dashboard._fetch_stripe_billing_sync()
    assert (r["available"], r["subscriptions"]["active"], r["mrr"]) == (True, 1, 19.9), r
    assert _so(local.pedidos, "/v1/subscriptions") == [{"status": ["all"], "limit": ["100"]}]


def _fatura_com_mais_linhas(local, linhas="prende"):
    """Assinatura com um extra na metadata e fatura cujas linhas embutidas estão truncadas (`has_more`)."""
    local.rotas["/v1/subscriptions/sub_1"] = {"object": "subscription", "id": "sub_1",
                                              "metadata": {"ebook_price": "price_x"}}
    local.rotas["/v1/invoices/in_1/lines"] = linhas
    return {"id": "in_1", "subscription": "sub_1", "amount_paid": 10_000,
            "lines": {"data": [], "has_more": True}}


def test_script_fatura_com_has_more_le_as_linhas_pelo_cliente(local, monkeypatch):
    """Sem `stripe.api_key` global, as linhas que faltam saem pelo cliente do script (positivo)."""
    modulo = _script(monkeypatch)
    _na_rede(monkeypatch, local.url)
    extra = {"object": "line_item", "amount": 4_000, "pricing": {"price_details": {"price": "price_x"}}}
    fatura = _fatura_com_mais_linhas(local, _lista("/v1/invoices/in_1/lines", extra))
    assert modulo._valor_do_plano(ebook_entrega.cliente_stripe("sk_test_x"), fatura) == 6_000
    assert _so(local.pedidos, "/v1/invoices/in_1/lines") == [{"limit": ["100"]}]


def test_script_linhas_da_fatura_desistem_no_prazo(local, monkeypatch):
    modulo = _script(monkeypatch)
    _na_rede(monkeypatch, local.url)
    fatura = _fatura_com_mais_linhas(local)
    fim = _em_thread(lambda: modulo._valor_do_plano(ebook_entrega.cliente_stripe("sk_test_x"), fatura))
    assert isinstance(fim.get("erro"), stripe.APIConnectionError), fim
    assert fim["s"] < 2, fim["s"]
    assert len(_so(local.pedidos, "/v1/invoices/in_1/lines")) == 1  # sem retentativa
