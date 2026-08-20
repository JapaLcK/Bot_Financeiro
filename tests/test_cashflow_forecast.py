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


def test_card_bill_due_date_canonica_rollover_clamp_e_mesmo_dia():
    # A projeção usa a regra canônica de db/cards.py (via _open_card_bills_due),
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
