"""O dia impresso de um lançamento é o de São Paulo nas DUAS listagens.

`criado_em` é `timestamptz`: o `datetime` que o psycopg devolve vem no fuso da
SESSÃO do Postgres — UTC no Railway, `America/New_York` no banco de teste local
—, nunca no fuso do app. `.date()` cru dava o dia daquele fuso, então um gasto
das 23:30 em São Paulo saía como o dia SEGUINTE para "liste mercado", enquanto
"meus últimos lançamentos" (já convertido com `day_tz`, PR #158) dizia o dia
certo: o MESMO lançamento com duas datas.

Controle NEGATIVO: troque `day_tz(r["dt"])` de volta por `r["dt"].date()` em
db/accounts.py (`list_launches_by_category`) — o primeiro assert fica vermelho.
Controle POSITIVO: o segundo assert é o caminho que o PR já consertou; ele prova
que a conversão continua de pé lá, e é a metade que o negativo NÃO derruba.

Os dois horários existem porque o teste tem que discriminar nos dois bancos:
00:30 em São Paulo já é o dia anterior em New York (−1h) e 23:30 já é o dia
seguinte em UTC (+3h). Com um só, o teste passaria de graça em um dos dois.
"""
from datetime import datetime, time, timedelta

import db
from core.handlers import launches as h
from utils_date import _tz, today_tz


def _grava(uid, dia, hora, minuto, nota):
    db.add_launch_and_update_balance(
        uid, "despesa", 10, nota, None, categoria="mercado",
        criado_em=datetime.combine(dia, time(hora, minuto), tzinfo=_tz()),
    )


def test_o_dia_e_o_de_sao_paulo_nas_duas_listagens(pro_user_id):
    dia = today_tz() - timedelta(days=10)   # nem "hoje" nem "ontem": sai como data
    _grava(pro_user_id, dia, 0, 30, "madrugada")
    _grava(pro_user_id, dia, 23, 30, "noite")

    rows, _ = db.list_launches_by_category(pro_user_id, "mercado")
    assert len(rows) == 2, rows
    assert {r["data"] for r in rows} == {dia}, [r["data"] for r in rows]

    texto = h.list_launches(pro_user_id, limit=10)
    assert texto.count(dia.strftime("%d/%m")) == 2, texto
