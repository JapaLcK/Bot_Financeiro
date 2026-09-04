"""Uma linha legada `tipo='saida'` não pode fazer as barras passarem do total.

`launches.tipo` tem duas formas para a mesma coisa: a moderna ('despesa',
'receita') e a legada ('saida', 'entrada'). Nenhum escritor de hoje grava a
legada (o comentário de `_TIPO_ALIASES`, db/accounts.py), mas muito read path
ainda a trata como tipo — e os números do MÊS no dashboard vinham de queries que
discordavam entre si:

| número na tela        | de onde vem                       | lia          |
|-----------------------|-----------------------------------|--------------|
| barras de categoria   | query 6 de `get_financial_data`   | as duas      |
| gráfico de gastos/dia | query 9                           | as duas      |
| "Gastos do mês"       | query 5 → `monthly_expense`       | só a moderna |
| "sobrou este mês"     | `monthly_income - expense - apt`  | só a moderna |
| evolução por mês      | `get_monthly_history`             | só a moderna |

Com uma linha legada na base, a soma das barras e o gráfico diário PASSAVAM do
"Gastos do mês", e o "sobrou" saía maior do que é — dinheiro a mais na cara do
usuário, sem erro nenhum.

Reconferido na produção (Railway) em 27/08/2026 21:50 UTC, sobre o head final
deste PR: `count(*) filter (where tipo='saida')` = 0 e o mesmo para 'entrada' em
`launches` — ZERO linhas legadas, então nenhum número de usuário muda hoje. O
conserto é da convenção, não de um incêndio.

Controle NEGATIVO: volte a query 5 para `SELECT tipo, ... GROUP BY tipo` em
frontend/finance_bot_websocket_custom.py — `test_barras_nao_passam_do_total_do_mes`
fica vermelho (barras 150 × total 50).
Controle POSITIVO: `test_base_sem_linha_legada_nao_muda_nenhum_numero` prova que
uma base normal (que é 100% da produção hoje) responde exatamente o mesmo.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time

import db
from db.accounts import _TIPO_ALIASES
from db.connection import TIPO_CANON_SQL, TIPO_DESPESA_SQL, TIPO_RECEITA_SQL
from utils_date import today_tz
import frontend.finance_bot_websocket_custom as dashboard


def _hoje_as(hora: int):
    return datetime.combine(today_tz(), time(hora, 0))


def _grava_tipo_legado(user_id, tipo, valor, categoria, *,
                       nota=None, alvo=None, criado_em=None, interno=False):
    """`add_launch_and_update_balance` não grava a forma legada (nem deve): a
    linha antiga entra por SQL, que é como ela existe numa base de verdade.

    `nota`/`alvo`/`criado_em`/`interno` são os eixos que os OUTROS leitores da
    forma legada precisam variar (descrição para casar merchant, mês anterior,
    saída interna) — ver `tests/test_tipo_legado_sem_numero.py`.

    `alvo` importa no dedupe do Open Finance: `alvo` NULO cai em
    `_is_generic_merchant` (db/open_finance.py:1322) e casa com QUALQUER
    estabelecimento — ver `tests/test_tipo_legado_no_dedupe_do_of.py`.

    `interno=True` marca `is_internal_movement` — a transferência antiga, que é
    linha legada E movimento interno ao mesmo tempo — e, sem `nota` explícita,
    a linha sai como "legado-interno" (é por esse texto que
    `tests/test_tipo_legado_na_cauda.py` a reconhece na lista do dia)."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria, nota, alvo, "
            "criado_em, is_internal_movement) values (%s,%s,%s,%s,%s,%s,%s,%s)",
            (user_id, tipo, valor, categoria,
             nota or ("legado-interno" if interno else "legado"), alvo,
             criado_em or _hoje_as(9), interno),
        )
        conn.commit()


def _dados(user_id):
    hoje = today_tz()
    return asyncio.run(
        dashboard.get_financial_data(user_id, year=hoje.year, month=hoje.month)
    )


