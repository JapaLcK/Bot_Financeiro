"""A regra do total investido (`db/investido.py`): taxonomia, divisão, centavos e motivos.

Banco real, espelho do Open Finance escrito direto (`tests/_patrimonio_helpers.py`). Em
todo caso: Σ por_tipo == total == Σ por_banco (null conta 0) e motivos de parte ⊆ topo.
"""
from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from conftest import usuario_pagante
from db import investido
from db.investido import tipo_da_posicao
from tests._patrimonio_helpers import (AGORA, caixinha, conexao, foto, horas_atras,
                                       investimento_manual, posicao, q)

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
    ("EQUITY", "OPTION", "outros"), ("EQUITY", "DERIVATIVES", "outros"), ("EQUITY", "ETF", "acoes"),
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


# ─── "não sei" ≠ zero: conexão sem linha no espelho, posições todas sem saldo (Codex P1/P2) ───

def _leitura_falhou(cid, motivo="investments_read_failed"):
    q("update open_finance_connections set status_reason=%s where id=%s", (motivo, cid))


# Sincronizou (last_sync_at = AGORA) com cada status_reason real. Sem linha no espelho
# não há R$ 0 (o sync não grava "li e veio vazio"; item em NEEDS_USER grava "" mesmo com
# /investments falhando); com linha, o número. `nenhum_investimento` só com a conexão
# saudável; em dúvida, o `banco_desatualizado` e o texto "ainda não consegui ler".
_FALHA = ["banco_desatualizado"]


@pytest.mark.parametrize("motivo, sem_posicao, com_posicao", [
    ("", ["nenhum_investimento"], []),
    (None, ["nenhum_investimento"], []),
    ("item_missing", _FALHA, _FALHA),
    ("no_accounts", _FALHA, _FALHA),
    ("coleta_estourada", _FALHA, _FALHA),
    ("investments_read_failed", _FALHA, _FALHA),
    ("read_failed", _FALHA, _FALHA),
])
@pytest.mark.parametrize("posicoes", [False, True])
def test_sem_linha_no_espelho_e_null_com_linha_e_numero(uid, motivo, sem_posicao, com_posicao, posicoes):
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    if posicoes:
        posicao(c, "inv-1", "100")
    _leitura_falhou(c, motivo)
    r = ler(uid)
    if posicoes:
        assert r["total"] == D("100.00") and r["motivos"] == com_posicao
        assert partes(r, "por_banco") == {"Nubank": D("100.00")}
    else:
        assert r == {"total": None, "por_tipo": [], "por_banco": [], "motivos": sem_posicao}


def test_leitura_atual_falhou_com_posicoes_de_antes_mantem_o_numero(uid):
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    posicao(c, "inv-1", "100")
    _leitura_falhou(c)
    r = ler(uid)
    assert r["total"] == D("100.00") and r["motivos"] == ["banco_desatualizado"]
    assert r["por_banco"] == [{"banco": "Nubank", "valor": D("100.00"), "motivos": ["banco_desatualizado"]}]


@pytest.mark.parametrize("motivo_b, esperado", [
    ("investments_read_failed", ["banco_desatualizado"]),  # falhou: o aviso é o desatualizado
    ("", []),  # saudável e sem linha: o número basta, sem aviso (nenhum_investimento só com total null)
])
def test_uma_com_posicoes_e_outra_sem_soma_a_que_tem(uid, motivo_b, esperado):
    posicao(conexao(uid, f"item-a-{uid}", banco="Nubank"), "inv-1", "100")
    _leitura_falhou(conexao(uid, f"item-b-{uid}", banco="XP"), motivo_b)
    r = ler(uid)
    assert r["total"] == D("100.00") and r["motivos"] == esperado
    assert r["por_banco"] == [{"banco": "Nubank", "valor": D("100.00"), "motivos": []}]


# Sem linha no espelho, por estado da conexão. `nenhum_investimento` só no sinal positivo
# de saúde (`desatualizada` False: tela "Atualizado", sync em 48 h, sem tentativa depois).
# O par (status, reason, health) é o que `resolve_connection_state` grava: o sync limpo é
# ACTIVE/"" (pluggy_sync.py:575 → pluggy_health.py:904); NEEDS_USER/ERROR é ERROR/"" (:847).
_DEPOIS = AGORA + timedelta(minutes=1)


