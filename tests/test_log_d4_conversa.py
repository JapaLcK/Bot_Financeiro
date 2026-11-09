"""PR-9a da fatia D (#657, D4): `print` e log INFO/DEBUG sem texto do usuário, telefone, código nem
`str(exc)`, medidos pelo caminho real. Nome de categoria conta como texto do usuário (decisão do dono).

Cada caso confere a saída E o desfecho: um conserto que calasse o fluxo passaria só pela ausência.
`_erro_real` é um 22P02 de verdade: um erro da classe 23 já chega redigido por `db/connection.py`,
e o teste passaria com ou sem o conserto.

CONTROLES (CLAUDE.md §3): volte o texto antigo de um ponto e o caso daquele ponto fica vermelho. O
positivo de cada grupo é o desfecho: a conversa responde, o lançamento fica, a categoria muda.

CLASSE CEGA: a OpenAI e a Graph API são dubladas, com corpo montado aqui. A forma real da linha no
log do Railway e os corpos reais de erro só se veem em produção.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from types import SimpleNamespace

import httpx
import openai
import pytest

import adapters.whatsapp.wa_client as wa_client
import adapters.whatsapp.wa_runtime as wa_runtime
import adapters.whatsapp.wa_tutorial as wa_tutorial
import core.ai_patterns as ai_patterns
import core.budget_alerts as budget_alerts
import core.handle_incoming as hi
import core.intent_classifier as ic
import core.services.ai_chat as ai_chat
import core.services.ai_chat.runner as runner
import core.services.email_service as email_service
import db
from _log_dono_helpers import FONE, _ALHEIO, _erro_real, _marcador, _uid, _vazou
from adapters.whatsapp.wa_parse import extract_messages
from core.handlers import greeting
from core.services.ai_chat import chips
from core.types import IncomingMessage
from test_account_deletion_pii_logs import (_SEND_EMAIL_REAL, _linhas_com, _novos, marca,  # noqa: F401
                                            tabelas_admin)  # noqa: F401
from test_ai_guard_log import _cliente, _historico
from test_category_normalization import _botao, _wa
from test_of_log_dono_coluna import _levanta
from tests._ia_falsa_helpers import openai_falso, texto
from utils_phone import mask_phone

NUMERO = f"55{FONE}"
# Capturado na coleção, antes do stub autouse `_block_outbound_network`, como o `_SEND_EMAIL_REAL`.
_OPENAI_REAL = openai.OpenAI


def _diga(uid: int, frase: str, attachments=()) -> str:
    saida = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text=frase, message_id=f"d4-{frase[:12]}",
        attachments=list(attachments), external_id=str(uid), raw={}))
    return saida[0].text if saida else ""


def _erro_openai(marcador: str) -> openai.BadRequestError:
    """Montado pelo SDK, como em produção: ele põe o corpo inteiro na mensagem da exceção."""
    corpo = {"error": {"message": f"Invalid value: '{marcador} {FONE}'", "param": "messages",
                       "type": "invalid_request_error", "code": "invalid_value"}}
    resposta = httpx.Response(400, json=corpo, request=httpx.Request(
        "POST", "https://api.openai.com/v1/chat/completions"))
    erro = _OPENAI_REAL(api_key="x")._make_status_error_from_response(resposta)
    assert marcador in str(erro), "pré-condição: o texto cru traz o que o usuário escreveu"
    return erro


def _linhas(saida: str, tag: str) -> list[str]:
    return [ln for ln in saida.splitlines() if tag in ln]


# ── T1 ───────────────────────────────────────────────────────────────────────

def test_t1_erro_da_openai_no_classificador_nao_leva_o_corpo(monkeypatch, capsys):
    uid, marcador = _uid(), _marcador()
    erro = _erro_openai(marcador)
    resultados = []
    real = ic._classify_llm_call
    monkeypatch.setattr(ic, "_classify_llm_call",
                        lambda *a, **k: resultados.append(real(*a, **k)) or resultados[-1])
    monkeypatch.setattr(ai_chat, "chat", lambda u, t, **k: "[IA]")
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda u: True)
    assert "50" in _diga(uid, "gastei 50 no mercado")
    ia = openai_falso(monkeypatch, erro)

    resposta = _diga(uid, f"foram cinquenta conto no açaí da {marcador}")

    assert ia.creates, "a IA não foi chamada e o caso não mede nada"
    assert resposta == "[IA]", resposta
    assert [r.intent for r in resultados] == ["out_of_scope"]
    cap = capsys.readouterr()
    assert _linhas(cap.out, "[intent_classifier] AI error: ") == [
        "[intent_classifier] AI error: motivo=BadRequestError sqlstate=None "
        "status=400 code=invalid_value"], cap
    assert not _linhas(cap.err, "[intent_classifier]"), cap.err
    assert not (p := _vazou(cap.out + cap.err, marcador)), f"{p!r} vazou: {cap}"


# ── T2 ───────────────────────────────────────────────────────────────────────

def test_t2_categoria_sobreposta_nao_loga_nota_nem_categoria(monkeypatch, caplog):
    uid, marcador = _uid(), _marcador()
    nota = f"deixei cinquenta conto no mercado da zélia, açaí {marcador}"
    entidades = {"tipo": "despesa", "valor": 50, "alvo": "mercado", "categoria": "lazer"}
    ia = openai_falso(monkeypatch, texto(json.dumps({
        "intent": "launches.add", "confidence": 0.97, "entities": entidades,
        "needs_clarification": False, "clarification_question": None})))

    with caplog.at_level(logging.INFO):
        _diga(uid, nota)

    assert ia.creates, "a IA não foi chamada e o caso não mede nada"
    linhas = [r.getMessage() for r in caplog.records if "sobreposta por regra" in r.getMessage()]
    assert len(linhas) == 1, [r.getMessage() for r in caplog.records]
    assert linhas[0].endswith(f"chars={len(nota)}"), linhas
    assert not (p := _vazou(caplog.text, marcador, "açaí", "lazer", "mercado")), f"{p!r} vazou: {caplog.text}"
    gravado = db.list_launches(uid, limit=5)
    assert [x["categoria"] for x in gravado] == ["mercado"], gravado


# ── T3 ───────────────────────────────────────────────────────────────────────

def test_t3_falha_do_alerta_de_orcamento_nao_leva_texto_nem_categoria(monkeypatch, capsys, caplog):
    uid, erro = _uid(), _erro_real()
    assert str(_ALHEIO) in str(erro), "pré-condição: o texto cru traz o dado do usuário"
    monkeypatch.setattr(budget_alerts, "get_conn", _levanta(erro))

    with caplog.at_level(logging.INFO):
        resposta = _diga(uid, "gastei 30 no açaí da esquina, mano")

    gravado = db.list_launches(uid, limit=5)
    assert len(gravado) == 1 and float(gravado[0]["valor"]) == 30, (resposta, gravado)
    # O `add_from_entities` engole o que subir daqui: só a chamada direta prova o `None`.
    assert budget_alerts.evaluate_after_expense(uid, "lazer", 10, datetime.now()) is None
    cap = capsys.readouterr()
    assert _linhas(cap.err, "[budget_alerts]") == [
        f"[budget_alerts] eval failed for user {uid}: motivo=InvalidTextRepresentation "
        "sqlstate=22P02 status=None"] * 2, cap
    assert not _linhas(cap.out, "[budget_alerts]"), cap.out
    texto = f"{cap.out} {cap.err} {caplog.text}"
    assert not (p := _vazou(texto, str(_ALHEIO), gravado[0]["categoria"])), f"{p!r} vazou: {texto}"


# ── T4 ───────────────────────────────────────────────────────────────────────

def test_t4_audio_nao_imprime_o_nome_do_arquivo(monkeypatch, capsys):
    uid, marcador = _uid(), _marcador()
    transcricoes = SimpleNamespace(create=lambda **k: "gastei 12 no açaí")
    monkeypatch.setattr(openai, "OpenAI", lambda **k: SimpleNamespace(
        audio=SimpleNamespace(transcriptions=transcricoes)))
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    anexo = SimpleNamespace(data=b"x", filename=f"extrato_{marcador}.ogg", content_type="audio/ogg")

    _diga(uid, "", [anexo])

    gravado = db.list_launches(uid, limit=5)
    assert [float(x["valor"]) for x in gravado] == [12], gravado
    cap = capsys.readouterr()
    assert _linhas(cap.out, "transcribe_audio chamado") == [
        "[media_service] transcribe_audio chamado | api_key=OK"], cap
    assert not (p := _vazou(cap.out + cap.err, marcador)), f"{p!r} vazou: {cap}"


# ── T5 ───────────────────────────────────────────────────────────────────────

def test_t5_parse_do_webhook_em_debug_mascara_o_numero(caplog):
    valor = {"contacts": [{"wa_id": NUMERO, "profile": {"name": "Zé"}}],
             "messages": [{"id": "wamid.d4", "from": NUMERO, "timestamp": "1710000000",
                           "type": "text", "text": {"body": "gastei 10 no café"}}]}
    with caplog.at_level(logging.DEBUG, logger="adapters.whatsapp.wa_parse"):
        mensagens = extract_messages({"entry": [{"changes": [{"value": valor}]}]})

    assert [m.wa_id for m in mensagens] == [NUMERO] and mensagens[0].text == "gastei 10 no café"
    assert "WA parse contacts count=1" in caplog.text, caplog.text
    assert f"from={mask_phone(NUMERO)} resolved_wa_id={mask_phone(NUMERO)}" in caplog.text, caplog.text
    assert not (p := _vazou(caplog.text)), f"{p!r} vazou: {caplog.text}"


# ── T6 ───────────────────────────────────────────────────────────────────────

ENVIOS = {
    "send_text": lambda: wa_client.send_text(NUMERO, "oi"),
    "send_template": lambda: wa_client.send_template(NUMERO, "modelo"),
    "send_typing_indicator": lambda: wa_client.send_typing_indicator("wamid.d4"),
    "send_interactive_buttons": lambda: wa_client.send_interactive_buttons(
        NUMERO, "oi", [{"id": "a", "title": "A"}]),
    "send_interactive_list": lambda: wa_client.send_interactive_list(
        NUMERO, "oi", "abrir", [{"title": "s", "rows": [{"id": "a", "title": "A"}]}]),
}


@pytest.mark.parametrize("envio", ENVIOS.values(), ids=ENVIOS.keys())
def test_t6_401_da_graph_api_nao_imprime_o_corpo(monkeypatch, capsys, envio):
    marcador = _marcador()
    corpo = json.dumps({"error": {"message": f"token de {FONE} {marcador}"}})
    monkeypatch.setenv("WA_TOKEN", "t")
    monkeypatch.setenv("WA_PHONE_NUMBER_ID", "1")
    monkeypatch.setattr(wa_client, "log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr(wa_client.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=401, text=corpo))

    assert envio() is None

    cap = capsys.readouterr()
    assert "SEND_ERROR" in cap.out, cap
    assert not (p := _vazou(cap.out + cap.err, marcador)), f"{p!r} vazou: {cap}"


# ── T7 ───────────────────────────────────────────────────────────────────────

def test_t7_chips_indisponiveis_nao_logam_o_corpo_da_openai(monkeypatch, caplog):
    marcador = _marcador()
    openai_falso(monkeypatch, _erro_openai(marcador))

    with caplog.at_level(logging.INFO, logger=chips.logger.name):
        assert chips.sugerir("quanto gastei?", "R$ 10", []) == []

    assert ("chips indisponíveis: motivo=BadRequestError sqlstate=None status=400 "
            "code=invalid_value") in caplog.text, caplog.text
    assert not (p := _vazou(caplog.text, marcador)), f"{p!r} vazou: {caplog.text}"


def test_t7_item_inconsistente_sai_sem_o_titulo_e_o_consistente_fica(caplog):
    marcador = _marcador()
    bom = {"title": "Você gastou 2x mais no mercado", "subtitle": "R$ 100,00 contra R$ 50,00"}
    ruim = {"title": f"Você gastou 9x mais em {marcador}", "subtitle": "R$ 100,00 contra R$ 50,00"}

    with caplog.at_level(logging.INFO, logger=ai_patterns.logger.name):
        itens = ai_patterns._sanitize_pattern_items([ruim, bom])

    assert [i["title"] for i in itens] == [bom["title"]]
    assert "claimed=9.00 actual=2.00" in caplog.text, caplog.text
    assert not (p := _vazou(caplog.text, marcador)), f"{p!r} vazou: {caplog.text}"


# ── T8 ───────────────────────────────────────────────────────────────────────

def _texto_debug(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def test_t8_nome_na_saudacao_falha_aberto_sem_o_texto(monkeypatch, caplog):
    erro = _erro_real()
    monkeypatch.setattr(db, "get_auth_user", _levanta(erro))

    with caplog.at_level(logging.DEBUG, logger=greeting.logger.name):
        assert greeting._get_display_name(1) is None

    assert "lookup failed: motivo=InvalidTextRepresentation sqlstate=22P02" in caplog.text, caplog.text
    assert not (p := _vazou(_texto_debug(caplog), str(_ALHEIO))), f"{p!r} vazou: {caplog.text}"


def test_t8_boas_vindas_seguem_sem_o_nome_e_sem_o_texto(monkeypatch, caplog):
    erro = _erro_real()
    enviados = []
    monkeypatch.setattr(db, "get_auth_user", _levanta(erro))
    monkeypatch.setattr(wa_tutorial, "send_interactive_buttons", lambda **k: enviados.append(k))

    with caplog.at_level(logging.DEBUG, logger=wa_tutorial.logger.name):
        wa_tutorial.send_welcome(NUMERO, user_id=1)

    assert [e["header"] for e in enviados] == ["🐷 Bem-vindo ao PigBank!"], enviados
    assert "name lookup failed: motivo=InvalidTextRepresentation" in caplog.text, caplog.text
    assert not (p := _vazou(_texto_debug(caplog), str(_ALHEIO))), f"{p!r} vazou: {caplog.text}"


def test_t8_guarda_de_afirmacoes_falha_sem_pilha_e_a_resposta_sai(monkeypatch, caplog):
    uid, erro = _uid(), _erro_real()
    monkeypatch.setattr("core.services.ai_guard.check", _levanta(erro))
    final = "🐷 Você tem R$ 999,99 disponível."

    with caplog.at_level(logging.DEBUG, logger=runner.logger.name):
        resposta = runner._run_tool_loop(_cliente(final, pede_tool=False), uid, _historico(uid))

    assert resposta == final
    falhas = [r for r in caplog.records if "guarda de afirmações falhou" in r.getMessage()]
    assert len(falhas) == 1 and falhas[0].exc_info is None, falhas
    assert "motivo=InvalidTextRepresentation sqlstate=22P02" in falhas[0].getMessage()
    assert not (p := _vazou(falhas[0].getMessage(), str(_ALHEIO))), f"{p!r} vazou: {falhas}"


# ── T10 ──────────────────────────────────────────────────────────────────────

def test_t10_recategorizar_nao_loga_a_categoria_do_usuario(monkeypatch, caplog):
    uid, nome = _uid(), "Psicólogo da Zélia"
    assert "50" in _diga(uid, "gastei 50 no mercado")
    lid = db.list_launches(uid, limit=1)[0]["id"]
    respostas = _wa(monkeypatch, uid)

    with caplog.at_level(logging.INFO):
        wa_runtime.process_message(_botao(f"{wa_runtime.WA_RECAT_PICK_PREFIX}{lid}:{nome}", uid))

    assert "atualizada" in respostas[-1], respostas
    categoria = db.list_launches(uid, limit=1)[0]["categoria"]
    assert categoria != "mercado", categoria
    linhas = [r.getMessage() for r in caplog.records if "WA recategorize pick" in r.getMessage()]
    assert len(linhas) == 1 and f"launch={lid}" in linhas[0], linhas
    assert not (p := _vazou(caplog.text, nome, categoria)), f"{p!r} vazou: {caplog.text}"


# ── T11 ──────────────────────────────────────────────────────────────────────

def test_t11_codigo_de_verificacao_so_no_corpo(monkeypatch, marca, caplog):  # noqa: F811
    code, email = "735194", "fulano.cadastro@example.com"
    enviados: list[dict] = []
    resend = SimpleNamespace(Emails=SimpleNamespace(send=lambda p: enviados.append(p) or {"id": "x"}))
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setattr(email_service, "send_email", _SEND_EMAIL_REAL)
    monkeypatch.setattr(email_service, "_get_resend", lambda: resend)

    with caplog.at_level(logging.DEBUG):
        assert email_service.send_verification_email(email, code) is True

    assert [p["to"] for p in enviados] == [[email]]
    assert enviados[0]["subject"] == "Seu código de verificação PigBank"
    assert code in enviados[0]["html"] and code in enviados[0]["text"], "o código tem de ir no corpo"
    assert code not in caplog.text, caplog.text
    assert _linhas_com(code, 0) == [], "o código ficou em system_event_logs"
    enviado = _novos(marca, "email_sent")
    assert len(enviado) == 1 and enviado[0]["details"]["subject"] == "Seu código de verificação PigBank"
