"""`db/rendimentos.calcular`: a regra do Mês e do Total, pura (sem banco). Uma linha por caso da tabela do dono."""
from datetime import date
from decimal import Decimal as D

import pytest

from db.rendimentos import calcular

HOJE = date(2026, 10, 20)
BASE_EM = date(2026, 9, 30)  # fim do mês passado de HOJE
LEU = date(2026, 10, 19)  # o banco leu depois da base


def ativo(tipo="renda_fixa", B="1100", A="1000", Q=None, base="padrao", atualizado_em=LEU, ausente=False, nome="x"):
    b = {"observed_on": BASE_EM, "B0": D("1050"), "A0": D("1000"), "Q0": None} if base == "padrao" else base
    return {"tipo": tipo, "nome": nome, "instituicao": "Banco", "logo_proprio": None, "saldo_ausente": ausente,
            "B": D(B), "A": None if A is None else D(A), "Q": None if Q is None else D(Q), "base": b,
            "atualizado_em": atualizado_em}


def um(**kw):
    a = calcular([ativo(**kw)], HOJE)["categorias"][0]["ativos"][0]
    return a["mes"], a["total"]


def base(B0="1050", A0="1000", Q0=None, em=BASE_EM):
    return {"observed_on": em, "B0": None if B0 is None else D(B0), "A0": None if A0 is None else D(A0),
            "Q0": None if Q0 is None else D(Q0)}


def test_renda_fixa_estavel():
    mes, total = um()
    assert mes == {"valor": D("50.00"), "pct": D("4.76"), "motivo": None}  # (100) - (50) sobre B0 1050
    assert total == {"valor": D("100.00"), "pct": D("10.00"), "motivo": None}


def test_aporte_sobe_saldo_e_aplicado_juntos_e_o_mes_e_so_o_juro():
    mes, total = um(B="1600", A="1500", base=base("1050", "1000"))  # aportou 500, rendeu 50 no mês
    assert mes["valor"] == D("50.00") and total["valor"] == D("100.00")


def test_resgate_parcial_mostra_como_vier():
    mes, _ = um(B="600", A="500", base=base("1050", "1000"))  # tirou 500: (100) - (50) = 50
    assert mes["valor"] == D("50.00")
    mes, _ = um(B="500", A="500", base=base("1050", "1000"))  # tirou 500 e rendeu 0: (0) - (50) = -50 (limite declarado)
    assert mes["valor"] == D("-50.00") and mes["motivo"] is None


def test_renda_fixa_sem_base():
    mes, total = um(base=None)
    assert mes == {"valor": None, "pct": None, "motivo": "sem_base"} and total["valor"] == D("100.00")


@pytest.mark.parametrize("A", [None, "0", "-5"])
def test_aplicado_ausente_no_total_e_no_mes(A):
    mes, total = um(A=A)
    assert total["motivo"] == mes["motivo"] == "aplicado_ausente" and total["valor"] is None


def test_aplicado_da_base_ausente_so_tira_o_mes():
    mes, total = um(base=base(A0=None))
    assert mes["motivo"] == "aplicado_ausente" and total["valor"] == D("100.00")


def test_saldo_da_base_ausente():
    mes, total = um(base=base(B0=None))
    assert mes["motivo"] == "saldo_ausente" and total["valor"] == D("100.00")


def test_saldo_menor_que_o_aplicado_e_perda_legitima():
    _, total = um(B="900", A="1000")
    assert total == {"valor": D("-100.00"), "pct": D("-10.00"), "motivo": None}


@pytest.mark.parametrize("atualizado", [BASE_EM, date(2026, 9, 1), None])
def test_sem_leitura_no_mes(atualizado):
    mes, total = um(atualizado_em=atualizado)
    assert mes["motivo"] == "sem_leitura_no_mes" and total["valor"] == D("100.00")


def test_saldo_ausente_hoje_vence_tudo():
    mes, total = um(ausente=True, base=None)
    assert mes["motivo"] == total["motivo"] == "saldo_ausente"
    assert calcular([ativo(ausente=True)], HOJE)["categorias"][0]["ativos"][0]["saldo"] is None


