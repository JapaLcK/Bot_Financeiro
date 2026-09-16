"""Previsão de saldo 30/60/90 dias (feature Pro).

`forecast_horizons` não tem lógica de projeção própria — só reusa `project()`
em hoje+30/60/90. Aqui garantimos o contrato (chaves + datas-alvo) sem tocar no
DB, mockando `project`.
"""
from datetime import date, timedelta


def test_forecast_horizons_reusa_project_nos_tres_horizontes(monkeypatch):
    import core.services.cashflow as cf

    alvos: list[date] = []

    def fake_project(uid, target, extra=0.0):
        alvos.append(target)
        return {"target": target.isoformat(), "projetado": 100.0, "tranquilo": True,
                "balance_source": "consolidated", "of_bank_count": 2,
                "banks_excluded": False}

    monkeypatch.setattr(cf, "project", fake_project)

    out = cf.forecast_horizons(42)

    assert set(out["horizons"].keys()) == {"30", "60", "90"}
    today = date.today()
    assert alvos == [today + timedelta(days=30),
                     today + timedelta(days=60),
                     today + timedelta(days=90)]
    assert out["today"] == today.isoformat()
    assert out["horizons"]["30"]["projetado"] == 100.0
    # origem do saldo sobe pro topo pro dashboard renderizar o aviso
    assert out["balance_source"] == "consolidated"
    assert out["of_bank_count"] == 2
    assert out["banks_excluded"] is False


def test_forecast_horizons_default_balance_source_quando_project_omite(monkeypatch):
    import core.services.cashflow as cf
    monkeypatch.setattr(cf, "project", lambda uid, target, extra=0.0: {"projetado": 0.0})
    out = cf.forecast_horizons(1, horizons=(30,))
    assert out["balance_source"] == "manual"
    assert out["banks_excluded"] is False


def test_forecast_horizons_aceita_horizontes_customizados(monkeypatch):
    import core.services.cashflow as cf
    monkeypatch.setattr(cf, "project", lambda uid, target, extra=0.0: {"t": target.isoformat()})
    out = cf.forecast_horizons(1, horizons=(7, 15))
    assert set(out["horizons"].keys()) == {"7", "15"}


def test_project_marca_unavailable_quando_consolidado_falha(monkeypatch):
    # P1: se a consulta ao saldo consolidado (OF/gate) falha, NÃO dá pra afirmar
    # que a carteira manual é o saldo completo. A projeção não pode devolver um
    # número aparentemente confiável sem aviso — marca balance_source
    # "unavailable" pro dashboard sinalizar a incerteza.
    import core.services.cashflow as cf
    import db, db.accounts, db.recurring, db.recurring_income, db.bills

    monkeypatch.setattr(db.accounts, "get_balance", lambda uid: 100.0)
    monkeypatch.setattr(db.recurring, "list_recurring_expenses", lambda uid: [])
    monkeypatch.setattr(db.recurring_income, "list_recurring_incomes", lambda uid: [])
    monkeypatch.setattr(db.bills, "list_bills",
                        lambda uid, include_paid=False, limit=1000: [])
    monkeypatch.setattr(cf, "_open_card_bills_detail", lambda uid, until: [])

    def boom(uid):
        raise RuntimeError("Open Finance indisponível")
    monkeypatch.setattr(db, "get_consolidated_balance", boom, raising=False)

    out = cf.project(1, date.today() + timedelta(days=30))

    assert out["balance_source"] == "unavailable"
    # sem of_bank_count confiável, banks_excluded não dispara — o aviso vem da
    # origem "unavailable"; o saldo de partida segue a carteira manual.
    assert out["banks_excluded"] is False
    assert out["saldo_atual"] == 100.0


def test_card_bill_due_date_canonica_rollover_clamp_e_mesmo_dia():
    # A projeção usa a regra canônica de db/cards.py (via _open_card_bills_detail),
    # não uma cópia própria — garante que não voltem a divergir.
    from db.cards import card_bill_due_date
    # due_day < fechamento → vencimento rola pro mês seguinte
    assert card_bill_due_date(date(2026, 7, 28), 28, 8) == date(2026, 8, 8)
    # due_day > fechamento → mesmo mês
    assert card_bill_due_date(date(2026, 7, 5), 5, 30) == date(2026, 7, 30)
    assert card_bill_due_date(date(2026, 1, 20), 20, 31) == date(2026, 1, 31)
    # clampa dia inexistente (fev) e vira o ano
    assert card_bill_due_date(date(2026, 2, 5), 5, 31) == date(2026, 2, 28)
    assert card_bill_due_date(date(2026, 12, 28), 28, 8) == date(2027, 1, 8)
    # caso da divergência: due_day == closing_day → MESMO mês (não rola)
    assert card_bill_due_date(date(2026, 7, 10), 10, 10) == date(2026, 7, 10)


