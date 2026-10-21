"""Pix pago numa cobrança que nunca mostrou o QR (#819), ponta a ponta pelo HTTP.

O cenário é o do mundo real, não uma fabricação: o `pixQrCode` do Asaas falha, a
saga deixa a linha em `creating` SEM `asaas_payment_id` (`qr_falha` do Asaas
falso), o cliente paga pela fatura que o Asaas mandou por e-mail e o Asaas chama
`POST /billing/asaas/webhook` com o corpo COMPLETO (campos extras, `id` com `&`).
Antes do conserto o evento morria em `NotNullViolation`, ficava aberto e o cliente
pagava sem acesso.

CONTROLES (medidos no G1; saídas no relato do PR):
  * NEGATIVO: volte `_ORIGENS_DE_PAGAMENTO` (core/services/pix_drain.py) a
    `("pending", "canceling", "canceled", "expired")` →
    `test_pago_em_creating_concede_pelo_webhook_real` VERMELHO, com
    `NotNullViolation` no `last_error` do evento (a assinatura exata da issue), e
    vermelhos também os de reentrega/attach tardio/paralelo/isolamento;
  * NEGATIVO 2: tire o `avisar_pago_sem_qr(...)` do `_decidir` → o alerta e o log
    de `test_pago_em_creating_concede_pelo_webhook_real` ficam vermelhos.
  * NEGATIVO 3: tire o `try/except` de `avisar` (core/services/pix_pago_sem_qr.py) →
    `test_aviso_que_falha_nao_atrasa_o_grant` VERMELHO nas duas variantes (positivo: o aviso
    normal segue saindo em `test_pago_em_creating_concede_pelo_webhook_real`).
  * POSITIVOS: `test_pago_em_pending_e_identico_e_sem_aviso` (a linha que mostrou o
    QR paga igual e NÃO dispara o aviso de QR quebrado) e
    `test_referencia_de_terceiro_nao_toca_em_nada_nosso`.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import efeitos, evento, ler, mundo_externo
from _pix_checkout_helpers import PRECO, _comprar, _linhas, asaas_falso, vendavel  # noqa: F401
from core.services.pix_checkout import CheckoutIndisponivel
from db.connection import get_conn
from db.pix_charges_saga import attach_pagamento
from db.plan_grants import list_grants
from test_pix_efeito_concorrente import _drenar_em_paralelo, _enfileirar

TOKEN = "token-g1-pago-sem-qr"
EXTRAS = [{"price": "price_cad_a", "url": "https://drive.test/a?x=1&y=2", "nome": "Caderno A", "valor_cents": 1990},
          {"price": "price_cad_b", "url": "https://drive.test/b", "nome": "Caderno B", "valor_cents": 2990}]
TOTAL = PRECO + sum(e["valor_cents"] for e in EXTRAS)


@pytest.fixture()
def externo(monkeypatch):
    garantir_system_event_logs()
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    return mundo_externo(monkeypatch)


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        r = cur.fetchall() if cur.description else None
        conn.commit()
    return r


def _creating(uid, asaas_falso, extras=True):
    """`creating` sem id pelo CAMINHO DE PRODUÇÃO (o QR falha), mais os cadernos."""
    conta(uid, "free", None)
    asaas_falso["qr_falha"] = True
    with pytest.raises(CheckoutIndisponivel):
        _comprar(uid)
    asaas_falso["qr_falha"] = False
    linha = _linhas(uid)[-1]
    assert linha["status"] == "creating" and linha["asaas_payment_id"] is None
    if extras:
        _sql("update pix_charges set extras = %s::jsonb where id = %s",
             (json.dumps(EXTRAS), linha["id"]))
    return linha, f"pay_{asaas_falso['marca']}_{asaas_falso['n']}"


def _corpo(linha, pid, tipo="PAYMENT_RECEIVED", event_id=None, referencia=None):
    """O corpo do Asaas como ele vem: campos que a minimização descarta, `customer`
    como string, `value`/`netValue` decimais e o `&` no `id` do evento."""
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex}&{int(time.time() * 1000) % 100000}",
        "event": tipo, "dateCreated": "2026-10-20 10:00:00",
        "payment": {"object": "payment", "id": pid, "dateCreated": "2026-10-20",
                    "customer": "cus_000005", "billingType": "PIX", "status": "RECEIVED",
                    "value": TOTAL / 100, "netValue": TOTAL / 100 - 0.99,
                    "dueDate": "2026-10-23", "description": "PigBank Pro Max",
                    "externalReference": referencia or linha["external_reference"],
                    "pixTransaction": "abc123", "deleted": False},
    }


def _post(corpo):
    return TestClient(dashboard.app).post("/billing/asaas/webhook", json=corpo,
                                          headers={"asaas-access-token": TOKEN})


def _grants(uid):
    return [g for g in list_grants(uid) if g["source"] == "pix" and g["status"] == "active"]


def _entregas(uid):
    return _sql("select ebook_price from ebook_entregas where user_id = %s", (uid,))


def _avisos_de_qr(uid):
    return _sql("select 1 from system_event_logs where event_type = 'pix_pago_sem_qr'"
                " and user_id = %s", (uid,))


def test_pago_em_creating_concede_pelo_webhook_real(user_id, vendavel, asaas_falso, externo):
    linha, pid = _creating(user_id, asaas_falso)
    corpo = _corpo(linha, pid)
    r = _post(corpo)
    assert r.status_code == 200, r.text
    ev = evento(corpo["id"])
    assert ev["processed_at"] is not None and ev["attempts"] == 0, (
        f"evento travado: attempts={ev['attempts']} last_error={ev['last_error']}")
    pago = ler(linha["id"])
    assert pago["status"] == "paid" and pago["asaas_payment_id"] == pid
    assert pago["paid_at"] and pago["access_starts_at"] and pago["access_expires_at"]
    assert pago["qr_payload_enc"] is None
    assert len(_grants(user_id)) == 1
    assert _sql("select plan from auth_accounts where user_id = %s", (user_id,))[0]["plan"] == "pro_max"
    assert (externo["email"], externo["ga4"], externo["capi"]) == (1, 1, 1)
    assert {e["ebook_price"] for e in _entregas(user_id)} == {"price_cad_a", "price_cad_b"}
    assert efeitos(pid) == {"stripe_cancel", "grant", "ebook", "ga4", "capi", "email"}
    # D2: o operador é avisado de que o QR não saiu (e só ali, uma vez).
    assert len([a for a in externo["alerta"] if "sem QR" in a]) == 1
    assert len(_avisos_de_qr(user_id)) == 1
    assert externo["alerta"] and not [a for a in externo["alerta"] if "valor diferente" in a]


@pytest.mark.parametrize("quebra", ["log", "alerta"])
def test_aviso_que_falha_nao_atrasa_o_grant(user_id, vendavel, asaas_falso, externo, monkeypatch, caplog, quebra):
    """O aviso roda DEPOIS do commit da transição: se levantasse, a 1ª passada deixaria
    `paid` com 0 grants e o acesso sairia só na passada seguinte (60 s)."""
    import core.services.admin_notify as an
    import core.services.pix_pago_sem_qr as aviso

    def _quebra(*a, **kw):
        raise RuntimeError("aviso fora do ar")

    alvo = (aviso, "log_system_event_sync") if quebra == "log" else (an, "notify_pix_alerta")
    monkeypatch.setattr(*alvo, _quebra)
    linha, pid = _creating(user_id, asaas_falso)
    corpo = _corpo(linha, pid)
    assert _post(corpo).status_code == 200
    ev = evento(corpo["id"])
    assert ev["processed_at"] is not None and ev["attempts"] == 0, ev["last_error"]
    assert ler(linha["id"])["status"] == "paid" and len(_grants(user_id)) == 1
    assert externo["email"] == 1
    assert efeitos(pid) == {"stripe_cancel", "grant", "ebook", "ga4", "capi", "email"}
    assert "pix_pago_sem_qr: aviso falhou (RuntimeError)" in caplog.text


def test_reentrega_e_evento_novo_do_mesmo_pagamento_nao_duplicam(user_id, vendavel, asaas_falso, externo):
    linha, pid = _creating(user_id, asaas_falso)
    corpo = _corpo(linha, pid)
    assert _post(corpo).status_code == 200
    avisos = len(externo["alerta"])
    assert _post(corpo).status_code == 200                       # mesma entrega (duplicata)
    assert _post(_corpo(linha, pid, "PAYMENT_CONFIRMED")).status_code == 200  # event_id novo
    assert _post(_corpo(linha, pid)).status_code == 200          # RECEIVED reentregue com id novo
    assert ler(linha["id"])["status"] == "paid"
    assert len(_grants(user_id)) == 1 and len(_entregas(user_id)) == 2
    assert (externo["email"], externo["ga4"], externo["capi"]) == (1, 1, 1)
    assert len(externo["alerta"]) == avisos, "reentrega gerou aviso novo"


def test_attach_tardio_nao_desfaz_o_pago_e_o_poll_respeita_o_dono(user_id, vendavel, asaas_falso,
                                                                    externo, monkeypatch):
    """O `_emitir` ainda vivo chama `attach_pagamento` DEPOIS de o dreno pagar:
    tem de devolver False, e a linha seguir `paid` (nunca voltar a `pending`)."""
    linha, pid = _creating(user_id, asaas_falso)
    assert _post(_corpo(linha, pid)).status_code == 200
    assert attach_pagamento(linha["id"], pid) is False
    assert ler(linha["id"])["status"] == "paid"
    outro = int(uuid.uuid4().int % 10_000_000_000)
    from db import ensure_user
    ensure_user(outro)
    cliente = TestClient(dashboard.app)
    for quem, esperado in ((user_id, 200), (outro, 404)):
        monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req, q=quem: q)
        r = cliente.get(f"/billing/pix/{linha['public_token']}")
        assert r.status_code == esperado, (quem, r.text)
        if esperado == 200:
            assert r.json()["status"] == "paid"


def test_dois_eventos_do_mesmo_pagamento_em_paralelo_executam_uma_vez(user_id, vendavel, asaas_falso, monkeypatch):
    import core.services.pix_drain_effects as ef

    garantir_system_event_logs()
    mundo_externo(monkeypatch)
    contagem, trava = {"ga4": 0, "capi": 0, "email": 0}, threading.Lock()

    def _conta(nome):
        def _efeito(cobranca, evt):
            time.sleep(0.05)
            with trava:
                contagem[nome] += 1
        return _efeito

    for nome in contagem:
        monkeypatch.setitem(ef.EXECUTORES, nome, _conta(nome))
    linha, pid = _creating(user_id, asaas_falso, extras=False)
    cob = dict(linha, asaas_payment_id=pid)
    erros = _drenar_em_paralelo([_enfileirar(cob, "PAYMENT_RECEIVED"),
                                 _enfileirar(cob, "PAYMENT_CONFIRMED")])
    assert erros == []
    assert ler(linha["id"])["status"] == "paid" and len(_grants(user_id)) == 1
    assert contagem == {"ga4": 1, "capi": 1, "email": 1}


def test_pagamento_de_um_usuario_nao_toca_o_outro(user_id, vendavel, asaas_falso, externo):
    """Isolamento: duas `creating` de donos diferentes; o evento de A paga A e só A."""
    from db import ensure_user

    linha_a, pid_a = _creating(user_id, asaas_falso)
    b = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(b)
    linha_b, _ = _creating(b, asaas_falso)
    assert _post(_corpo(linha_a, pid_a)).status_code == 200
    assert ler(linha_a["id"])["status"] == "paid" and len(_grants(user_id)) == 1
    assert ler(linha_b["id"])["status"] == "creating"
    assert _grants(b) == [] and _entregas(b) == []


def test_pago_em_pending_e_identico_e_sem_aviso(user_id, vendavel, asaas_falso, externo):
    """POSITIVO: o QR saiu (`pending`) → mesmo desfecho, e o aviso de QR quebrado NÃO dispara."""
    conta(user_id, "free", None)
    _comprar(user_id)
    linha = _linhas(user_id)[-1]
    assert linha["status"] == "pending"
    _sql("update pix_charges set extras = %s::jsonb where id = %s", (json.dumps(EXTRAS), linha["id"]))
    corpo = _corpo(linha, linha["asaas_payment_id"])
    assert _post(corpo).status_code == 200
    ev = evento(corpo["id"])
    assert ev["processed_at"] is not None and ev["attempts"] == 0
    assert ler(linha["id"])["status"] == "paid" and len(_grants(user_id)) == 1
    assert (externo["email"], externo["ga4"], externo["capi"]) == (1, 1, 1)
    assert not [a for a in externo["alerta"] if "sem QR" in a] and _avisos_de_qr(user_id) == []


def test_referencia_de_terceiro_nao_toca_em_nada_nosso(user_id, vendavel, asaas_falso, externo):
    """POSITIVO: Pix avulso da conta do negócio (fora de `^pix:[0-9]+$`) é só log."""
    linha, pid = _creating(user_id, asaas_falso)
    corpo = _corpo(linha, "pay_de_terceiro", referencia="venda-balcao-77")
    assert _post(corpo).status_code == 200
    assert evento(corpo["id"])["processed_at"] is not None
    assert ler(linha["id"])["status"] == "creating" and _grants(user_id) == []
    assert externo["alerta"] == [] and externo["email"] == 0
