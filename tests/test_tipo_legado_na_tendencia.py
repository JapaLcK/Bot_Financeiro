"""A forma legada do `tipo` também conta nos leitores de TENDÊNCIA mensal.

Irmão do `tests/test_tipo_legado_no_dashboard.py`, encaminhado do #158 para cá
por verificabilidade (`CLAUDE.md` §4): lá o conserto foi nos números do MÊS do
dashboard, aqui são os três leitores que respondem a mesma pergunta por outras
telas —

| leitor                          | onde aparece        | lia antes                    |
|---------------------------------|---------------------|------------------------------|
| `get_spending_trend`            | tool de tendência   | só a moderna, as duas fora   |
|                                 | da IA               |                              |
| `compute_kpis` (SQL)            | KPI da Análise      | 'saida' sim, 'entrada' NÃO   |
| `compute_kpis` (Python)         | o mesmo             | espelhava a assimetria       |

O `entrada` faltando nos dois últimos é o pior dos três: a receita legada some
do income, o `net` sobe, e a `savings_rate` (net/income) sai errada nos DOIS
sentidos ao mesmo tempo.

Conserto: `TIPO_CANON_SQL` colapsa a forma legada na moderna no SQL, e o Python
deixa de repetir a lista de aliases — uma regra, um lugar (§0.7).

Medido na produção (Railway) em 27/08/2026 21:50 UTC: `count(*) filter (where
tipo='saida')` = 0 e o mesmo para 'entrada' em `launches`. Nenhum número de
usuário muda hoje; isto fecha a classe, não apaga um incêndio.

Controle NEGATIVO: volte `{TIPO_CANON_SQL} as tipo` + o filtro para
`tipo, valor` + `and tipo in ('despesa','receita')` em `get_spending_trend`
(db/accounts.py), ou o par equivalente em `compute_kpis` (db/analytics.py) —
`test_tendencia_conta_as_duas_formas` e `test_kpis_contam_as_duas_formas` ficam
vermelhos, cada um pelo seu lado.
Controle POSITIVO: `test_base_moderna_responde_o_mesmo_de_sempre` — a base de
100% da produção hoje, que não pode ter mudado de número.
"""
from datetime import datetime, time, timedelta

import db
from db.analytics import compute_kpis
from utils_date import month_range_today, today_tz


def _grava(user_id, tipo, valor, categoria="mercado", dia=None):
    """A forma legada não tem escritor (nem deve ter): entra por SQL, que é como
    ela existe numa base de verdade."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria, nota, "
            "criado_em, is_internal_movement) values (%s,%s,%s,%s,%s,%s,false)",
            (user_id, tipo, valor, categoria, "legado",
             datetime.combine(dia or today_tz(), time(10, 0))),
        )
        conn.commit()


def _mes_corrente(trend):
    hoje = today_tz()
    linha = [t for t in trend if t["year"] == hoje.year and t["month"] == hoje.month]
    assert linha, trend
    return linha[0]


# ── get_spending_trend: a tool de tendência da IA ──────────────────────────

def test_tendencia_conta_as_duas_formas(pro_user_id):
    _grava(pro_user_id, "despesa", 50)
    _grava(pro_user_id, "saida", 100)      # despesa legada
    _grava(pro_user_id, "receita", 200, categoria="rendimentos")
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")   # receita legada

    linha = _mes_corrente(db.get_spending_trend(pro_user_id, months=1))
    assert linha["despesa"] == 150.0, linha
    assert linha["receita"] == 500.0, linha


# ── compute_kpis: o SQL e o agregador Python da Análise ────────────────────

def test_kpis_contam_as_duas_formas(pro_user_id):
    """`entrada` era a que faltava: sem ela o income cai, o net sobe e a
    savings_rate mente nos dois sentidos de uma vez."""
    _grava(pro_user_id, "despesa", 50)
    _grava(pro_user_id, "saida", 100)
    _grava(pro_user_id, "receita", 200, categoria="rendimentos")
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")

    start, end = month_range_today()
    k = compute_kpis(pro_user_id, start, end + timedelta(days=1))

    assert k["total_expense"] == 150.0, k
    assert k["total_income"] == 500.0, k
    assert k["net"] == 350.0, k
    assert k["transactions_count"] == 4, k


def test_as_duas_telas_dao_o_mesmo_numero(pro_user_id):
    """O ponto do apontamento: o mesmo mês não pode valer um número na Análise e
    outro na tendência da IA."""
    _grava(pro_user_id, "saida", 100)
    _grava(pro_user_id, "entrada", 300, categoria="rendimentos")

    start, end = month_range_today()
    k = compute_kpis(pro_user_id, start, end + timedelta(days=1))
    linha = _mes_corrente(db.get_spending_trend(pro_user_id, months=1))

    assert (k["total_expense"], k["total_income"]) == (linha["despesa"], linha["receita"])


# ── controle positivo ──────────────────────────────────────────────────────

def test_base_moderna_responde_o_mesmo_de_sempre(pro_user_id):
    """Controle POSITIVO — 100% da produção hoje (zero linhas legadas, medido em
    27/08/2026 21:50 UTC). Um alias que mudasse ESTES números seria pior que o
    descasamento que ele conserta.

    A movimentação interna continua fora dos dois leitores, que é a outra coisa
    que um alias mal escrito quebraria.
    """
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 45, "compra", None, categoria="mercado",
        criado_em=datetime.combine(today_tz(), time(12, 0)),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 80, "freela", None, categoria="rendimentos",
        criado_em=datetime.combine(today_tz(), time(13, 0)),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 500, "aporte", None, categoria="investimento_aporte",
        criado_em=datetime.combine(today_tz(), time(14, 0)), is_internal_movement=True,
    )

    linha = _mes_corrente(db.get_spending_trend(pro_user_id, months=1))
    assert (linha["despesa"], linha["receita"]) == (45.0, 80.0), linha

    start, end = month_range_today()
    k = compute_kpis(pro_user_id, start, end + timedelta(days=1))
    assert (k["total_expense"], k["total_income"]) == (45.0, 80.0), k
