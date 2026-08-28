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

DUAS ÂNCORAS DE DATA, de propósito — a escolha é da produção, não do teste.
Dois leitores derivam ano/mês de `date.today()` por DENTRO, sem parâmetro de
janela: `sum_spent_in_category_this_month` (db/budgets.py:200) e
`get_budgets_status_for_month(month=None)` (via `_parse_ym`, :300-302). Os
outros caminhos daqui caem em `month_range_today()` → `today_tz()` — é o que
`spend_query` e `_responder_categoria` (core/handlers/launches.py:924, :851)
usam quando não há período no texto. Nesses dois primeiros a chamada é SEM
janela de propósito, para exercitar o caminho default da produção:
`sum_spent_in_category_this_month(user_id, categoria)` não tem parâmetro de
janela nenhum, e `get_budgets_status_for_month(user_id, month=None)` tem
(`month="YYYY-MM"` realinha o mês) e o teste não o usa. Nos dois, o único ponto
que o teste alcança é a data de GRAVAÇÃO, e por isso eles são gravados em
`date.today()`; os outros leitores aceitam janela,
o teste a controla (o donut por ano/mês, os de período pelo `resolve_window`) e a
gravação deles vai em `today_tz()`. As duas âncoras divergem sempre que o fuso
do APP ≠ o fuso do PROCESSO: `date.today()` não conhece `REPORT_TIMEZONE`, então
definir só ela já separa as duas (medido, `REPORT_TIMEZONE=Etc/GMT+12` às
08:52 UTC de 2026-08-28: `date.today()=2026-08-28`, `today_tz()=2026-08-27`), e o
CI, que não define nenhuma das duas, roda em UTC contra o default
America/Sao_Paulo de `today_tz()` — já `TZ` sozinha NÃO separa, porque o `_tz()`
(utils_date.py:13) a lê no fallback e move as duas pontas juntas.

Divergir como DATA não basta para o teste ficar vermelho: os dois leitores
ancorados em `date.today()` filtram por `date_part('year'/'month')`, então
divergência de DIA dentro do mesmo mês não muda o resultado — só a que cruza
virada de MÊS ou de ANO derruba. Em `tests/test_tipo_legado_na_tendencia.py` é o
contrário, e a frase de lá ("não precisa da virada do mês") vale só lá: as
janelas daquele arquivo são diárias, e qualquer divergência basta.

Quem monta janela AQUI monta com fim INCLUSIVO (`db/budgets.py:259-260`,
`db/accounts.py:467-468`, `db/accounts.py:645,658`). Por isso o `resolve_window`
entra com `- timedelta(days=1)`: ele devolve fim EXCLUSIVO, e entregá-lo cru
alargaria a janela em um dia. É o oposto de `tests/test_tipo_legado_na_tendencia.py`,
onde o fim vai para leitores exclusivos e o `resolve_window` entra inteiro.
"""
import asyncio
from datetime import date, datetime, time, timedelta

import db
from core.handlers import launches as h
from db.analytics import resolve_window
from db.connection import CAT_VAZIA_LABEL
from utils_date import month_range_today, today_tz
import frontend.finance_bot_websocket_custom as dashboard


def _grava(user_id, valor, categoria, tipo="despesa", interna=False, dia=None):
    """`add_launch_and_update_balance` sempre grava alguma categoria; a linha sem
    categoria entra por SQL, que é como o importador do Open Finance a cria."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria, nota, "
            "criado_em, is_internal_movement) values (%s,%s,%s,%s,%s,%s,%s)",
            (user_id, tipo, valor, categoria, "importado",
             datetime.combine(dia or today_tz(), time(10, 0)), interna),
        )
        conn.commit()


def _donut(user_id, dia=None):
    hoje = dia or today_tz()
    d = asyncio.run(dashboard.get_financial_data(user_id, year=hoje.year, month=hoje.month))
    return {c["categoria"]: c["total"] for c in d["expense_categories"]}


