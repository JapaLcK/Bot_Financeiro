"""Barreira do cartão (classe irmã do #759): compra de A apontando para o cartão de B.

Nenhum escritor grava essa linha; é defesa em profundidade. Cada junção compra↔cartão virou
`left join credit_cards c on c.id = t.card_id and c.user_id = t.user_id`: o VALOR da compra
continua contando e o nome/final/cor/bandeira do cartão de B viram NULL ('Cartão' onde o
texto é gravado ou impresso). O tile da consulta 8 do /app só lê fatura do dono do cartão:
fatura de B nunca aparece. O portão no fim cobre toda junção por `card_id`
(compra, fatura, recorrente); a fatura apontando para o cartão de B (#770) é provada com
banco em test_barreira_fatura_cartao.py.

Cena (`_cena`): A tem cartão "Nubank" com compra legítima de 80; uma compra de 4321 na
fatura de A com `card_id` trocado para o cartão de B ("Cartao do B", final 9999); e um 3x300
de A com as três parcelas apontando para o cartão de B.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import usuario_pagante
from db.analytics import compute_kpis, list_history
from tests._patrimonio_helpers import q
from tests.test_barreira_fatura import _texto, _unidades
from tests.test_resumo_mes_regra import FIM, INICIO

DIA = INICIO.replace(day=5)
_DE_B = ("Cartao do B", '"9999"', "cor-do-b", "BandeiraDoB")


def _limpo(ret) -> str:
    """Serializa o retorno inteiro e garante que nada do cartão de B aparece nele."""
    txt = json.dumps(ret, default=str, ensure_ascii=False)
    assert not [m for m in _DE_B if m in txt], txt
    return txt


def _cena():
    a, b = usuario_pagante(), usuario_pagante()
    cartao_a = db.create_card(a, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(a, cartao_a, 80, "mercado", "de A", DIA)
    cartao_b = db.create_card(b, "Cartao do B", closing_day=10, due_day=17)
    q("update credit_cards set last4 = '9999', color = 'cor-do-b', flag = 'BandeiraDoB' where id = %s",
      (cartao_b,))
    trocada = db.add_credit_purchase(a, cartao_a, 4321, "mercado", "trocada", DIA)[0]
    r, _ = db.add_credit_purchase_installments(a, cartao_a, 300, "casa", "3x", DIA, 3)
    q("update credit_transactions set card_id = %s where id = %s or group_id = %s::uuid",
      (cartao_b, trocada, r["group_id"]))
    return a, r["group_id"]


def test_maior_gasto_historico_e_exportacao_mostram_o_valor_sem_o_cartao_de_b():
    a, _ = _cena()
    maior = compute_kpis(a, INICIO, FIM)["largest_expense"]
    _limpo(maior)
    assert (maior["valor"], maior["alvo"]) == (4321.0, None)

    hist = list_history(a, INICIO, FIM, limit=100)["items"]
    assert "Nubank" in _limpo(hist)
    assert {(i["valor"], i["alvo"]) for i in hist if i["tipo"] == "credito"} >= {(4321.0, None), (80.0, "Nubank")}
    assert not [i for i in list_history(a, INICIO, FIM, q="Cartao do B")["items"] if i["tipo"] == "credito"]

    app = asyncio.run(dashboard.get_financial_data(a, year=INICIO.year, month=INICIO.month, limit=100))
    assert "Nubank" in _limpo(app)
    credito = {(float(i["valor"]), i["alvo"]) for i in app["recent_launches"] if i["tipo"] == "credito"}
    assert credito >= {(4321.0, None), (80.0, "Nubank")}

    exp = asyncio.run(dashboard._fetch_export_items(a, INICIO, FIM - timedelta(days=1)))
    assert "Nubank" in _limpo(exp)
    assert {(i["valor"], i["descricao"]) for i in exp} >= {(4321.0, "trocada"), (80.0, "de A · Nubank")}


def test_parcelamentos_mostram_o_valor_sem_o_cartao_de_b():
    a, gid = _cena()
    grupo = [g for g in db.list_installment_groups(a) if str(g["group_id"]) == gid]
    _limpo(grupo)
    assert [(g["card_name"], float(g["total"]), len(g["upcoming_due_dates"])) for g in grupo] == [("Cartão", 300.0, 3)]

    det = [g for g in db.list_installment_groups_detailed(a) if g["group_id"] == gid]
    _limpo(det)
    assert [(g["card_name"], g["card_last4"], g["total"]) for g in det] == [(None, None, 300.0)]
    assert [p["due_date"] for p in det[0]["parcelas"]] == [p["period_end"] for p in det[0]["parcelas"]]

    imp = db.get_installment_group_delete_impact(a, gid)
    _limpo(imp)
    assert (imp["full_total"], imp["card_name"]) == (300.0, None)


def test_antecipar_e_desfazer_nao_gravam_nem_devolvem_o_nome_do_cartao_de_b():
    a, gid = _cena()
    ant = db.anticipate_installment(a, gid)
    _limpo(ant)
    assert ant["card_name"] == "Cartão"
    assert q("select alvo from launches where id = %s", (ant["launch_id"],))["alvo"] == "antecipacao:Cartão"
    desf = db.undo_installment_group(a, gid)
    _limpo(desf)
    assert (desf["card_name"], desf["removed_count"]) == ("cartão", 2)


# ── ponto 3: o tile da fatura (consulta 8 de get_financial_data) ────────────

def _tile():
    """A com cartão e compra de 80 (fatura fecha dia 10); uma 2ª fatura de 999, de B, no MESMO
    cartão, fechando dia 20 — o LIMIT 1 pegaria a mais tarde."""
    a, b = usuario_pagante(), usuario_pagante()
    cartao = db.create_card(a, "Nubank", closing_day=10, due_day=17)
    fatura = db.add_credit_purchase(a, cartao, 80, "mercado", "de A", DIA)[2]
    q("""insert into credit_bills (user_id, card_id, period_start, period_end, total)
         values (%s, %s, %s, %s, 999)""",
      (b, cartao, INICIO.replace(day=11), INICIO.replace(day=20)))
    app = asyncio.run(dashboard.get_financial_data(a, year=INICIO.year, month=INICIO.month))
    return [c for c in app["credit_cards"] if c["id"] == cartao][0], fatura


def test_tile_mostra_a_fatura_de_a_e_nunca_a_de_b():
    tile, fatura = _tile()
    assert (tile["bill_id"], tile["total"]) == (fatura, 80.0)


# ── portão de varredura: toda junção por card_id tem a guarda do dono ──────

_J = r"join\s+credit_cards\s+(?:as\s+)?(\w+)\s+on\s+\1\.id\s*=\s*(\w+)\.card_id\b"
_G = _J + r"\s+and\s+\1\.user_id\s*=\s*\2\.user_id\b"


def _sem_guarda(texto: str) -> bool:
    return len(re.findall(_J, texto, re.I)) > len(re.findall(_G, texto, re.I))


def test_toda_juncao_por_card_id_tem_a_guarda_do_dono():
    """Por unidade (`_unidades` do #759), nº de junções `c.id = X.card_id` (qualquer alias X:
    compra, fatura, recorrente) <= nº das que trazem `and c.user_id = X.user_id` logo em
    seguida, com o MESMO alias. Prende a classe TEXTUAL; quem prova comportamento são os
    testes com banco (aqui e em test_barreira_fatura_cartao.py). O portão NÃO enxerga: junção
    sem alias ou com `using`; `default_card_id = c.id` e outras junções pelo lado do cartão;
    a busca do nome por `select ... from credit_cards where id = %s` (undo_installment_group)
    e o LATERAL da consulta 8 — só os testes com banco cobrem; comparação de dono feita em
    Python; escrita (INSERT/UPDATE) sem guarda; e string solta da unidade com o texto da
    junção guardada mascara uma sem guarda (o contador não pareia)."""
    com_juncao, faltando = set(), []
    for unidade, texto in _unidades():
        if re.search(_J, texto, re.I):
            com_juncao.add(unidade)
            if _sem_guarda(texto):
                faltando.append(unidade)
    assert {("db/analytics.py", "list_history"), ("db/cards.py", "list_installment_groups_detailed"),
            ("frontend/finance_bot_websocket_custom.py", "get_financial_data"),
            ("db/cards.py", "list_open_bills"), ("frontend/routes/cards.py", "pay_bill_route"),
            ("db/recurring.py", "ler_recorrentes")} <= com_juncao
    assert not faltando, f"join credit_cards por X.card_id sem c.user_id = X.user_id: {faltando}"


def test_o_contador_do_portao_da_juncao_de_cartao():
    j = "join credit_cards c on c.id = ct.card_id"
    assert _sem_guarda(j)
    assert not _sem_guarda("LEFT JOIN credit_cards c ON c.id = ct.card_id AND c.user_id = ct.user_id")
    assert _sem_guarda("join credit_cards c on c.id = b.card_id")
    assert not _sem_guarda("join credit_cards c on c.id = b.card_id and c.user_id = b.user_id")
    assert _sem_guarda("left join credit_cards c on c.id = r.card_id")
    assert _sem_guarda("join credit_cards c on c.id = b.card_id and c.user_id = t.user_id")
    assert _sem_guarda("join credit_cards c on c.id = t.card_id and c.user_id = b.user_id")