# ─── Trajetória diária (90 dias) + pior dia — feature Pro ──────────────────
#
# `daily_trajectory` estende `project`/`forecast_horizons` com uma série dia a
# dia (em vez de só marcos 30/60/90) e o "pior dia" no caminho. Os dois consomem
# a MESMA fonte de eventos (`_cashflow_events`), que concentra todos os filtros.


def _mock_sources(monkeypatch, *, saldo=0.0, incomes=(), expenses=(), bills=(), card_bills=()):
    """Mocka as fontes de `project`/`daily_trajectory` na camada de db.*, sem
    tocar banco real — mesmo padrão de `test_project_marca_unavailable_...`."""
    import core.services.cashflow as cf
    import db, db.accounts, db.recurring, db.recurring_income, db.bills

    monkeypatch.setattr(db.accounts, "get_balance", lambda uid: saldo)
    monkeypatch.setattr(db.recurring, "list_recurring_expenses", lambda uid: list(expenses))
    monkeypatch.setattr(db.recurring_income, "list_recurring_incomes", lambda uid: list(incomes))
    monkeypatch.setattr(db.bills, "list_bills",
                        lambda uid, include_paid=False, limit=1000: list(bills))
    # Fiel ao SQL real: só faturas com vencimento até `until`.
    monkeypatch.setattr(cf, "_open_card_bills_detail",
                        lambda uid, until: [dict(c) for c in card_bills if c["due_date"] <= until])
    # Sem Open Finance conectado nestes cenários: saldo fica na carteira manual.
    monkeypatch.setattr(db, "get_consolidated_balance",
                        lambda uid: {"of_bank_count": 0, "consolidated": 0.0}, raising=False)
    return cf


# 1) Datas de recorrente e regras de valor da fonte de eventos

def test_recurring_occurrence_dates_mensal_dentro_da_janela():
    import core.services.cashflow as cf
    dates = cf._recurring_occurrence_dates(15, "monthly", None, None,
                                            date(2026, 1, 1), date(2026, 3, 31))
    assert dates == [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)]


def test_recurring_occurrence_dates_anual_filtra_por_mes():
    import core.services.cashflow as cf
    dates = cf._recurring_occurrence_dates(10, "annual", 6, None,
                                            date(2026, 1, 1), date(2027, 12, 31))
    assert dates == [date(2026, 6, 10), date(2027, 6, 10)]


def test_recurring_occurrence_dates_respeita_start_date():
    import core.services.cashflow as cf
    dates = cf._recurring_occurrence_dates(5, "monthly", None, date(2026, 2, 1),
                                            date(2026, 1, 1), date(2026, 3, 31))
    assert dates == [date(2026, 2, 5), date(2026, 3, 5)]


def test_recurring_occurrence_dates_clampa_dia_inexistente():
    import core.services.cashflow as cf
    # dia 31 não existe em fevereiro (2026 não é bissexto) — clampa pra 28
    dates = cf._recurring_occurrence_dates(31, "monthly", None, None,
                                            date(2026, 1, 31), date(2026, 3, 1))
    assert dates == [date(2026, 2, 28)]


def test_cashflow_events_recorrente_nao_positivo_nao_gera_evento_boleto_gera(monkeypatch):
    # Regra herdada de `project`: receita/gasto fixo com valor <= 0 não entra;
    # boleto pendente entra com qualquer valor (0 e negativo inclusive).
    cf = _mock_sources(
        monkeypatch,
        incomes=[{"is_active": True, "pay_day": 15, "frequency": "monthly", "amount": 0.0, "name": "R0"},
                 {"is_active": True, "pay_day": 15, "frequency": "monthly", "amount": -50.0, "name": "Rneg"}],
        expenses=[{"is_active": True, "payment_mode": "autopay", "frequency": "monthly",
                   "due_day": 15, "amount": 0.0, "name": "G0"}],
        bills=[{"status": "pending", "due_date": date(2026, 2, 1), "amount": 0.0, "name": "B0"},
               {"status": "pending", "due_date": date(2026, 2, 2), "amount": -10.0, "name": "Bneg"}],
    )
    events = cf._cashflow_events(1, date(2026, 1, 1), date(2026, 3, 31))
    assert [(d, tipo, nome, valor) for d, tipo, nome, valor in events] == [
        (date(2026, 2, 1), "boleto", "B0", -0.0),
        (date(2026, 2, 2), "boleto", "Bneg", 10.0),
    ]


