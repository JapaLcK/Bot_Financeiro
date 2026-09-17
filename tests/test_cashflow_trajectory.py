"""Trajetória diária de saldo projetado (feature Pro): série, compromissos no
dia certo, vencidos e o limite de segurança.
"""
from datetime import date, timedelta

import pytest

from _cashflow_helpers import _mock_sources
from core.services.cashflow_forecast import forecast_with_trajectory


# ─── Trajetória diária (90 dias) + pior dia — feature Pro ──────────────────
#
# `forecast_with_trajectory` estende `project`/`forecast_horizons` com uma série dia a
# dia (em vez de só marcos 30/60/90) e o "pior dia" no caminho. Os dois consomem
# a MESMA fonte de eventos (`_cashflow_events`), que concentra todos os filtros.


# 3) forecast_with_trajectory — série básica

def test_daily_trajectory_serie_basica_90_dias_contiguos(monkeypatch):
    cf = _mock_sources(monkeypatch, saldo=1000.0)
    out = forecast_with_trajectory(1, 90)

    today = date.today()
    assert len(out["trajectory"]) == 90
    assert [item["date"] for item in out["trajectory"]] == [
        (today + timedelta(days=i)).isoformat() for i in range(1, 91)
    ]
    assert out["period"] == {
        "start": (today + timedelta(days=1)).isoformat(),
        "end": (today + timedelta(days=90)).isoformat(),
    }


# 4) forecast_with_trajectory — compromisso no dia certo
#
# Recorrente MENSAL num horizonte de 90 dias reapareceria ~3 vezes (a cada
# ~30 dias) — "em nenhum outro dia" só é verificável isolando UMA ocorrência.
# Por isso o horizonte aqui é menor que um mês (days=20): o suficiente pra
# garantir uma única ocorrência sem mudar o algoritmo testado.

def test_daily_trajectory_compromisso_no_dia_certo(monkeypatch):
    today = date.today()
    offset = 10
    due_date = today + timedelta(days=offset)
    expense = {
        "is_active": True, "payment_mode": "autopay", "frequency": "monthly",
        "due_day": due_date.day, "due_month": None, "start_date": None,
        "amount": 300.0, "name": "Aluguel",
    }
    cf = _mock_sources(monkeypatch, saldo=1000.0, expenses=[expense])
    out = forecast_with_trajectory(1, days=20)

    hit = out["trajectory"][offset - 1]
    assert hit["date"] == due_date.isoformat()
    assert hit["compromissos"] == [{"tipo": "gasto_fixo", "nome": "Aluguel", "valor": 300.0}]
    for i, item in enumerate(out["trajectory"]):
        if i != offset - 1:
            assert item["compromissos"] == []


# 5) forecast_with_trajectory — boleto vencido não entra na série nem nas causas, pesa no
# saldo de partida e é listado em `vencidos` (sem isso ele sumia da resposta)

def test_daily_trajectory_boleto_vencido_pesa_no_saldo_e_vai_para_vencidos(monkeypatch):
    today = date.today()
    ontem = today - timedelta(days=1)
    bills = [{"status": "pending", "due_date": today, "amount": 200.0, "name": "Água"},
             {"status": "pending", "due_date": ontem, "amount": 30.0, "name": "Telefone"},
             {"status": "pending", "due_date": ontem, "amount": 20.0, "name": "Gás"},
             {"status": "pending", "due_date": today + timedelta(days=3), "amount": 100.0, "name": "Luz"}]
    cards = [{"due_date": today - timedelta(days=30), "remaining": 400.0, "card_name": "Nubank"}]
    cf = _mock_sources(monkeypatch, saldo=1000.0, bills=bills, card_bills=cards)
    out = forecast_with_trajectory(1, days=90)

    assert out["trajectory"][0]["saldo_projetado"] == 350.0
    # Ordem de data, mais antigo primeiro, com tipos misturados: a fatura atrasada
    # há 30 dias vem antes dos boletos (a fonte a entrega por último). Empate de
    # data mantém a ordem da fonte (Telefone antes de Gás).
    assert out["vencidos"] == [
        {"date": (today - timedelta(days=30)).isoformat(), "tipo": "fatura_cartao", "nome": "Nubank", "valor": 400.0},
        {"date": ontem.isoformat(), "tipo": "boleto", "nome": "Telefone", "valor": 30.0},
        {"date": ontem.isoformat(), "tipo": "boleto", "nome": "Gás", "valor": 20.0},
    ]
    # Vence hoje não é vencido (`due_date < hoje`, db/bills.py): lista própria, mesmo peso no saldo.
    assert out["vencem_hoje"] == [{"date": today.isoformat(), "tipo": "boleto", "nome": "Água", "valor": 200.0}]
    nomes_na_serie = {c["nome"] for item in out["trajectory"] for c in item["compromissos"]}
    assert nomes_na_serie == {"Luz"}
    assert [c["nome"] for c in out["worst_day"]["causas"]] == ["Luz"]


