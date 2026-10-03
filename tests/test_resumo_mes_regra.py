"""A regra única do mês (`db/resumo_mes.TOTAIS_SQL`, Q18/Q30): os MESMOS dados pelos dois caminhos.

Matriz no mês anterior ao de hoje (dentro da janela do Plus, para a consulta 5 do /app
não cortar nada): despesa e receita manuais, linha legada saida/entrada, movimento
interno, criação e depósito de caixinha, lançamento do Open Finance, compra no cartão à
vista e em 3x (faturas de meses diferentes), compra num 2º cartão (outra fatura), compra
de fatura do mês seguinte, estorno,
conciliação pendente (o par lançamento manual × importado), saque em espécie (Q41: os
dois lados internos) e o usuário B com dados que NÃO podem entrar.

(i) /app e Análises: o novo é IGUAL ao SQL antigo, guardado CONGELADO aqui (cópia de antes
    deste PR) — diferença zero na consulta 5 (`get_financial_data`), no `compute_kpis` e no
    `compute_evolution`.
(ii) WhatsApp: novo − `get_summary_by_period` = EXATAMENTE as compras no cartão das faturas
    que fecham no mês; a receita é a mesma.
(iii) §0.7: `compute_evolution` é cópia em consulta única de `TOTAIS_SQL`; as duas batem
    mês a mês (A e B, meses vazios incluídos). Controle NEGATIVO medido: `AND false` na
    perna do cartão de `compute_evolution` deixa o teste vermelho.

Controle NEGATIVO (mutação em `db/resumo_mes.py`, medido): sem a perna do cartão, sem o
filtro de movimento interno ou com o `user_id` de `launches` virando `(user_id = %s or
true)`, todo caso daqui fica vermelho (no do movimento interno, todos menos o do B). Os
`user_id` de `ct` e da fatura (`b`) são medidos em `test_api_v2_resumo_mes.py::test_compra_de_a_em_fatura_de_b_nao_entra`.
Controle POSITIVO: a fatura do mês seguinte e o estorno ficam fora; mês sem dados dá zero.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import usuario_pagante
from db.analytics import (
    compute_categories, compute_evolution, compute_history_quick_stats, compute_kpis,
    compute_top_merchants, list_history,
)
from db.connection import TIPO_CANON_SQL, TIPO_DESPESA_SQL, TIPO_RECEITA_SQL, get_conn
from db.resumo_mes import mes_de, totais, totais_do_mes
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from utils_date import _tz, today_tz

D = Decimal
INICIO, FIM = mes_de(today_tz().replace(day=1) - timedelta(days=1))  # o mês anterior
CARTAO_NO_MES = D("230")  # 80 à vista + a 1ª de 3 parcelas de 100 + 50 no 2º cartão
ENTROU, SAIU = D("1040"), D("490")

# ── os SQLs de antes deste PR, CONGELADOS (não importe; é a régua) ──────────
CONSULTA_5_ANTIGA = f"""
    SELECT {TIPO_CANON_SQL} AS tipo, SUM(valor) AS total FROM (
        SELECT tipo, valor FROM launches
        WHERE user_id = %s AND criado_em >= %s AND criado_em < %s AND is_internal_movement = false
        UNION ALL
        SELECT 'despesa' AS tipo, ct.valor FROM credit_transactions ct
        JOIN credit_bills b ON b.id = ct.bill_id
        WHERE ct.user_id = %s AND ct.is_refund = false AND b.period_end >= %s AND b.period_end < %s
    ) merged GROUP BY 1
"""
KPIS_ANTIGO = f"""
    SELECT tipo, SUM(valor) AS total, SUM(cnt) AS count FROM (
      SELECT {TIPO_CANON_SQL} AS tipo, valor, 1 AS cnt FROM launches
      WHERE user_id = %s AND criado_em >= %s AND criado_em < %s AND is_internal_movement = false
        AND ({TIPO_DESPESA_SQL} OR {TIPO_RECEITA_SQL})
      UNION ALL
      SELECT 'despesa' AS tipo, ct.valor, 1 AS cnt FROM credit_transactions ct
      JOIN credit_bills b ON b.id = ct.bill_id
      WHERE ct.user_id = %s AND ct.is_refund = false AND b.period_end >= %s AND b.period_end < %s
    ) merged GROUP BY tipo