def test_project_n_boletos_conta_todo_pendente_ate_a_data(monkeypatch):
    """Regra de HEAD (`git show HEAD:core/services/cashflow.py`): conta todo boleto
    `pending` com vencimento até a data, qualquer que seja o valor (0, negativo,
    ausente); vencido conta; pago, sem data ou depois da data, não."""
    today = date.today()
    alvo = today + timedelta(days=30)
    cf = _mock_sources(monkeypatch, saldo=0.0, bills=[
        {"status": "pending", "due_date": today + timedelta(days=5), "amount": 100.0, "name": "A"},
        {"status": "pending", "due_date": today + timedelta(days=6), "amount": 0.0, "name": "Zero"},
        {"status": "pending", "due_date": today + timedelta(days=7), "amount": -10.0, "name": "Negativo"},
        {"status": "pending", "due_date": today + timedelta(days=8), "amount": None, "name": "Sem valor"},
        {"status": "pending", "due_date": today - timedelta(days=3), "amount": 40.0, "name": "Vencido"},
        {"status": "paid", "due_date": today + timedelta(days=9), "amount": 70.0, "name": "Pago"},
        {"status": "pending", "due_date": None, "amount": 80.0, "name": "Sem data"},
        {"status": "pending", "due_date": alvo + timedelta(days=1), "amount": 90.0, "name": "Depois"},
    ])
    out = cf.project(1, alvo)

    assert out["n_boletos"] == 5
    assert out["boletos_ate"] == 130.0


def test_project_soma_as_ocorrencias_da_fonte_de_eventos(monkeypatch):
    today = date.today()
    cf = _mock_sources(
        monkeypatch, saldo=0.0,
        incomes=[{"is_active": True, "pay_day": (today + timedelta(days=3)).day,
                  "frequency": "monthly", "amount": 100.0, "name": "Freela"}],
    )
    # dias +3, ~+33, ~+63: três ocorrências em 90 dias, qualquer que seja o mês
    out = cf.project(1, today + timedelta(days=90))
    assert out["receitas_previstas"] == 300.0
    assert out["projetado"] == 300.0


# 2) _open_card_bills_detail inclui card_name (DB real)

def test_open_card_bills_detail_inclui_card_name(user_id):
    import db
    import core.services.cashflow as cf

    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(user_id, card_id)
    db.add_credit_purchase(user_id, card_id, 250.0, "outros", "compra", date.today())

    until = date.today() + timedelta(days=90)
    detail = cf._open_card_bills_detail(user_id, until)
    assert len(detail) == 1
    assert detail[0]["card_name"] == "Nubank"
    assert detail[0]["remaining"] == 250.0


# 3) daily_trajectory — série básica

def test_daily_trajectory_serie_basica_90_dias_contiguos(monkeypatch):
    cf = _mock_sources(monkeypatch, saldo=1000.0)
    out = cf.daily_trajectory(1, 90)

    today = date.today()
    assert len(out["trajectory"]) == 90
    assert [item["date"] for item in out["trajectory"]] == [
        (today + timedelta(days=i)).isoformat() for i in range(1, 91)
    ]
    assert out["period"] == {
        "start": (today + timedelta(days=1)).isoformat(),
        "end": (today + timedelta(days=90)).isoformat(),
    }


# 4) daily_trajectory — compromisso no dia certo
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
    out = cf.daily_trajectory(1, days=20)

    hit = out["trajectory"][offset - 1]
    assert hit["date"] == due_date.isoformat()
    assert hit["compromissos"] == [{"tipo": "gasto_fixo", "nome": "Aluguel", "valor": 300.0}]
    for i, item in enumerate(out["trajectory"]):
        if i != offset - 1:
            assert item["compromissos"] == []


# 5) daily_trajectory — boleto vencido não entra na série nem nas causas, pesa no
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
    out = cf.daily_trajectory(1, days=90)

    assert out["trajectory"][0]["saldo_projetado"] == 350.0
    # Ordem de data, mais antigo primeiro, com tipos misturados: a fatura atrasada
    # há 30 dias vem antes dos boletos (a fonte a entrega por último). Empate de
    # data mantém a ordem da fonte (Telefone antes de Gás).
    assert out["vencidos"] == [
        {"date": (today - timedelta(days=30)).isoformat(), "tipo": "fatura_cartao", "nome": "Nubank", "valor": 400.0},
        {"date": ontem.isoformat(), "tipo": "boleto", "nome": "Telefone", "valor": 30.0},
        {"date": ontem.isoformat(), "tipo": "boleto", "nome": "Gás", "valor": 20.0},
        {"date": today.isoformat(), "tipo": "boleto", "nome": "Água", "valor": 200.0},
    ]
    nomes_na_serie = {c["nome"] for item in out["trajectory"] for c in item["compromissos"]}
    assert nomes_na_serie == {"Luz"}
    assert [c["nome"] for c in out["worst_day"]["causas"]] == ["Luz"]


