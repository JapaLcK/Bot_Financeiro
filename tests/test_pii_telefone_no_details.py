"""Telefone completo fora do `details` e das linhas INFO (N4, PR 2).

O `details` de `system_event_logs` não tem retenção, e as linhas INFO vão para
o log do Railway. Este arquivo prova:

- o aviso de vinculação continua saindo UMA vez por usuário, agora com o dedup
  por `user_id` (o `details.wa_id` gravado é mascarado e não serve de chave);
- linha antiga, gravada com o `wa_id` cru, continua contando como "já enviado";
- o `wa_client` grava `to` mascarado nas falhas e nenhum `to` no sucesso, sem
  mexer no payload enviado à Meta;
- as linhas INFO do envio, do tutorial e do `process_message` levam a máscara.
"""
from __future__ import annotations

import json
import logging
import uuid

import pytest

import adapters.whatsapp.wa_client as wa_client
import adapters.whatsapp.wa_runtime as wa_runtime
import adapters.whatsapp.wa_tutorial as wa_tutorial
from adapters.whatsapp.wa_parse import InboundMessage
from db import ensure_user, get_conn
from tests._billing_grants_helpers import garantir_system_event_logs
from tests.test_category_normalization import _wa
from utils_phone import mask_phone

FONE = "5511987654321"
FONE_2 = "5511000004321"  # mesma máscara do FONE
MASCARA = "5511******4321"
AVISO = "Encontrei mais de uma conta com este número"


def _oi(fone: str, n: int) -> InboundMessage:
    return InboundMessage(wa_id=fone, text="oi", timestamp=None, attachments=[],
                          raw={"id": f"wamid.pii2.{fone}.{n}", "type": "text"})


def _linhas_do_aviso(uids: tuple[int, ...]) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, details FROM system_event_logs"
                    " WHERE event_type = 'whatsapp_autolink_greeting_warning_sent'"
                    " AND user_id = ANY(%s) ORDER BY id", (list(uids),))
        return cur.fetchall()


def test_aviso_de_vinculacao_um_por_usuario(user_id, monkeypatch):
    garantir_system_event_logs()
    respostas = _wa(monkeypatch, user_id)
    monkeypatch.setattr(wa_runtime, "attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "multiple_accounts"})
    monkeypatch.setattr(wa_runtime, "handle_incoming", lambda *a, **k: [])
    uid2 = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid2)
    try:
        wa_runtime.process_message(_oi(FONE, 1))
        wa_runtime.process_message(_oi(FONE, 2))
        assert sum(AVISO in r for r in respostas) == 1, respostas  # dedup

        # Outro usuário com a MESMA máscara recebe: a chave não é a máscara nem global.
        monkeypatch.setattr(wa_runtime, "get_or_create_canonical_user", lambda p, e: uid2)
        wa_runtime.process_message(_oi(FONE_2, 1))
        assert sum(AVISO in r for r in respostas) == 2, respostas

        linhas = _linhas_do_aviso((user_id, uid2))
        assert [r["user_id"] for r in linhas] == [user_id, uid2], linhas
        for details in (r["details"] for r in linhas):
            assert details["wa_id"] == MASCARA, details
            assert FONE not in json.dumps(details) and FONE_2 not in json.dumps(details), details
    finally:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM system_event_logs WHERE user_id IN (%s, %s)", (user_id, uid2))
            conn.commit()


def test_linha_antiga_com_wa_id_cru_ainda_conta(user_id):
    garantir_system_event_logs()
    wa_runtime.log_system_event_sync(
        "info", "whatsapp_autolink_greeting_warning_sent", "x", source="test_pii2",
        user_id=user_id, details={"wa_id": FONE, "status": "multiple_accounts"})
    try:
        assert wa_runtime._autolink_warning_already_sent(user_id, "multiple_accounts") is True
        assert wa_runtime._autolink_warning_already_sent(user_id + 1, "multiple_accounts") is False
    finally:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM system_event_logs WHERE source = 'test_pii2'")
            conn.commit()


