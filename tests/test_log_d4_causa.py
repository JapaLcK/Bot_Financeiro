"""`causa_segura` e `causa_do_provedor` (core/pg_text.py): a saída de `print` e log para uma exceção.

Unitário, tabela de entrada e saída: rodam dentro de `except`, então exceção hostil não pode trocar o
desfecho nem levar texto. O caminho real está em `tests/test_log_d4_conversa.py`.
"""
from __future__ import annotations

import logging
from http import HTTPStatus

import pytest

import core.intent_classifier as ic
import db
from _log_dono_helpers import FONE, _erro_real, _marcador, _uid, _violacao
from core.handlers import greeting
from core.pg_text import causa_do_provedor, causa_segura
from core.services.ai_chat import chips
from core.services.pluggy import PluggyApiError
from test_of_log_dono_coluna import _levanta
from tests._ia_falsa_helpers import openai_falso


def test_t9_causa_segura():
    marcador = _marcador()
    real = causa_segura(_erro_real())
    assert real == "motivo=InvalidTextRepresentation sqlstate=22P02 status=None"
    assert causa_segura(_violacao(marcador)) == "motivo=UniqueViolation sqlstate=23505 status=None"
    pluggy = PluggyApiError(marcador)
    pluggy.status_code = 503
    assert causa_segura(pluggy) == "motivo=PluggyApiError sqlstate=None status=503"
    assert causa_segura(ValueError(marcador)) == "motivo=ValueError sqlstate=None status=None"


class _Levanta(Exception):
    @property
    def status_code(self):
        raise RuntimeError("boom dentro do except")


class _StatusTexto(Exception):
    status_code = f"texto {FONE}"


class _Hostil:
    def __format__(self, spec):
        raise ValueError("fmt")


class _SqlstateHostil(Exception):
    sqlstate = _Hostil()


class _StrHostil(str):
    def __format__(self, spec):
        raise ValueError("fmt")


class _SqlstateStr(Exception):
    sqlstate = _StrHostil("22P02")


class _SqlstateTexto(Exception):
    sqlstate = f"texto {FONE}"


class _SqlstateComRabo(Exception):
    sqlstate = "22P02 zélia"


class _StatusBool(Exception):
    status_code = True


class _IntHostil(int):
    def __format__(self, spec):
        raise ValueError("fmt")


class _StatusIntHostil(Exception):
    status_code = _IntHostil(500)


class _CodeLevanta(Exception):
    @property
    def code(self):
        raise RuntimeError("boom dentro do except")


class _CodeTexto(Exception):
    code = f"texto {FONE}"


class _CodeNumero(Exception):
    code = FONE


class _CodeHostil(Exception):
    code = _StrHostil("insufficient_quota")


@pytest.mark.parametrize("erro", [_CodeLevanta, _CodeTexto, _CodeNumero, _CodeHostil])
def test_t9_code_hostil_nao_troca_o_desfecho_nem_vaza(monkeypatch, caplog, capsys, erro):
    uid = _uid()
    esperado = f"motivo={erro.__name__} sqlstate=None status=None code=None"
    openai_falso(monkeypatch, erro("x"), erro("x"))

    with caplog.at_level(logging.INFO, logger=chips.logger.name):
        assert chips.sugerir("quanto gastei?", "R$ 10", []) == []
    assert ic._classify_llm_call("oi", uid).intent == "out_of_scope"

    assert esperado in caplog.text and esperado in capsys.readouterr().out, caplog.text


@pytest.mark.parametrize("erro", [_Levanta, _StatusTexto, _SqlstateHostil, _SqlstateStr, _SqlstateTexto,
                                  _SqlstateComRabo, _StatusBool, _StatusIntHostil])
def test_t9_causa_segura_com_excecao_hostil_nao_levanta_nem_vaza(monkeypatch, erro):
    assert causa_segura(erro("x")) == f"motivo={erro.__name__} sqlstate=None status=None"
    monkeypatch.setattr(db, "get_auth_user", _levanta(erro("x")))
    assert greeting._get_display_name(1) is None, "o fallback do `except` tem de sair"


def test_t9_causa_segura_mantem_status_inteiro_e_sqlstate_real():
    erro = ValueError("x")
    erro.status_code, erro.sqlstate = 429, "23505"
    assert causa_segura(erro) == "motivo=ValueError sqlstate=23505 status=429"


@pytest.mark.parametrize("code, esperado", [
    ("insufficient_quota", "insufficient_quota"),
    ("invalid_value", "invalid_value"),
    ("a" * 60, "a" * 60),
    ("Psiquiatra", "Psiquiatra"),           # filtro de forma, não catálogo
    ("a" * 61, None),
    ("x11987654321", None),
    ("wa5511987654321", None),
    ("cpf.123.456.789-01", None),
    (f"texto {FONE}", None),
    (429, None),
], ids=str)
def test_t9_code_passa_pelo_filtro_de_forma(code, esperado):
    erro = ValueError("x")
    erro.code = code
    assert causa_do_provedor(erro) == f"motivo=ValueError sqlstate=None status=None code={esperado}"


