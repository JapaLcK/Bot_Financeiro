"""Simulador de decisão financeira (Pro): `simulate` (comparação de cenários
sobre a mesma leitura da previsão de saldo), validação e isolamento por usuário.
A parte pura — parcelas, calendário, eventos e contrato — está em
test_decision_simulator_calculo.py."""
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from _cashflow_helpers import _mock_sources, fontes_que_mudam
from core.services.cashflow_forecast import forecast_with_trajectory
from core.services.decision_simulator import DIAS, Simulacao, _add_months, simulate

HOJE = date.today()


def _sim(*cenarios, reserva=0.0):
    return Simulacao.model_validate({"reserva_minima": reserva, "cenarios": list(cenarios)})


# ─── simulate ────────────────────────────────────────────────────────────────

def test_entrada_maior_que_o_saldo_deixa_o_pior_dia_negativo_sem_erro(monkeypatch):
    _mock_sources(monkeypatch, saldo=1000.0)
    out = simulate(1, _sim({"nome": "À vista", "preco": 5000.0}, reserva=200.0))
    r = out["cenarios"][0]["resumo"]
    assert r["pior_dia"]["saldo"] == -4000.0
    assert r["saldo_final_90"] == -4000.0
    assert r["delta_vs_atual"] == -5000.0
    assert r["dias_abaixo_da_reserva"] == 90
    assert out["atual"]["dias_abaixo_da_reserva"] == 0 and out["atual"]["primeiro_dia_abaixo"] is None


def test_reserva_vale_para_os_cenarios_e_primeiro_dia_e_o_primeiro(monkeypatch):
    """Cenário fica positivo mas abaixo da reserva: só conta se a reserva for o
    limite da trajetória DO CENÁRIO."""
    _mock_sources(monkeypatch, saldo=1000.0)
    out = simulate(1, _sim({"nome": "TV", "preco": 500.0}, reserva=800.0))
    r = out["cenarios"][0]["resumo"]
    assert r["saldo_final_90"] == 500.0
    assert r["dias_abaixo_da_reserva"] == 90
    assert r["primeiro_dia_abaixo"] == (date.today() + timedelta(days=1)).isoformat()


def test_48x_hoje_conta_parcelas_dentro_e_fora_dos_90_dias(monkeypatch):
    _mock_sources(monkeypatch, saldo=50000.0)
    out = simulate(1, _sim({"nome": "Carro", "preco": 180000.0, "entrada": 36000.0,
                            "parcelas": 48, "juros_mensal_pct": 1.49}))
    today = date.today()
    # 2 ou 3 parcelas conforme o dia de hoje (17/09 + 3 meses = 91 dias)
    dentro = sum(1 for k in range(1, 49) if _add_months(today, k) <= today + timedelta(days=DIAS))
    fora = out["cenarios"][0]["contrato"]["parcelas_fora_do_horizonte"]
    assert fora["quantidade"] == 48 - dentro
    assert fora["valor"] == round((47 - dentro) * 4220.98 + 4220.78, 2)
    r = out["cenarios"][0]["resumo"]
    assert r["saldo_final_90"] == round(50000.0 - 36000.0 - dentro * 4220.98, 2)


def test_atual_bate_com_a_previsao_de_saldo_nas_mesmas_fontes(monkeypatch):
    today = date.today()
    _mock_sources(
        monkeypatch, saldo=800.0,
        incomes=[{"is_active": True, "amount": 3000.0, "name": "Salário", "pay_day": 5}],
        expenses=[{"is_active": True, "amount": 1200.0, "name": "Aluguel", "due_day": 10}],
        bills=[{"status": "pending", "due_date": today + timedelta(days=12), "amount": 950.0, "name": "IPVA"}],
    )
    out = simulate(1, _sim({"nome": "TV", "preco": 2000.0, "parcelas": 10}, reserva=500.0))
    fc = forecast_with_trajectory(1, 90, 500.0)
    assert out["atual"]["saldo_final_90"] == fc["trajectory"][-1]["saldo_projetado"]
    assert out["atual"]["pior_dia"] == {"date": fc["worst_day"]["date"], "saldo": fc["worst_day"]["saldo_projetado"]}
    assert out["atual"]["dias_abaixo_da_reserva"] == sum(it["abaixo_do_limite"] for it in fc["trajectory"])


