"""O dia impresso de um lançamento é o de São Paulo nas DUAS listagens.

`criado_em` é `timestamptz`: o `datetime` que o psycopg devolve vem no fuso da
SESSÃO do Postgres. Enquanto essa sessão era livre — UTC no Railway,
`America/New_York` no banco de teste local — `.date()` cru dava o dia daquele
fuso, e um gasto das 23:30 em São Paulo saía como o dia SEGUINTE para "liste
mercado", enquanto "meus últimos lançamentos" (já convertido com `day_tz`,
PR #158) dizia o dia certo: o MESMO lançamento com duas datas.

São dois casos com papéis diferentes, e o segundo existe porque o primeiro
perdeu o controle negativo. `utils_date.align_process_tz` põe a sessão do
Postgres no fuso do app (via `PGTZ`), então `.date()` cru e `day_tz` passam a
devolver o mesmo dia: medido em 29/08/2026, trocar `day_tz(r["dt"])` de volta
por `r["dt"].date()` em db/accounts.py deixa `test_o_dia_e_o_de_sao_paulo_...`
VERDE. Ele fica como POSITIVO declarado — a pergunta que sobra continua viva:
as duas portas têm de dizer o mesmo dia para o mesmo lançamento, e isso quebra
de novo se alguém mexer só em uma delas.

`test_day_tz_ignora_a_sessao_do_postgres` é o NEGATIVO: com a sessão alinhada,
a única forma de manter o hazard observável é abrir uma conexão que NÃO esteja
alinhada. Sem ele, `day_tz` ficou sem cobertura de produto — sabotá-lo para
`dt.date()` não deixava uma linha vermelha na suíte inteira (medido).

Os dois horários do primeiro caso continuam existindo porque descrevem o caso do
usuário: 00:30 e 23:30 em São Paulo são os instantes que cruzam a meia-noite
para cada lado.
"""
import os
from datetime import datetime, time, timedelta

import psycopg

import db
from core.handlers import launches as h
from utils_date import _tz, day_tz, today_tz


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


# Controle NEGATIVO deste caso: troque `day_tz(...)` por `dt.date()` em
# `utils_date.day_tz` → o segundo assert fica vermelho (medido em 29/08/2026:
# devolve o dia SEGUINTE). O primeiro assert é a PREMISSA e tem de continuar
# verde: sem ele, um dia em que a sessão desta conexão não estivesse mesmo em
# UTC deixaria o caso passar de graça, medindo nada.
#
# Conexão PRÓPRIA e não `db.get_conn()`: um `set time zone` na conexão do pool
# volta com ela e envenena os testes seguintes.

def test_day_tz_ignora_a_sessao_do_postgres(pro_user_id):
    """A sessão do Postgres em UTC — o que a produção tinha — não move o dia.

    `align_process_tz` alinhou a sessão, e com isso `.date()` cru passou a
    acertar por tabela: a única forma de continuar MEDINDO `day_tz` é ler o
    mesmo `timestamptz` por uma sessão desalinhada, que é o estado de qualquer
    cliente que não passe pelo `PGTZ` (psql, pgAdmin, um job de fora).
    """
    dia = today_tz() - timedelta(days=10)
    _grava(pro_user_id, dia, 23, 30, "noite em sao paulo")

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("set time zone 'UTC'")
        cur.execute(
            "select criado_em from launches where user_id=%s and alvo=%s",
            (pro_user_id, "noite em sao paulo"),
        )
        criado_em = cur.fetchone()[0]

    assert criado_em.date() == dia + timedelta(days=1), criado_em
    assert day_tz(criado_em) == dia, criado_em