# ── `status` de httpx e requests (#1477, item 4): vem de `response.status_code` ──
# Nenhum ponto do PR-9b recebe estas exceções hoje (plano §4): o caminho real é o dos topos de tarefa
# do 9c. Aqui, exceção real das bibliotecas, com segredo e telefone na URL que nunca podem sair.

_URL = f"https://graph.example/v1?access_token=SEGREDO657&to=55{FONE}"


def _httpx_503():
    import httpx
    req = httpx.Request("GET", _URL)
    return httpx.HTTPStatusError(f"x {_URL}", request=req, response=httpx.Response(503, request=req))


def _requests_401():
    import requests
    r = requests.Response()
    r.status_code, r.url = 401, _URL
    return requests.HTTPError(f"401 for url: {_URL}", response=r)


def _requests_sem_response():
    import requests
    return requests.HTTPError(f"x {_URL}")


def _requests_conexao():
    import requests
    return requests.ConnectionError(f"x {_URL}")


def _httpx_conexao():
    import httpx
    return httpx.ConnectError(f"falhou {_URL}", request=httpx.Request("GET", _URL))


class _Resposta:
    def __init__(self, status):
        self.status_code = status


class _ResponseLevanta(Exception):
    @property
    def response(self):
        raise RuntimeError("boom dentro do except")


class _ResponseTexto(Exception):
    response = f"texto {FONE}"


class _ResponseStatusBool(Exception):
    response = _Resposta(True)


class _ResponseStatusTexto(Exception):
    response = _Resposta(f"texto {FONE}")


class _StatusTextoComResposta(Exception):
    """O próprio não é `int`: vale o da resposta (o ramo é `is not int`, não `is None`)."""
    status_code, response = f"texto {FONE}", _Resposta(502)


class _StatusEnumComResposta(Exception):
    status_code, response = HTTPStatus.BAD_GATEWAY, _Resposta(503)


class _StatusAbsurdo(Exception):
    status_code = 10**5000  # formatar estoura o limite de 4300 dígitos do `int` -> `str`


class _ResponseStatusAbsurdo(Exception):
    response = _Resposta(10**5000)


class _StatusNegativo(Exception):
    status_code = -10**5000  # o piso da faixa também barra a formatação que levanta


class _ResponseStatusNegativo(Exception):
    response = _Resposta(-10**5000)


@pytest.mark.parametrize("faz, esperado", [
    (_httpx_503, "motivo=HTTPStatusError sqlstate=None status=503"),
    (_requests_401, "motivo=HTTPError sqlstate=None status=401"),
    (_requests_sem_response, "motivo=HTTPError sqlstate=None status=None"),
    (_requests_conexao, "motivo=ConnectionError sqlstate=None status=None"),
    (_httpx_conexao, "motivo=ConnectError sqlstate=None status=None"),
    (lambda: _ResponseLevanta(_URL), "motivo=_ResponseLevanta sqlstate=None status=None"),
    (lambda: _ResponseTexto(_URL), "motivo=_ResponseTexto sqlstate=None status=None"),
    (lambda: _ResponseStatusBool(_URL), "motivo=_ResponseStatusBool sqlstate=None status=None"),
    (lambda: _ResponseStatusTexto(_URL), "motivo=_ResponseStatusTexto sqlstate=None status=None"),
    (lambda: _StatusTextoComResposta(_URL), "motivo=_StatusTextoComResposta sqlstate=None status=502"),
    (lambda: _StatusEnumComResposta(_URL), "motivo=_StatusEnumComResposta sqlstate=None status=503"),
    (lambda: _StatusAbsurdo(_URL), "motivo=_StatusAbsurdo sqlstate=None status=None"),
    (lambda: _ResponseStatusAbsurdo(_URL), "motivo=_ResponseStatusAbsurdo sqlstate=None status=None"),
    (lambda: _StatusNegativo(_URL), "motivo=_StatusNegativo sqlstate=None status=None"),
    (lambda: _ResponseStatusNegativo(_URL), "motivo=_ResponseStatusNegativo sqlstate=None status=None"),
], ids=["httpx_503", "requests_401", "requests_sem_response", "requests_conexao", "httpx_conexao",
        "response_levanta", "response_str", "response_status_bool", "response_status_texto",
        "status_texto_com_resposta", "status_enum_com_resposta", "status_absurdo", "response_status_absurdo",
        "status_negativo", "response_status_negativo"])
def test_causa_segura_le_o_status_da_resposta_http_sem_a_url(faz, esperado):
    erro = faz()
    assert "SEGREDO657" in str(erro) or FONE in str(erro), "pré-condição: o texto cru traz o segredo"
    saida = causa_segura(erro)
    assert saida == esperado
    assert "SEGREDO657" not in saida and FONE not in saida


def test_status_proprio_vence_o_da_resposta():
    """`openai.APIStatusError` traz os dois: o próprio é o que vale."""
    erro = ValueError("x")
    erro.status_code, erro.response = 429, _Resposta(500)
    assert causa_segura(erro) == "motivo=ValueError sqlstate=None status=429"
