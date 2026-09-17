"""Projeção de caixa até uma data (`core/services/cashflow.py`): datas de
recorrente, regras de valor da fonte de eventos, `project`, e a projeção pura
sobre os eventos de um horizonte maior.
"""
from datetime import date, timedelta

from _cashflow_helpers import _mock_sources


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


def test_eventos_do_maior_horizonte_filtrados_sao_os_de_cada_alvo(monkeypatch):
    """Ler até o maior horizonte e filtrar por data dá os eventos e a projeção de
    cada alvo — é o que deixa horizontes e trajetória saírem de UMA leitura."""
    today = date.today()
    d = lambda n: today + timedelta(days=n)
    cf = _mock_sources(
        monkeypatch, saldo=500.0,
        incomes=[{"is_active": True, "pay_day": 31, "frequency": "annual", "pay_month": d(60).month,
                  "start_date": d(30), "amount": 800.0, "name": "Anual dia 31"}],
        expenses=[{"is_active": True, "payment_mode": "autopay", "frequency": "monthly", "due_day": 31,
                   "start_date": d(20), "amount": 100.0, "name": "Mensal dia 31"},
                  {"is_active": True, "payment_mode": "manual", "frequency": "monthly", "due_day": 8,
                   "amount": 222.0, "name": "Gasto manual"},
                  {"is_active": True, "payment_mode": "autopay", "frequency": "weekly", "due_day": 9,
                   "amount": 333.0, "name": "Gasto semanal"}],
        bills=[*({"status": "pending", "due_date": d(n), "amount": 10.0 + n, "name": f"B{n}"}
                 for n in (-4, 0, 15, 90, 91)),
               {"status": "paid", "due_date": d(5), "amount": 70.0, "name": "Pago"},
               {"status": "pending", "due_date": None, "amount": 80.0, "name": "Sem data"}],
        card_bills=[{"due_date": d(n), "remaining": 50.0 + n, "card_name": f"F{n}"} for n in (-1, 0, 20, 95)],
    )
    events90 = cf._cashflow_events(1, today, d(90))
    # Controle positivo: sem os 4 tipos no conjunto maior, a igualdade abaixo passaria no vazio.
    assert {tipo for _d, tipo, _nome, _valor in events90} == {"receita", "gasto_fixo", "boleto", "fatura_cartao"}

    sb = cf._starting_balance(1)
    for n in range(-5, 91):
        assert [e for e in events90 if e[0] <= d(n)] == cf._cashflow_events(1, today, d(n)), n
        assert cf._projection(today, sb, events90, d(n)) == cf.project(1, d(n)), n


def test_project_boleto_novo_sai_do_projetado_alem_de_90_dias(monkeypatch):
    """`amount` da rota `/recurring-bills/{id}/projection` e da tool `check_cashflow`:
    o boleto em análise sai do projetado. O alvo passa de 90 dias, com compromisso
    entre 91 e 120, porque a projeção até uma data não tem teto de horizonte."""
    today = date.today()
    alvo = today + timedelta(days=120)
    cf = _mock_sources(monkeypatch, saldo=1000.0, bills=[
        {"status": "pending", "due_date": today + timedelta(days=100), "amount": 200.0, "name": "IPVA"}])

    assert cf.project(1, alvo)["projetado"] == 800.0
    out = cf.project(1, alvo, 500)
    assert (out["boletos_ate"], out["boleto_novo"], out["projetado"]) == (200.0, 500.0, 300.0)