# 6) Paridade com project() nos 3 marcos (30/60/90): os dois blocos do mesmo
# payload saem da mesma fonte de eventos. Há itens ENTRE os marcos (só um marco
# posterior os enxerga) e itens que os filtros da fonte têm de excluir — sem a
# checagem de nomes, um filtro removido passaria aqui, porque somaria nos dois.

def test_daily_trajectory_bate_com_project_nos_tres_marcos(monkeypatch):
    today = date.today()
    d50 = today + timedelta(days=50)
    incomes = [
        {"is_active": True, "pay_day": 5, "frequency": "monthly", "amount": 1000.0, "name": "Salário"},
        {"is_active": True, "pay_day": d50.day, "frequency": "annual", "pay_month": d50.month,
         "amount": 800.0, "name": "13º"},
        {"is_active": False, "pay_day": 6, "frequency": "monthly", "amount": 999.0, "name": "Receita inativa"},
    ]
    expenses = [
        {"is_active": True, "payment_mode": "autopay", "frequency": "monthly",
         "due_day": 20, "amount": 400.0, "name": "Aluguel"},
        {"is_active": False, "payment_mode": "autopay", "frequency": "monthly",
         "due_day": 7, "amount": 111.0, "name": "Gasto inativo"},
        {"is_active": True, "payment_mode": "manual", "frequency": "monthly",
         "due_day": 8, "amount": 222.0, "name": "Gasto manual"},
        {"is_active": True, "payment_mode": "autopay", "frequency": "weekly",
         "due_day": 9, "amount": 333.0, "name": "Gasto semanal"},
    ]
    bills = [
        {"status": "pending", "due_date": today + timedelta(days=45), "amount": 150.0, "name": "Água"},
        {"status": "pending", "due_date": today - timedelta(days=3), "amount": 60.0, "name": "Multa"},
        {"status": "paid", "due_date": today + timedelta(days=15), "amount": 444.0, "name": "Boleto pago"},
    ]
    card_bills = [
        {"due_date": today + timedelta(days=10), "remaining": 300.0, "card_name": "Nubank"},
        {"due_date": today + timedelta(days=75), "remaining": 250.0, "card_name": "Inter"},
        {"due_date": today + timedelta(days=100), "remaining": 555.0, "card_name": "Fora do horizonte"},
    ]
    cf = _mock_sources(monkeypatch, saldo=2000.0, incomes=incomes, expenses=expenses,
                       bills=bills, card_bills=card_bills)

    traj = cf.daily_trajectory(1, days=90)
    for idx, dias in ((29, 30), (59, 60), (89, 90)):
        esperado = cf.project(1, today + timedelta(days=dias))["projetado"]
        assert traj["trajectory"][idx]["saldo_projetado"] == esperado, dias

    vistos = {c["nome"] for item in traj["trajectory"] for c in item["compromissos"]}
    vistos |= {v["nome"] for v in traj["vencidos"]}
    assert {"Salário", "13º", "Aluguel", "Água", "Multa", "Nubank", "Inter"} <= vistos
    assert not vistos & {"Receita inativa", "Gasto inativo", "Gasto manual", "Gasto semanal",
                         "Boleto pago", "Fora do horizonte"}


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
    out = cf.daily_trajectory(1, days=90, threshold=0.0)

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
    out = cf.daily_trajectory(1, days=90)

    assert out["worst_day"]["date"] == pior_dia.isoformat()


def test_daily_trajectory_threshold_igual_ao_saldo_nao_e_aperto(monkeypatch):
    """Fronteira: saldo_projetado == threshold exatamente não é aperto (< estrito)."""
    cf = _mock_sources(monkeypatch, saldo=500.0)
    out = cf.daily_trajectory(1, days=5, threshold=500.0)

    assert all(not item["abaixo_do_limite"] for item in out["trajectory"])


def test_daily_trajectory_threshold_marca_aperto_com_saldo_positivo(monkeypatch):
    """O limite de segurança é o que explica o aperto: saldo cai de 1000 pra 300
    no dia 10 e nunca fica negativo. Com limite 500, aperto a partir do dia 10;
    com limite 0, nenhum dia. (Sem o par, `abaixo_do_limite` fixo em False ou
    comparado com 0 passaria.)"""
    today = date.today()
    bill = {"status": "pending", "due_date": today + timedelta(days=10), "amount": 700.0, "name": "Aluguel"}
    cf = _mock_sources(monkeypatch, saldo=1000.0, bills=[bill])

    com_limite = cf.daily_trajectory(1, days=90, threshold=500.0)["trajectory"]
    assert [item["abaixo_do_limite"] for item in com_limite] == [False] * 9 + [True] * 81

    sem_limite = cf.daily_trajectory(1, days=90, threshold=0.0)["trajectory"]
    assert not any(item["abaixo_do_limite"] for item in sem_limite)


