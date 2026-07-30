"""
Quando um lançamento múltiplo tem um trecho SEM valor ("gastei 500 no ifood e
paguei o aluguel"), o bot registra o que deu e PERGUNTA o valor do que faltou —
em vez de dropar o lançamento em silêncio.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from core.handlers import launches
from parsers import describe_valueless_launch


# --- unit puro ------------------------------------------------------------

def test_describe_valueless_extrai_tipo_e_desc():
    assert describe_valueless_launch("paguei o aluguel") == ("despesa", "aluguel")
    assert describe_valueless_launch("recebi de freela") == ("receita", "freela")
    assert describe_valueless_launch("gastei no mercado") == ("despesa", "mercado")


def test_describe_valueless_com_valor_retorna_none():
    assert describe_valueless_launch("paguei 100 no aluguel") is None


def test_describe_valueless_sem_verbo_retorna_none():
    assert describe_valueless_launch("no mercado") is None


def test_describe_valueless_so_verbo_retorna_none():
    assert describe_valueless_launch("paguei") is None


# --- helpers de integração ------------------------------------------------

def _valores(user_id, limit=10):
    rows = sorted(db.list_launches(user_id, limit=limit), key=lambda r: r["id"])
    return [(r["tipo"], float(r["valor"])) for r in rows]


def _pending(user_id):
    return db.get_pending_action(user_id)


# --- fluxo ----------------------------------------------------------------

def test_pergunta_valor_do_trecho_sem_numero(user_id):
    msg = launches.add(user_id, "gastei 500 no ifood e paguei o aluguel", {})

    # registrou o ifood
    assert _valores(user_id) == [("despesa", 500.0)]
    # perguntou o valor do aluguel
    assert "aluguel" in msg.lower()
    assert "quanto" in msg.lower()
    # armou o pending certo
    p = _pending(user_id)
    assert p["action_type"] == "multi_launch_values"
    assert p["payload"]["queue"] == [{"tipo": "despesa", "desc": "aluguel"}]


def test_resposta_com_valor_registra_o_que_faltava(user_id):
    launches.add(user_id, "gastei 500 no ifood e paguei o aluguel", {})
    resp = launches.resolve_multi_launch_value(user_id, "800", _pending(user_id))

    assert resp is not None
    assert _valores(user_id) == [("despesa", 500.0), ("despesa", 800.0)]
    assert db.get_balance(user_id) == Decimal("-1300")
    # fila esvaziou → pending não é mais multi_launch_values
    p = _pending(user_id)
    assert p is None or p["action_type"] != "multi_launch_values"


def test_dois_faltando_pergunta_um_a_um(user_id):
    launches.add(user_id, "recebi 100 de x e paguei o aluguel e paguei a luz", {})
    # registrou a receita, faltam aluguel e luz
    assert _valores(user_id) == [("receita", 100.0)]
    p = _pending(user_id)
    assert [i["desc"] for i in p["payload"]["queue"]] == ["aluguel", "luz"]

    # responde o primeiro
    r1 = launches.resolve_multi_launch_value(user_id, "1500", _pending(user_id))
    assert "luz" in r1.lower()  # já pergunta o próximo
    assert [i["desc"] for i in _pending(user_id)["payload"]["queue"]] == ["luz"]

    # responde o segundo
    launches.resolve_multi_launch_value(user_id, "200", _pending(user_id))
    assert _valores(user_id) == [
        ("receita", 100.0),
        ("despesa", 1500.0),
        ("despesa", 200.0),
    ]


def test_cancelar_descarta_pendencia(user_id):
    launches.add(user_id, "gastei 500 no ifood e paguei o aluguel", {})
    resp = launches.resolve_multi_launch_value(user_id, "cancelar", _pending(user_id))

    assert "aluguel" in resp.lower()
    assert _valores(user_id) == [("despesa", 500.0)]  # nada novo
    p = _pending(user_id)
    assert p is None or p["action_type"] != "multi_launch_values"


def test_resposta_sem_valor_abandona_e_retorna_none(user_id):
    launches.add(user_id, "gastei 500 no ifood e paguei o aluguel", {})
    # user muda de assunto — não é valor
    resp = launches.resolve_multi_launch_value(user_id, "qual meu saldo", _pending(user_id))

    assert resp is None  # roteador segue normal
    assert _valores(user_id) == [("despesa", 500.0)]  # nada registrado a mais
    assert _pending(user_id) is None  # pendência abandonada


def test_multi_com_todos_valores_nao_arma_pergunta(user_id):
    msg = launches.add(user_id, "gastei 500 no ifood e mais 800 no mercado", {})
    assert _valores(user_id) == [("despesa", 500.0), ("despesa", 800.0)]
    p = _pending(user_id)
    assert p is None or p["action_type"] != "multi_launch_values"