"""


def _antigo(sql, uid, inicio=INICIO, fim=FIM) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (uid, inicio, fim, uid, inicio, fim))
        rows = cur.fetchall()
    return {r["tipo"]: r for r in rows}


def _em(dia: int) -> datetime:
    return datetime.combine(INICIO.replace(day=dia), time(12), tzinfo=_tz())


def _lanc(uid, tipo, valor, dia, interno=False, source="manual") -> int:
    return q("""insert into launches (user_id, tipo, valor, criado_em, is_internal_movement, source)
                values (%s, %s, %s, %s, %s, %s) returning id""",
             (uid, tipo, valor, _em(dia), interno, source))["id"]


def semeia_a(uid: int) -> None:
    _lanc(uid, "despesa", 100, 3)
    _lanc(uid, "receita", 1000, 3)
    _lanc(uid, "saida", 30, 4)                       # legado
    _lanc(uid, "entrada", 40, 4)                     # legado
    _lanc(uid, "despesa", 500, 5, interno=True)      # transferência entre contas
    _lanc(uid, "criar_caixinha", 0, 6, interno=True)
    _lanc(uid, "deposito_caixinha", 200, 6, interno=True)
    _lanc(uid, "despesa", 70, 7, source="open_finance")
    # conciliação pendente: os dois lados contam, como hoje (sai com motivo)
    manual = db.add_launch_and_update_balance(uid, "despesa", 30, "mercado", None, criado_em=_em(8))[0]
    importado = _lanc(uid, "despesa", 30, 8, source="open_finance")
    acc = conta(conexao(uid, f"item-{uid}"), "acc-1", "1000")
    tx_banco(acc, "tx-p", "-30", imported_launch_id=importado, match_launch_id=manual,
             reconciliation_status="pending")
    # saque em espécie (Q41): o débito do banco e o crédito na Carteira, os dois internos
    _lanc(uid, "despesa", 200, 9, interno=True, source="open_finance")
    _lanc(uid, "receita", 200, 9, interno=True)
    cartao = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, cartao, 80, "mercado", "vista", INICIO.replace(day=5))
    db.add_credit_purchase_installments(uid, cartao, 300, "casa", "3x",
                                        (INICIO - timedelta(days=1)).replace(day=20), 3)
    db.add_credit_purchase(uid, cartao, 999, "fora", "fatura seguinte", INICIO.replace(day=15))
    db.add_credit_refund(uid, cartao, 25, "mercado", "estorno", INICIO.replace(day=6))
    outro = db.create_card(uid, "Inter", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, outro, 50, "farmácia", "2º cartão", INICIO.replace(day=5))


def semeia_b(uid: int) -> None:
    _lanc(uid, "despesa", 7777, 3)
    _lanc(uid, "receita", 5555, 3)
    cartao = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, cartao, 888, "mercado", "do B", INICIO.replace(day=5))


def semeia_a_em_fatura_de_b(a: int, b: int) -> None:
    """Linha corrompida (nenhum escritor grava): compra de A de 4321 na fatura de B, mais 80
    de A e 1 de B legítimos. Com a barreira da fatura, A soma 80 e B soma 1."""
    cartao_a = db.create_card(a, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(a, cartao_a, 80, "mercado", "de A", INICIO.replace(day=5))
    cartao_b = db.create_card(b, "Nubank", closing_day=10, due_day=17)
    _, _, fatura_b = db.add_credit_purchase(b, cartao_b, 1, "x", "de B", INICIO.replace(day=5))
    q("""insert into credit_transactions (bill_id, user_id, card_id, valor, purchased_at)
         values (%s, %s, %s, 4321, %s)""", (fatura_b, a, cartao_a, INICIO.replace(day=5)))


def _par():
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a(a)
    semeia_b(b)
    return a, b


def test_consulta_5_do_app_igual_ao_sql_antigo():
    a, _ = _par()
    velho = _antigo(CONSULTA_5_ANTIGA, a)
    dados = asyncio.run(dashboard.get_financial_data(a, year=INICIO.year, month=INICIO.month))
    assert dados["monthly_income"] == float(velho["receita"]["total"]) == float(ENTROU)
    assert dados["monthly_expense"] == float(velho["despesa"]["total"]) == float(SAIU)


def test_kpis_e_evolucao_das_analises_iguais_ao_sql_antigo():
    a, _ = _par()
    velho = _antigo(KPIS_ANTIGO, a)
    k = compute_kpis(a, INICIO, FIM)
    assert k["total_income"] == round(float(velho["receita"]["total"]), 2) == float(ENTROU)
    assert k["total_expense"] == round(float(velho["despesa"]["total"]), 2) == float(SAIU)
    assert k["transactions_count"] == sum(int(r["count"]) for r in velho.values())
    barra = {b["month"]: b for b in compute_evolution(a, months=3)}[f"{INICIO:%Y-%m}"]
    assert (barra["income"], barra["expense"]) == (float(ENTROU), float(SAIU))


def test_whatsapp_so_ganha_o_cartao_das_faturas_do_mes():
    a, _ = _par()
    velho = db.get_summary_by_period(a, INICIO, FIM - timedelta(days=1))
    novo = totais_do_mes(a, INICIO)
    assert (novo["entrou"], novo["saiu"]) == (ENTROU, SAIU)
    assert novo["saiu"] - D(str(velho["despesa"])) == CARTAO_NO_MES
    assert novo["entrou"] == D(str(velho["receita"]))


def test_b_ve_so_o_dele_e_mes_sem_dados_da_zero():
    _, b = _par()
    assert (totais_do_mes(b, INICIO)["entrou"], totais_do_mes(b, INICIO)["saiu"]) == (D("5555"), D("8665"))
    vazio = totais_do_mes(b, date(2020, 1, 15))
    assert (vazio["entrou"], vazio["saiu"], vazio["n"]) == (0, 0, 0)


def test_evolucao_bate_com_a_regra_unica_mes_a_mes():
    a, b = _par()
    for uid in (a, b):
        barras = {x["month"]: x for x in compute_evolution(uid, months=4)}
        for mes, barra in barras.items():
            with get_conn() as conn, conn.cursor() as cur:
                t = totais(cur, uid, *mes_de(date.fromisoformat(f"{mes}-01")))
            assert (barra["income"], barra["expense"]) == (float(t["entrou"]), float(t["saiu"])), (uid, mes)
        if uid == a:  # o mês da matriz cheio, e os dois de antes vazios (positivo: zero = zero)
            assert (barras[f"{INICIO:%Y-%m}"]["income"], barras[f"{INICIO:%Y-%m}"]["expense"]) == (1040.0, 490.0)
            assert sum(x["income"] == x["expense"] == 0 for x in barras.values()) == 2


def test_evolucao_nao_soma_compra_de_a_em_fatura_de_b():
    """A barreira `b.user_id` vale também na cópia (`compute_evolution`). Controle NEGATIVO
    medido: sem ela, A sai 4401 e este fica vermelho."""
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a_em_fatura_de_b(a, b)
    for uid, saiu in ((a, 80.0), (b, 1.0)):  # positivo: a compra legítima de cada um entra
        barra = {x["month"]: x for x in compute_evolution(uid, months=3)}[f"{INICIO:%Y-%m}"]
        assert barra["expense"] == float(totais_do_mes(uid, INICIO)["saiu"]) == saiu, uid


def test_analises_e_historico_nao_contam_compra_de_a_em_fatura_de_b():
    """A barreira `b.user_id` nos outros agregados do cartão de `db/analytics.py`: cards do
    Histórico, dia de pico e maior gasto, categorias e estabelecimentos. Controle NEGATIVO
    medido: sem a barreira em qualquer um deles, A conta a compra de 4321 e fica vermelho."""
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a_em_fatura_de_b(a, b)
    q("update credit_transactions set nota = 'mercado' where user_id = %s and valor = 4321 returning id", (a,))
    for uid, valor in ((a, 80.0), (b, 1.0)):  # positivo: a compra legítima de cada um entra
        st = compute_history_quick_stats(uid, INICIO, FIM)
        assert (st["despesas_count"], st["total_count"]) == (1, 1), uid
        k = compute_kpis(uid, INICIO, FIM)
        assert (k["peak_day"]["total"], k["largest_expense"]["valor"]) == (valor, valor), uid
        assert [c["total"] for c in compute_categories(uid, INICIO, FIM)] == [valor], uid
        assert [m["total"] for m in compute_top_merchants(uid, INICIO, FIM)] == [valor], uid


def _donut(uid) -> list[float]:
    dados = asyncio.run(dashboard.get_financial_data(uid, year=INICIO.year, month=INICIO.month))
    return [c["total"] for c in dados["expense_categories"]]


def test_donut_do_app_nao_soma_compra_de_a_em_fatura_de_b_e_bate_com_as_analises():
    """A barreira `b.user_id` na consulta 6 do /app (donut), gêmea de `compute_categories`:
    as duas telas dão o mesmo número. Controle NEGATIVO medido: sem a barreira, A sai
    [4321.0, 80.0] e este fica vermelho."""
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a_em_fatura_de_b(a, b)
    for uid, valor in ((a, 80.0), (b, 1.0)):  # positivo: a compra legítima de cada um entra
        assert _donut(uid) == [c["total"] for c in compute_categories(uid, INICIO, FIM)] == [valor], uid


def test_donut_do_app_conta_o_segundo_cartao_e_bate_com_as_analises():
    """Positivo da barreira na consulta 6: a fatura do 2º cartão de A (farmácia, 50) entra."""
    a, _ = _par()
    donut = _donut(a)
    assert donut == [c["total"] for c in compute_categories(a, INICIO, FIM)]
    assert 50.0 in donut and sum(donut) == float(SAIU)


def test_cards_do_historico_contam_o_segundo_cartao_e_nao_o_b():
    """Positivo da barreira em `compute_history_quick_stats`: na matriz, a fatura do 2º cartão
    de A entra (2 receitas; 5 despesas de lançamento + 3 do cartão, uma delas no 2º cartão) —
    o mesmo que o SQL de antes dava; nada do B."""
    a, _ = _par()
    st = compute_history_quick_stats(a, INICIO, FIM)
    assert (st["receitas_count"], st["despesas_count"], st["total_count"]) == (2, 8, 10)


def _cartao_nas_listas(uid) -> dict:
    """O cartão do mês nas três listas: a do /history (`list_history`), a do /app (consultas
    3 e 4 de `get_financial_data`) e a exportação (`_fetch_export_items`)."""
    h = list_history(uid, from_date=INICIO, to_date=FIM, limit=200)
    app = asyncio.run(dashboard.get_financial_data(uid, year=INICIO.year, month=INICIO.month, limit=100))
    exp = asyncio.run(dashboard._fetch_export_items(uid, INICIO, FIM - timedelta(days=1)))
    return {
        "historico": sorted(i["valor"] for i in h["items"] if i["tipo"] == "credito"),
        "historico_total": (h["total"], len(h["items"])),
        "app": sorted(float(r["valor"]) for r in app["recent_launches"] if r["tipo"] == "credito"),
        "app_total": (app["launches_pagination"]["total"], len(app["recent_launches"])),
        "export": sorted(i["valor"] for i in exp if i["label"] == "Cartão"),
    }


def test_listas_e_exportacao_nao_mostram_compra_de_a_em_fatura_de_b():
    """A barreira `b.user_id` nas LISTAS (e nas contagens delas): /history, consultas 3 e 4
    do /app e exportação. A lista do Histórico bate com os cards dele. Controle NEGATIVO
    medido: sem a barreira em qualquer uma, A lista [80.0, 4321.0] e este fica vermelho."""
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a_em_fatura_de_b(a, b)
    for uid, valor in ((a, 80.0), (b, 1.0)):  # positivo: a compra legítima de cada um entra
        x = _cartao_nas_listas(uid)
        assert x["historico"] == x["app"] == x["export"] == [valor], (uid, x)
        st = compute_history_quick_stats(uid, INICIO, FIM)
        assert x["historico_total"] == x["app_total"] == (st["total_count"], st["total_count"]) == (1, 1), (uid, x)


def test_listas_e_exportacao_contam_o_segundo_cartao_e_batem_com_os_cards():
    """Positivo da barreira nas listas: a compra do 2º cartão de A (50) aparece nas três, e
    a lista do Histórico tem o mesmo número de itens, receitas e despesas que os cards."""
    a, _ = _par()
    x = _cartao_nas_listas(a)
    assert x["historico"] == x["app"] == x["export"] == [50.0, 80.0, 100.0], x
    h = list_history(a, from_date=INICIO, to_date=FIM, limit=200)
    st = compute_history_quick_stats(a, INICIO, FIM)
    receitas = sum(i["tipo"] == "receita" for i in h["items"])
    assert (h["total"], len(h["items"]), receitas, len(h["items"]) - receitas) == (
        st["total_count"], st["total_count"], st["receitas_count"], st["despesas_count"]) == (10, 10, 2, 8)
