"""O gate do smoke pós-deploy: a comparação de SHA de scripts/smoke_prod.py.

Vale um teste porque o modo de falha é SILENCIOSO: se a comparação aceitar
qualquer coisa, o gate abre cedo, o smoke mede o deploy ANTERIOR e sai verde
sobre código que ninguém testou — o oposto do que ele existe para fazer.
O script não é importável como módulo do pacote, então é carregado por caminho.
"""
import importlib.util
import pathlib

import pytest

_CAMINHO = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "smoke_prod.py"
_spec = importlib.util.spec_from_file_location("smoke_prod", _CAMINHO)
smoke_prod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_prod)

_SHA = "d3ff5370a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.mark.parametrize(
    "visto",
    [
        "",  # RAILWAY_GIT_COMMIT_SHA definida e vazia: "".startswith() casa com tudo
        "   ",
        "abc",  # curto demais para identificar commit
        "unknown",  # o literal que o /health devolve quando a variável não chegou
        "z" * 40,  # não é hexadecimal
        "0" * 40,  # hex válido, mas outro commit
    ],
)
def test_nao_abre_o_gate_com_valor_que_nao_identifica_o_commit(visto):
    assert smoke_prod.sha_bate(visto, _SHA) is False


@pytest.mark.parametrize("visto", [_SHA, _SHA[:7], _SHA[:12], _SHA.upper()])
def test_abre_o_gate_no_commit_certo_inclusive_abreviado(visto):
    """Controle positivo: sem isto, uma guarda que recusa tudo passaria nos
    testes acima e o gate nunca abriria — pior que o bug."""
    assert smoke_prod.sha_bate(visto, _SHA) is True
    assert smoke_prod.sha_bate(_SHA, visto) is True
