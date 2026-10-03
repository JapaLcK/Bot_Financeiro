"""`scripts/comparar_resumo_mes.py` contra o banco de teste, numa transação SÓ DE LEITURA.

A matriz de `test_resumo_mes_regra.py` no mês anterior: a receita igual, o Saiu ganha
exatamente o cartão (230) e sai "só cartão? sim". O usuário que não existe não vira linha
em `users` (o `ensure_user` do antigo grava; o script o troca por no-op) — e qualquer
escrita estouraria na transação read only.

O antigo usa a MESMA janela do novo (mês inteiro), também no mês mais recente: com `hoje`
no dia 10, lançamentos dos dias 20 e 25 entram nos dois lados e sai "só cartão? sim".
Controle NEGATIVO medido: a régua antiga até `hoje` deixa esse caso vermelho.

A régua do cartão (`CARTAO_SQL`) tem a barreira da fatura (`b.user_id`) como `TOTAIS_SQL`:
compra de A pendurada em fatura de B (com dono ou NULL no cartão de B) não entra na régua de A.
Controle NEGATIVO medido: a guarda antiga (`or b.user_id is null`) deixa o caso NULL vermelho.
"""
import db
from db.connection import get_conn
from scripts.comparar_resumo_mes import comparar
from tests.test_resumo_mes_regra import FATURA_B_NULL, INICIO, _lanc, semeia_a, semeia_a_em_fatura_de_b
from conftest import usuario_pagante
from utils_date import today_tz

NINGUEM = 987_654_321_098


def test_mostra_antigo_novo_e_so_cartao_sem_escrever():
    a = usuario_pagante()
    semeia_a(a)
    with get_conn() as conn:
        conn.execute("set transaction read only")
        linhas = comparar(conn, [a, NINGUEM], meses=2, hoje=today_tz())
        existe = conn.execute("select 1 from users where id = %s", (NINGUEM,)).fetchone()
        conn.rollback()
    mes_a = next(linha for linha in linhas if linha.startswith(f"{a} {INICIO:%Y-%m}"))
    assert "entrou 1040.0 -> 1040" in mes_a and "saiu 260.0 -> 490" in mes_a, mes_a
    assert "(dif 230" in mes_a and "cartão 230" in mes_a and mes_a.endswith("só cartão? sim"), mes_a
    assert sum(linha.startswith(f"{NINGUEM} ") for linha in linhas) == 2 and existe is None


def test_data_futura_no_mes_mais_recente_entra_nos_dois_lados():
    a = usuario_pagante()
    _lanc(a, "despesa", 100, 3)
    _lanc(a, "despesa", 40, 20)   # depois de `hoje` (dia 10), dentro do mês
    _lanc(a, "receita", 500, 25)  # idem
    cartao = db.create_card(a, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(a, cartao, 80, "mercado", "vista", INICIO.replace(day=5))
    with get_conn() as conn:
        conn.execute("set transaction read only")
        (linha,) = comparar(conn, [a], meses=1, hoje=INICIO.replace(day=10))  # relógio congelado
        conn.rollback()
    assert "entrou 500.0 -> 500" in linha and "saiu 140.0 -> 220" in linha, linha
    assert "(dif 80" in linha and "cartão 80" in linha and linha.endswith("só cartão? sim"), linha


@FATURA_B_NULL
def test_regua_do_cartao_nao_soma_compra_de_a_em_fatura_de_b(fatura_b_null):
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a_em_fatura_de_b(a, b, fatura_b_null)
    with get_conn() as conn:
        conn.execute("set transaction read only")
        linha_a, linha_b = comparar(conn, [a, b], meses=1, hoje=INICIO)
        conn.rollback()
    assert "cartão 80" in linha_a and linha_a.endswith("só cartão? sim"), linha_a
    assert "cartão 1" in linha_b and linha_b.endswith("só cartão? sim"), linha_b