# Par negativo/positivo do "pior dia + limite de segurança" (§3 CLAUDE.md raiz)

def test_daily_trajectory_sem_aperto_quando_tudo_acima_do_limite(monkeypatch):
    """Positivo: saldo alto e gastos pequenos — 90 dias inteiros acima do
    threshold. Prova que o caminho legítimo (sem aperto) não acende o alerta."""
    expense = {
        "is_active": True, "payment_mode": "autopay", "frequency": "monthly",
        "due_day": (date.today() + timedelta(days=5)).day, "due_month": None,
        "start_date": None, "amount": 50.0, "name": "Internet",
    }
    cf = _mock_sources(monkeypatch, saldo=10_000.0, expenses=[expense])
    out = forecast_with_trajectory(1, days=90, threshold=0.0)

    assert all(not item["abaixo_do_limite"] for item in out["trajectory"])
    assert not out["worst_day"]["abaixo_do_limite"]


def test_daily_trajectory_worst_day_e_o_minimo_nao_o_primeiro_nem_o_ultimo(monkeypatch):
    """Negativo: o menor saldo cai no MEIO dos 90 dias (dia 45), nada relevante
    nos dias 1 e 90. `worst_day` tem que ser o mínimo, não um extremo por
    default de implementação (ex.: pegar o primeiro ou o último item)."""
    today = date.today()
    pior_dia = today + timedelta(days=45)
    bill = {"status": "pending", "due_date": pior_dia, "amount": 5000.0, "name": "IPVA"}
    cf = _mock_sources(monkeypatch, saldo=6000.0, bills=[bill])
    out = forecast_with_trajectory(1, days=90)

    assert out["worst_day"]["date"] == pior_dia.isoformat()


def test_daily_trajectory_threshold_igual_ao_saldo_nao_e_aperto(monkeypatch):
    """Fronteira: saldo_projetado == threshold exatamente não é aperto (< estrito)."""
    cf = _mock_sources(monkeypatch, saldo=500.0)
    out = forecast_with_trajectory(1, days=5, threshold=500.0)

    assert all(not item["abaixo_do_limite"] for item in out["trajectory"])


def test_daily_trajectory_threshold_marca_aperto_com_saldo_positivo(monkeypatch):
    """O limite de segurança é o que explica o aperto: saldo cai de 1000 pra 300
    no dia 10 e nunca fica negativo. Com limite 500, aperto a partir do dia 10;
    com limite 0, nenhum dia. (Sem o par, `abaixo_do_limite` fixo em False ou
    comparado com 0 passaria.)"""
    today = date.today()
    bill = {"status": "pending", "due_date": today + timedelta(days=10), "amount": 700.0, "name": "Aluguel"}
    cf = _mock_sources(monkeypatch, saldo=1000.0, bills=[bill])

    com_limite = forecast_with_trajectory(1, days=90, threshold=500.0)["trajectory"]
    assert [item["abaixo_do_limite"] for item in com_limite] == [False] * 9 + [True] * 81

    sem_limite = forecast_with_trajectory(1, days=90, threshold=0.0)["trajectory"]
    assert not any(item["abaixo_do_limite"] for item in sem_limite)


def test_daily_trajectory_threshold_com_fracao_de_centavo_segue_o_eco(monkeypatch):
    """`threshold=500.004` ecoa 500.0; um dia em 500.00 é "igual ao limite" na
    resposta, logo não pode ser aperto."""
    cf = _mock_sources(monkeypatch, saldo=500.0)
    out = forecast_with_trajectory(1, days=5, threshold=500.004)

    assert out["threshold"] == 500.0
    assert out["trajectory"][0]["saldo_projetado"] == 500.0
    assert not any(item["abaixo_do_limite"] for item in out["trajectory"])


@pytest.mark.parametrize("saldo, valor, limite", [
    pytest.param(1.0, 0.9, 0.1, id="centavos_com_ruido_de_float"),
    pytest.param(1000.0, 500.004, 500.0, id="fracao_de_centavo"),
])
def test_daily_trajectory_saldo_exibido_igual_ao_limite_nao_e_aperto(monkeypatch, saldo, valor, limite):
    """R$ 1,00 − 0,90 soma 0,09999999999999998 em float e R$ 1000 − 500,004 soma
    499,996: os dois mostram o limite, então não são aperto. Comparar a soma exata
    marcaria os dois."""
    today = date.today()
    _mock_sources(monkeypatch, saldo=saldo, bills=[
        {"status": "pending", "due_date": today + timedelta(days=3), "amount": valor, "name": "Conta"}])
    out = forecast_with_trajectory(1, days=5, threshold=limite)

    assert out["trajectory"][2]["saldo_projetado"] == limite
    assert not any(item["abaixo_do_limite"] for item in out["trajectory"])