# ── a fonte única das duas formas ──────────────────────────────────────────

def test_o_sql_e_o_python_falam_dos_mesmos_aliases():
    """§0.7: a regra existe em Python (`_TIPO_ALIASES`, para a lista de uma
    categoria) e em SQL (`db/connection.py`, para o dashboard). Enquanto forem
    duas, um teste compara — senão só uma é corrigida no dia em que mudar."""
    for tipo in _TIPO_ALIASES["despesa"]:
        assert f"'{tipo}'" in TIPO_DESPESA_SQL, (tipo, TIPO_DESPESA_SQL)
    for tipo in _TIPO_ALIASES["receita"]:
        assert f"'{tipo}'" in TIPO_RECEITA_SQL, (tipo, TIPO_RECEITA_SQL)
    # e o CASE que colapsa a legada na moderna usa exatamente esses dois
    assert TIPO_DESPESA_SQL in TIPO_CANON_SQL and TIPO_RECEITA_SQL in TIPO_CANON_SQL


# ── os números do mês têm que concordar ────────────────────────────────────

def test_barras_nao_passam_do_total_do_mes(pro_user_id):
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "compra", None,
        categoria="mercado", criado_em=_hoje_as(10),
    )
    _grava_tipo_legado(pro_user_id, "saida", 100, "mercado")

    d = _dados(pro_user_id)
    barras = sum(c["total"] for c in d["expense_categories"])
    diario = sum(x["total"] for x in d["daily_expenses"])

    assert d["monthly_expense"] == 150.0, d["monthly_expense"]
    assert barras == d["monthly_expense"], (barras, d["monthly_expense"])
    assert diario == d["monthly_expense"], (diario, d["monthly_expense"])


def test_receita_legada_entra_no_total_e_no_sobrou(pro_user_id):
    """`entrada` é o espelho de `saida`: fora do total, o "sobrou este mês"
    (receitas − gastos − aportes, calculado no front a partir destes números)
    aparece MENOR do que é."""
    _grava_tipo_legado(pro_user_id, "entrada", 300, "salario")
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 200, "freela", None,
        categoria="rendimentos", criado_em=_hoje_as(11),
    )
    d = _dados(pro_user_id)
    assert d["monthly_income"] == 500.0, d["monthly_income"]


def test_evolucao_por_mes_bate_com_o_gasto_do_mes(pro_user_id):
    """A barra do mês corrente da evolução e o "Gastos do mês" são o mesmo
    número em duas telas — não podem discordar por causa do tipo."""
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "compra", None,
        categoria="mercado", criado_em=_hoje_as(10),
    )
    _grava_tipo_legado(pro_user_id, "saida", 100, "mercado")

    d = _dados(pro_user_id)
    hist = asyncio.run(dashboard.get_monthly_history(pro_user_id, n_months=1))
    mes = today_tz().strftime("%Y-%m")
    linha = [h for h in hist if h["month"] == mes]
    assert linha, hist
    assert linha[0]["expense"] == d["monthly_expense"] == 150.0, (linha, d["monthly_expense"])


def test_base_sem_linha_legada_nao_muda_nenhum_numero(pro_user_id):
    """Controle POSITIVO — é a base da produção inteira hoje (zero linhas
    legadas; mesma medição do topo do arquivo, 27/08/2026 21:50 UTC): os mesmos
    números de sempre."""
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "compra", None,
        categoria="mercado", criado_em=_hoje_as(10),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 80, "freela", None,
        categoria="rendimentos", criado_em=_hoje_as(11),
    )
    d = _dados(pro_user_id)
    assert d["monthly_expense"] == 50.0, d["monthly_expense"]
    assert d["monthly_income"] == 80.0, d["monthly_income"]
    assert sum(c["total"] for c in d["expense_categories"]) == 50.0, d["expense_categories"]
    assert sum(x["total"] for x in d["daily_expenses"]) == 50.0, d["daily_expenses"]
