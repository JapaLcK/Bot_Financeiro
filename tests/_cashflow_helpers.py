"""Fontes mockadas da projeção de caixa (`core/services/cashflow.py`), para os
testes de projeção, horizontes, trajetória, pior dia e rotas de previsão.

Sem prefixo `test_` de propósito: o pytest não coleta este arquivo.
"""
from datetime import date, timedelta


def _carteira(saldo):
    """`get_consolidated_balance` (db/open_finance.py) sem banco conectado: a projeção
    parte de `manual`, e sem banco o consolidado é o próprio `manual`."""
    return {"of_bank_count": 0, "manual": saldo, "consolidated": saldo}


def _mock_sources(monkeypatch, *, saldo=0.0, incomes=(), expenses=(), bills=(), card_bills=()):
    """Mocka as fontes de `project`/`forecast_with_trajectory` na camada de db.*, sem
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
                        lambda uid: _carteira(saldo), raising=False)
    return cf


def fontes_que_mudam(monkeypatch):
    """As 5 fontes, cada uma com contador de leituras, mudando a partir da 2ª: na 1ª
    leitura, saldo 1000 e boleto de 700 daqui a 10 dias; depois, saldo 10 e nenhum
    boleto. Quem lê uma vez só projeta 300 em todo horizonte a partir do dia 10;
    quem relê mistura 300 com 10. Devolve `{nome_da_fonte: leituras}`."""
    import db.accounts, db.recurring, db.recurring_income, db.bills

    cf = _mock_sources(monkeypatch)
    boleto = {"status": "pending", "due_date": date.today() + timedelta(days=10),
              "amount": 700.0, "name": "Aluguel"}
    leituras: dict[str, int] = {}

    def contando(nome, primeira, depois):
        def fonte(*args, **kwargs):
            leituras[nome] += 1
            return primeira() if leituras[nome] == 1 else depois()
        return fonte

    for modulo, nome, primeira, depois in (
        (db, "get_consolidated_balance", lambda: _carteira(1000.0), lambda: _carteira(10.0)),
        (db.recurring, "list_recurring_expenses", list, list),
        (db.recurring_income, "list_recurring_incomes", list, list),
        (db.bills, "list_bills", lambda: [dict(boleto)], list),
        (cf, "_open_card_bills_detail", list, list),
    ):
        leituras[nome] = 0
        monkeypatch.setattr(modulo, nome, contando(nome, primeira, depois))
    return leituras
