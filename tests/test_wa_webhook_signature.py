"""verify_webhook_signature (adapters/whatsapp/wa_runtime.py).

O X-Hub-Signature-256 é escolhido por quem posta no webhook — é o primeiro
dado não confiável que o processo toca, antes de qualquer autenticação. O
hub.verify_token do GET /webhook (wa_app.wa_verify) é a mesma categoria e
mora aqui embaixo.
"""

import asyncio
import hashlib
import hmac
import json

import pytest

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


# ─── corpo do POST /webhook (adapters/whatsapp/wa_app.py) ─────────────────────
# Com assinatura HMAC válida, o corpo ainda é escolhido por quem posta. O
# `json.loads` estava fora de qualquer try: 4 classes de corpo viravam 500 em
# laço. Por que a resposta é 200 e não 400: comentário do `except` em
# `wa_app.wa_webhook` — não repito aqui para não virar segunda fonte.

def _wa_webhook(corpo: bytes, monkeypatch):
    """POST /webhook com X-Hub-Signature-256 calculado de verdade. Devolve
    (resposta, fila, eventos) — fila fresca por teste, para não sujar a de
    módulo; `eventos` captura as chamadas de log_system_event_sync (o mock
    precisa guardar, não descartar, senão o `details` não é medido por
    ninguém)."""
    monkeypatch.setattr(wa_app, "APP_SECRET", SEGREDO)
    eventos: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(
        wa_app, "log_system_event_sync", lambda *a, **k: eventos.append((a, k))
    )
    fila = asyncio.Queue(maxsize=500)
    monkeypatch.setattr(wa_app, "_queue", fila)
    digest = hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()

    async def receive():
        return {"type": "http.request", "body": corpo, "more_body": False}

    req = Request(
        {"type": "http", "method": "POST", "query_string": b"",
         "headers": [(b"x-hub-signature-256", f"sha256={digest}".encode())]},
        receive=receive,
    )
    return asyncio.run(wa_app.wa_webhook(req)), fila, eventos


PAYLOAD_META = {
    "entry": [{"changes": [{"field": "messages", "value": {
        "messages": [{"from": "5511999998888", "type": "text",
                      "text": {"body": "gastei 50 no mercado"}}]}}]}]
}


def test_webhook_payload_da_meta_continua_enfileirado(monkeypatch):
    """Controle positivo do grupo: sem ele, um handler que respondesse 200 a
    TUDO e não processasse nada passaria nos casos abaixo — pior que o
    bug. Segue verde sob a mutação usada na prova negativa (json.loads cru)."""
    resp, fila, _ = _wa_webhook(json.dumps(PAYLOAD_META).encode(), monkeypatch)

    assert resp.status_code == 200
    assert fila.qsize() == 1
    assert fila.get_nowait() == PAYLOAD_META


@pytest.mark.parametrize("corpo, erro", [
    pytest.param(b"", "JSONDecodeError", id="corpo_vazio"),
    pytest.param(b"   ", "JSONDecodeError", id="so_espaco"),
    pytest.param(b'{"entry":', "JSONDecodeError", id="json_truncado"),
    pytest.param(b'{"a":"\xff\xfe"}', "UnicodeDecodeError", id="utf8_invalido"),
    pytest.param(b"[" * 100_000, "RecursionError", id="aninhamento_fundo"),
    pytest.param(b'{"a":' + b"1" * 5000 + b"}", "ValueError",
                 id="int_acima_de_4300_digitos"),
])
def test_webhook_corpo_ilegivel_200_sem_enfileirar(corpo, erro, monkeypatch):
    """Uma classe por caso: JSONDecodeError, UnicodeDecodeError, RecursionError
    e o ValueError do limite de 4300 dígitos do int — todas 500 antes do
    conserto.

    Prova negativa: trocando o try/except de wa_app.wa_webhook de volta pelo
    `payload = json.loads(raw.decode("utf-8"))` cru, estes ficam vermelhos por
    exceção e o positivo acima continua verde.

    Fila vazia importa tanto quanto o status: responder 200 e enfileirar lixo
    seria outro defeito. E `details["erro"]` importa porque na main o 500 dava
    traceback no mesmo painel: sem o nome da classe, o diagnóstico piora."""
    resp, fila, eventos = _wa_webhook(corpo, monkeypatch)

    assert resp.status_code == 200
    assert json.loads(resp.body)["ignored"] is True
    assert fila.qsize() == 0
    (args, kwargs), = eventos
    assert args[1] == "whatsapp_webhook_corpo_invalido"
    assert kwargs["details"] == {"bytes": len(corpo), "erro": erro}