def test_daily_trajectory_threshold_com_fracao_de_centavo_segue_o_eco(monkeypatch):
    """`threshold=500.004` ecoa 500.0; um dia em 500.00 é "igual ao limite" na
    resposta, logo não pode ser aperto."""
    cf = _mock_sources(monkeypatch, saldo=500.0)
    out = cf.daily_trajectory(1, days=5, threshold=500.004)

    assert out["threshold"] == 500.0
    assert out["trajectory"][0]["saldo_projetado"] == 500.0
    assert not any(item["abaixo_do_limite"] for item in out["trajectory"])


def test_trajetoria_e_project_batem_com_fracao_de_centavo(monkeypatch):
    """As colunas de valor são `numeric` sem escala: fração de centavo chega do
    banco. Somada em ordem ou agrupamento diferentes (trajetória por dia, `project`
    por tipo), ela arredondava diferente; com soma exata, não. Cenários mínimos
    achados por busca, cada um quebra com uma forma de soma inexata: o 1º com a de
    HEAD (subtotais em sequência + expressão agrupada, trajetória acumulando); o 2º
    com só a trajetória acumulando ou só `project` agrupando."""
    today = date.today()
    for saldo, v1, v2 in ((66.3, 19.485, 48.48), (78.2, 5.168, 14.967)):
        cf = _mock_sources(monkeypatch, saldo=saldo, bills=[
            {"status": "pending", "due_date": today + timedelta(days=1), "amount": v1, "name": "B1"},
            {"status": "pending", "due_date": today + timedelta(days=2), "amount": v2, "name": "B2"},
        ])
        traj = cf.daily_trajectory(1, days=3)["trajectory"]
        for n in (1, 2, 3):
            assert traj[n - 1]["saldo_projetado"] == cf.project(1, today + timedelta(days=n))["projetado"], (saldo, n)


def test_project_fracao_de_centavo_soma_como_o_painel(monkeypatch):
    """20 × R$ 10,005 soma 200,10, como o painel "Em aberto" e a tool de contas
    (soma crua). Arredondar cada valor antes de somar daria 200,20 — R$ 0,10 de
    diferença entre dois números lado a lado na tela. Vale para as quatro fontes
    e para o saldo de partida. Esperados conferidos contra o `project()` de HEAD."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    vinte = range(20)  # dias 3..22: cada recorrente cai uma única vez até o dia 30
    cenarios = [
        ("boletos_ate", -200.1, dict(bills=[
            {"status": "pending", "due_date": d(3), "amount": 10.005, "name": f"B{i}"} for i in vinte])),
        ("receitas_previstas", 200.1, dict(incomes=[
            {"is_active": True, "pay_day": d(3 + i).day, "frequency": "monthly", "amount": 10.005, "name": f"R{i}"}
            for i in vinte])),
        ("gastos_fixos_previstos", -200.1, dict(expenses=[
            {"is_active": True, "payment_mode": "autopay", "frequency": "monthly", "due_day": d(3 + i).day,
             "amount": 10.005, "name": f"G{i}"} for i in vinte])),
        ("faturas_cartao", -200.1, dict(card_bills=[
            {"due_date": d(4), "remaining": 10.005, "card_name": "Nubank"} for _ in vinte])),
    ]
    for campo, projetado, fontes in cenarios:
        cf = _mock_sources(monkeypatch, **fontes)
        out = cf.project(1, d(30))
        assert (out[campo], out["projetado"]) == (200.1, projetado), campo
        assert cf.daily_trajectory(1, days=30)["trajectory"][29]["saldo_projetado"] == projetado, campo

    # saldo de partida 0,004 + estorno de 0,004 = 0,008 → 0,01 (arredondando o saldo antes: 0,00)
    cf = _mock_sources(monkeypatch, saldo=0.004, bills=[
        {"status": "pending", "due_date": d(2), "amount": -0.004, "name": "Estorno"}])
    assert cf.project(1, d(30))["projetado"] == 0.01


def test_project_tranquilo_usa_o_valor_exato(monkeypatch):
    """Tarifa de R$ 0,004 com saldo zero: o projetado exibido arredonda para 0,00,
    mas o caixa fica abaixo de zero — `tranquilo` é False, como em HEAD."""
    today = date.today()
    cf = _mock_sources(monkeypatch, saldo=0.0, bills=[
        {"status": "pending", "due_date": today + timedelta(days=2), "amount": 0.004, "name": "Tarifa"}])
    out = cf.project(1, today + timedelta(days=30))

    assert out["projetado"] == 0.0
    assert out["tranquilo"] is False


# Causas do pior dia: as SAÍDAS desde o último pico (maior saldo antes do pior
# dia; o mais recente em empate; num patamar, o dia em que o saldo chegou lá).

def test_worst_day_causas_inclui_todos_os_degraus_da_queda(monkeypatch):
    """Queda em 3 degraus de tipos diferentes: as 3 são causa, não só a do dia. A
    receita no meio da queda (dia 10) não é causa."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=2000.0,
        incomes=[{"is_active": True, "pay_day": d(10).day, "frequency": "monthly",
                  "amount": 100.0, "name": "Freela"}],
        expenses=[{"is_active": True, "payment_mode": "autopay", "frequency": "monthly",
                   "due_day": d(5).day, "amount": 900.0, "name": "Aluguel"}],
        card_bills=[{"due_date": d(8), "remaining": 700.0, "card_name": "Nubank"}],
        bills=[{"status": "pending", "due_date": d(12), "amount": 600.0, "name": "Luz"}],
    )
    wd = cf.daily_trajectory(1, days=15)["worst_day"]

    assert wd["date"] == d(12).isoformat() and wd["saldo_projetado"] == -100.0
    assert wd["desde"] == today.isoformat()
    assert wd["causas"] == [
        {"date": d(5).isoformat(), "tipo": "gasto_fixo", "nome": "Aluguel", "valor": 900.0},
        {"date": d(8).isoformat(), "tipo": "fatura_cartao", "nome": "Nubank", "valor": 700.0},
        {"date": d(12).isoformat(), "tipo": "boleto", "nome": "Luz", "valor": 600.0},
    ]
    assert wd["compromissos"] == [{"tipo": "boleto", "nome": "Luz", "valor": 600.0}]