class _Resp:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.text = '{"error": "x"}'

    def json(self):
        return {"messages": [{"id": "wamid.ok"}]}


_ENVIOS = {
    "send_text": lambda: wa_client.send_text(FONE, "oi", access_token="t", phone_number_id="p"),
    "send_template": lambda: wa_client.send_template(FONE, "tpl", access_token="t", phone_number_id="p"),
    "send_interactive_buttons": lambda: wa_client.send_interactive_buttons(
        FONE, "oi", [{"id": "a", "title": "A"}], access_token="t", phone_number_id="p"),
    "send_interactive_list": lambda: wa_client.send_interactive_list(
        FONE, "oi", "ver", [{"title": "s", "rows": []}], access_token="t", phone_number_id="p"),
}


@pytest.mark.parametrize("envio", list(_ENVIOS))
@pytest.mark.parametrize("resultado", ["excecao", 401, 400, 200])
def test_wa_client_details_sem_telefone_cru(envio, resultado, monkeypatch):
    enviados: list[dict] = []
    eventos: list[dict] = []

    def post(url, headers=None, json=None, timeout=None):
        enviados.append(json)
        if resultado == "excecao":
            raise ConnectionError("rede")
        return _Resp(resultado)

    monkeypatch.setattr(wa_client.requests, "post", post)
    monkeypatch.setattr(wa_client, "log_system_event_sync",
                        lambda *a, **kw: eventos.append(kw["details"]))
    if resultado in ("excecao", 400):
        with pytest.raises(Exception):
            _ENVIOS[envio]()
    else:
        _ENVIOS[envio]()

    assert enviados and enviados[0]["to"] == FONE, enviados  # o envio não quebrou
    assert len(eventos) == 1, eventos
    details = eventos[0]
    assert FONE not in json.dumps(details), details
    if resultado == 200:
        assert "to" not in details, details
    else:
        assert details["to"] == MASCARA, details


def _send_reply(monkeypatch):
    monkeypatch.setattr(wa_runtime, "send_text", lambda **kw: {
        "messages": [{"id": "wamid.ok"}], "contacts": [{"wa_id": FONE}]})
    wa_runtime._send_reply(FONE, "oi")
    return ("WA sending reply", "WA send_text accepted")


def _tutorial(monkeypatch):
    monkeypatch.setattr(wa_tutorial, "send_interactive_buttons", lambda **kw: None)
    monkeypatch.setattr(wa_tutorial, "send_text", lambda *a, **kw: None)
    wa_tutorial.handle_tutorial_button(FONE, "tut_start")
    return ("Tutorial step",)


def _process_message(monkeypatch, user_id):
    _wa(monkeypatch, user_id)
    monkeypatch.setattr(wa_runtime, "handle_incoming", lambda *a, **k: ["ok"])
    wa_runtime.process_message(InboundMessage(wa_id=FONE, text="saldo", timestamp=None,
                                              attachments=[], raw={"id": "wamid.pii2.info", "type": "text"}))
    return ("WA canonical user resolved", "WA generated outgoing messages")


@pytest.mark.parametrize("disparo", ["send_reply", "tutorial", "process_message"])
def test_info_sem_telefone_cru(disparo, user_id, monkeypatch, caplog):
    with caplog.at_level(logging.INFO):
        if disparo == "send_reply":
            prefixos = _send_reply(monkeypatch)
        elif disparo == "tutorial":
            prefixos = _tutorial(monkeypatch)
        else:
            prefixos = _process_message(monkeypatch, user_id)
    msgs = [r.getMessage() for r in caplog.records]
    assert not [m for m in msgs if FONE in m], msgs
    for p in prefixos:
        linha = [m for m in msgs if m.startswith(p)]
        assert linha and MASCARA in linha[0], (p, msgs)


def test_mask_phone_aceita_int():
    assert mask_phone(5511987654321) == MASCARA
