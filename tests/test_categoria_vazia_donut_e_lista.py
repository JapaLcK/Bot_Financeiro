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
from db.connection import CAT_VAZIA_LABEL
from utils_date import today_tz
import frontend.finance_bot_websocket_custom as dashboard


def _grava_sem_categoria(user_id, valor, categoria):
    """`add_launch_and_update_balance` sempre grava alguma categoria; a linha sem
    categoria entra por SQL, que é como o importador do Open Finance a cria."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into launches (user_id, tipo, valor, categoria, nota, "
            "criado_em, is_internal_movement) values (%s,'despesa',%s,%s,%s,%s,false)",
            (user_id, valor, categoria, "importado", datetime.combine(today_tz(), time(10, 0))),
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
