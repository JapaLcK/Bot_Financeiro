"""
Múltiplos lançamentos numa única mensagem de TEXTO digitado.

Regressão: "gastei 500 no ifood e mais 800 no mercado" registrava só o 500 —
o texto digitado nunca era separado (só o áudio era). Agora
`launches.add` usa `parsers.split_financial_transactions` e registra cada
lançamento, com o segmento implícito ("... e mais 800 ...") herdando o
verbo/tipo do anterior.
"""
from __future__ import annotations

from decimal import Decimal

import db
from core.handlers import launches


def _valores(user_id, limit=10):
    """Valores dos lançamentos, do mais antigo pro mais novo, como floats."""
    rows = db.list_launches(user_id, limit=limit)
    rows = sorted(rows, key=lambda r: r["id"])
    return [(r["tipo"], float(r["valor"])) for r in rows]


def test_dois_gastos_e_mais_implicito(user_id):
    msg = launches.add(user_id, "gastei 500 no ifood e mais 800 no mercado", {})

    assert _valores(user_id) == [("despesa", 500.0), ("despesa", 800.0)]
    # ambas as confirmações aparecem na resposta
    assert msg.count("Despesa registrada") == 2
    assert db.get_balance(user_id) == Decimal("-1300")


def test_tres_gastos(user_id):
    launches.add(user_id, "gastei 500 no ifood e mais 800 no mercado e 20 no pão", {})
    assert _valores(user_id) == [
        ("despesa", 500.0),
        ("despesa", 800.0),
        ("despesa", 20.0),
    ]


def test_receita_e_despesa_mistas(user_id):
    # verbo explícito em cada trecho → cada um mantém seu tipo
    launches.add(user_id, "recebi 600 da mãe e gastei 100 no mercado", {})
    assert _valores(user_id) == [("receita", 600.0), ("despesa", 100.0)]


def test_duas_receitas_implicito_herda_receita(user_id):
    launches.add(user_id, "recebi 1000 de salário e mais 200 de freela", {})
    assert _valores(user_id) == [("receita", 1000.0), ("receita", 200.0)]


def test_lancamento_unico_continua_funcionando(user_id):
    msg = launches.add(user_id, "gastei 50 no mercado", {})
    assert _valores(user_id) == [("despesa", 50.0)]
    assert msg.count("Despesa registrada") == 1


def test_conector_mais_sozinho(user_id):
    launches.add(user_id, "gastei 40 no almoço mais 15 no café", {})
    assert _valores(user_id) == [("despesa", 40.0), ("despesa", 15.0)]