def _cenario_pico_intermediario(monkeypatch):
    """1000 → gasto 300 (dia 3) → receita 2000 (dia 6) → gasto 2500 (dia 10)."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=1000.0,
        incomes=[{"is_active": True, "pay_day": d(6).day, "frequency": "monthly",
                  "amount": 2000.0, "name": "Salário"}],
        bills=[{"status": "pending", "due_date": d(3), "amount": 300.0, "name": "Mercado"},
               {"status": "pending", "due_date": d(10), "amount": 2500.0, "name": "IPVA"}],
    )
    return cf.daily_trajectory(1, days=20), d


def test_worst_day_causas_so_depois_do_ultimo_pico(monkeypatch):
    """O gasto do dia 3 veio ANTES do pico do dia 6 — não explica a queda até o dia 10."""
    out, d = _cenario_pico_intermediario(monkeypatch)
    wd = out["worst_day"]

    assert wd["date"] == d(10).isoformat() and wd["saldo_projetado"] == 200.0
    assert wd["desde"] == d(6).isoformat()
    assert wd["causas"] == [{"date": d(10).isoformat(), "tipo": "boleto", "nome": "IPVA", "valor": 2500.0}]


def test_worst_day_pico_empatado_vale_o_mais_recente(monkeypatch):
    """1000 → 500 (dia 3) → volta a 1000 (dia 6) → 200 (dia 10): dois picos de
    1000; vale o do dia 6, e o gasto do dia 3 já foi recuperado."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=1000.0,
        incomes=[{"is_active": True, "pay_day": d(6).day, "frequency": "monthly",
                  "amount": 500.0, "name": "Reembolso"}],
        bills=[{"status": "pending", "due_date": d(3), "amount": 500.0, "name": "Conserto"},
               {"status": "pending", "due_date": d(10), "amount": 800.0, "name": "IPVA"}],
    )
    wd = cf.daily_trajectory(1, days=20)["worst_day"]

    assert wd["date"] == d(10).isoformat()
    assert wd["desde"] == d(6).isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["IPVA"]


def test_worst_day_nao_altera_o_item_da_trajetoria(monkeypatch):
    out, d = _cenario_pico_intermediario(monkeypatch)
    item = next(it for it in out["trajectory"] if it["date"] == out["worst_day"]["date"])

    assert "causas" not in item and "desde" not in item
    assert set(item) == {"date", "saldo_projetado", "abaixo_do_limite", "compromissos"}


