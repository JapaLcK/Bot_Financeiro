"""A barra "sem categoria" do donut e a lista daquela categoria têm que casar.

O donut do dashboard agrupa por `cat_key_sql` (db/connection.py), que colapsa
NULL e '' no rótulo `sem categoria`. A lista de uma categoria
(`list_launches_by_category`, db/accounts.py) casava contra a coluna CRUA via
`cat_norm_sql`, e `translate(lower(NULL))` é NULL — que não é igual a nada, nem a
si mesmo. Medido antes do conserto: donut `[('sem categoria', 100.0)]`, lista
`rows=0` tanto para 'sem categoria' quanto para 'outros'.

Isso chega em produção pela importação de cartão do Open Finance
(`add_imported_credit_purchase`, db/open_finance.py), que grava
`credit_transactions.categoria` NULO quando o provedor não manda categoria.

Controle NEGATIVO: troque as três `cat_key_sql` de volta por `cat_norm_sql` em
`list_launches_by_category` — os DOIS testes ficam vermelhos (0 linhas contra
R$ 120,00 e R$ 100,00 nas barras). Medido.
Controle POSITIVO: o primeiro assert de `test_categoria_normal_continua_casando`
(categoria escrita, com acento e caixa trocados → 1 linha, R$ 50,00) é o caminho
de sempre; ele passa com o conserto e passaria sem ele. Está aqui porque a
mudança RESTRINGE o casamento, e uma chave que só achasse o vazio — ou que
varresse a linha sem categoria para dentro de 'alimentação' — seria pior que o
bug original.
"""
import asyncio
from datetime import datetime, time

import db
from core.handlers import launches as h
from db.connection import CAT_VAZIA_LABEL
from utils_date import month_range_today, today_tz
import frontend.finance_bot_websocket_custom as dashboard


