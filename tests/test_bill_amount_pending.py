from unittest.mock import Mock

from core.handlers import bills


def test_variable_bill_stores_pending_and_accepts_bare_amount(monkeypatch):
    pending = {}
    bill = {
        "id": 41,
        "name": "Luz",
        "status": "pending",
        "variable_amount": True,
    }
    monkeypatch.setattr("db.bills.list_bills", lambda *_args, **_kwargs: [bill])
    monkeypatch.setattr("db.bills.mark_bill_paid", Mock())
    monkeypatch.setattr(
        "db.set_pending_action",
        lambda uid, kind, payload: pending.update(
            user_id=uid, action_type=kind, payload=payload
        ),
    )

    question = bills.try_pay_from_text(7, "paguei a luz")

    assert "só o valor" in question
    assert pending == {
        "user_id": 7,
        "action_type": "bill_amount_expected",
        "payload": {"bill_id": 41, "bill_name": "Luz"},
    }

    paid = {"name": "Luz", "paid_amount": 132.5}
    mark = Mock(return_value=paid)
    clear = Mock()
    monkeypatch.setattr("db.bills.mark_bill_paid", mark)
    monkeypatch.setattr("db.clear_pending_action", clear)

    response = bills.resolve_bill_amount(7, "132,50", pending)

    mark.assert_called_once_with(7, 41, 132.5)
    clear.assert_called_once_with(7)
    assert "Conta paga" in response
    assert "R$ 132,50" in response


def test_non_numeric_reply_abandons_bill_question(monkeypatch):
    clear = Mock()
    mark = Mock()
    monkeypatch.setattr("db.clear_pending_action", clear)
    monkeypatch.setattr("db.bills.mark_bill_paid", mark)
    pending = {
        "action_type": "bill_amount_expected",
        "payload": {"bill_id": 41, "bill_name": "Luz"},
    }

    assert bills.resolve_bill_amount(7, "mostra meu saldo", pending) is None
    clear.assert_called_once_with(7)
    mark.assert_not_called()


def test_zero_keeps_bill_question_pending(monkeypatch):
    clear = Mock()
    mark = Mock()
    monkeypatch.setattr("db.clear_pending_action", clear)
    monkeypatch.setattr("db.bills.mark_bill_paid", mark)
    pending = {
        "action_type": "bill_amount_expected",
        "payload": {"bill_id": 41, "bill_name": "Luz"},
    }

    response = bills.resolve_bill_amount(7, "0", pending)

    assert "maior que zero" in response
    clear.assert_not_called()
    mark.assert_not_called()
