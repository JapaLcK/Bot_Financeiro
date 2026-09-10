"""As três rotas do Pix pelo HTTP de verdade (§8.1, §13.6).

`TestClient` e não chamada direta ao handler: o que se mede aqui é o contrato
que o Asaas e o navegador veem — status, header, isenção de CSRF —, e nada disso
existe quando se chama a função.

**A escada do webhook é a parte que não pode ceder**, e cada degrau tem um
motivo diferente:

  * sem token configurado → **503**, não 401. Um webhook que chega antes de a env
    existir é problema NOSSO, e 401 faria o Asaas pausar a fila depois de 15
    tentativas por causa de um deploy incompleto;
  * token errado → **401**, com rastro e sem corpo;
  * corpo malformado → **400** (o Asaas não reenvia o que ele mesmo mandou torto);
  * duplicata → **200 sem trabalho**.

CONTROLES NEGATIVOS MEDIDOS:

  * troque o 503 do token ausente por 401 → `test_webhook_sem_token_configurado_e_503`
    vermelho;
  * troque `constant_time_eq` por `==` → nenhum teste fica vermelho (é timing, e
    a suíte não mede tempo). **É a cegueira declarada deste arquivo**, e por
    isso a comparação usa a função do repositório em vez de `==` na revisão;
  * tire o `user_id` do `where` de `buscar_por_public_token` →
    `test_poll_de_token_alheio_e_404` vermelho, e é vazamento entre contas.

POSITIVO do grupo: `test_webhook_valido_grava_e_responde_200`. Sem ele, um
handler que responde 401 para tudo passaria em todos os negativos.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta, garantir_system_event_logs
from db.connection import get_conn
from db.pix_charges import criar_cobranca

client = TestClient(dashboard.app)
TOKEN = "token-de-teste-do-asaas"


def _csrf() -> dict[str, str]:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def _corpo(event_id: str) -> dict:
    return {"id": event_id, "event": "PAYMENT_RECEIVED",
            "payment": {"id": "pay_x", "externalReference": "pix:1",
                        "value": 499.0, "status": "RECEIVED"}}


@pytest.fixture()
def sem_dreno(monkeypatch):
    """O dreno vira contador: o que este arquivo mede é o HANDLER.

    Ele é o `background_tasks` do §8.1 — o handler responde 200 e o trabalho sai
    de dentro da requisição. Contar as chamadas é o que prova o agendamento.
    """
    garantir_system_event_logs()
    chamadas = []
    monkeypatch.setattr(rotas, "drenar_evento", lambda eid: chamadas.append(eid),
                        raising=False)
    import core.services.pix_drain as dreno
    monkeypatch.setattr(dreno, "drenar_evento", lambda eid: chamadas.append(eid))
    return chamadas


# ── o webhook: a escada do §8.1 ──────────────────────────────────────────────

def test_webhook_sem_token_configurado_e_503(monkeypatch, sem_dreno):
    """DISCRIMINA. 503 e NÃO 401 — ver o cabeçalho.

    O evento também não é gravado: aceitar sem poder autenticar encheria a
    outbox com corpo de qualquer origem.
    """
    monkeypatch.delenv("ASAAS_WEBHOOK_TOKEN", raising=False)
    eid = f"evt_{uuid.uuid4().hex[:12]}"
    r = client.post("/billing/asaas/webhook", json=_corpo(eid))
    assert r.status_code == 503
    assert _evento(eid) is None and sem_dreno == []


@pytest.mark.parametrize("cabecalhos", [
    {},                                         # sem header nenhum
    {"asaas-access-token": ""},                 # header vazio
    {"asaas-access-token": "outro-token"},      # token de outra origem
    {"asaas-access-token": TOKEN + "x"},        # prefixo certo, valor errado
])
def test_webhook_com_token_errado_e_401(monkeypatch, sem_dreno, cabecalhos):
    """DISCRIMINA. Nada grava e nada dreno — 401 é fim de linha.

    O prefixo certo com sufixo errado está na tabela de propósito: é a forma que
    passaria numa comparação por `startswith`.
    """
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    eid = f"evt_{uuid.uuid4().hex[:12]}"
    r = client.post("/billing/asaas/webhook", json=_corpo(eid), headers=cabecalhos)
    assert r.status_code == 401
    assert _evento(eid) is None and sem_dreno == []


@pytest.mark.parametrize("corpo,tipo", [
    ("nao e json", "texto"),
    ('[1,2,3]', "lista no topo"),
    ('{"event": "PAYMENT_RECEIVED"}', "sem id de evento"),
    ('{"id": "evt_1"}', "sem tipo de evento"),
    ('{"id": "", "event": "PAYMENT_RECEIVED"}', "id vazio"),
])
def test_webhook_com_corpo_invalido_e_400(monkeypatch, sem_dreno, corpo, tipo):
    """400 para o que o Asaas mandou torto — ele não reenvia isso.

    `[1,2,3]` está na tabela porque topo válido em JSON mas não-objeto passava
    pelo parse e quebrava no `.get()` (é o #310 do webhook da Pluggy, mesma
    forma). 500 aqui viraria laço de reenvio.
    """
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    r = client.post("/billing/asaas/webhook", content=corpo,
                    headers={"asaas-access-token": TOKEN,
                             "content-type": "application/json"})
    assert r.status_code == 400, tipo
    assert sem_dreno == []


def test_webhook_valido_grava_e_responde_200(monkeypatch, sem_dreno):
    """POSITIVO do grupo. Grava CIFRADO, agenda o dreno e responde 200.

    A asserção do payload é sobre a coluna crua: `payload_enc` não pode conter o
    `externalReference` em texto (§13.3). Sem ela, um `registrar_evento` que
    parasse de cifrar passaria verde.
    """
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    eid = f"evt_{uuid.uuid4().hex[:12]}"
    r = client.post("/billing/asaas/webhook", json=_corpo(eid),
                    headers={"asaas-access-token": TOKEN})
    assert r.status_code == 200
    linha = _evento(eid)
    assert linha is not None and linha["event_type"] == "PAYMENT_RECEIVED"
    assert "pix:1" not in (linha["payload_enc"] or ""), "payload em texto puro"
    assert sem_dreno == [eid], "o dreno não foi agendado"


def test_webhook_duplicado_responde_200_sem_trabalho(monkeypatch, sem_dreno):
    """A reentrega do Asaas é normal e não pode custar trabalho nenhum.

    A segunda entrega responde 200 e **não agenda o dreno de novo** — quem
    decide é o `on conflict` do insert, não um `select` antes.
    """
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    eid = f"evt_{uuid.uuid4().hex[:12]}"
    cab = {"asaas-access-token": TOKEN}
    assert client.post("/billing/asaas/webhook", json=_corpo(eid), headers=cab).status_code == 200
    assert client.post("/billing/asaas/webhook", json=_corpo(eid), headers=cab).status_code == 200
    assert sem_dreno == [eid], f"a duplicata custou trabalho: {sem_dreno}"


def test_webhook_nao_precisa_de_csrf_e_o_checkout_precisa():
    """O par que a isenção cria, medido pelo COMPORTAMENTO e não pela constante.

    O webhook é server-to-server e passa sem token de CSRF (chega ao 503/401 do
    próprio handler). O checkout é do navegador e **tem** de ser barrado pelo
    middleware — 403 antes de qualquer lógica de venda.
    """
    client.cookies.clear()
    sem_csrf = client.post("/billing/asaas/webhook", json=_corpo("evt_csrf"))
    assert sem_csrf.status_code in (401, 503), sem_csrf.status_code

    checkout = client.post("/billing/pix/checkout",
                           json={"plan": "pro_max", "cpf_cnpj": "12345678901"})
    assert checkout.status_code == 403, "o checkout ganhou isenção de CSRF"


# ── o poll: `public_token` e só ele, filtrado por dono (§13.6) ───────────────

def test_poll_de_token_alheio_e_404(user_id, monkeypatch):
    """DISCRIMINA o isolamento (CLAUDE.md §0): o token do usuário A não abre a
    cobrança de A para o usuário B. Token chega pela URL, logo é `unsafe`."""
    conta(user_id, "free", None)
    linha = _cobranca(user_id)
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id",
                        lambda req: user_id + 10_000_000)
    r = client.get(f"/billing/pix/{linha['public_token']}")
    assert r.status_code == 404


def test_poll_pelo_id_do_asaas_e_404_e_o_qr_nao_sai(user_id, monkeypatch):
    """Caso 42b do §16, as duas metades.

    O `asaas_payment_id` **não** é chave de URL — buscar por ele tem de dar 404.
    E a resposta do poll não carrega o QR: ele sai do servidor uma vez só, na
    resposta do checkout.
    """
    from db.pix_charges_saga import attach_pagamento

    conta(user_id, "free", None)
    linha = _cobranca(user_id)
    attach_pagamento(linha["id"], f"pay_{uuid.uuid4().hex[:10]}",
                     qr_payload_enc="gAAAA-cifrado")
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)

    pelo_pagamento = client.get(f"/billing/pix/pay_qualquer")
    assert pelo_pagamento.status_code == 404

    ok = client.get(f"/billing/pix/{linha['public_token']}")
    assert ok.status_code == 200
    corpo = ok.json()
    assert corpo["status"] == "pending" and corpo["plan"] == "pro_max"
    assert not [c for c in corpo if "qr" in c.lower()], (
        f"o poll devolveu o instrumento de pagamento: {sorted(corpo)}"
    )


def test_poll_expoe_agendada_como_a_resposta_do_checkout(user_id, monkeypatch):
    """§0.7: duas montagens da MESMA resposta, e só uma tem o campo que decide.

    Não compara as chaves inteiras de propósito — os dois contratos divergem
    legitimamente (o poll não leva QR, o checkout não leva `status`). O que se
    trava aqui é o campo cuja ausência reproduz o bug original na tela.
    """
    conta(user_id, "free", None)
    linha = _cobranca(user_id)
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)

    corpo = client.get(f"/billing/pix/{linha['public_token']}").json()
    assert "starts_at" in corpo, f"contrato do poll mudou: {sorted(corpo)}"
    assert "agendada" in corpo, (
        f"o poll expõe `starts_at` sem `agendada`: {sorted(corpo)}"
    )
    # A FÓRMULA (`inicio > agora`) é medida em
    # test_pix_checkout.py::test_agendada_sai_da_data_e_nao_da_presenca_dela,
    # nas três datas. Aqui basta que o poll passe pela mesma função: cobrança
    # recém-criada tem `access_starts_at` nulo, e nulo não é agendado.
    assert corpo["agendada"] is False, f"agendada sem data: {corpo['agendada']}"


def _cobranca(uid: int) -> dict:
    return criar_cobranca(uid, public_token=uuid.uuid4().hex, plan="pro_max",
                          plan_stored="pro_max", price_cents=49900,
                          credit_cents=0, amount_cents=49900, duration_days=365)


def _evento(event_id: str) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from pix_webhook_events where event_id = %s", (event_id,))
        row = cur.fetchone()
    return dict(row) if row else None


# ── o monólito: Pix ANTES do Stripe, e a flag pela função ───────────────────

def test_subscription_reconhece_pix_antes_do_stripe(user_id, monkeypatch):
    """DISCRIMINA a ORDEM do §14 item 12, e a ordem é o conteúdo.

    Quem migrou do cartão fica com `stripe_customer_id` preenchido **para
    sempre**. Perguntando ao Stripe primeiro, a tela de quem paga no Pix
    mostraria "Trocar de plano" e chamaria `/billing/change-plan`, que não tem
    assinatura para trocar — é o PT4 do §16.1.

    O Stripe é MOCKADO para explodir: se o handler chegar até ele, o teste fica
    vermelho por exceção, o que prova que a consulta nem aconteceu.

    *Negativo: mova o bloco do Pix para depois do bloco do Stripe → vermelho.*
    """
    from datetime import datetime, timedelta, timezone

    from db.plan_grants import upsert_grant
    from utils_date import day_tz

    conta(user_id, "pro_max", None)
    _guardar_stripe_customer(user_id, "cus_antigo")
    inicio = datetime.now(timezone.utc) - timedelta(days=5)
    fim = inicio + timedelta(days=365)
    upsert_grant(user_id, "pix", str(_cobranca(user_id)["id"]), "pro_max",
                 inicio, fim, 1, last_event_id="e1")
    monkeypatch.setattr(dashboard, "_find_active_subscription",
                        lambda *a: pytest.fail("consultou o Stripe antes do Pix"))
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_x")

    corpo = _subscription(monkeypatch, user_id)
    assert corpo["active"] is True and corpo["gateway"] == "pix"
    assert corpo["plan"] == "pro", "o plano LEGADO vazou para a tela sem tradução"
    assert corpo["interval"] == "annual"
    # `day_tz`, e não `fim.date()`: o handler colapsa um INSTANTE em dia de
    # parede, e o `ends_at` que ele lê volta do Postgres no fuso da SESSÃO
    # (America/Sao_Paulo, via `align_process_tz`), não em UTC. Derivar o dia
    # aqui em UTC punha os dois lados em referenciais diferentes e o teste
    # ficava vermelho entre 00:00 e 03:00 UTC — `'2027-09-04' == '2027-09-05'`,
    # medido às 00:36 UTC. Um dia de tolerância esconderia o desalinhamento;
    # `day_tz` é o mesmo referencial do servidor, em qualquer hora do dia.
    assert corpo["current_period_end"] == day_tz(fim).isoformat()


def test_subscription_sem_grant_pix_segue_para_o_stripe(user_id, monkeypatch):
    """POSITIVO do par: sem grant Pix, o caminho do cartão continua inteiro.

    Sem ele, um bloco de Pix que devolvesse `active: False` para todo mundo
    passaria no teste acima e quebraria a tela de quem paga no cartão.
    """
    conta(user_id, "free", None)
    corpo = _subscription(monkeypatch, user_id)
    assert corpo == {"active": False}, corpo


def test_plans_config_publica_a_flag_do_pix(monkeypatch):
    """A /precos precisa saber se mostra o botão. A flag vem da FUNÇÃO do
    checkout — ler a env no monólito derrubaria o portão de destino."""
    monkeypatch.setenv("ASAAS_PIX_ANNUAL_ENABLED", "0")
    assert client.get("/billing/plans-config").json()["pix_annual_available"] is False
    monkeypatch.setenv("ASAAS_PIX_ANNUAL_ENABLED", "1")
    assert client.get("/billing/plans-config").json()["pix_annual_available"] is True


def _subscription(monkeypatch, uid: int) -> dict:
    """Chama o handler com a sessão resolvida — ele usa `Depends`, não o
    `shared.resolve_dashboard_user_id` dos routers."""
    import asyncio

    return asyncio.run(dashboard.billing_subscription(user_id=uid))


def _guardar_stripe_customer(uid: int, customer: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set stripe_customer_id = %s"
                    " where user_id = %s", (customer, uid))
        conn.commit()
