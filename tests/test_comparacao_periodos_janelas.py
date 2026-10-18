"""Comparação de períodos (PL-07), parte PURA: janelas, `_linha`, `para_json` (sem banco).

O banco real, o cartão pela fatura, o isolamento e a tool estão em `tests/test_comparacao_periodos.py`.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal as D

import pytest

from core.services import comparacao_periodos as cp


# ── janelas (puro) ──────────────────────────────────────────────────────────
def d(s):
    return date.fromisoformat(s)


@pytest.mark.parametrize("hoje,ano,mes,modo,a,p,encurtado,igual", [
    # P1-B: mês aberto = mês inteiro × anterior inteiro, em qualquer dia de hoje (o dia não muda a janela)
    ("2026-10-12", 2026, 10, "mes_aberto", ("10-01", "10-31"), ("09-01", "09-30"), False, False),
    ("2026-10-31", 2026, 10, "mes_aberto", ("10-01", "10-31"), ("09-01", "09-30"), False, False),
    ("2026-10-01", 2026, 10, "mes_aberto", ("10-01", "10-31"), ("09-01", "09-30"), False, False),
    ("2026-03-30", 2026, 3, "mes_aberto", ("03-01", "03-31"), ("02-01", "02-28"), False, False),
    ("2026-03-28", 2026, 3, "mes_aberto", ("03-01", "03-31"), ("02-01", "02-28"), False, False),
    ("2028-03-29", 2028, 3, "mes_aberto", ("03-01", "03-31"), ("02-01", "02-29"), False, False),
    ("2028-03-30", 2028, 3, "mes_aberto", ("03-01", "03-31"), ("02-01", "02-29"), False, False),
    ("2028-02-29", 2028, 2, "mes_aberto", ("02-01", "02-29"), ("01-01", "01-31"), False, False),
    ("2026-10-12", 2026, 3, "mes_fechado", ("03-01", "03-31"), ("02-01", "02-28"), False, False),
    ("2026-10-12", 2026, 9, "mes_fechado", ("09-01", "09-30"), ("08-01", "08-31"), False, False),
])
def test_janelas_do_mes(hoje, ano, mes, modo, a, p, encurtado, igual):
    j = cp.escolher_janelas(d(hoje), ano, mes)
    ref = lambda mmdd, y: d(f"{y}-{mmdd}")  # noqa: E731
    assert j["modo"] == modo
    assert (j["atual"]["inicio"], j["atual"]["fim"]) == (ref(a[0], ano), ref(a[1], ano))
    assert (j["anterior"]["inicio"], j["anterior"]["fim"]) == (ref(p[0], ano), ref(p[1], ano))
    assert (j["anterior_encurtado"], j["mesmo_tamanho"]) == (encurtado, igual)
    assert j["escolha"]
    assert (j["atual"]["dias"], j["anterior"]["dias"]) == (
        (d(f"{ano}-{a[1]}") - d(f"{ano}-{a[0]}")).days + 1, (d(f"{ano}-{p[1]}") - d(f"{ano}-{p[0]}")).days + 1)


def test_escolha_do_mes_aberto_avisa_que_o_mes_nao_acabou_e_nao_diz_mesmos_dias():
    e = cp.escolher_janelas(d("2026-10-12"), 2026, 10)["escolha"]
    assert "O mês ainda não acabou" in e and "mesmos dias" not in e


@pytest.mark.parametrize("hoje,ano,mes,a,p,encurtado", [
    ("2027-01-15", 2027, 1, ("2027-01-01", "2027-01-31"), ("2026-12-01", "2026-12-31"), False),
    ("2027-01-31", 2027, 1, ("2027-01-01", "2027-01-31"), ("2026-12-01", "2026-12-31"), False),
    ("2027-10-12", 2027, 1, ("2027-01-01", "2027-01-31"), ("2026-12-01", "2026-12-31"), False),  # fechado
])
def test_virada_de_ano(hoje, ano, mes, a, p, encurtado):
    j = cp.escolher_janelas(d(hoje), ano, mes)
    assert ((j["atual"]["inicio"], j["atual"]["fim"]), (j["anterior"]["inicio"], j["anterior"]["fim"])) == (
        (d(a[0]), d(a[1])), (d(p[0]), d(p[1])))
    assert j["anterior_encurtado"] is encurtado


def test_mes_futuro_e_malformado_dao_erro_e_o_futuro_diz_o_mes_corrente():
    with pytest.raises(ValueError, match="2026-10"):
        cp.escolher_janelas(d("2026-10-12"), 2026, 11)
    with pytest.raises(ValueError, match="2026-10"):
        cp.escolher_janelas(d("2026-10-12"), 2027, 1)
    for ano, mes in ((2026, 13), (2026, 0), ("2026", 10), (2026, None), (1, 1), (2026, True), (True, 10)):
        with pytest.raises(ValueError, match="AAAA-MM"):
            cp.escolher_janelas(d("2026-10-12"), ano, mes)


@pytest.mark.parametrize("a,p,msg", [
    ((d("2026-05-31"), d("2026-05-01")), (d("2026-04-01"), d("2026-04-30")), "fim"),
    ((d("2026-05-01"), d("2026-05-31")), (d("2025-01-01"), d("2026-04-30")), "400"),
    ((d("2026-05-01"), d("9999-12-31")), (d("2026-04-01"), d("2026-04-30")), "entre 2000 e 2100"),
    ((d("2026-05-01"), d("2026-05-31")), (d("1999-12-01"), d("1999-12-31")), "entre 2000 e 2100"),
])
def test_intervalos_invalidos(a, p, msg):
    with pytest.raises(ValueError, match=msg):
        cp._intervalos(d("2026-10-12"), a, p)


def test_intervalo_em_andamento_avisa_e_diz_os_tamanhos():
    j = cp._intervalos(d("2026-10-12"), (d("2026-10-01"), d("2026-10-31")), (d("2026-09-01"), d("2026-09-30")))
    assert j["modo"] == "periodos_informados" and not j["mesmo_tamanho"]
    assert "ainda não terminou" in j["escolha"] and "31 e 30 dias" in j["escolha"]
    assert "não é previsão" in j["escolha"] and "contam zero" not in j["escolha"]
    fechado = cp._intervalos(d("2026-10-12"), (d("2026-09-01"), d("2026-09-30")), (d("2026-08-01"), d("2026-08-30")))
    assert "ainda não terminou" not in fechado["escolha"]


@pytest.mark.parametrize("atual,ant,estado,pct", [
    ("45", "0", "nova", None),
    ("0", "200", "sem_gasto", D("-100.0")),
    ("100", "100", "igual", D("0.0")),
    ("90", "100", "variou", D("-10.0")),
    ("111.46", "100", "variou", D("11.5")),        # 11,46 -> 11,5, nunca 12 (um arredondamento só)
    ("0", "0", "vazio", None),
    ("99.96", "100", "variou", D("0.0")),          # -0,04% -> "0.0", nunca "-0.0"
])
def test_linha(atual, ant, estado, pct):
    r = cp._linha(D(atual), D(ant))                # base zero não levanta ZeroDivisionError
    assert (r["estado"], r["pct"]) == (estado, pct) and r["total_delta"] == D(atual) - D(ant)
    assert pct is None or str(r["pct"]) == str(pct)


def test_para_json_dinheiro_e_pct_viram_numero_json():
    r = cp.para_json({"a": D("2400.00"), "l": [{"p": D("-10.0"), "d": date(2026, 10, 1)}], "n": 3, "x": None})
    assert json.dumps(r) == '{"a": 2400.0, "l": [{"p": -10.0, "d": "2026-10-01"}], "n": 3, "x": null}'
