"""O wamid carrega o telefone inteiro em base64, e nenhum log o leva cru (D4 da #657).

O formato público (`wamid.` + base64) decodifica para o número do destinatário, então a máscara do
telefone na mesma linha não valia nada. O log e `details` levam `mask_wamid`, digest do id inteiro.
O wamid real segue onde é dado: `_seen_recent`, typing indicator, envio.

Caminho real: `_send_reply`, `process_message` e os envios do `wa_client` até `log_system_event_sync`.
Só o HTTP da Graph API e o `_wa` da conversa são dublados.

CONTROLES (CLAUDE.md §3): voltar o id cru em qualquer ponto deixa o caso daquele ponto vermelho.
Positivo: o mesmo wamid dá o mesmo digest nas linhas, e o id inteiro chega ao envio e à dedupe.

CLASSE CEGA: o formato real do wamid não foi medido em produção. O digest não depende dele, mas a
prova da decodificação vale só para a forma pública usada aqui.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import adapters.whatsapp.wa_client as wa_client
import adapters.whatsapp.wa_demo as wa_demo
import adapters.whatsapp.wa_runtime as wa_runtime
from _log_dono_helpers import FONE, _uid, _vazou
from adapters.whatsapp.wa_parse import InboundMessage
from test_account_deletion_pii_logs import _novos, marca, tabelas_admin  # noqa: F401
from test_category_normalization import _wa
from utils_phone import mask_wamid

NUMERO = f"55{FONE}"
_CORPO = b"\x1c\x18\x0d" + NUMERO.encode() + b"\x15\x02\x00\x12\x18\x143A0D5B25B27914FA9C24\x00"
WAMID = "wamid." + base64.b64encode(_CORPO).decode()
B64 = WAMID.removeprefix("wamid.")
DIGEST = hashlib.sha256(WAMID.encode()).hexdigest()[:12]
_SEEN_REAL = wa_runtime._seen_recent


def _sem_wamid(texto: str) -> None:
    """Nenhuma janela de 6 caracteres do base64, nem o telefone decodificado: um pedaço do meio também
    carrega dígitos do número."""
    assert NUMERO.encode() in base64.b64decode(B64), "pré-condição do formato"
    janelas = [B64[i:i + 6] for i in range(len(B64) - 5)]
    assert not (j := next((j for j in janelas if j in texto), None)), f"{j!r} vazou: {texto}"
    assert not (p := _vazou(texto)), f"{p!r} vazou: {texto}"


def _graph(monkeypatch, status=200, corpo=None, falha=None) -> list[dict]:
    enviados: list[dict] = []

    def post(url, headers=None, json=None, timeout=None):
        enviados.append(json)
        if falha:
            raise falha
        return SimpleNamespace(status_code=status, text="erro", json=lambda: corpo or {})

    monkeypatch.setenv("WA_TOKEN", "t")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "1")
    monkeypatch.setattr(wa_client.requests, "post", post)
    return enviados


def _mensagem(texto: str = "gastei 10 no café") -> InboundMessage:
    return InboundMessage(wa_id=NUMERO, text=texto, timestamp=None, attachments=[],
                          raw={"id": WAMID, "type": "text"})


def test_mask_wamid():
    assert mask_wamid(WAMID) == DIGEST != mask_wamid(WAMID + "x")
    assert mask_wamid(None) == mask_wamid("")
    assert mask_wamid("wamid.\ud800")        # surrogate do JSON não pode derrubar o log


def test_envio_de_texto_correlaciona_log_e_details_sem_o_wamid(monkeypatch, marca, caplog, capsys):  # noqa: F811
    _graph(monkeypatch, corpo={"contacts": [{"wa_id": NUMERO}], "messages": [{"id": WAMID}, {}]})

    with caplog.at_level(logging.INFO):
        wa_runtime._send_reply(NUMERO, "oi")

    linhas = _novos(marca, "whatsapp_send_success")
    assert len(linhas) == 1 and linhas[0]["details"]["message_ids"] == [DIGEST], linhas
    assert f"message_ids=['{DIGEST}']" in caplog.text, caplog.text
    cap = capsys.readouterr()
    _sem_wamid(f"{caplog.text} {linhas[0]} {cap.out} {cap.err}")


def test_envio_de_template_grava_o_digest(monkeypatch, marca):  # noqa: F811
    _graph(monkeypatch, corpo={"messages": [{"id": WAMID}, {}]})

    wa_client.send_template(NUMERO, "modelo")

    linhas = _novos(marca, "whatsapp_send_success")
    assert len(linhas) == 1 and linhas[0]["details"]["message_ids"] == [DIGEST], linhas
    _sem_wamid(str(linhas[0]))


@pytest.mark.parametrize("evento, http, levanta", [
    ("whatsapp_typing_indicator_success", {"corpo": {"success": True}}, None),
    ("whatsapp_token_invalid", {"status": 401}, None),
    ("whatsapp_typing_indicator_failed", {"status": 500}, RuntimeError),
    ("whatsapp_typing_indicator_exception", {"falha": ConnectionError("fora")}, ConnectionError),
], ids=["ok", "401", "500", "excecao"])
def test_typing_grava_o_digest_e_envia_o_id_inteiro(monkeypatch, marca, evento, http, levanta):  # noqa: F811
    enviados = _graph(monkeypatch, **http)

    if levanta:
        with pytest.raises(levanta):
            wa_client.send_typing_indicator(WAMID)
    else:
        wa_client.send_typing_indicator(WAMID)

    assert [e["message_id"] for e in enviados] == [WAMID], "o wamid real segue no envio"
    linhas = _novos(marca, evento)
    assert len(linhas) == 1 and linhas[0]["details"]["message_id"] == DIGEST, linhas
    _sem_wamid(str(linhas[0]))


def test_duplicata_pela_conversa_loga_o_digest(monkeypatch, caplog):
    uid = _uid()
    _wa(monkeypatch, uid)
    vistos, digitando = [], []
    monkeypatch.setattr(wa_runtime, "_seen_recent", lambda m: vistos.append(m) or _SEEN_REAL(m))
    monkeypatch.setattr(wa_runtime, "send_typing_indicator", digitando.append)

    with caplog.at_level(logging.INFO):
        wa_runtime.process_message(_mensagem())
        wa_runtime.process_message(_mensagem())

    assert vistos == [WAMID, WAMID] and digitando == [WAMID], "dedupe e typing usam o id inteiro"
    assert f"WA duplicate ignored message_id={DIGEST}" in caplog.text, caplog.text
    _sem_wamid(caplog.text)


def test_typing_que_falha_loga_o_digest(monkeypatch, caplog):
    uid = _uid()
    _wa(monkeypatch, uid)

    def falha(m):
        raise RuntimeError("fora")

    monkeypatch.setattr(wa_runtime, "send_typing_indicator", falha)

    with caplog.at_level(logging.INFO):
        wa_runtime.process_message(_mensagem())

    assert f"WA typing indicator failed message_id={DIGEST}" in caplog.text, caplog.text
    _sem_wamid(caplog.text)


def test_duplicata_do_demo_loga_o_digest(monkeypatch, caplog):
    monkeypatch.setattr(wa_demo, "decidir", lambda m: object())
    monkeypatch.setattr(wa_runtime, "_seen_recent", lambda m: True)

    with caplog.at_level(logging.INFO):
        wa_runtime.process_message(_mensagem())

    assert f"WA demo duplicate ignored message_id={DIGEST}" in caplog.text, caplog.text
    _sem_wamid(caplog.text)


def test_typing_do_demo_que_falha_loga_o_digest(monkeypatch, caplog):
    digitando, respostas = [], []

    def falha(m):
        digitando.append(m)
        raise RuntimeError("fora")

    monkeypatch.setattr(wa_client, "send_typing_indicator", falha)
    monkeypatch.setattr(wa_demo, "_funil", lambda: MagicMock(reservar_mensagem=lambda c: 1))
    monkeypatch.setattr(wa_demo, "_texto", lambda to, t: respostas.append(t))
    monkeypatch.setattr("core.services.demo.conversa.responder", lambda *a, **k: "resposta do piggy")

    with caplog.at_level(logging.INFO):
        wa_demo._responder(SimpleNamespace(code="c", h="h"), _mensagem(), WAMID)

    assert any("resposta do piggy" in r for r in respostas), respostas
    assert digitando == [WAMID], "o typing do demo usa o id inteiro"
    assert f"WA demo typing indicator failed message_id={DIGEST}" in caplog.text, caplog.text
    _sem_wamid(caplog.text)
