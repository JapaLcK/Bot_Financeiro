"""Falhas do especialista mantêm diagnóstico útil sem registrar a conversa."""
import logging
import re

import httpx
import openai
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.services import agent_chat
from core.services import agent_chat_errors as errors
from frontend.routes import agents


@pytest.mark.parametrize("failure", [
    errors.ChatError("unavailable", 503, "Conversa indisponível."),
    errors.ChatError("model_timeout", 504, "A IA demorou mais que o esperado.",
                     retryable=True, request_id="a" * 32),
])
def test_rota_preserva_contrato_e_expoe_campos_de_recuperacao(monkeypatch, failure):
    app = FastAPI()
    app.state.limiter = agents.shared.limiter
    app.include_router(agents.router)
    monkeypatch.setattr(agents.shared, "authorize_dashboard_access", lambda *a: None)
    monkeypatch.setattr(agents, "_require_agents_beta", lambda *a: None)

    def fail(*args):
        raise failure

    monkeypatch.setattr(agent_chat, "chat", fail)
    response = TestClient(app).post("/agents/42/carteiro/chat", json={"message": "Entradas do mês?"})
    assert response.status_code == failure.status
    assert response.json()["detail"] == {
        "error": failure.code, "message": str(failure),
        "retryable": failure.retryable, "request_id": failure.request_id,
    }


def provider_error(error_type, status):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status, request=request, headers={"x-request-id": "req-provedor"})
    return error_type("CONTEUDO_PRIVADO", response=response, body={"error": {"message": "CONTEUDO_PRIVADO"}})


@pytest.mark.parametrize("exc,stage,code,status,retryable", [
    (openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com")),
     "routing", "model_timeout", 504, True),
    (openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
     "answering", "model_unavailable", 503, True),
    (provider_error(openai.RateLimitError, 429), "routing", "model_busy", 503, True),
    (provider_error(openai.AuthenticationError, 401), "routing", "model_configuration_error", 503, False),
    (provider_error(openai.PermissionDeniedError, 403), "routing", "model_configuration_error", 503, False),
    (provider_error(openai.BadRequestError, 400), "routing", "model_configuration_error", 503, False),
    (provider_error(openai.NotFoundError, 404), "routing", "model_configuration_error", 503, False),
    (provider_error(openai.UnprocessableEntityError, 422), "routing", "model_configuration_error", 503, False),
    (provider_error(openai.InternalServerError, 500), "answering", "model_unavailable", 503, True),
    (KeyError("CONTEUDO_PRIVADO"), "configuration", "model_configuration_error", 503, False),
    (openai.APIResponseValidationError(
        httpx.Response(200, request=httpx.Request("POST", "https://api.openai.com")),
        {"message": "CONTEUDO_PRIVADO"}, message="CONTEUDO_PRIVADO"),
     "routing", "invalid_model_response", 502, True),
    (errors.ModelResponseError("CONTEUDO_PRIVADO"), "routing", "invalid_model_response", 502, True),
    (errors.AnswerPolicyError("off_topic"), "answering", "answer_rejected", 422, True),
    (errors.DataQueryError("CONTEUDO_PRIVADO"), "answering", "data_unavailable", 503, True),
    (RuntimeError("CONTEUDO_PRIVADO"), "context", "internal_error", 500, False),
])
def test_falhas_distintas_tem_recuperacao_e_diagnostico_seguro(caplog, exc, stage, code, status, retryable):
    with caplog.at_level(logging.WARNING):
        result = errors.handle_failure(exc, 42, "faria_limer", stage)
    assert (result.code, result.status, result.retryable) == (code, status, retryable)
    assert re.fullmatch(r"[0-9a-f]{32}", result.request_id)
    assert "Sua cota não foi descontada." in str(result)
    assert "CONTEUDO_PRIVADO" not in caplog.text
    assert "CONTEUDO_PRIVADO" not in str(result)
    assert f"stage={stage}" in caplog.text
    assert f"request_id={result.request_id}" in caplog.text
    records = [record for record in caplog.records if record.name == "core.observability"]
    assert len(records) == 1
    assert records[0].exc_info is None


def test_erro_de_cota_nao_promete_reembolso_nem_estimula_reenvio(caplog):
    result = errors.handle_failure(RuntimeError("CONTEUDO_PRIVADO"), 42, "carteiro", "quota")
    assert (result.code, result.status, result.retryable) == ("quota_unavailable", 503, False)
    assert "não foi descontada" not in str(result)
    assert "Confira sua cota" in str(result)
    assert "CONTEUDO_PRIVADO" not in caplog.text


def test_log_nao_inclui_chave_headers_corpo_ou_id_do_provedor(caplog):
    secret = "CHAVE_E_PERGUNTA_PRIVADAS"
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {secret}"}, json={"messages": [secret]})
    response = httpx.Response(400, request=request, headers={"x-request-id": secret})
    exc = openai.BadRequestError(secret, response=response, body={"error": {"message": secret}})
    result = errors.handle_failure(exc, 42, "carteiro", "routing")
    assert secret not in caplog.text
    assert secret not in str(result)
    assert secret != result.request_id
    assert "provider_status=400" in caplog.text
    assert "causa=BadRequestError" in caplog.text


def test_chat_error_legado_preserva_campos_e_nao_e_remapeado():
    error = errors.ChatError("activate", 403, "Ative o agente.")
    assert error.retryable is False
    assert error.request_id is None
    assert errors.handle_failure(error, 42, "carteiro", "access_final") is error


def test_motivo_de_politica_nao_altera_a_mensagem_da_excecao():
    error = errors.AnswerPolicyError("off_topic")
    assert error.reason == "off_topic"
    assert str(error) == "Answer outside specialist policy"
