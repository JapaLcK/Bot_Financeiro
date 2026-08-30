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


# ── Os três vereditos do /health ────────────────────────────────────────────
# `{"status":"ok"}` sem `commit` cobre dois estados que pedem reações opostas:
# produção ainda no código anterior (esperar) e código novo no ar com token
# errado (reprovar já). O `Cache-Control` é o que os separa — a versão anterior
# de /health não manda header nenhum, a nova sempre manda `no-store`, medido em
# produção. Sem isso, toda configuração errada custa o timeout inteiro.

_NO_STORE = "no-store"
_VELHO = ""  # a versão anterior de /health não manda Cache-Control


def test_codigo_velho_sem_commit_espera():
    veredito, _ = smoke_prod.veredito_do_health(_VELHO, {"status": "ok"}, _SHA)
    assert veredito == "espera"


def test_codigo_novo_sem_commit_reprova_na_hora():
    veredito, motivo = smoke_prod.veredito_do_health(_NO_STORE, {"status": "ok"}, _SHA)
    assert veredito == "falha"
    assert "token" in motivo.lower()


@pytest.mark.parametrize("corpo", [{"status": "ok", "commit": ""}, {"status": "ok", "commit": "   "}])
def test_commit_vazio_conta_como_ausente(corpo):
    """`commit: ""` é a variável definida e vazia — não é um commit, e sem esta
    normalização cairia no sha_bate em vez do ramo de ausente."""
    assert smoke_prod.veredito_do_health(_VELHO, corpo, _SHA)[0] == "espera"
    assert smoke_prod.veredito_do_health(_NO_STORE, corpo, _SHA)[0] == "falha"


def test_unknown_reprova_com_qualquer_header():
    for cc in (_VELHO, _NO_STORE):
        veredito, motivo = smoke_prod.veredito_do_health(cc, {"commit": "unknown"}, _SHA)
        assert veredito == "falha"
        assert "RAILWAY_GIT_COMMIT_SHA" in motivo


def test_commit_certo_abre_e_commit_de_outro_deploy_espera():
    """Controle positivo: sem ele, um veredito que reprova/espera sempre passaria
    em todos os testes acima e o gate nunca abriria."""
    assert smoke_prod.veredito_do_health(_NO_STORE, {"commit": _SHA}, _SHA) == ("abre", _SHA)
    assert smoke_prod.veredito_do_health(_NO_STORE, {"commit": _SHA[:7]}, _SHA)[0] == "abre"
    assert smoke_prod.veredito_do_health(_NO_STORE, {"commit": "0" * 40}, _SHA)[0] == "espera"