def test_lista_encontra_o_que_o_donut_mostra(pro_user_id):
    _grava(pro_user_id, 100, None)   # NULL: o que o Open Finance grava
    _grava(pro_user_id, 20, "")      # '' — o mesmo pro usuário

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
    _grava(pro_user_id, 100, None)

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
    _grava(pro_user_id, 120, None)
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
    _grava(pro_user_id, 120, None)
    _grava(pro_user_id, 80, None, interna=True)

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
    # `date.today()`: `sum_spent_in_category_this_month` não tem parâmetro de
    # janela, e `get_budgets_status_for_month` tem (`month`) mas é chamada sem
    # ele de propósito — as duas fixam o mês por dentro, e o donut é o leitor
    # daqui que dá para realinhar com elas.
    _grava(pro_user_id, 100, "", dia=date.today())

    barras = _donut(pro_user_id, dia=date.today())
    assert barras == {CAT_VAZIA_LABEL: 100.0}, barras

    assert db.sum_spent_in_category_this_month(pro_user_id, CAT_VAZIA_LABEL) == 100.0

    status = db.get_budgets_status_for_month(pro_user_id)
    linha = [c for c in status["budgets"] if c["categoria"] == CAT_VAZIA_LABEL]
    assert linha and linha[0]["spent"] == 100.0, status


# ── a forma legada do `tipo` nos leitores de categoria ─────────────────────

def test_linha_legada_saida_conta_como_gasto_nas_duas_categorias(pro_user_id):
    """`sum_spent_in_category_period` e `get_largest_expenses` fixavam
    `tipo = 'despesa'` enquanto a lista da mesma categoria conta
    `('despesa','saida')` (`_TIPO_ALIASES`). A diferença entre os dois é o que o
    `_total_despesa` chama de movimentação interna — então uma despesa legada
    saía como "não conta como gasto" e sumia dos "Maiores gastos".

    Vale nas DUAS categorias, e são caminhos diferentes: a escrita passa pelo
    `cat_filter`/`_CAT_EQ` com um nome, e a vazia só é alcançável desde que a
    chave compartilhada entrou. Zero linhas legadas na produção às 27/08/2026
    21:50 UTC — isto fecha a classe, não apaga um incêndio.

    Controle NEGATIVO: `{TIPO_DESPESA_SQL}` de volta para `tipo = 'despesa'` em
    db/budgets.py (`sum_spent_in_category_period`) ou em db/accounts.py
    (`get_largest_expenses`) — este teste fica vermelho.
    """
    _grava(pro_user_id, 100, "mercado", tipo="saida")
    _grava(pro_user_id, 70, None, tipo="saida")
    start, end = month_range_today()

    for categoria, esperado in (("mercado", 100.0), (CAT_VAZIA_LABEL, 70.0)):
        total = db.sum_spent_in_category_period(pro_user_id, categoria, start, end)
        _, resumo = db.list_launches_by_category(
            pro_user_id, categoria, start, end, tipo="despesa", limit=1,
        )
        assert total == resumo["despesa"] == esperado, (categoria, total, resumo)

        maiores = db.get_largest_expenses(pro_user_id, start, end, categoria=categoria)
        assert [m["valor"] for m in maiores] == [esperado], (categoria, maiores)

        resposta = h._total_despesa(pro_user_id, categoria, start, end, "neste mês")
        assert "movimenta" not in resposta.lower(), (categoria, resposta)


def test_despesa_moderna_responde_exatamente_o_mesmo(pro_user_id):
    """Controle POSITIVO do alias legado — a base de 100% da produção hoje.

    Sem linha legada nenhuma, os três leitores têm que devolver o que sempre
    devolveram; um alias que mudasse o número da despesa moderna seria pior que
    o descasamento que ele conserta.
    """
    # `date.today()` nas TRÊS: o último assert é `sum_spent_in_category_this_month`,
    # que fixa o mês por dentro. Inclusive a interna — fora da janela, o
    # `== 45.0` passaria sem provar o filtro `is_internal_movement`.
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 45, "compra", None, categoria="mercado",
        criado_em=datetime.combine(date.today(), time(12, 0)),
    )
    _grava(pro_user_id, 30, None, dia=date.today())   # moderna, sem categoria
    _grava(pro_user_id, 25, "mercado", interna=True,  # interna: fora do gasto
           dia=date.today())
    start, end_excl = resolve_window(months=1)  # fim EXCLUSIVO
    end = end_excl - timedelta(days=1)          # os leitores daqui querem INCLUSIVO

    assert db.sum_spent_in_category_period(pro_user_id, "mercado", start, end) == 45.0
    assert db.sum_spent_in_category_period(pro_user_id, CAT_VAZIA_LABEL, start, end) == 30.0
    assert [m["valor"] for m in
            db.get_largest_expenses(pro_user_id, start, end, categoria="mercado")] == [45.0]
    assert db.sum_spent_in_category_this_month(pro_user_id, "mercado") == 45.0
