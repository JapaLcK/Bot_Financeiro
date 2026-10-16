"""Opção B (P2 do Codex no #969): depois de casar, o lançamento da DESPESA/RECEITA declarada passa a ter o
valor e a data do extrato (dia da transação, hora local declarada), para o Resumo bater com o banco.

O declarado original fica em `bank_movement_declarations.amount`/`declared_at` e é o único que a
elegibilidade lê: se o banco corrigir a transação para fora do critério, o vínculo cai e o lançamento
VOLTA ao declarado (o teste automático 50,04 -> 50,09 prova que a conta é contra o declarado, não contra
o valor já copiado do banco). Aporte/caixinha não passam por aqui.
Controles negativos: `projeta` sem o filtro `user_id` fica vermelho em `test_projecao_nao_escreve_em_outro_usuario`;
sem o `if not e_fluxo` em `test_aporte_...`; sem a chamada de `projeta_todas` no reconcile, os de bind
automático, correção e restauração.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

import db
import db.bank_conferencia as conferencia
import db.bank_movements as movements
from conftest import usuario_pagante
from db.bank_declaracoes import projeta
from db.resumo_mes import totais_do_mes
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank, _deposit, _sync, _tx
from tests.test_declaracao_fluxo_reconcilia import decl, declara, n_linhas, sincroniza
from utils_date import _tz, today_tz

D = Decimal


def t(amount, dia, txid="t1"):
    return {"provider_transaction_id": txid, "description": "Mercado Bom Preco", "amount": amount,
            "transaction_date": dia, "category": "Groceries"}


def em(dia, hhmm=(23, 50)):
    return datetime.combine(dia, time(*hhmm), tzinfo=_tz())


def lanc(lid):
    return q("select valor, criado_em, efeitos from launches where id=%s", (lid,))


def local(dt):
    return dt.astimezone(_tz())


def virada():
    """Último dia do mês anterior (declarado 23:50) e o dia 2 deste mês (extrato): o exemplo do Codex
    (31/01 -> 02/02) sem data fixa, para o congelamento de 45 dias não entrar."""
    primeiro = today_tz().replace(day=1)
    return primeiro - timedelta(days=1), primeiro + timedelta(days=1)


def test_exemplo_do_codex_confirmacao_manual_traz_valor_e_data_do_extrato(user_id):
    cid, _ = _bank(user_id)
    dia_decl, dia_tx = virada()
    lid = declara(user_id, 1000, criado_em=em(dia_decl))
    sincroniza(cid, user_id, t(-1100, dia_tx))
    assert decl(lid)["matched_transaction_id"] is None  # 100 de diferença: só pelo modal
    [mov] = conferencia.list_bank_movements(user_id)
    conferencia.confirm_bank_movement(user_id, lid, mov["candidates"][0]["id"])
    l = lanc(lid)
    assert l["valor"] == 1100 and local(l["criado_em"]).date() == dia_tx
    assert local(l["criado_em"]).time().replace(second=0, microsecond=0) == time(23, 50)
    assert totais_do_mes(user_id, dia_decl)["saiu"] == 0 and totais_do_mes(user_id, dia_tx)["saiu"] == 1100
    assert n_linhas(user_id, 1100) == 1 and n_linhas(user_id, 1000) == 0  # a sombra sumiu; uma linha só
    d = decl(lid)  # o declarado fica na tabela
    assert d["amount"] == -1000 and local(d["declared_at"]).date() == dia_decl
    assert D(str(l["efeitos"]["delta_conta"])) == 0 and db.get_balance(user_id) == 0


def test_automatico_traz_valor_e_dia_do_extrato_e_o_fuso_nao_desloca(user_id):
    cid, _ = _bank(user_id)
    dia_decl, dia_tx = virada()  # declarado 23:50 do dia 31/30/..., extrato no dia 1 do mês seguinte
    dia_tx = dia_decl + timedelta(days=1)
    lid = declara(user_id, 50, criado_em=em(dia_decl))
    sincroniza(cid, user_id, t(D("-50.04"), dia_tx))
    assert decl(lid)["confirmation_method"] == "automatic"
    l = lanc(lid)
    assert l["valor"] == D("50.04") and local(l["criado_em"]).date() == dia_tx  # não ficou no dia declarado
    assert local(l["criado_em"]).time().replace(second=0, microsecond=0) == time(23, 50)
    assert totais_do_mes(user_id, dia_tx)["saiu"] == D("50.04") and totais_do_mes(user_id, dia_decl)["saiu"] == 0


def test_receita_tambem_segue_o_extrato(user_id):
    cid, _ = _bank(user_id)
    dia = today_tz() - timedelta(days=1)
    lid = declara(user_id, 3000, "receita", criado_em=em(dia, (9, 0)))
    sincroniza(cid, user_id, t(D("3000.03"), today_tz()))
    l = lanc(lid)
    assert l["valor"] == D("3000.03") and local(l["criado_em"]).date() == today_tz()
    assert totais_do_mes(user_id, today_tz())["entrou"] == D("3000.03")


def test_banco_corrige_dentro_do_criterio_acompanha(user_id):
    cid, _ = _bank(user_id)
    dia = today_tz() - timedelta(days=1)
    lid = declara(user_id, 50, criado_em=em(dia, (18, 0)))
    sincroniza(cid, user_id, t(D("-50.04"), dia))
    assert lanc(lid)["valor"] == D("50.04")
    sincroniza(cid, user_id, t(D("-50.02"), dia))
    assert lanc(lid)["valor"] == D("50.02") and decl(lid)["matched_transaction_id"]


def test_banco_corrige_para_fora_do_criterio_restaura_e_a_conta_e_contra_o_declarado(user_id):
    """50,09 está a 0,09 do DECLARADO (> 0,05), mas a 0,05 do valor 50,04 já copiado do banco: tem de
    desvincular. Se a elegibilidade lesse `launches.valor`, o vínculo ficaria (banco contra banco)."""
    cid, _ = _bank(user_id)
    dia = today_tz() - timedelta(days=1)
    declarado = em(dia, (18, 0))
    lid = declara(user_id, 50, criado_em=declarado)
    sincroniza(cid, user_id, t(D("-50.04"), dia))
    assert lanc(lid)["valor"] == D("50.04")
    sincroniza(cid, user_id, t(D("-50.09"), dia))
    d, l = decl(lid), lanc(lid)
    assert d["matched_transaction_id"] is None and d["requires_review"]
    assert l["valor"] == 50 and l["criado_em"] == declarado  # restaurado ao declarado
    assert n_linhas(user_id, D("50.09")) == 1  # a sombra reaparece com o import
    assert totais_do_mes(user_id, dia)["saiu"] == 50 + D("50.09")


def test_manual_corrigido_dentro_dos_dez_por_cento_acompanha_e_fora_restaura(user_id):
    cid, _ = _bank(user_id)
    dia_decl, dia_tx = virada()
    declarado = em(dia_decl)
    lid = declara(user_id, 1000, criado_em=declarado)
    sincroniza(cid, user_id, t(-1100, dia_tx))
    conferencia.confirm_bank_movement(user_id, lid, conferencia.list_bank_movements(user_id)[0]["candidates"][0]["id"])
    sincroniza(cid, user_id, t(-1090, dia_tx))
    assert lanc(lid)["valor"] == 1090 and decl(lid)["matched_transaction_id"]
    sincroniza(cid, user_id, t(-1200, dia_tx))
    assert decl(lid)["matched_transaction_id"] is None and decl(lid)["requires_review"]
    l = lanc(lid)
    assert l["valor"] == 1000 and l["criado_em"] == declarado
    assert totais_do_mes(user_id, dia_decl)["saiu"] == 1000  # voltou ao mês declarado


def test_transacao_apagada_restaura(user_id):
    cid, _ = _bank(user_id)
    dia = today_tz() - timedelta(days=1)
    declarado = em(dia, (18, 0))
    lid = declara(user_id, 50, criado_em=declarado)
    sincroniza(cid, user_id, t(D("-50.04"), today_tz()))
    assert lanc(lid)["valor"] == D("50.04")
    db.delete_open_finance_transactions(f"decl-{user_id}", ["t1"])
    l = lanc(lid)
    assert decl(lid)["matched_transaction_id"] is None
    assert l["valor"] == 50 and l["criado_em"] == declarado


def test_congelada_ainda_sincroniza_e_restaura(user_id):
    cid, _ = _bank(user_id)
    dia = today_tz() - timedelta(days=50)
    declarado = em(dia, (12, 0))
    lid = declara(user_id, 50, criado_em=declarado)
    sincroniza(cid, user_id, t(-50, dia))
    sincroniza(cid, user_id, t(D("-50.03"), dia + timedelta(days=1)))  # banco corrige valor e data
    l = lanc(lid)
    assert l["valor"] == D("50.03") and local(l["criado_em"]).date() == dia + timedelta(days=1)
    sincroniza(cid, user_id, t(-52, dia))
    assert decl(lid)["matched_transaction_id"] is None
    l = lanc(lid)
    assert l["valor"] == 50 and l["criado_em"] == declarado


def test_aporte_nao_muda_valor_nem_data(user_id):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    antes = lanc(lid)
    _sync(cid, 500, _tx(days=-2))  # extrato dois dias antes: casa, mas o lançamento do aporte não anda
    assert decl(lid)["matched_transaction_id"]
    depois = lanc(lid)
    assert (depois["valor"], depois["criado_em"]) == (antes["valor"], antes["criado_em"]) and depois["valor"] == 500


def test_duas_rodadas_de_reconcile_nao_reescrevem(user_id):
    cid, _ = _bank(user_id)
    dia = today_tz() - timedelta(days=1)
    lid = declara(user_id, 50, criado_em=em(dia, (18, 0)))
    sincroniza(cid, user_id, t(D("-50.04"), today_tz()))
    def fotografa():
        return q("select valor, criado_em, xmin::text x from launches where id=%s", (lid,))
    antes = fotografa()
    for _ in range(2):
        with db.get_conn() as conn, conn.cursor() as cur:
            movements.reconcile_bank_movements(cur, user_id)
            conn.commit()
    assert fotografa() == antes  # mesmo xmin: nenhuma reescrita


def test_projecao_nao_escreve_em_outro_usuario():
    a, b = usuario_pagante(), usuario_pagante()
    cb, _ = _bank(b)
    lb = declara(b, 50)
    _sync(cb, 1000, t(D("-50"), today_tz()))
    with db.get_conn() as conn, conn.cursor() as cur:
        dB = next(d for d in movements._declarations(cur, b) if d["launch_id"] == lb)
        txB = {"amount": D("-70"), "transaction_date": today_tz()}
        projeta(cur, a, dB, txB)  # o dono é o A: nenhuma linha do B pode mudar
        conn.commit()
    assert lanc(lb)["valor"] == 50
