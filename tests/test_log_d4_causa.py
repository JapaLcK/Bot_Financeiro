"""`causa_segura` e `causa_do_provedor` (core/pg_text.py): a saída de `print` e log para uma exceção.

Unitário, tabela de entrada e saída: rodam dentro de `except`, então exceção hostil não pode trocar o
desfecho nem levar texto. O caminho real está em `tests/test_log_d4_conversa.py`.
"""
from __future__ import annotations

import logging

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
