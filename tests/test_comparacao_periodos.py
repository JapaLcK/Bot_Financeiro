"""Comparação de períodos equivalentes (PL-07): a janela (tabela pura) e os números (banco real).

Relógio: `agora` injetado (12/10/2026, fuso do app); o mês aberto é o mês INTEIRO (P1-B, igual ao `resumo_do_mes`). Cartão pela FATURA, closing_day 10: a compra de
20/09 cai na fatura de 10/10 e o parcelado de 20/08 (3x) tem uma parcela em 10/09, uma em 10/10 e
uma em 10/11. Controles por mutação (CLAUDE.md §3), medidos para o GRUPO: base trocada por
`get_summary_by_period`, mês aberto trocado pelos mesmos N dias, `user_id` fora de uma perna, gate
depois da leitura, `hoje` em UTC, pct arredondado em 0 casas. Os resultados estão no relato do PR.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

import db
from conftest import usuario_pagante
from core.services import comparacao_periodos as cp
from core.services import plan_service as plans
from core.services.ai_guard import check
from core.services.ai_chat.tools import launches
from core.services.ai_chat.tools.launches import _compare_periods
from db import resumo_mes
from db.analytics import compute_kpis
from tests._patrimonio_helpers import q
from utils_date import _tz

AGORA = datetime(2026, 10, 12, 10, 0, tzinfo=_tz())


@pytest.fixture(autouse=True)
def _relogio(monkeypatch):
    """Nenhum teste daqui lê o relógio real (a guarda de histórico quebraria com o calendário)."""
    monkeypatch.setattr(cp, "now_tz", lambda: AGORA)
    monkeypatch.setattr(launches, "today_tz", lambda: AGORA.date())   # `_meses_inteiros`/`_limita_periodo` da tool


@pytest.fixture
def tier(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    atual = ["plus"]
    monkeypatch.setattr(plans, "get_plan_tier", lambda uid: atual[0])
    return lambda v: atual.__setitem__(0, v)


def d(s):
    return date.fromisoformat(s)


# ── banco real ──────────────────────────────────────────────────────────────
def _em(mes: int, dia: int, ano: int = 2026) -> datetime:
    return datetime(ano, mes, dia, 12, tzinfo=_tz())


def _lanc(uid, tipo, valor, mes, dia, cat=None, interno=False, ano=2026):
    q("""insert into launches (user_id, tipo, valor, categoria, criado_em, is_internal_movement)
         values (%s, %s, %s, %s, %s, %s)""", (uid, tipo, valor, cat, _em(mes, dia, ano), interno))


def semeia(uid: int, fator: int = 1) -> None:
    """Outubro (1-12) e setembro (1-12) de 2026, mais o que tem de ficar de fora de cada janela."""
    desp = lambda *a, **k: _lanc(uid, "despesa", a[0] * fator, *a[1:], **k)  # noqa: E731
    desp(600, 10, 3, "Alimentação"); desp(300, 10, 5, "Alimentacao")   # noqa: E702  duas grafias, uma categoria
    desp(45, 10, 4, "Streaming"); desp(20, 10, 6, None); desp(100, 10, 8, "Transporte")        # noqa: E702
    desp(500, 10, 7, "Alimentação", interno=True)                         # movimento interno: fora
    desp(777, 10, 15, "Alimentação")                                      # depois de hoje (12): entra, o mês aberto é inteiro
    _lanc(uid, "receita", 3000 * fator, 10, 5)
    desp(1000, 9, 2, "alimentação"); desp(200, 9, 8, "Lazer"); desp(100, 9, 3, "Transporte")   # noqa: E702
    desp(1800, 9, 20, "Alimentação")                                      # setembro depois do dia 12: entra, o anterior é inteiro
    _lanc(uid, "receita", 2500 * fator, 9, 1)
    cartao = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, cartao, 70 * fator, "Alimentação", "fatura 10/10", date(2026, 9, 20))
    db.add_credit_purchase(uid, cartao, 150 * fator, "Saúde", "fatura 10/09", date(2026, 9, 3))
    db.add_credit_purchase(uid, cartao, 999 * fator, "Lazer", "fatura 10/11", date(2026, 10, 15))
    db.add_credit_purchase_installments(uid, cartao, 300 * fator, "Transporte", "3x", date(2026, 8, 20), 3)
    _lanc(uid, "despesa", 400 * fator, 8, 15, "Transporte")               # agosto, p/ o mês fechado
    _lanc(uid, "receita", 1000 * fator, 8, 15)


def _linhas(r, chave="categorias"):
    return {c["categoria"]: c for c in r[chave]}


def test_mes_aberto_inteiro_cartao_pela_fatura_e_categorias():
    uid = usuario_pagante("plus")
    semeia(uid)
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["modo"] == "mes_aberto" and not r["mesmo_tamanho"] and not r["anterior_encurtado"]   # 31 × 30 dias
    assert (r["atual"]["fim"], r["anterior"]["fim"]) == (date(2026, 10, 31), date(2026, 9, 30))
    # Outubro inteiro: 900 + 45 + 20 + 100 + 777 (dia 15) + (70 + parcela de 100 na fatura de 10/10). Setembro inteiro:
    # 1000 + 200 + 100 + 1800 (dia 20) + (150 + 100). A receita não muda (3000 e 2500: nenhuma é depois do dia 12).
    assert (r["despesa"]["total_atual"], r["despesa"]["total_anterior"]) == (D("2012.00"), D("3350.00"))
    assert (r["despesa"]["total_delta"], r["despesa"]["pct"]) == (D("-1338.00"), D("-39.9"))
    assert (r["receita"]["total_atual"], r["receita"]["pct"]) == (D("3000.00"), D("20.0"))
    assert (r["resultado"]["total_atual"], r["resultado"]["total_anterior"], r["resultado"]["total_delta"]) == (D("988.00"), D("-850.00"), D("1838.00"))
    assert r["base"] == {"regra": "entrou_e_saiu_do_mes", "cartao": "fatura", "origem": "lançamentos + faturas de cartão",
                         "cartao_lancamentos": {"atual": 2, "anterior": 2}}
    assert r["motivo"] is None and r["categorias_omitidas"] == 0
    assert [c["categoria"] for c in r["categorias"]] == [  # |delta| desc
        "Alimentação", "Lazer", "Saúde", "Streaming", "sem categoria", "Transporte"]
    por = _linhas(r)
    assert (por["Streaming"]["estado"], por["Streaming"]["pct"]) == ("nova", None)
    assert (por["Lazer"]["estado"], por["Lazer"]["pct"], por["Lazer"]["total_delta"]) == ("sem_gasto", D("-100.0"), D("-200.00"))
    assert (por["Saúde"]["estado"], por["Transporte"]["estado"]) == ("sem_gasto", "igual")
    assert (por["Alimentação"]["total_atual"], por["Alimentação"]["total_anterior"], por["Alimentação"]["pct"]) == (D("1747.00"), D("2800.00"), D("-37.6"))
    assert por["sem categoria"]["estado"] == "nova"
    assert sum(c["total_atual"] for c in r["categorias"]) == r["despesa"]["total_atual"]


def test_o_dia_de_hoje_nao_muda_o_mes_aberto():
    uid = usuario_pagante("plus")
    semeia(uid)
    r = cp.comparar_mes(uid, 2026, 10, datetime(2026, 10, 31, 9, tzinfo=_tz()))
    assert not r["anterior_encurtado"] and not r["mesmo_tamanho"] and "Outubro até agora" in r["escolha"]
    assert (r["atual"]["dias"], r["anterior"]["dias"]) == (31, 30)
    assert r["despesa"] == cp.comparar_mes(uid, 2026, 10, AGORA)["despesa"]     # dia 31 e dia 12: o mesmo número
    # setembro inteiro: 1000 + 200 + 100 + 1800 e as faturas que fecham em setembro (150 + 100)
    assert r["despesa"]["total_anterior"] == D("3350.00")


def test_hora_perto_da_virada_usa_o_dia_do_fuso_do_app_nao_utc():
    uid = usuario_pagante("plus")
    sp = datetime(2026, 10, 31, 23, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert sp.astimezone(timezone.utc).month == 11          # em UTC já é novembro
    r = cp.comparar_mes(uid, 2026, 10, sp)
    assert r["modo"] == "mes_aberto" and r["atual"]["fim"] == date(2026, 10, 31)


def test_mes_fechado_fecha_com_os_outros_consumidores():
    """Diferencial: a mesma conta do Saiu do mês, do resumo do mês e das Análises."""
    uid = usuario_pagante("plus")
    semeia(uid)
    r = cp.comparar_mes(uid, 2026, 9, AGORA)
    assert r["modo"] == "mes_fechado" and not r["mesmo_tamanho"]
    res = resumo_mes.resumo_do_mes(uid, 2026, 9, AGORA)
    assert (r["despesa"]["total_atual"], r["receita"]["total_atual"]) == (res["saiu"], res["entrou"])
    assert (r["despesa"]["total_anterior"], r["receita"]["total_anterior"]) == (res["anterior"]["saiu"], res["anterior"]["entrou"])
    k, ka = compute_kpis(uid, date(2026, 9, 1), date(2026, 10, 1)), compute_kpis(uid, date(2026, 8, 1), date(2026, 9, 1))
    assert (float(r["despesa"]["total_atual"]), float(r["receita"]["total_atual"])) == (k["total_expense"], k["total_income"])
    assert (float(r["despesa"]["total_anterior"]), float(r["receita"]["total_anterior"])) == (ka["total_expense"], ka["total_income"])
    assert sum(c["total_atual"] for c in r["categorias"]) == r["despesa"]["total_atual"]
    assert sum(c["total_anterior"] for c in r["categorias"]) == r["despesa"]["total_anterior"]


def test_usuario_b_na_mesma_janela_nao_vaza_nem_centavo():
    a, b = usuario_pagante("plus"), usuario_pagante("plus")
    semeia(a)
    semeia(b, fator=7)
    ra, rb = cp.comparar_mes(a, 2026, 10, AGORA), cp.comparar_mes(b, 2026, 10, AGORA)
    assert (ra["despesa"]["total_atual"], ra["despesa"]["total_anterior"]) == (D("2012.00"), D("3350.00"))
    assert ra["base"]["cartao_lancamentos"] == {"atual": 2, "anterior": 2}
    assert _linhas(ra)["Alimentação"]["total_atual"] == D("1747.00")
    janelas = (("2026-10-01", "2026-10-12"), ("2026-09-01", "2026-09-12"))   # e o período livre (cartão pela compra)
    la, lb = _intervalo(a, *janelas), _intervalo(b, *janelas)
    assert (la["despesa"]["total_atual"], la["despesa"]["total_anterior"]) == (D("1065.00"), D("1450.00"))
    assert (lb["despesa"]["total_atual"], lb["despesa"]["total_anterior"]) == (D("7455.00"), D("10150.00"))
    assert la["base"]["cartao_lancamentos"] == lb["base"]["cartao_lancamentos"] == {"atual": 0, "anterior": 1}
    assert (rb["despesa"]["total_atual"], rb["despesa"]["total_anterior"]) == (D("14084.00"), D("23450.00"))   # 7 × A
    assert _linhas(rb)["Alimentação"]["total_atual"] == D("12229.00")


def test_so_o_mes_atual_com_dados_vira_sem_base_sem_dividir_por_zero():
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 80, 10, 3, "Mercado")
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["motivo"] == "sem_base" and r["categorias"] == []
    assert (r["despesa"]["estado"], r["despesa"]["pct"], r["despesa"]["total_atual"]) == ("nova", None, D("80.00"))
    assert (r["receita"]["estado"], r["resultado"]["pct"]) == ("vazio", None)
    assert "Nenhuma fatura de cartão no mês" in r["escolha"]            # sem cartão na janela: o texto diz


def test_categoria_vazia_vira_sem_categoria_e_grafias_formam_uma_linha():
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 10, 9, 3, "Alimentacao"); _lanc(uid, "despesa", 20, 9, 4, "")      # noqa: E702
    _lanc(uid, "despesa", 30, 10, 3, "alimentação"); _lanc(uid, "despesa", 40, 10, 4, None)  # noqa: E702
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    por = _linhas(r)
    assert sorted(por) == ["alimentação", "sem categoria"]               # rótulo = grafia do lado atual
    assert (por["alimentação"]["total_atual"], por["alimentação"]["total_anterior"]) == (D("30.00"), D("10.00"))
    assert (por["sem categoria"]["total_atual"], por["sem categoria"]["total_anterior"]) == (D("40.00"), D("20.00"))


def test_mais_de_dez_categorias_omite_as_menores():
    uid = usuario_pagante("plus")
    for i in range(12):
        _lanc(uid, "despesa", 10 + i, 10, 3, f"cat{i:02d}")
    _lanc(uid, "despesa", 1, 9, 3, "base")
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert len(r["categorias"]) == 10 and r["categorias_omitidas"] == 3   # 12 de outubro + "base" (sem gasto)
    assert r["categorias"][0]["categoria"] == "cat11"


def test_downgrade_devolve_none_sem_ler_nada(tier, monkeypatch):
    uid = usuario_pagante("plus")
    semeia(uid)
    assert cp.comparar_mes(uid, 2026, 10, AGORA)["despesa"]["total_atual"] == D("2012.00")   # positivo: Plus lê
    tier("essencial")
    boom = lambda *a, **k: pytest.fail("leu algo depois do gate")  # noqa: E731
    for alvo in ("get_conn", "history_earliest_date"):
        monkeypatch.setattr(cp, alvo, boom)
    for nome in ("totais", "categorias"):
        monkeypatch.setattr(resumo_mes, nome, boom)
    assert cp.comparar_mes(uid, 2026, 10, AGORA) is None
    assert cp.comparar_intervalos(uid, (date(2026, 10, 1), date(2026, 10, 12)), (date(2026, 9, 1), date(2026, 9, 12)), AGORA) is None
    for args in ({}, {"mes": "2026-10"}, {"period_a_start": "2026-09-01", "period_a_end": "2026-09-12",
                                          "period_b_start": "2026-10-01", "period_b_end": "2026-10-12"}):
        assert _compare_periods(uid, args)["error"] == "pro_required"


def test_historico_do_plano_corta_sem_devolver_totais(tier):
    uid = usuario_pagante("plus")
    semeia(uid)
    r = cp.comparar_mes(uid, 2025, 10, AGORA)          # Plus: 365 dias; o anterior (09/2025) começa antes do corte
    assert r["motivo"] == "inicio_do_historico" and r["despesa"] is None and r["categorias"] == []
    assert r["anterior"]["inicio"] == date(2025, 9, 1) and r["escolha"]
    assert cp.comparar_mes(uid, 2026, 9, AGORA)["despesa"] is not None   # positivo: dentro da janela


def test_categorias_falhando_mantem_os_totais_e_loga_so_a_classe(monkeypatch, caplog):
    uid = usuario_pagante("plus")
    semeia(uid)

    def falha(*a, **k):
        raise RuntimeError("segredo-do-usuario 1234")
    monkeypatch.setattr(resumo_mes, "categorias", falha)
    with caplog.at_level(logging.WARNING):
        r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["motivo"] == "categorias_indisponiveis" and r["categorias"] == []
    assert r["despesa"]["total_atual"] == D("2012.00")
    assert "RuntimeError" in caplog.text and "segredo" not in caplog.text


def _so_json(v):
    """Nada de Decimal/date cru: só o que `json.dumps` entende sem `default=`."""
    if isinstance(v, dict):
        return all(_so_json(x) for x in v.values())
    if isinstance(v, list):
        return all(_so_json(x) for x in v)
    return v is None or type(v) in (str, int, float, bool)


def test_tool_devolve_json_puro_e_valida_os_argumentos():
    uid = usuario_pagante("plus")
    semeia(uid)
    r = _compare_periods(uid, {"mes": "2026-09"})
    assert _so_json(r) and json.dumps(r) and r["despesa"]["total_atual"] == 3350.0 and r["atual"]["inicio"] == "2026-09-01"
    r4 = _compare_periods(uid, {"period_a_start": "2026-09-01", "period_a_end": "2026-09-12",
                                "period_b_start": "2026-10-01", "period_b_end": "2026-10-12"})
    # período livre: cartão pela data da compra (nenhuma compra de cartão em 01-12/10; 150 em 03/09), não pela fatura
    assert r4["modo"] == "periodos_informados" and r4["despesa"]["total_atual"] == 1065.0 and r4["despesa"]["total_anterior"] == 1450.0
    assert r4["base"]["cartao"] == "data_da_compra"
    assert "error" in _compare_periods(uid, {})                      # preserva o contrato do eval
    for ruim in ("2026-13", "outubro", "2026-10-05", "2026-11", True, 202610, ["2026-10"]):
        assert "error" in _compare_periods(uid, {"mes": ruim}), ruim
    assert "2026-10" in _compare_periods(uid, {"mes": "2026-11"})["error"]
    assert "error" in _compare_periods(uid, {"period_a_start": "2026-05-01", "period_a_end": "2026-04-01",
                                             "period_b_start": "2026-05-01", "period_b_end": "2026-05-31"})
    assert "error" in _compare_periods(uid, {"period_a_start": "2026-04-01"})


@pytest.mark.parametrize("valor", [20260901, ["2026-09-01"], {"d": 1}, "9999-12-31", "2026-02-30", "ontem"])
def test_tool_data_que_nao_le_vira_error_limpo(valor):
    uid = usuario_pagante("plus")
    args = {"period_a_start": "2026-09-01", "period_a_end": "2026-09-12",
            "period_b_start": "2026-10-01", "period_b_end": "2026-10-12"}
    assert "error" not in _compare_periods(uid, args)                # positivo: os argumentos limpos leem
    for campo in args:
        r = _compare_periods(uid, {**args, campo: valor})
        if valor == "9999-12-31" and campo.endswith("_end"):   # fim no futuro: `_limita_periodo` corta no mês corrente
            assert r["atual" if campo == "period_b_end" else "anterior"]["fim_ajustado"] is True and "error" not in r
            continue
        assert list(r) == ["error"] or list(r) == ["error", "message"], (campo, r)


# ── rodada 2: ordem trocada, bordas, rótulo, fuso ───────────────────────────
def _intervalo(uid, atual, anterior, agora=AGORA):
    return cp.comparar_intervalos(uid, tuple(map(d, atual)), tuple(map(d, anterior)), agora)


def test_historico_vale_para_o_periodo_mais_antigo_mesmo_com_a_ordem_trocada(tier):
    """Plus: 365 dias. Com AGORA em 12/10/2026 o corte é 12/10/2025. O modelo pode mandar A e B trocados."""
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 111, 1, 15, "Velho", ano=2025)
    _lanc(uid, "despesa", 50, 10, 3, "Novo")
    novo = ("2026-10-01", "2026-10-09")
    for atual, anterior in ((novo, ("2025-01-10", "2025-01-20")), (("2025-01-10", "2025-01-20"), novo)):
        r = _intervalo(uid, atual, anterior)
        assert r["motivo"] == "inicio_do_historico" and r["despesa"] is None and r["categorias"] == [], (atual, anterior)
    # positivo: dentro do corte, na ordem normal e na trocada
    for atual, anterior in ((novo, ("2025-10-12", "2025-10-20")), (("2025-10-12", "2025-10-20"), novo)):
        assert _intervalo(uid, atual, anterior)["despesa"] is not None
    # borda: início == corte passa; um dia antes corta
    assert _intervalo(uid, novo, ("2025-10-12", "2025-10-13"))["motivo"] != "inicio_do_historico"
    assert _intervalo(uid, novo, ("2025-10-11", "2025-10-13"))["motivo"] == "inicio_do_historico"


def test_agora_em_utc_vira_o_dia_do_fuso_do_app():
    """01/11 02:30 UTC ainda é 31/10 23:30 em São Paulo: outubro segue aberto."""
    uid = usuario_pagante("plus")
    utc = datetime(2026, 11, 1, 2, 30, tzinfo=timezone.utc)
    r = cp.comparar_mes(uid, 2026, 10, utc)
    assert r["modo"] == "mes_aberto" and r["atual"]["fim"] == date(2026, 10, 31)


def test_rotulo_da_categoria_e_uma_linha_curta_sem_marcacao():
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 10, 10, 3, "Lazer\n*grande*​_x_`y`")
    _lanc(uid, "despesa", 20, 10, 4, "z" * 500)
    _lanc(uid, "despesa", 1, 9, 3, "base")
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    nomes = [c["categoria"] for c in r["categorias"] if c["estado"] == "nova"]
    assert len(nomes) == 2
    for n in nomes:
        assert len(n) <= 40 and not set(n) & set("\n*_`​"), n
    assert "Lazer grandexy" in nomes and any(n.endswith("…") for n in nomes)


def test_rotulo_e_a_grafia_do_lancamento_mais_recente_e_cartao_sem_categoria():
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 10, 10, 3, "alimentação")
    _lanc(uid, "despesa", 10, 10, 9, "Alimentacao")                 # o mais recente manda
    _lanc(uid, "despesa", 10, 9, 3, "ALIMENTAÇÃO")
    cartao = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, cartao, 15, None, "sem categoria", date(2026, 9, 25))   # fatura de 10/10
    db.add_credit_purchase(uid, cartao, 5, "", "vazia", date(2026, 9, 26))
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    por = _linhas(r)
    assert sorted(por) == ["Alimentacao", "sem categoria"]
    assert por["sem categoria"]["total_atual"] == D("20.00") and por["sem categoria"]["estado"] == "nova"


def test_ultimo_dia_da_janela_entra_e_contagens_de_cartao_assimetricas():
    uid = usuario_pagante("plus")
    cartao = db.create_card(uid, "Nubank", closing_day=12, due_day=19)    # a fatura fecha no dia 12, dentro do mês
    for dia in (3, 5, 7):
        db.add_credit_purchase(uid, cartao, 10, "Mercado", f"out{dia}", date(2026, 10, dia))
    db.add_credit_purchase(uid, cartao, 40, "Mercado", "set", date(2026, 9, 4))
    q("""insert into launches (user_id, tipo, valor, categoria, criado_em) values (%s, 'despesa', 7, 'Mercado', %s),
         (%s, 'despesa', 3, 'Mercado', %s)""", (uid, datetime(2026, 10, 12, 23, 59, tzinfo=_tz()), uid,
                                              datetime(2026, 9, 12, 23, 59, tzinfo=_tz())))
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert (r["atual"]["fim"], r["anterior"]["fim"]) == (date(2026, 10, 31), date(2026, 9, 30))   # mesmo número: nada depois do dia 12
    assert (r["despesa"]["total_atual"], r["despesa"]["total_anterior"]) == (D("37.00"), D("43.00"))
    assert r["base"]["cartao_lancamentos"] == {"atual": 3, "anterior": 1}
    assert "Nenhuma fatura" not in r["escolha"]


def test_categorias_com_erro_de_banco_nao_derrubam_a_conexao(monkeypatch):
    uid = usuario_pagante("plus")
    semeia(uid)

    def erro_sql(cur, *a, **k):
        cur.execute("select 1/0")
    monkeypatch.setattr(resumo_mes, "categorias", erro_sql)
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["motivo"] == "categorias_indisponiveis" and r["despesa"]["total_atual"] == D("2012.00")
    monkeypatch.undo()
    assert cp.comparar_mes(uid, 2026, 10, AGORA)["motivo"] is None   # a conexão voltou limpa ao pool


# ── rodada 3: motivos, período futuro, ai_guard, UTC em intervalos ──────────
def test_agora_utc_tambem_vale_para_intervalos():
    """01/11 02:30 UTC é 31/10 23:30 em SP: o período que acaba em 01/11 ainda não terminou."""
    uid = usuario_pagante("plus")
    utc = datetime(2026, 11, 1, 2, 30, tzinfo=timezone.utc)
    r = cp.comparar_intervalos(uid, (d("2026-10-01"), d("2026-11-01")), (d("2026-09-01"), d("2026-10-01")), utc)
    assert "ainda não terminou (hoje é 31/10/2026)" in r["escolha"]


def test_periodo_futuro_so_traz_o_lancado_e_diz_que_nao_e_previsao():
    uid = usuario_pagante("plus")
    cartao = db.create_card(uid, "Nubank", closing_day=31, due_day=10)
    db.add_credit_purchase_installments(uid, cartao, 900, "casa", "3x", date(2026, 10, 5), 3)   # 3 parcelas de 300
    r = _intervalo(uid, ("2026-11-01", "2026-11-30"), ("2026-10-01", "2026-10-31"))
    # período livre: o parcelado entra INTEIRO no dia da compra (as 3 parcelas têm a mesma purchased_at)
    assert (r["despesa"]["total_atual"], r["despesa"]["total_anterior"]) == (D("0.00"), D("900.00"))
    assert "não é previsão" in r["escolha"] and "contam zero" not in r["escolha"]
    assert "inteira no dia da compra" in r["escolha"]


def test_motivos_do_bloco_de_contas_viram_aviso_e_falha_deles_degrada(monkeypatch, caplog):
    import db.contas_hoje as contas
    uid = usuario_pagante("plus")
    semeia(uid)
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["motivos"] == [] and r["nota"] is None                       # usuário limpo: sem aviso
    monkeypatch.setattr(contas, "listar", lambda cur, u, **k: {"motivos": ["banco_desatualizado", "conciliacao_pendente"]})
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["motivos"] == ["conciliacao_pendente", "banco_desatualizado"]   # a ordem fixa do resumo do mês
    assert "incompleto" in r["nota"] and "conciliacao_pendente" in r["nota"]

    def falha(cur, u, **k):
        raise RuntimeError("segredo-do-usuario 1234")
    monkeypatch.setattr(contas, "listar", falha)
    with caplog.at_level(logging.WARNING):
        r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["motivos"] == [] and r["nota"] is None and r["despesa"]["total_atual"] == D("2012.00")
    assert "RuntimeError" in caplog.text and "segredo" not in caplog.text


def test_erro_de_sql_num_extra_nao_derruba_o_outro_extra(monkeypatch):
    """Savepoint: o 1/0 aborta só o extra; o outro extra e os totais seguem."""
    import db.contas_hoje as contas
    uid = usuario_pagante("plus")
    semeia(uid)

    def erro_sql(cur, *a, **k):
        cur.execute("select 1/0")
    def listar_que_le(cur, u, **k):
        cur.execute("select 1")                      # precisa de transação viva depois do erro das categorias
        return {"motivos": ["conciliacao_pendente"]}
    monkeypatch.setattr(resumo_mes, "categorias", erro_sql)
    monkeypatch.setattr(contas, "listar", listar_que_le)
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["motivo"] == "categorias_indisponiveis" and r["motivos"] == ["conciliacao_pendente"]
    monkeypatch.undo()
    monkeypatch.setattr(contas, "listar", lambda cur, u, **k: cur.execute("select 1/0"))
    r = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert r["motivos"] == [] and r["motivo"] is None and r["categorias"]   # categorias seguem


def test_afirmacao_certa_fica_sustentada_pelo_json_da_tool_e_a_trocada_nao():
    uid = usuario_pagante("plus")
    semeia(uid)
    r = _compare_periods(uid, {"mes": "2026-10"})
    evidencia = [json.dumps(r, ensure_ascii=False)]                    # sem default=: tudo já é JSON
    certo = check("🐷 Você gastou R$ 2.012,00 contra R$ 3.350,00 em setembro.", evidencia, "compara")
    assert certo and all(c.supported for c in certo), certo
    errado = check("🐷 Você gastou R$ 2.013,00 contra R$ 3.350,00 em setembro.", evidencia, "compara")
    assert any(not c.supported for c in errado), errado
    cat = check("🐷 Em Lazer foram R$ 0,00 contra R$ 200,00.", evidencia, "compara")
    assert cat and all(c.supported for c in cat), cat


def test_periodo_livre_cartao_pela_data_da_compra_e_mes_pela_fatura():
    uid = usuario_pagante("plus")
    semeia(uid)
    atual, anterior = ("2026-10-01", "2026-10-10"), ("2026-09-01", "2026-09-10")   # fim < hoje: nenhuma é "mês inteiro"
    livre = _intervalo(uid, atual, anterior)
    assert livre["base"]["regra"] == "periodo_livre" and livre["base"]["cartao"] == "data_da_compra"
    assert "data da compra" in livre["escolha"]
    # Outubro 1-10 não tem compra de cartão (as faturas de 10/10 são de compras de setembro e agosto); setembro
    # 1-10 tem a de 150 do dia 03. A fatura que fecha na janela NÃO entra: 1065 e 1450, não 2012 e 3350 do `mes`.
    assert (livre["despesa"]["total_atual"], livre["despesa"]["total_anterior"]) == (D("1065.00"), D("1450.00"))
    assert livre["base"]["cartao_lancamentos"] == {"atual": 0, "anterior": 1}
    # o parcelado 3x de 20/08 (as 3 parcelas de 100 com a mesma data) entra inteiro em agosto
    ago = _intervalo(uid, ("2026-08-01", "2026-08-31"), ("2026-07-01", "2026-07-31"))
    assert ago["despesa"]["total_atual"] == D("700.00") and ago["base"]["cartao_lancamentos"]["atual"] == 3
    # o mesmo período pelo caminho `mes`: fatura
    mes = cp.comparar_mes(uid, 2026, 10, AGORA)
    assert mes["base"]["cartao"] == "fatura" and mes["despesa"]["total_atual"] == D("2012.00")
    # diferencial contra as outras tools de período livre da IA (mesmas consultas)
    from core.services.ai_chat.tools import get_tool
    for (ini, fim), lado in ((atual, "total_atual"), (anterior, "total_anterior")):
        a = {"start_date": ini, "end_date": fim}
        resumo = get_tool("get_period_summary").execute(uid, a)
        top = get_tool("get_top_categories").execute(uid, {**a, "limit": 20})
        assert resumo["cartao_por"] == "data_da_compra"
        assert float(livre["despesa"][lado]) == resumo["despesa"] == round(sum(c["total"] for c in top["categories"]), 2)
    assert float(livre["receita"]["total_atual"]) == get_tool("get_period_summary").execute(
        uid, {"start_date": atual[0], "end_date": atual[1]})["receita"]


def test_periodo_livre_une_grafias_que_so_diferem_em_caixa_e_acento_entre_janelas():
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 10, 9, 3, "Alimentacao")
    _lanc(uid, "despesa", 30, 10, 3, "ALIMENTAÇÃO")
    r = _intervalo(uid, ("2026-10-01", "2026-10-12"), ("2026-09-01", "2026-09-12"))
    assert [(c["categoria"], c["total_atual"], c["total_anterior"], c["estado"]) for c in r["categorias"]] == [
        ("ALIMENTAÇÃO", D("30.00"), D("10.00"), "variou")]


def _quatro(a, b):
    """As 4 datas da tool: A (o anterior) e B (o atual), cada um `(início, fim)`."""
    return {"period_a_start": a[0], "period_a_end": a[1], "period_b_start": b[0], "period_b_end": b[1]}


def test_quatro_datas_de_meses_inteiros_dao_o_mesmo_numero_de_mes_e_do_painel():
    uid = usuario_pagante("plus")
    semeia(uid)
    r = _compare_periods(uid, _quatro(("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")))
    m = _compare_periods(uid, {"mes": "2026-09"})
    res = resumo_mes.resumo_do_mes(uid, 2026, 9, AGORA)
    assert r["modo"] == "periodos_informados" and r["base"]["cartao"] == "fatura" and r["base"]["regra"] == "entrou_e_saiu_do_mes"
    assert (r["despesa"]["total_atual"], r["receita"]["total_atual"]) == (float(res["saiu"]), float(res["entrou"])) == (3350.0, 2500.0)
    assert (r["despesa"]["total_anterior"], r["receita"]["total_anterior"]) == (float(res["anterior"]["saiu"]), float(res["anterior"]["entrou"]))
    for k in ("despesa", "receita", "resultado", "categorias", "base"):
        assert r[k] == m[k], k                                            # idêntico ao `mes=` fechado
    assert "fatura" in r["escolha"]


def test_mes_corrente_inteiro_em_andamento_vira_o_mes_todo_pela_fatura():
    """Critério de `_meses_inteiros`: dia 1 até hoje ou depois, no mês corrente, conta como mês inteiro (a main)."""
    uid = usuario_pagante("plus")
    semeia(uid)
    r = _compare_periods(uid, _quatro(("2026-08-01", "2026-08-31"), ("2026-10-01", "2026-10-12")))   # agosto × outubro
    assert r["modo"] == "periodos_informados" and r["base"]["cartao"] == "fatura"
    assert r["atual"]["fim"] == "2026-10-31"                                              # o mês TODO, como o painel
    # 900 + 45 + 20 + 100 + 777 (dia 15) nos lançamentos e as faturas que fecham em outubro (70 + parcela de 100)
    assert (r["despesa"]["total_atual"], r["despesa"]["total_anterior"]) == (2012.0, 400.0)
    assert "ainda não terminou" in r["escolha"] and "não é previsão" in r["escolha"]


def test_janela_parcial_manda_as_duas_para_a_data_da_compra():
    uid = usuario_pagante("plus")
    semeia(uid)
    # 1-15/set x 1-15/out: parciais (hoje = 12/10, mas setembro já fechou antes do dia 15 ser "tudo")
    r = _compare_periods(uid, _quatro(("2026-09-01", "2026-09-15"), ("2026-10-01", "2026-10-15")))
    assert r["base"]["cartao"] == "data_da_compra" and "data da compra" in r["escolha"]
    assert (r["despesa"]["total_atual"], r["despesa"]["total_anterior"]) == (2841.0, 1450.0)   # 1842 + 999 de 15/10; 1300 + 150
    # uma janela inteira (setembro) e outra parcial (1-10/out): data da compra nas DUAS, sem misturar
    r = _compare_periods(uid, _quatro(("2026-09-01", "2026-09-30"), ("2026-10-01", "2026-10-10")))
    assert r["base"]["cartao"] == "data_da_compra" and "data da compra" in r["escolha"]
    assert (r["despesa"]["total_atual"], r["despesa"]["total_anterior"]) == (1065.0, 3320.0)   # setembro: 3100 + 150 + 70


# ── rodada 7: bordas do período livre ───────────────────────────────────────
JANELAS_LIVRES = (("2026-10-01", "2026-10-10"), ("2026-09-01", "2026-09-10"))   # fim < hoje (12/10): período livre


def test_periodo_livre_soma_grafias_da_mesma_categoria_na_mesma_janela_e_rotula_pela_maior():
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 10, 10, 3, "Alimentacao")
    _lanc(uid, "despesa", 30, 10, 4, "ALIMENTAÇÃO")
    _lanc(uid, "despesa", 5, 9, 3, "Base")
    r = _intervalo(uid, *JANELAS_LIVRES)
    por = _linhas(r)
    assert por["ALIMENTAÇÃO"]["total_atual"] == D("40.00") and "Alimentacao" not in por   # soma e rótulo de maior total


def test_compra_de_cartao_no_ultimo_dia_da_janela_livre_conta():
    uid = usuario_pagante("plus")
    cartao = db.create_card(uid, "Nubank", closing_day=20, due_day=27)
    db.add_credit_purchase(uid, cartao, 25, "Mercado", "último dia", date(2026, 10, 10))
    _lanc(uid, "despesa", 5, 9, 3, "Base")
    r = _intervalo(uid, *JANELAS_LIVRES)
    assert r["base"]["cartao_lancamentos"]["atual"] == 1 and r["despesa"]["total_atual"] == D("25.00")


def test_receita_e_despesa_no_ultimo_dia_da_janela_livre_entram():
    uid = usuario_pagante("plus")
    _lanc(uid, "receita", 70, 10, 10)
    _lanc(uid, "despesa", 9, 10, 10, "Mercado")
    _lanc(uid, "despesa", 5, 9, 3, "Base")
    r = _intervalo(uid, *JANELAS_LIVRES)
    assert (r["receita"]["total_atual"], r["despesa"]["total_atual"]) == (D("70.00"), D("9.00"))


# ── rodada 8: redirecionamento, rótulos, conexões ───────────────────────────
@pytest.mark.parametrize("fim_b", ["2026-10-12", "2026-10-31"])
def test_mes_corrente_x_anterior_por_datas_responde_como_mes(fim_b):
    """"outubro contra setembro" por 4 datas (hoje = 12/10) é `mes=2026-10`: campo a campo, o mês inteiro."""
    uid = usuario_pagante("plus")
    semeia(uid)
    r = _compare_periods(uid, _quatro(("2026-09-01", "2026-09-30"), ("2026-10-01", fim_b)))
    m = _compare_periods(uid, {"mes": "2026-10"})
    assert r == m and r["modo"] == "mes_aberto" and r["base"]["cartao"] == "fatura"
    assert (r["atual"]["inicio"], r["atual"]["fim"], r["anterior"]["fim"]) == ("2026-10-01", "2026-10-31", "2026-09-30")
    # não redireciona: setembro × agosto (fechados) e outubro × agosto (dois meses atrás) seguem por datas
    for a, b in ((("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")), (("2026-08-01", "2026-08-31"), ("2026-10-01", fim_b))):
        assert _compare_periods(uid, _quatro(a, b))["modo"] == "periodos_informados", (a, b)


def test_categoria_sem_nome_sai_outros_no_periodo_livre_e_sem_categoria_em_mes():
    """Diferença declarada: a consulta da main junta vazio com o "outros" real antes de agrupar."""
    uid = usuario_pagante("plus")
    _lanc(uid, "despesa", 10, 10, 3, None)
    _lanc(uid, "despesa", 5, 9, 3, None)
    assert [c["categoria"] for c in _intervalo(uid, *JANELAS_LIVRES)["categorias"]] == ["outros"]
    assert [c["categoria"] for c in cp.comparar_mes(uid, 2026, 10, AGORA)["categorias"]] == ["sem categoria"]


@pytest.mark.parametrize("nome", ["Alimentação", "alimentacao", "ALIMENTAÇÃO", "Açaí", "Educação", "São Paulo", "pão de queijo",
                                  "Lazer e Diversão", "Saúde", "ÁÉÍÓÚ ÂÊÔ ÃÕ Ç", "  espaços  ", "Cartão de crédito", "Transporte"])
def test_chave_em_python_casa_com_cat_key_sql(nome):
    """§0.7: `_chave` (período livre) e `cat_key_sql` (`mes=`) agrupam igual nos nomes realistas em PT-BR. Casos exóticos
    de Unicode (Ñ, Å, NFD, ligaturas) podem diferir: o SQL só traduz as vogais acentuadas e o ç do português."""
    from db.connection import cat_key_sql
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select {cat_key_sql('%s::text')} as k", (nome,))
        assert cp._chave(nome) == cur.fetchone()["k"]


def test_periodo_livre_nao_abre_conexao_dentro_de_outra(monkeypatch):
    """O pool é pequeno: as consultas da main (que abrem a própria conexão) rodam fora do `with get_conn()`."""
    uid = usuario_pagante("plus")
    semeia(uid)
    aberto = [0]
    real = cp.get_conn

    class Rastreia:
        def __init__(self):
            self.cm = real()

        def __enter__(self):
            aberto[0] += 1
            return self.cm.__enter__()

        def __exit__(self, *a):
            aberto[0] -= 1
            return self.cm.__exit__(*a)

    def fora(obj, nome):
        orig = getattr(obj, nome)

        def f(*a, **k):
            assert aberto[0] == 0, f"{nome} dentro de um get_conn()"
            return orig(*a, **k)
        monkeypatch.setattr(obj, nome, f)
    monkeypatch.setattr(cp, "get_conn", Rastreia)
    for nome in ("get_top_expense_categories", "get_summary_by_period"):
        fora(db, nome)
    fora(resumo_mes, "n_compras_cartao")
    assert _intervalo(uid, *JANELAS_LIVRES)["despesa"]["total_atual"] == D("1065.00")


def test_redirecionamento_para_mes_mantem_os_avisos_do_fim_ajustado():
    """A=01-30/09, B=01/10 a 31/12 (hoje = 12/10): `_limita_periodo` corta B em 31/10, redireciona para `mes=`, e o aviso fica."""
    uid = usuario_pagante("plus")
    semeia(uid)
    r = _compare_periods(uid, _quatro(("2026-09-01", "2026-09-30"), ("2026-10-01", "2026-12-31")))
    assert r["modo"] == "mes_aberto"
    assert r["atual"]["fim_ajustado"] is True and r["atual"]["aviso_fim"] and r["atual"]["pedido"]["end_date"] == "2026-12-31"
    assert "fim_ajustado" not in r["anterior"]