@pytest.mark.parametrize("status, item_status, sync, tentativa, esperado", [
    ("ACTIVE", "UPDATED", AGORA, None, ["nenhum_investimento"]),   # sync limpo
    ("ACTIVE", None, AGORA, None, ["nenhum_investimento"]),        # sync limpo, saúde não medida
    ("ERROR", "LOGIN_ERROR", AGORA, None, _FALHA),                 # reconexão pendente
    ("ERROR", "WAITING_USER_INPUT", AGORA, None, _FALHA),
    ("ERROR", "OUTDATED", AGORA, None, _FALHA),
    ("ERROR", "ERROR", AGORA, None, _FALHA),                       # item em erro na Pluggy
    ("ERROR", None, AGORA, None, _FALHA),                          # status local ERROR
    ("LOGIN_ERROR", None, AGORA, None, _FALHA),                    # status local pede o usuário
    ("ACTIVE", "UPDATED", horas_atras(49), None, _FALHA),          # desatualizada
    ("ACTIVE", "UPDATED", AGORA, _DEPOIS, _FALHA),                 # tentativa depois do sync
])
def test_sem_linha_reason_vazio_so_diz_nenhum_com_conexao_saudavel(uid, status, item_status, sync,
                                                                    tentativa, esperado):
    c = conexao(uid, f"item-{uid}", status=status, sync=sync, tentativa=tentativa)
    q("update open_finance_connections set status_reason='', health=%s where id=%s",
      (Jsonb({"item_status": item_status}) if item_status else None, c))
    assert ler(uid) == {"total": None, "por_tipo": [], "por_banco": [], "motivos": esperado}


def test_duas_sem_linha_uma_pede_reconexao_nao_diz_nenhum_investimento(uid):
    conexao(uid, f"item-a-{uid}")
    conexao(uid, f"item-b-{uid}", status="LOGIN_ERROR")
    assert ler(uid) == {"total": None, "por_tipo": [], "por_banco": [], "motivos": ["banco_desatualizado"]}


def test_duas_sem_linha_uma_falhou_nao_diz_nenhum_investimento(uid):
    conexao(uid, f"item-a-{uid}")
    _leitura_falhou(conexao(uid, f"item-b-{uid}"))
    assert ler(uid) == {"total": None, "por_tipo": [], "por_banco": [], "motivos": ["banco_desatualizado"]}


# Posição excluída do total (pausada, outra moeda, resgatada) + conexão viva saudável sem
# linha. Só a pausada chega ao "sem linha": moeda/resgatada são linhas da própria conexão
# viva, que entra em `ultima` e sai com número (0 + o motivo). O controle positivo (viva
# saudável vazia, nada excluído → nenhum_investimento) é o caso ("", ...) acima.
def test_pausada_com_posicao_e_viva_vazia_nao_diz_nenhum_investimento(uid):
    posicao(conexao(uid, f"item-p-{uid}", status="PAUSED"), "inv-p", "999")
    conexao(uid, f"item-{uid}")
    assert ler(uid) == {"total": None, "por_tipo": [], "por_banco": [], "motivos": ["conexao_pausada"]}


@pytest.mark.parametrize("moeda, status, motivos", [
    ("USD", None, ["outra_moeda"]),            # existe, só fora do total em R$: o selo avisa
    ("BRL", "TOTAL_WITHDRAWAL", []),           # resgatada: zero de fato
])
def test_so_posicao_excluida_na_viva_e_zero_com_motivo_nunca_nenhum(uid, moeda, status, motivos):
    posicao(conexao(uid, f"item-{uid}"), "inv-x", "999", moeda=moeda, code=moeda, status=status)
    assert ler(uid) == {"total": D("0.00"), "por_tipo": [], "por_banco": [], "motivos": motivos}


def test_todas_sem_saldo_total_null_e_partes_null(uid):
    c = conexao(uid, f"item-{uid}", banco="Nubank")
    posicao(c, "inv-1", None)
    posicao(c, "inv-2", None, tipo="EQUITY")
    r = ler(uid)
    assert r["total"] is None and r["motivos"] == ["saldo_ausente"]
    assert partes(r) == {"renda_fixa": None, "acoes": None} and partes(r, "por_banco") == {"Nubank": None}


def test_positivo_zero_informado_pelo_banco_e_zero(uid):
    """Controle positivo: posições com saldo 0 vindo do banco somam R$ 0 de verdade."""
    c = conexao(uid, f"item-{uid}")
    posicao(c, "inv-1", "0")
    posicao(c, "inv-2", "0", tipo="EQUITY")
    assert ler(uid) == {"total": D("0.00"), "por_tipo": [], "por_banco": [], "motivos": []}
