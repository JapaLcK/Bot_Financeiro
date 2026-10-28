"""PR-B (ADR 0006): recusa não deixa efeito persistido. A regra da #hashtag (`user_category_rules`) só nasce quando o
lançamento grava; antes ela era aprendida no parser, ANTES de a declaração recusar (T6, T4, T9) ou de a pergunta de forma
ser respondida, e a regra recusada passava a categorizar a despesa seguinte em dinheiro ("mercado" -> investimento).

Controle negativo: `aprender = True` fixo em `launches.add` deixa vermelhos todos os casos de recusa e de pergunta.
Positivos: declaração gravada, Carteira e resposta "dinheiro" continuam aprendendo.
"""
from __future__ import annotations

import pytest

from tests._declaracao_b import ana, carteira, declaracoes, liga, manda_wamid, n_lancamentos  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests._patrimonio_helpers import q


def regras(uid) -> list[tuple[str, str]]:
    return [(r["keyword"], r["category"]) for r in
            q("select keyword, category from user_category_rules where user_id=%s", (uid,), fetch=True)]


def test_t6_recusa_nao_aprende_e_a_despesa_seguinte_nao_vira_investimento(ana, liga):
    assert "movimentação interna" in manda_wamid(ana, "gastei 200 #investimento no mercado pelo banco")[0]
    assert regras(ana) == [] and n_lancamentos(ana) == 0
    manda_wamid(ana, "gastei 50 no mercado em dinheiro vivo")
    assert q("select categoria, is_internal_movement from launches where user_id=%s", (ana,)) == {
        "categoria": "mercado", "is_internal_movement": False}


@pytest.mark.parametrize("texto,marca", [
    ("gastei 50,123 #lazer no ifood pelo banco", "2 casas"),       # T4
    ("gastei 0 #lazer no ifood pelo banco", "valor"),              # valor zero, antes de qualquer gravação
    ("gastei 2,2k #lazer no ifood pelo banco", "não tenho certeza"),  # T10
])
def test_recusas_de_valor_nao_aprendem(ana, liga, texto, marca):
    assert marca in manda_wamid(ana, texto)[0]
    assert regras(ana) == [] and declaracoes(ana) == []


def test_t9_sem_wamid_nao_aprende(ana, liga):
    assert "Não consegui confirmar seu banco" in manda_wamid(ana, "gastei 50 #lazer no ifood pelo banco", "sem-wamid")[0]
    assert regras(ana) == [] and n_lancamentos(ana) == 0


def test_pergunta_de_forma_nao_aprende_antes_da_resposta_t6(ana, liga):
    manda_wamid(ana, "gastei 200 #investimento no mercado")
    assert regras(ana) == []  # a pergunta ainda está de pé
    assert "movimentação interna" in manda_wamid(ana, "banco")[0]
    assert regras(ana) == [] and n_lancamentos(ana) == 0


def test_flag_desligada_a_recusa_de_hoje_tambem_nao_aprende(ana):
    assert "Não registrei" in manda_wamid(ana, "gastei 200 #investimento no mercado pelo banco")[0]
    manda_wamid(ana, "gastei 200 #investimento no mercado")
    assert "Não registrei" in manda_wamid(ana, "banco")[0]
    assert regras(ana) == []


def test_positivo_declaracao_gravada_com_hashtag_comum_aprende(ana, liga):
    assert "registrada pelo banco" in manda_wamid(ana, "gastei 50 #lazer no ifood pelo banco")[0]
    assert ("ifood", "lazer") in regras(ana)


def test_positivo_declaracao_pela_resposta_aprende(ana, liga):
    manda_wamid(ana, "gastei 50 #lazer no ifood")
    assert regras(ana) == []
    assert "registrada pelo banco" in manda_wamid(ana, "banco")[0]
    assert ("ifood", "lazer") in regras(ana)


def test_positivo_carteira_e_resposta_dinheiro_aprendem(ana, liga):
    manda_wamid(ana, "gastei 50 #lazer no ifood em dinheiro")
    assert ("ifood", "lazer") in regras(ana)
    manda_wamid(ana, "gastei 40 #saude na farmacia")
    assert ("farmacia", "saude") not in regras(ana)  # a pergunta de forma ainda está de pé
    manda_wamid(ana, "dinheiro")
    assert ("farmacia", "saude") in regras(ana)


def test_aprendizado_da_declaracao_roda_depois_do_commit(ana, liga, monkeypatch):
    """A regra só nasce com o lançamento já gravado: se o aprendizado fosse movido para antes da gravação, uma recusa
    do escritor (T4, T6, T3) voltaria a deixar regra para trás."""
    from core.handlers import launches
    real, vistas = launches.learn_from_explicit_category, []

    def espia(uid, *a, **kw):
        vistas.append(n_lancamentos(uid))  # quantos lançamentos existem no instante em que a regra é aprendida
        return real(uid, *a, **kw)
    monkeypatch.setattr(launches, "learn_from_explicit_category", espia)
    manda_wamid(ana, "gastei 50 #lazer no ifood pelo banco")
    assert vistas == [1] and ("ifood", "lazer") in regras(ana)


def test_aprendizado_que_estoura_nao_derruba_a_declaracao(ana, liga, monkeypatch):
    from core.handlers import launches

    def estoura(*a, **kw):
        raise RuntimeError("regra caiu")
    monkeypatch.setattr(launches, "learn_from_explicit_category", estoura)
    r = manda_wamid(ana, "gastei 50 #lazer no ifood pelo banco")[0]
    assert "registrada pelo banco" in r and len(declaracoes(ana)) == 1 and carteira(ana) == 200.0