def test_menor_entrada_melhora_o_saldo_de_90_dias_e_paga_mais_juros(monkeypatch):
    _mock_sources(monkeypatch, saldo=100000.0)
    base = {"preco": 180000.0, "parcelas": 48, "juros_mensal_pct": 1.49}
    out = simulate(1, _sim({**base, "nome": "Entrada 36k", "entrada": 36000.0},
                           {**base, "nome": "Entrada 72k", "entrada": 72000.0}))
    menor, maior = out["cenarios"]
    assert menor["resumo"]["saldo_final_90"] > maior["resumo"]["saldo_final_90"]
    assert menor["contrato"]["juros_totais"] > maior["contrato"]["juros_totais"]
    # "Sem apontar vencedor" NÃO se mede varrendo as chaves do próprio código: a
    # resposta é uma LISTA e o contrato é a ordem pedida, não um ranking. Aqui: o
    # cenário de saldo pior vem primeiro porque foi pedido primeiro, e nenhuma
    # chave nova classifica os cenários (o texto que proíbe recomendar mora na
    # `note` da tool — `test_note_da_tool_...` em test_simulator_rotas.py).
    assert [c["nome"] for c in out["cenarios"]] == ["Entrada 36k", "Entrada 72k"]
    assert set(out["cenarios"][0]) == {"nome", "resumo", "contrato"}
    assert set(out["cenarios"][0]["resumo"]) == {
        "saldo_final_90", "pior_dia", "dias_abaixo_da_reserva", "primeiro_dia_abaixo",
        "delta_vs_atual", "compra_fora_da_janela"}


def test_uma_leitura_das_fontes_por_simulacao(monkeypatch):
    leituras = fontes_que_mudam(monkeypatch)
    out = simulate(1, _sim({"nome": "A", "preco": 100.0},
                           {"nome": "B", "preco": 200.0}))
    assert out["atual"]["saldo_final_90"] == 300.0
    assert [c["resumo"]["saldo_final_90"] for c in out["cenarios"]] == [200.0, 100.0]
    assert set(leituras.values()) == {1}


def test_cada_fonte_e_lida_com_o_user_id_pedido_e_nao_com_um_id_cravado(monkeypatch):
    """Isolamento por usuário (§0 do CLAUDE.md) medido, não conferido no olho.

    O mock das fontes descarta o `uid` por construção, então um `_starting_balance(1)`
    ou um `_cashflow_events(1, ...)` cravado dentro de `simulate` devolveria os dados
    de outra conta e passaria verde. `uids=` anota quem cada fonte recebeu.
    """
    uids: dict[str, list] = {}
    _mock_sources(monkeypatch, saldo=4321.0, uids=uids,
                  bills=[{"status": "pending", "due_date": date.today() + timedelta(days=3),
                          "amount": 21.0, "name": "Luz"}])
    out = simulate(7, _sim({"nome": "TV", "preco": 100.0}))
    # as 5 fontes de `_starting_balance` + `_cashflow_events` foram lidas...
    assert set(uids) == {"get_balance", "get_consolidated_balance", "list_recurring_expenses",
                         "list_recurring_incomes", "list_bills", "_open_card_bills_detail"}
    # ...todas com o 7, e nenhuma com outro id
    assert {u for lidos in uids.values() for u in lidos} == {7}
    # controle positivo: o número da conta do 7 é o que sai na resposta
    assert out["atual"]["saldo_final_90"] == 4300.0
    assert out["cenarios"][0]["resumo"]["saldo_final_90"] == 4200.0


# ─── validação (a mesma da rota e da tool) ───────────────────────────────────

@pytest.mark.parametrize("campo,valor", [
    ("preco", float("nan")), ("preco", float("inf")), ("entrada", float("nan")),
    ("juros_mensal_pct", float("nan")), ("custos_unicos", float("inf")),
    ("despesa_mensal_nova", float("nan")),
    ("preco", 0), ("entrada", 1000.01), ("parcelas", 0), ("parcelas", 421), ("juros_mensal_pct", 20.01),
    ("nome", "x" * 61), ("data_compra", (date.today() - timedelta(days=1)).isoformat()),
    ("preco", True), ("preco", "180000"), ("entrada", "10"), ("parcelas", True), ("juros_mensal", 1.5),
    ("parcelas", 1.49), ("parcelas", "48"), ("juros_mensal_pct", "1.49"), ("juros_mensal_pct", True),
])
def test_cenario_invalido_e_recusado(campo, valor):
    valido = {"nome": "Ok", "preco": 1000.0, "entrada": 100.0, "parcelas": 12, "juros_mensal_pct": 1.0}
    _sim(valido)  # controle positivo: o mesmo corpo sem o campo ruim passa
    _sim({**valido, "preco": 180000, "entrada": 36000})  # int JSON continua valendo em campo float
    with pytest.raises(ValidationError):
        _sim({**valido, campo: valor})


def test_reserva_nao_finita_e_numero_de_cenarios_sao_recusados():
    ok = {"nome": "Ok", "preco": 10.0}
    _sim(ok, {**ok, "nome": "Ok2"}, {**ok, "nome": "Ok3"})
    for corpo in ({"reserva_minima": float("nan"), "cenarios": [ok]}, {"cenarios": []},
                  {"cenarios": [{**ok, "nome": f"n{i}"} for i in range(4)]}, {"reserva_minima": -1, "cenarios": [ok]},
                  {"reserva": 10, "cenarios": [ok]}, {"reserva_minima": True, "cenarios": [ok]}):
        with pytest.raises(ValidationError):
            Simulacao.model_validate(corpo)