def test_worst_day_pico_pode_ser_o_saldo_de_partida(monkeypatch):
    """Saldo 1000 hoje, cartão 300 no dia 1, luz 200 no dia 5: o pico é a partida
    (dia 0), então a queda vem desde hoje e as duas saídas são causa."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(monkeypatch, saldo=1000.0, bills=[
        {"status": "pending", "due_date": d(1), "amount": 300.0, "name": "Cartão loja"},
        {"status": "pending", "due_date": d(5), "amount": 200.0, "name": "Luz"}])
    wd = cf.daily_trajectory(1, days=20)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d(5).isoformat(), 500.0)
    assert wd["desde"] == today.isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["Cartão loja", "Luz"]


def test_worst_day_queda_abaixo_da_partida_depois_de_receita(monkeypatch):
    """Caso mais comum: saldo 100 hoje, salário 3000 amanhã, aluguel 1200 no dia 5,
    cartão 900 no dia 8, limite 1500. O pior dia (1000) fica ACIMA da partida (100),
    mas houve queda desde o pico do salário — ela tem de vir explicada."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=100.0,
        incomes=[{"is_active": True, "pay_day": d(1).day, "frequency": "monthly", "amount": 3000.0, "name": "Salário"}],
        bills=[{"status": "pending", "due_date": d(5), "amount": 1200.0, "name": "Aluguel"}],
        card_bills=[{"due_date": d(8), "remaining": 900.0, "card_name": "Nubank"}],
    )
    wd = cf.daily_trajectory(1, days=25, threshold=1500.0)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"], wd["abaixo_do_limite"]) == (d(8).isoformat(), 1000.0, True)
    assert wd["desde"] == d(1).isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["Aluguel", "Nubank"]


def test_worst_day_pico_considera_vencidos_no_saldo_de_partida(monkeypatch):
    """Saldo 3000, mas o aluguel vencido (2500) já o leva a 500 na partida; gasto de
    300 no dia 2, salário 1500 no dia 3, gasto de 1650 no dia 10. O pico é o do
    salário, não os 3000 que o vencido já consumiu."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=3000.0,
        incomes=[{"is_active": True, "pay_day": d(3).day, "frequency": "monthly", "amount": 1500.0, "name": "Salário"}],
        bills=[{"status": "pending", "due_date": d(-5), "amount": 2500.0, "name": "Aluguel atrasado"},
               {"status": "pending", "due_date": d(2), "amount": 300.0, "name": "Mercado"},
               {"status": "pending", "due_date": d(10), "amount": 1650.0, "name": "Oficina"}],
    )
    wd = cf.daily_trajectory(1, days=20)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d(10).isoformat(), 50.0)
    assert wd["desde"] == d(3).isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["Oficina"]


def test_worst_day_sem_eventos_nao_tem_causas(monkeypatch):
    cf = _mock_sources(monkeypatch, saldo=50.0)
    out = cf.daily_trajectory(1, days=90)

    assert out["worst_day"]["causas"] == []
    assert out["worst_day"]["desde"] is None  # sem queda, nada a explicar
    assert out["vencidos"] == []


def test_worst_day_sem_queda_nao_culpa_saida(monkeypatch):
    """Partida 100; no dia 1, salário 1000 e luz 50 → o "pior dia" é o dia 1 com
    1050. O saldo subiu: dizer que a luz causou uma queda seria falso."""
    today = date.today()
    d1 = today + timedelta(days=1)
    cf = _mock_sources(
        monkeypatch, saldo=100.0,
        incomes=[{"is_active": True, "pay_day": d1.day, "frequency": "monthly", "amount": 1000.0, "name": "Salário"}],
        bills=[{"status": "pending", "due_date": d1, "amount": 50.0, "name": "Luz"}],
    )
    wd = cf.daily_trajectory(1, days=25)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d1.isoformat(), 1050.0)
    assert wd["causas"] == [] and wd["desde"] is None
    assert wd["compromissos"] == [{"tipo": "receita", "nome": "Salário", "valor": 1000.0},
                                  {"tipo": "boleto", "nome": "Luz", "valor": 50.0}]


def test_worst_day_saida_compensada_no_meio_do_patamar_nao_e_causa(monkeypatch):
    """1000 → salário 1700 no dia 3 (2700) → no dia 7 cartão 900 e freela 900 (continua
    2700) → IPVA 2500 no dia 12. `desde` é a chegada ao patamar (dia 3), mas o cartão
    foi 100% compensado: a única causa é o IPVA."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=1000.0,
        incomes=[{"is_active": True, "pay_day": d(3).day, "frequency": "monthly", "amount": 1700.0, "name": "Salário"},
                 {"is_active": True, "pay_day": d(7).day, "frequency": "monthly", "amount": 900.0, "name": "Freela"}],
        bills=[{"status": "pending", "due_date": d(7), "amount": 900.0, "name": "Cartão"},
               {"status": "pending", "due_date": d(12), "amount": 2500.0, "name": "IPVA"}],
    )
    wd = cf.daily_trajectory(1, days=25)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d(12).isoformat(), 200.0)
    assert wd["desde"] == d(3).isoformat()
    assert wd["causas"] == [{"date": d(12).isoformat(), "tipo": "boleto", "nome": "IPVA", "valor": 2500.0}]


