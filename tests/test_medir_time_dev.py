"""Testa só a lógica pura de `scripts/medir_time_dev.py`: parse do marcador e
soma de tokens deduplicada. Sem rede, sem `gh`, sem disco."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from medir_time_dev import (  # noqa: E402
    extrair_mapa_agentes,
    parse_marcador,
    somar_tokens,
)


def _usage(mid, entrada=10, cache_criacao=0, cache_leitura=0, saida=5):
    return {
        "id": mid,
        "usage": {
            "input_tokens": entrada,
            "cache_creation_input_tokens": cache_criacao,
            "cache_read_input_tokens": cache_leitura,
            "output_tokens": saida,
        },
    }


def test_parse_marcador_valido():
    corpo = "texto\n<!-- time-dev: grupo=com faixa=Leve internos=3 bloqueantes=1 -->\nresto"
    assert parse_marcador(corpo) == {
        "grupo": "com", "faixa": "Leve", "internos": 3, "bloqueantes": 1,
    }


def test_parse_marcador_ausente():
    assert parse_marcador("PR sem marcador nenhum") is None
    assert parse_marcador("") is None
    assert parse_marcador(None) is None


def test_parse_marcador_malformado():
    # grupo fora do vocabulário
    assert parse_marcador("<!-- time-dev: grupo=talvez faixa=Leve internos=0 bloqueantes=0 -->") is None
    # falta um campo
    assert parse_marcador("<!-- time-dev: grupo=com faixa=Leve internos=0 -->") is None
    # internos não é inteiro
    assert parse_marcador("<!-- time-dev: grupo=com faixa=Leve internos=x bloqueantes=0 -->") is None


def test_somar_tokens_dedup_por_message_id():
    # mesma message.id em duas linhas (dois blocos de conteúdo) conta uma vez.
    linhas = [
        {"gitBranch": "b", "message": _usage("m1", entrada=100, saida=10)},
        {"gitBranch": "b", "message": _usage("m1", entrada=100, saida=10)},
        {"gitBranch": "b", "message": _usage("m2", entrada=1, saida=1)},
    ]
    soma = somar_tokens(linhas, "b")
    assert soma == {"entrada": 101, "saida": 11}


def test_somar_tokens_filtra_branch_exato_mesmo_com_prefixo():
    # "Japa/foo" é prefixo de "Japa/foo-bar": não pode vazar tokens do outro branch.
    linhas = [
        {"gitBranch": "Japa/foo", "message": _usage("a", entrada=5, saida=1)},
        {"gitBranch": "Japa/foo-bar", "message": _usage("b", entrada=999, saida=999)},
    ]
    assert somar_tokens(linhas, "Japa/foo") == {"entrada": 5, "saida": 1}
    assert somar_tokens(linhas, "Japa/foo-bar") == {"entrada": 999, "saida": 999}


def test_somar_tokens_branch_none_nao_filtra():
    # subagente sem gitBranch próprio: já vem pré-filtrado pelo chamador (I/O).
    linhas = [{"message": _usage("a", entrada=3, saida=2)}]
    assert somar_tokens(linhas, None) == {"entrada": 3, "saida": 2}


def test_extrair_mapa_agentes():
    linhas = [
        {"type": "assistant", "message": {}},
        {"type": "user", "toolUseResult": {"agentId": "id1", "agentType": "coder"}},
        {"type": "user", "toolUseResult": {"agentId": "id2", "agentType": "tester"}},
        {"type": "user", "toolUseResult": "não é dict"},
    ]
    assert extrair_mapa_agentes(linhas) == {"id1": "coder", "id2": "tester"}