def test_financiado_pequeno_demais_para_as_parcelas_e_recusado():
    """Sem esta recusa, a última parcela sairia negativa (resíduo maior que a parcela)."""
    with pytest.raises(ValidationError):
        _sim({"nome": "x", "preco": 0.05, "parcelas": 7})
    _sim({"nome": "x", "preco": 0.07, "parcelas": 7})


def test_parcelas_float_inteiro_vale_e_null_em_opcional_vale_o_padrao():
    assert _sim({"nome": "x", "preco": 1000.0, "parcelas": 48.0}).cenarios[0].parcelas == 48
    sim = Simulacao.model_validate({"reserva_minima": None, "cenarios": [
        {"nome": "x", "preco": 1000.0, "entrada": None, "parcelas": None, "juros_mensal_pct": None,
         "data_compra": None, "custos_unicos": None, "despesa_mensal_nova": None}]})
    padrao = _sim({"nome": "x", "preco": 1000.0})
    assert sim == padrao
    with pytest.raises(ValidationError):  # obrigatório em null continua recusado
        _sim({"nome": "x", "preco": None})


# ─── à vista, contradições e marcadores ──────────────────────────────────────


@pytest.mark.parametrize("cen,motivo", [
    ({"nome": "x", "preco": 1000.0, "entrada": 400.0}, "entrada parcial sem parcelas"),
    ({"nome": "x", "preco": 1000.0, "entrada": 1000.0, "parcelas": 12}, "nada a financiar"),
])
def test_contradicao_entre_entrada_e_parcelas_e_recusada(cen, motivo):
    _sim({**cen, "entrada": 0.0, "parcelas": 12})  # controle positivo
    with pytest.raises(ValidationError, match=motivo):
        _sim(cen)


def test_null_em_chave_desconhecida_continua_recusado():
    _sim({"nome": "x", "preco": 10.0, "entrada": None})  # conhecida em null cai no padrão
    with pytest.raises(ValidationError):
        _sim({"nome": "x", "preco": 10.0, "juros_mensal": None})
    with pytest.raises(ValidationError):
        Simulacao.model_validate({"reserva": None, "cenarios": [{"nome": "x", "preco": 10.0}]})


def test_cenarios_com_o_mesmo_nome_sao_recusados():
    ok = {"nome": "A", "preco": 10.0}
    _sim(ok, {**ok, "nome": "B"})  # controle positivo
    with pytest.raises(ValidationError, match="mesmo nome"):
        _sim(ok, dict(ok))


def test_compra_depois_da_janela_vem_marcada(monkeypatch):
    _mock_sources(monkeypatch, saldo=1000.0)
    fora = (HOJE + timedelta(days=DIAS + 5)).isoformat()
    out = simulate(1, _sim({"nome": "Longe", "preco": 500.0, "data_compra": fora},
                           {"nome": "Agora", "preco": 500.0}))
    longe, agora = (c["resumo"] for c in out["cenarios"])
    assert longe["compra_fora_da_janela"] is True and longe["delta_vs_atual"] == 0.0
    assert agora["compra_fora_da_janela"] is False and agora["delta_vs_atual"] == -500.0


def test_uma_parcela_com_juros_e_recusada_e_sem_juros_passa():
    """`parcelas: 1` é à vista, e à vista não tem juros: aceitar jogaria a taxa fora
    (medido: preço 50.000 a 5 %/mês devolvia total_pago 50.000, juros 0)."""
    _sim({"nome": "x", "preco": 50000.0, "parcelas": 1, "juros_mensal_pct": 0.0})
    for cen in ({"nome": "x", "preco": 50000.0, "parcelas": 1, "juros_mensal_pct": 5.0},
                {"nome": "x", "preco": 50000.0, "juros_mensal_pct": 5.0}):  # sem parcelas = à vista
        with pytest.raises(ValidationError, match="quantas parcelas"):
            _sim(cen)


@pytest.mark.parametrize("segundo", ["a", "A ", " a  "])
def test_nome_repetido_e_pego_com_caixa_e_espaco_diferentes(segundo):
    _sim({"nome": "A", "preco": 10.0}, {"nome": "B", "preco": 10.0})  # controle positivo
    with pytest.raises(ValidationError, match="mesmo nome"):
        _sim({"nome": "A", "preco": 10.0}, {"nome": segundo, "preco": 10.0})


def test_juros_sem_parcelas_orienta_sem_oferecer_zerar_a_taxa():
    with pytest.raises(ValidationError) as exc:
        Simulacao.model_validate({"cenarios": [{"nome": "x", "preco": 50000.0, "juros_mensal_pct": 5.0}]})
    msg = str(exc.value)
    assert "quantas parcelas" in msg and "acréscimo ao preco" in msg
    assert "juros 0" not in msg