def test_worst_day_saida_no_dia_do_pico_nao_e_causa(monkeypatch):
    """No dia 5, receita 3000 e saída 200 levam o saldo ao pico (3800); a queda
    começa no dia 6. A saída do próprio dia do pico não explica a queda."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=1000.0,
        incomes=[{"is_active": True, "pay_day": d(5).day, "frequency": "monthly", "amount": 3000.0, "name": "Bônus"}],
        bills=[{"status": "pending", "due_date": d(5), "amount": 200.0, "name": "Farmácia"},
               {"status": "pending", "due_date": d(6), "amount": 3500.0, "name": "Viagem"}],
    )
    wd = cf.daily_trajectory(1, days=20)["worst_day"]

    assert (wd["date"], wd["saldo_projetado"]) == (d(6).isoformat(), 300.0)
    assert wd["desde"] == d(5).isoformat()
    assert [c["nome"] for c in wd["causas"]] == ["Viagem"]


# Rotas /forecast e /recurring-bills/.../projection pelo HTTP de verdade
# (TestClient), com auth/plano mockados como em tests/test_export_email.py.

def _cliente(monkeypatch, **fontes):
    from fastapi.testclient import TestClient
    import frontend.finance_bot_websocket_custom as app_mod

    monkeypatch.setattr(app_mod, "_authorize_dashboard_access", lambda req, user_id: None)
    monkeypatch.setattr(app_mod, "_require_pro", lambda user_id, feature: None)
    monkeypatch.setattr(app_mod, "_require_boletos_access", lambda user_id: None)
    cf = _mock_sources(monkeypatch, **fontes)
    # sem raise: um 500 tem de aparecer como 500, não como exceção do teste
    return TestClient(app_mod.app, raise_server_exceptions=False), cf


def test_rota_forecast_devolve_trajetoria_causas_vencidos_e_horizons_intactos(monkeypatch):
    today = date.today()
    client, cf = _cliente(monkeypatch, saldo=1000.0, bills=[
        {"status": "pending", "due_date": today - timedelta(days=2), "amount": 100.0, "name": "Multa"},
        {"status": "pending", "due_date": today + timedelta(days=10), "amount": 700.0, "name": "Aluguel"},
    ])
    r = client.get("/forecast/1?threshold=250")
    assert r.status_code == 200, r.text
    fc = r.json()["forecast"]

    assert len(fc["trajectory"]) == 90
    assert fc["threshold"] == 250.0
    assert fc["period"] == {"start": (today + timedelta(days=1)).isoformat(),
                            "end": (today + timedelta(days=90)).isoformat()}
    assert fc["premises"] == cf.daily_trajectory(1)["premises"]
    assert fc["vencidos"] == [{"date": (today - timedelta(days=2)).isoformat(),
                               "tipo": "boleto", "nome": "Multa", "valor": 100.0}]
    wd = fc["worst_day"]
    assert (wd["date"], wd["saldo_projetado"], wd["abaixo_do_limite"]) == (
        (today + timedelta(days=10)).isoformat(), 200.0, True)
    assert wd["desde"] == today.isoformat()
    assert wd["causas"] == [{"date": (today + timedelta(days=10)).isoformat(),
                             "tipo": "boleto", "nome": "Aluguel", "valor": 700.0}]
    # o bloco que já existia continua vindo de forecast_horizons, intocado
    esperado = cf.forecast_horizons(1)
    assert fc["horizons"] == esperado["horizons"]
    for campo in ("today", "balance_source", "of_bank_count", "banks_excluded"):
        assert fc[campo] == esperado[campo]


def test_rota_forecast_recusa_threshold_nao_finito(monkeypatch):
    client, _ = _cliente(monkeypatch, saldo=1000.0)
    assert client.get("/forecast/1?threshold=100").status_code == 200  # finito passa
    for valor in ("nan", "inf", "-inf"):
        r = client.get(f"/forecast/1?threshold={valor}")
        assert r.status_code == 400, (valor, r.status_code, r.text)


def test_rota_projection_recusa_amount_nao_finito(monkeypatch):
    client, _ = _cliente(monkeypatch, saldo=1000.0)
    alvo = (date.today() + timedelta(days=30)).isoformat()
    assert client.get(f"/recurring-bills/1/projection?date={alvo}&amount=100").status_code == 200
    for valor in ("nan", "inf"):
        r = client.get(f"/recurring-bills/1/projection?date={alvo}&amount={valor}")
        assert r.status_code == 400, (valor, r.status_code, r.text)