def _grava_sem_categoria(user_id, valor, categoria, interna=False):
    """`add_launch_and_update_balance` sempre grava alguma categoria; a linha sem
    categoria entra por SQL, que é como o importador do Open Finance a cria."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria, nota, "
            "criado_em, is_internal_movement) values (%s,'despesa',%s,%s,%s,%s,%s)",
            (user_id, valor, categoria, "importado",
             datetime.combine(today_tz(), time(10, 0)), interna),
        )
        conn.commit()


def _donut(user_id):
    hoje = today_tz()
    d = asyncio.run(dashboard.get_financial_data(user_id, year=hoje.year, month=hoje.month))
    return {c["categoria"]: c["total"] for c in d["expense_categories"]}


def test_lista_encontra_o_que_o_donut_mostra(pro_user_id):
    _grava_sem_categoria(pro_user_id, 100, None)   # NULL: o que o Open Finance grava
    _grava_sem_categoria(pro_user_id, 20, "")      # '' — o mesmo pro usuário

    assert _donut(pro_user_id) == {CAT_VAZIA_LABEL: 120.0}, _donut(pro_user_id)

    rows, resumo = db.list_launches_by_category(pro_user_id, CAT_VAZIA_LABEL)
    assert len(rows) == 2, rows
    assert resumo["despesa"] == 120.0, resumo


def test_categoria_normal_continua_casando(pro_user_id):
    """Controle POSITIVO: a chave nova ainda é case- e acento-insensível, e não
    varre a linha sem categoria para dentro de uma categoria escrita."""
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "compra", None, categoria="Alimentação",
        criado_em=datetime.combine(today_tz(), time(11, 0)),
    )
    _grava_sem_categoria(pro_user_id, 100, None)

    rows, resumo = db.list_launches_by_category(pro_user_id, "alimentacao")
    assert len(rows) == 1 and resumo["despesa"] == 50.0, (rows, resumo)

    rows, resumo = db.list_launches_by_category(pro_user_id, CAT_VAZIA_LABEL)
    assert len(rows) == 1 and resumo["despesa"] == 100.0, (rows, resumo)


def test_o_total_da_categoria_nao_chama_gasto_de_movimentacao_interna(pro_user_id):
    """O total e a lista têm que descrever as MESMAS linhas.

    `_total_despesa` (core/handlers/launches.py) subtrai
    `sum_spent_in_category_period` do resumo da lista e atribui a diferença a
    movimentação interna. Com a chave crua só de um lado, a despesa sem
    categoria dava total 0 × lista R$ 120,00, e o bot respondia — medido, com a
    lista já consertada e o total ainda não:

        🔁 R$ 120,00 movimentados em **sem categoria** neste mês.
        Não conta como gasto — é movimentação interna.

    sobre um gasto de verdade. Pior que a lista vazia de antes: número certo com
    explicação errada. Controle NEGATIVO: volte as três `cat_key_sql` de
    `sum_spent_in_category_period` (db/budgets.py) para `cat_norm_sql` — este
    teste fica vermelho.
    """
    _grava_sem_categoria(pro_user_id, 120, None)
    start, end = month_range_today()

    total = db.sum_spent_in_category_period(pro_user_id, CAT_VAZIA_LABEL, start, end)
    _, resumo = db.list_launches_by_category(
        pro_user_id, CAT_VAZIA_LABEL, start, end, tipo="despesa", limit=1,
    )
    assert total == resumo["despesa"] == 120.0, (total, resumo)

    # os "5 maiores" saem ao lado desse total na mesma resposta
    maiores = db.get_largest_expenses(pro_user_id, start, end, categoria=CAT_VAZIA_LABEL)
    assert len(maiores) == 1 and maiores[0]["valor"] == 120.0, maiores

    resposta = h._total_despesa(pro_user_id, CAT_VAZIA_LABEL, start, end, "neste mês")
    assert "movimenta" not in resposta.lower(), resposta
    assert "120,00" in resposta, resposta


def test_spend_query_soma_gasto_real_e_deixa_interna_de_fora(pro_user_id):
    """A pergunta como o usuário digita, com as duas linhas que precisam se
    separar: uma despesa REAL sem categoria e uma movimentação INTERNA sem
    categoria, no mesmo mês e na mesma categoria.

    O gasto real tem que entrar no "você gastou"; a interna tem que ficar fora
    dele e ser anunciada à parte. Antes da chave compartilhada o total dava 0 e
    as DUAS viravam "movimentação interna" — os R$ 120,00 reais junto.

    Controle NEGATIVO: `_CAT_EQ`/`_CAT_CT_EQ` ou as três `cat_key_sql` de
    `sum_spent_in_category_period` (db/budgets.py) de volta para `cat_norm_sql`
    → este teste fica vermelho.
    """
    _grava_sem_categoria(pro_user_id, 120, None)
    _grava_sem_categoria(pro_user_id, 80, None, interna=True)

    resposta = h.spend_query(pro_user_id, "quanto gastei em sem categoria")

    # (2) a despesa real conta como GASTO
    assert "R$ 120,00" in resposta, resposta
    # (3) a interna fica FORA do gasto, e aparece separada
    assert "R$ 80,00" in resposta and "movimenta" in resposta.lower(), resposta
    # e o total do gasto não é a soma das duas
    assert "R$ 200,00" not in resposta, resposta

    start, end = month_range_today()
    assert db.sum_spent_in_category_period(pro_user_id, CAT_VAZIA_LABEL, start, end) == 120.0


def test_orcamento_sem_categoria_ve_o_mesmo_gasto_do_donut(pro_user_id):
    """Um orçamento PODE se chamar "sem categoria" — `upsert_budget` aceita.

    Medido antes do conserto: o donut mostrava `budget 500,00 / 20%` sobre
    R$ 100,00, e `sum_spent_in_category_this_month('sem categoria')` devolvia
    `0.0` no mesmo mês. Duas telas, o mesmo orçamento, consumo diferente.

    Controle NEGATIVO: `_CAT_EQ`/`_CAT_CT_EQ` (db/budgets.py) de volta para
    `cat_norm_sql` → o assert do `this_month` fica vermelho.
    """
    db.upsert_budget(pro_user_id, CAT_VAZIA_LABEL, 500)
    _grava_sem_categoria(pro_user_id, 100, "")

    barras = _donut(pro_user_id)
    assert barras == {CAT_VAZIA_LABEL: 100.0}, barras

    assert db.sum_spent_in_category_this_month(pro_user_id, CAT_VAZIA_LABEL) == 100.0

    status = db.get_budgets_status_for_month(pro_user_id)
    linha = [c for c in status["budgets"] if c["categoria"] == CAT_VAZIA_LABEL]
    assert linha and linha[0]["spent"] == 100.0, status
