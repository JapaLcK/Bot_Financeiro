"""verify_webhook_signature (adapters/whatsapp/wa_runtime.py).

O X-Hub-Signature-256 é escolhido por quem posta no webhook — é o primeiro
dado não confiável que o processo toca, antes de qualquer autenticação. O
hub.verify_token do GET /webhook (wa_app.wa_verify) é a mesma categoria e
mora aqui embaixo.
"""

import asyncio
import hashlib
import hmac

from starlette.requests import Request

import adapters.whatsapp.wa_app as wa_app
from adapters.whatsapp.wa_runtime import verify_webhook_signature

CORPO = b'{"entry":[]}'
SEGREDO = "app-secret-de-teste"


def test_assinatura_valida_aceita():
    """Controle positivo do grupo: sem ele, um verify que recusasse TUDO
    passaria nos outros dois."""
    esperado = hmac.new(SEGREDO.encode(), CORPO, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(CORPO, f"sha256={esperado}", SEGREDO) is True


def test_assinatura_errada_recusa():
    assert verify_webhook_signature(CORPO, "sha256=" + "0" * 64, SEGREDO) is False


def test_assinatura_nao_ascii_recusa_sem_typeerror():
    """`compare_digest` sobre str não-ASCII levanta TypeError → 500 com stack
    trace, de graça, pra quem mandar um header acentuado."""
    assert verify_webhook_signature(CORPO, "sha256=café", SEGREDO) is False


# ─── hub.verify_token (GET /webhook, adapters/whatsapp/wa_app.py) ─────────────

def _wa_verify(query: str, monkeypatch):
    monkeypatch.setattr(wa_app, "VERIFY_TOKEN", "verify-token-de-teste")
    monkeypatch.setattr(wa_app, "log_system_event_sync", lambda *a, **k: None)
    req = Request({"type": "http", "method": "GET", "headers": [],
                   "query_string": query.encode()})
    return asyncio.run(wa_app.wa_verify(req))


def test_wa_verify_token_correto_devolve_challenge(monkeypatch):
    """Controle positivo: sem ele o grupo passaria num wa_verify que recusa
    TUDO — e aí a Meta nunca revalida o webhook."""
    resp = _wa_verify(
        "hub.mode=subscribe&hub.verify_token=verify-token-de-teste&hub.challenge=1234",
        monkeypatch,
    )

    assert resp.status_code == 200
    assert resp.body == b"1234"


def test_wa_verify_token_errado_403(monkeypatch):
    resp = _wa_verify(
        "hub.mode=subscribe&hub.verify_token=errado&hub.challenge=1234", monkeypatch
    )

    assert resp.status_code == 403


def test_wa_verify_sem_token_403_e_nao_500(monkeypatch):
    """`hub.verify_token` ausente vira None — a guarda `token and` antes do
    compare em tempo constante é o que mantém isso 403 em vez de
    AttributeError."""
    resp = _wa_verify("hub.mode=subscribe&hub.challenge=1234", monkeypatch)

    assert resp.status_code == 403


def test_wa_verify_token_nao_ascii_403_e_nao_500(monkeypatch):
    """NÃO prova o conserto original: aqui era `token == VERIFY_TOKEN`, e `==`
    nunca levantou. O que este caso guarda é a MIGRAÇÃO — trocar o `==` por um
    `compare_digest` cru (em vez do helper) levantaria TypeError e viraria 500
    numa query string que qualquer um escolhe. Provado mutando o helper para
    `hmac.compare_digest`: este é o teste que fica vermelho."""
    resp = _wa_verify(
        "hub.mode=subscribe&hub.verify_token=caf%C3%A9&hub.challenge=1234", monkeypatch
    )

    assert resp.status_code == 403
