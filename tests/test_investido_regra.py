"""A regra do total investido (`db/investido.py`): taxonomia, divisão, centavos e motivos.

Banco real, espelho do Open Finance escrito direto (`tests/_patrimonio_helpers.py`). Em
todo caso: Σ por_tipo == total == Σ por_banco (null conta 0) e motivos de parte ⊆ topo.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from conftest import usuario_pagante
from db import investido
from db.investido import tipo_da_posicao
from tests._patrimonio_helpers import (caixinha, conexao, foto, horas_atras, investimento_manual,
                                       posicao, q)

D = Decimal


@pytest.fixture
def uid():
    return usuario_pagante()


def ler(uid) -> dict:
    r = investido.ler(uid)
    for lado in ("por_tipo", "por_banco"):
        assert sum((p["valor"] or 0 for p in r[lado]), D(0)) == (r["total"] or 0), (lado, r)
        assert all(set(p["motivos"]) <= set(r["motivos"]) for p in r[lado]), r
        assert all(p["valor"] is None or p["valor"] != 0 for p in r[lado]), r
    assert r["motivos"] == [m for m in investido.MOTIVOS if m in r["motivos"]]
    return r


def partes(r, lado="por_tipo"):
    chave = "tipo" if lado == "por_tipo" else "banco"
    return {p[chave]: p["valor"] for p in r[lado]}


@pytest.mark.parametrize("tipo,subtipo,esperado", [
    ("FIXED_INCOME", "CDB", "renda_fixa"), ("FIXED_INCOME", None, "renda_fixa"),
    ("FIXED_INCOME", "", "renda_fixa"), ("FIXED_INCOME", "TREASURY", "tesouro"),
    ("FIXED_INCOME", "LCI", "renda_fixa"), ("EQUITY", "STOCK", "acoes"), ("EQUITY", None, "acoes"),
    ("EQUITY", "REAL_ESTATE_FUND", "fii"), ("EQUITY", "BDR", "acoes"),
    ("MUTUAL_FUND", "INVESTMENT_FUND", "fundos"), ("ETF", None, "etf"), ("COE", None, "outros"),
    ("SECURITY", "RETIREMENT", "outros"), (None, None, "outros"), ("fixed_income", "cdb", "renda_fixa"),
])
def test_taxonomia(tipo, subtipo, esperado):
    assert tipo_da_posicao(tipo, subtipo) == esperado


def test_positivo_cada_tipo_entra_na_parte_certa(uid):
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    for i, (t, st) in enumerate([("FIXED_INCOME", "CDB"), ("FIXED_INCOME", "TREASURY"), ("EQUITY", "STOCK"),
                                 ("EQUITY", "REAL_ESTATE_FUND"), ("MUTUAL_FUND", None), ("ETF", None),
                                 ("COE", None)], start=1):
        posicao(c, f"inv-{i}", str(i * 100), tipo=t, subtipo=st)
    r = ler(uid)
    assert r["total"] == D("2800.00") and r["motivos"] == []
    assert [(p["tipo"], p["rotulo"], p["valor"]) for p in r["por_tipo"]] == [
        ("outros", "Outros", D("700.00")), ("etf", "ETFs", D("600.00")),
        ("fundos", "Fundos de investimento", D("500.00")), ("fii", "Fundos imobiliários", D("400.00")),
        ("acoes", "Ações", D("300.00")), ("tesouro", "Tesouro Direto", D("200.00")),
        ("renda_fixa", "Renda fixa", D("100.00"))]
    assert r["por_banco"] == [{"banco": "Nubank", "valor": D("2800.00"), "motivos": []}]


def test_mesma_posicao_em_duas_conexoes_conta_uma_vez_no_banco_da_mais_nova(uid):
    posicao(conexao(uid, f"item-velho-{uid}", banco="Banco Velho"), "inv-1", "444")
    posicao(conexao(uid, f"item-{uid}", banco="Nubank"), "inv-1", "500")
    r = ler(uid)
    assert r["total"] == D("500.00") and partes(r, "por_banco") == {"Nubank": D("500.00")}


def test_duas_instituicoes_duas_partes_mesmo_nome_uma_parte(uid):
    posicao(conexao(uid, f"item-a-{uid}", banco="Nubank"), "inv-1", "100")
    posicao(conexao(uid, f"item-b-{uid}", banco="Nubank"), "inv-2", "50")
    posicao(conexao(uid, f"item-c-{uid}", banco="XP"), "inv-3", "200", tipo="EQUITY")
    r = ler(uid)
    assert r["por_banco"] == [{"banco": "XP", "valor": D("200.00"), "motivos": []},
                              {"banco": "Nubank", "valor": D("150.00"), "motivos": []}]
    assert partes(r) == {"acoes": D("200.00"), "renda_fixa": D("150.00")}


def test_outra_moeda_fora_e_moeda_presumida_na_parte(uid):
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    posicao(c, "inv-usd", "999", moeda="USD", code="USD")
    posicao(c, "inv-1", "100", code=None)
    r = ler(uid)
    assert r["total"] == D("100.00")
    assert r["motivos"] == ["moeda_presumida", "outra_moeda"]
    assert r["por_tipo"][0]["motivos"] == r["por_banco"][0]["motivos"] == ["moeda_presumida"]


def test_pausada_fora_de_tudo(uid):
    posicao(conexao(uid, f"item-p-{uid}", status="PAUSED", banco="Itaú"), "inv-p", "999")
    posicao(conexao(uid, f"item-{uid}", banco="Nubank"), "inv-1", "100")
    r = ler(uid)
    assert r["total"] == D("100.00") and r["motivos"] == ["conexao_pausada"]
    assert partes(r, "por_banco") == {"Nubank": D("100.00")}


def test_so_pausada_e_null(uid):
    posicao(conexao(uid, f"item-p-{uid}", status="PAUSED"), "inv-p", "999")
    assert ler(uid) == {"total": None, "por_tipo": [], "por_banco": [],
                        "motivos": ["sem_banco_conectado", "conexao_pausada"]}


def test_sem_banco_e_null_mesmo_com_manual(uid):
    investimento_manual(uid, "CDB manual", "70")
    caixinha(uid, "Viagem", "50")
    assert ler(uid) == {"total": None, "por_tipo": [], "por_banco": [], "motivos": ["sem_banco_conectado"]}


def test_viva_nunca_sincronizada_e_null(uid):
    conexao(uid, f"item-{uid}", sync=None)
    r = ler(uid)
    assert r["total"] is None and r["por_tipo"] == r["por_banco"] == []
    assert r["motivos"] == ["banco_desatualizado"]


def test_zero_some_e_sem_saldo_e_null(uid):
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    posicao(c, "inv-zero", "0", tipo="EQUITY")
    posicao(c, "inv-sem", None, tipo="MUTUAL_FUND")
    posicao(c, "inv-1", "100")
    r = ler(uid)
    assert r["total"] == D("100.00") and r["motivos"] == ["saldo_ausente"]
    assert r["por_tipo"] == [
        {"tipo": "renda_fixa", "rotulo": "Renda fixa", "valor": D("100.00"), "motivos": []},
        {"tipo": "fundos", "rotulo": "Fundos de investimento", "valor": None, "motivos": ["saldo_ausente"]}]
    assert r["por_banco"] == [{"banco": "Nubank", "valor": D("100.00"), "motivos": ["saldo_ausente"]}]


def test_desatualizado_e_fora_do_sync_marcam_a_parte(uid):
    velho = conexao(uid, f"item-v-{uid}", sync=horas_atras(49), banco="Itaú")
    posicao(velho, "inv-v", "10", tipo="EQUITY")
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    posicao(c, "inv-1", "100")
    antiga = posicao(c, "inv-2", "5")
    q("update open_finance_investments set updated_at=%s where id=%s", (horas_atras(1), antiga))
    r = ler(uid)
    assert r["total"] == D("115.00")
    assert r["motivos"] == ["banco_desatualizado", "conta_fora_do_ultimo_sync"]
    assert {p["banco"]: p["motivos"] for p in r["por_banco"]} == {
        "Nubank": ["conta_fora_do_ultimo_sync"], "Itaú": ["banco_desatualizado"]}


def test_resgatada_fora_sem_motivo(uid):
    c = conexao(uid, f"item-{uid}")
    posicao(c, "inv-r", "300", status="TOTAL_WITHDRAWAL")
    posicao(c, "inv-1", "100")
    r = ler(uid)
    assert r["total"] == D("100.00") and r["motivos"] == []


def test_manual_e_caixinhas_nao_mudam_o_total(uid):
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    espelhada = posicao(c, "inv-500", "500")
    antes = ler(uid)
    caixinha(uid, "Caixinha do banco", "500", of_investment_id=espelhada)
    caixinha(uid, "Viagem", "50")
    investimento_manual(uid, "CDB manual", "70")
    assert ler(uid) == antes and antes["total"] == D("500.00")


def test_centavos_por_posicao(uid):
    a = conexao(uid, f"item-a-{uid}", banco="Nubank")
    b = conexao(uid, f"item-b-{uid}", banco="XP")
    posicao(a, "inv-1", "0.005")
    posicao(a, "inv-2", "0.015", tipo="EQUITY")
    posicao(b, "inv-3", "10.005", tipo="EQUITY")
    r = ler(uid)  # 0.00 + 0.02 + 10.00 (meio para o par)
    assert r["total"] == D("10.02")
    assert partes(r) == {"acoes": D("10.02")}  # renda fixa fecha em 0.00 e some
    assert partes(r, "por_banco") == {"XP": D("10.00"), "Nubank": D("0.02")}


def test_centavos_aleatorios_fecham_e_ficam_perto_da_foto(uid):
    rnd = random.Random(20261007)
    conexoes = [conexao(uid, f"item-a-{uid}", banco="Nubank"), conexao(uid, f"item-b-{uid}", banco="XP")]
    tipos = ["FIXED_INCOME", "EQUITY", "MUTUAL_FUND"]
    for _ in range(200):
        q("delete from open_finance_investments where connection_id = any(%s)", (conexoes,))
        n = rnd.randint(1, 8)
        for i in range(n):
            saldo = D(rnd.randint(0, 10**7)).scaleb(-rnd.randint(0, 4))
            posicao(rnd.choice(conexoes), f"inv-{i}", str(saldo), tipo=rnd.choice(tipos))
        r = ler(uid)  # a igualdade das partes está no `ler`
        assert abs(r["total"] - foto(uid)["investimentos_banco"]) <= D("0.005") * n


def test_isolamento_b_nunca_entra_em_a(uid):
    b = usuario_pagante()
    posicao(conexao(uid, f"item-{uid}", banco="Nubank"), "inv-1", "100")
    posicao(conexao(b, f"item-{b}", banco="Segredo"), "inv-1", "9999", tipo="EQUITY")
    posicao(conexao(b, f"item-2-{b}", banco="Segredo"), "inv-b", "777")
    r = ler(uid)
    assert r["total"] == D("100.00")
    assert partes(r) == {"renda_fixa": D("100.00")} and partes(r, "por_banco") == {"Nubank": D("100.00")}
    assert ler(b)["total"] == D("10776.00")