def test_acao_quantidade_igual_diferente_e_ausente():
    kw = dict(tipo="acoes", B="1100", A=None, Q="10", base=base("1000", None, "10"))
    mes, total = um(**kw)
    assert mes == {"valor": D("100.00"), "pct": D("10.00"), "motivo": None}
    assert total == {"valor": None, "pct": None, "motivo": "sem_custo"}
    assert um(**{**kw, "base": base("1000", None, "11")})[0]["motivo"] == "quantidade_mudou"
    assert um(**{**kw, "Q": None})[0]["motivo"] == "quantidade_ausente"
    assert um(**{**kw, "base": base("1000", None, None)})[0]["motivo"] == "quantidade_ausente"
    assert um(**{**kw, "base": base(None, None, "10")})[0]["motivo"] == "saldo_ausente"
    assert um(**{**kw, "Q": "10.0"})[0]["valor"] == D("100.00"), "10 == 10.0 em Decimal"


def test_base_zero_nao_tem_pct():
    mes, _ = um(tipo="fii", B="10", A=None, Q="1", base=base("0", None, "1"))
    assert mes["valor"] == D("10.00") and mes["pct"] is None


def test_menos_zero_vira_zero():
    mes, _ = um(tipo="fii", B="1000.001", A=None, Q="1", base=base("1000.004", None, "1"))
    assert str(mes["valor"]) == "0.00" and str(mes["pct"]) == "0.00"


def test_tesouro_e_custo_na_categoria_tesouro():
    c = calcular([ativo(tipo="tesouro")], HOJE)["categorias"][0]
    assert (c["tipo"], c["rotulo"]) == ("tesouro", "Tesouro Direto") and c["total"]["valor"] == D("100.00")


def test_categoria_parcial_soma_so_quem_tem_dado_e_conta_o_resto():
    r = calcular([ativo(nome="a"), ativo(nome="b", base=None), ativo(nome="c", A=None)], HOJE)
    c = r["categorias"][0]
    assert c["quantidade"] == 3
    assert c["mes"] == {"valor": D("50.00"), "pct": D("4.76"), "sem_dado": 2}  # só "a"; "c" perde o Mês também
    assert c["total"] == {"valor": D("200.00"), "pct": D("10.00"), "sem_dado": 1}  # a + b: 200 sobre A 2000


def test_soma_das_categorias_e_o_topo_exato_e_a_ordem_e_por_saldo():
    r = calcular([ativo(nome="pequeno", B="105.01", A="100", base=base("102.003", "100")),
                  ativo(nome="grande", B="2000", A="1000", base=base("1900", "1000")),
                  ativo(tipo="acoes", B="3000", A=None, Q="2", base=base("2900", None, "2"), nome="PETR")], HOJE)
    assert [c["tipo"] for c in r["categorias"]] == ["acoes", "renda_fixa"]
    assert [a["nome"] for a in r["categorias"][1]["ativos"]] == ["grande", "pequeno"]
    for per in ("mes", "total"):
        assert r[per]["valor"] == sum(c[per]["valor"] for c in r["categorias"] if c[per]["valor"] is not None)
    assert r["mes"]["valor"] == D("100.00") + D("100.00") + D("3.01") and r["total"]["sem_dado"] == 1
    assert r["saldo"] == D("5105.01") and r["quantidade"] == 3 and r["mes_desde"] == BASE_EM


def test_agregado_sem_nenhum_dado_e_null():
    r = calcular([ativo(tipo="acoes", Q="1", base=None, A=None)], HOJE)
    assert r["mes"] == {"valor": None, "pct": None, "sem_dado": 1}
    assert r["total"] == {"valor": None, "pct": None, "sem_dado": 1}
    assert calcular([], HOJE)["mes"] == {"valor": None, "pct": None, "sem_dado": 0}


def test_saldo_zero_com_ausente_e_null():
    r = calcular([ativo(B="0", ausente=True)], HOJE)
    assert r["saldo"] is None and r["categorias"][0]["saldo"] is None


@pytest.mark.parametrize("tipo", ["fundos", "etf", "fii"])
def test_fundos_etf_fii_seguem_a_cotacao_e_nao_o_custo(tipo):
    mes, total = um(tipo=tipo, B="1100", A="1000", Q="10", base=base("1000", "900", "10"))
    assert total == {"valor": None, "pct": None, "motivo": "sem_custo"}
    assert mes["valor"] == D("100.00"), "Mês = B − B0 (cotação), não (B−A)−(B0−A0) = 0"


def test_saldo_do_ativo_nunca_e_menos_zero():
    a = calcular([ativo(B="-0.00")], HOJE)["categorias"][0]["ativos"][0]
    assert str(a["saldo"]) == "0.00"
