"""Erros do chat especialista: recuperação explícita e logs sem conteúdo financeiro."""
from __future__ import annotations

import logging
from uuid import uuid4

from core.observability import _log_falha


class ChatError(Exception):
    def __init__(self, code: str, status: int, message: str, *,
                 retryable: bool = False, request_id: str | None = None):
        super().__init__(message)
        self.code, self.status = code, status
        self.retryable, self.request_id = retryable, request_id


class ModelResponseError(ValueError):
    """A resposta do modelo está ausente, incompleta ou fora do contrato."""


class AnswerPolicyError(ValueError):
    def __init__(self, reason: str = "policy"):
        super().__init__("Answer outside specialist policy")
        self.reason = reason


class DataQueryError(RuntimeError):
    """Não foi possível consultar os dados necessários à resposta."""


def _failure_spec(exc: Exception, stage: str) -> tuple[str, int, str, bool]:
    if stage == "quota":
        # Uma falha ao confirmar a gravação pode acontecer depois do commit.
        return ("quota_unavailable", 503,
                "Não consegui confirmar o registro desta resposta. Confira sua cota antes de tentar novamente.", False)
    if stage == "configuration":
        return ("model_configuration_error", 503,
                "A conversa está indisponível por um problema de configuração. Tente novamente mais tarde.", False)
    if isinstance(exc, AnswerPolicyError):
        return ("answer_rejected", 422,
                "Não consegui preparar uma resposta dentro dos limites deste agente. Tente reformular a pergunta.", True)
    if isinstance(exc, ModelResponseError):
        return ("invalid_model_response", 502,
                "Não consegui concluir uma resposta válida. Tente novamente.", True)
    if isinstance(exc, DataQueryError):
        return ("data_unavailable", 503,
                "Não consegui consultar seus dados agora. Tente novamente em alguns instantes.", True)
    # O módulo de erros também precisa funcionar se a importação do SDK falhar.
    try:
        import openai
    except ImportError:
        openai = None
    if openai is not None:
        if isinstance(exc, openai.APIResponseValidationError):
            return ("invalid_model_response", 502,
                    "Não consegui concluir uma resposta válida. Tente novamente.", True)
        if isinstance(exc, openai.APITimeoutError):
            return ("model_timeout", 504,
                    "A IA demorou mais que o esperado. Tente novamente.", True)
        if isinstance(exc, openai.RateLimitError):
            return ("model_busy", 503,
                    "A IA está ocupada no momento. Aguarde um pouco e tente novamente.", True)
        if isinstance(exc, openai.APIConnectionError):
            return ("model_unavailable", 503,
                    "Não consegui me conectar à IA agora. Tente novamente em alguns instantes.", True)
        if isinstance(exc, openai.APIStatusError):
            if exc.status_code in {400, 401, 403, 404, 422}:
                return ("model_configuration_error", 503,
                        "A conversa está indisponível por um problema de configuração. Tente novamente mais tarde.", False)
            return ("model_unavailable", 503,
                    "A IA está temporariamente indisponível. Tente novamente em alguns instantes.", True)
    return ("internal_error", 500,
            "Ocorreu um erro ao preparar sua resposta. Tente novamente mais tarde.", False)


def handle_failure(exc: Exception, user_id: int, kind: str, stage: str) -> ChatError:
    if isinstance(exc, ChatError):
        return exc
    code, status, message, retryable = _failure_spec(exc, stage)
    request_id = uuid4().hex
    provider_status = getattr(exc, "status_code", None)
    if not isinstance(provider_status, int):
        provider_status = None
    _log_falha(
        "agent_chat", user_id, exc,
        nivel=logging.WARNING if isinstance(exc, AnswerPolicyError) else logging.ERROR,
        kind=kind, stage=stage, code=code, provider_status=provider_status,
        request_id=request_id,
    )
    if stage != "quota":
        message += " Sua cota não foi descontada."
    return ChatError(code, status, message, retryable=retryable, request_id=request_id)
