"""#919: o `status` com `errors` que a Meta manda ao webhook não grava o
telefone cru em `system_event_logs`.

Caminho real: POST assinado em `/wa/webhook` do monólito, com o INSERT de
verdade (o `log_system_event_sync` não é mockado). As linhas são escopadas
pelo `id` anterior ao POST: cada worker do xdist tem o próprio database e os
testes dele rodam em sequência, então `id > antes` é só o que este POST gravou.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

import adapters.whatsapp.wa_app as wa_app
import frontend.finance_bot_websocket_custom as dashboard
from _corpo_json_helpers import _admin_tables  # noqa: F401  (fixture autouse)
from db import get_conn
from tests.test_wa_webhook_signature import SEGREDO

FONE = "5511987654321"
MASCARA = "5511******4321"


def _chave() -> str:
    """uuid só com letras: dígito na chave seria redigido pelo `[num]`."""
    return uuid.uuid4().hex.translate(str.maketrans("0123456789", "ghijklmnop"))


def _select(sql: str, *params):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall() or []]


@pytest.fixture
def postar(monkeypatch):
    monkeypatch.setattr(wa_app, "APP_SECRET", SEGREDO)
    monkeypatch.setattr(wa_app, "_queue", asyncio.Queue(maxsize=500))

    def _postar(*statuses: dict) -> list[dict]:
        antes = _select("select coalesce(max(id), 0) as m from system_event_logs")[0]["m"]
        corpo = json.dumps({"entry": [{"changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp", "statuses": list(statuses)}}]}]}).encode()
        digest = hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()
        r = TestClient(dashboard.app).post("/wa/webhook", content=corpo, headers={
            "Content-Type": "application/json", "x-hub-signature-256": f"sha256={digest}"})
        assert r.status_code == 200, r.text
        return _select("select event_type, message, details, details::text as txt "
                       "from system_event_logs where id > %s order by id", antes)

    return _postar


def _status(**extra) -> dict:
    return {"id": f"wamid.{uuid.uuid4().hex}", "status": "failed",
            "timestamp": "1700000000", "recipient_id": FONE, **extra}


def test_status_com_erro_grava_mascarado_so_code_e_title(postar):
    chave = _chave()
    linhas = postar(_status(errors=[{
        "code": 131026,
        "title": f"Message undeliverable {chave}",
        "message": f"Recipient {FONE} is not a valid WhatsApp user",
        "error_data": {"details": f"Number +{FONE} cannot receive messages"},
        "href": "https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes/",
    }, {
        # fora do formato da doc: os VALORES de `code` e `title` também passam
        # pela fronteira (`code` só int; número no `title` vira `[num]`).
        "code": FONE,
        "title": "Recipient +55 11 98765-4321 not on WhatsApp",
    }, {
        "code": 5511987654321, "title": "Call 98765-4321",   # int grande; 9 dígitos
    }, {
        # 115 + 13 dígitos: cortar em 120 ANTES de redigir deixaria "55119".
        "code": True, "title": "a" * 115 + FONE,
    }]))

    vazou = [l for l in linhas if FONE in (l["message"] or "") or FONE in l["txt"]]
    assert not vazou, vazou
    erro = [l for l in linhas if l["event_type"] == "whatsapp_status_error"
            and chave in l["txt"]]
    assert len(erro) == 1, linhas
    assert erro[0]["details"] == {
        "status": "failed",
        "recipient_id": MASCARA,
        "errors": [{"code": 131026, "title": f"Message undeliverable {chave}"},
                   {"code": None, "title": "Recipient [num] not on WhatsApp"},
                   {"code": None, "title": "Call [num]"},
                   {"code": None, "title": "a" * 115 + "[num]"}],
    }


def test_errors_fora_de_lista_nao_derruba_os_status_seguintes(postar):
    """`errors` int fazia o `for` levantar TypeError dentro do `try` do resumo,
    e o `except` levava junto o status seguinte do mesmo POST."""
    chave = _chave()
    linhas = postar(_status(errors=131026),
                    _status(errors=[{"code": 131047, "title": f"Re-engagement {chave}"}]))

    erros = [l["details"] for l in linhas if l["event_type"] == "whatsapp_status_error"]
    assert erros == [
        {"status": "failed", "recipient_id": MASCARA, "errors": []},
        {"status": "failed", "recipient_id": MASCARA,
         "errors": [{"code": 131047, "title": f"Re-engagement {chave}"}]},
    ], linhas


def test_status_sem_erro_nao_grava_a_linha_de_erro(postar):
    """Guarda contra log em excesso: entrega normal não vira `status_error`
    nem deixa o número em linha nenhuma. Só confere ausência; o positivo de
    verdade (a linha existe, com o conteúdo certo) é o `==` do primeiro teste."""
    linhas = postar(_status(status="delivered"))

    assert not [l for l in linhas if l["event_type"] == "whatsapp_status_error"], linhas
    assert not [l for l in linhas if FONE in l["txt"]], linhas
