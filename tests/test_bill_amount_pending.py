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


def test_pergunta_de_valor_esta_na_lista_que_barra_a_ia():
    """P1 do Codex: sem isto, o usuário Pro tem a resposta sequestrada pela IA.

    Um número solto classifica como `out_of_scope`. O `handle_incoming` decide
    entregar à IA olhando `_RESUMABLE_PENDING_TYPES`; se o tipo não estiver lá,
    a IA responde antes de `route()` chegar no resolvedor — e a conta fica sem
    pagar. É o bug da issue #132 sobrevivendo justamente para quem paga.
    """
    from core.handle_incoming import _RESUMABLE_PENDING_TYPES
    assert "bill_amount_expected" in _RESUMABLE_PENDING_TYPES


def test_conversa_inteira_numero_solto_paga_a_conta(monkeypatch):
    """Ponta a ponta, pelo caminho real — o que os testes com mock não cobrem.

    Os outros testes deste arquivo chamam `try_pay_from_text` e
    `resolve_bill_amount` direto. Medido: removendo o bloco novo do
    `intent_router`, os três continuam passando. Este falha.
    """
    import uuid
    import db
    import db.bills
    from core.types import IncomingMessage
    from core.intent_classifier import classify
    from core.intent_router import route

    bill = {"id": 41, "name": "Luz", "status": "pending", "variable_amount": True,
            "amount": 150.0, "due_day": 10, "recurring_id": 1, "launch_id": None}
    monkeypatch.setattr(db.bills, "list_bills", lambda *a, **k: [bill])
    pago = {"name": "Luz", "paid_amount": None}

    def _mark(uid, bid, amt):
        pago["paid_amount"] = amt
        return {"name": "Luz", "paid_amount": amt}

    monkeypatch.setattr(db.bills, "mark_bill_paid", _mark)

    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)

    def diga(txt):
        m = IncomingMessage(platform="whatsapp", user_id=uid, text=txt,
                            message_id="x", attachments=[], external_id="e", raw={})
        return route(classify(txt, user_id=uid), m)

    pergunta = diga("paguei a luz")
    assert "valor variável" in pergunta
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected"

    resposta = diga("132")
    assert resposta and "Conta paga" in resposta, f"o número não pagou a conta: {resposta!r}"
    assert pago["paid_amount"] == 132.0
