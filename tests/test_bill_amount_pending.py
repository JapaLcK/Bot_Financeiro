import pytest
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
    # A reivindicação atômica (compare-and-swap) roda ANTES do pagamento, para
    # duas respostas concorrentes não criarem dois lançamentos. Aqui ela vence.
    reivindica = Mock(return_value=True)
    monkeypatch.setattr("db.bills.mark_bill_paid", mark)
    monkeypatch.setattr("db.advance_pending_action", reivindica)

    response = bills.resolve_bill_amount(7, "132,50", pending)

    mark.assert_called_once_with(7, 41, 132.5)
    # A pendência é apagada pela própria reivindicação (grava None se o payload
    # ainda for o lido), não mais por um clear incondicional.
    reivindica.assert_called_once_with(
        7, "bill_amount_expected", {"bill_id": 41, "bill_name": "Luz"}, None)
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


def test_duas_respostas_simultaneas_pagam_a_conta_uma_vez_so(monkeypatch):
    """P1 do Codex: sem reivindicação atômica, dois lançamentos para uma conta.

    `mark_bill_paid` cria o lançamento que debita o saldo ANTES da atualização
    condicional de status. Duas respostas concorrentes leem a mesma pendência,
    as duas chegam lá, e só uma conta muda de status — mas os DOIS lançamentos
    existem. Com o compare-and-swap, quem perde sai sem fazer nada.

    Determinístico: as duas chamadas usam o MESMO `pending` (o que as duas
    tarefas teriam lido), que é o mesmo intercalamento da corrida.
    """
    import uuid
    import db
    import db.bills
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    payload = {"bill_id": 41, "bill_name": "Luz"}
    db.set_pending_action(uid, "bill_amount_expected", payload)
    pending = db.get_pending_action(uid)

    pagamentos = []

    def _mark(u, bid, amt):
        pagamentos.append((bid, amt))
        return {"name": "Luz", "paid_amount": amt}

    monkeypatch.setattr(db.bills, "mark_bill_paid", _mark)

    r1 = H.resolve_bill_amount(uid, "132", pending)
    r2 = H.resolve_bill_amount(uid, "132", pending)   # a leitura velha

    assert len(pagamentos) == 1, f"a conta foi paga {len(pagamentos)}x: {pagamentos}"
    assert r1 and "Conta paga" in r1
    assert r2 is None, f"a segunda deveria sair calada, devolveu {r2!r}"


def test_valor_negativo_mantem_a_pergunta_e_nao_paga(monkeypatch):
    """P2 do Codex: `-10` caía no ramo de texto não-monetário.

    A pendência era descartada e o usuário ia pro fallback genérico, tendo que
    recomeçar. E há uma armadilha: `parse_money("-10")` devolve **10.0**, então
    apenas aceitar o sinal faria o bot pagar R$ 10 de um "-10". Por isso o sinal
    é capturado e tratado como valor inválido.
    """
    import uuid
    import db
    import db.bills
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    payload = {"bill_id": 41, "bill_name": "Luz"}
    db.set_pending_action(uid, "bill_amount_expected", payload)

    pagou = []
    monkeypatch.setattr(db.bills, "mark_bill_paid",
                        lambda u, b, a: pagou.append(a) or {"name": "Luz", "paid_amount": a})

    for entrada in ("-10", "R$ -5", "- 10"):
        db.set_pending_action(uid, "bill_amount_expected", payload)
        r = H.resolve_bill_amount(uid, entrada, db.get_pending_action(uid))
        assert r and "maior que zero" in r, f"{entrada!r} devolveu {r!r}"
        p = db.get_pending_action(uid) or {}
        assert p.get("action_type") == "bill_amount_expected", (
            f"{entrada!r} descartou a pergunta")

    assert pagou == [], f"valor negativo virou pagamento: {pagou}"


def test_devolucao_nao_atropela_pendencia_mais_nova(monkeypatch):
    """P2 do Codex: a devolução usava upsert incondicional.

    Se o pagamento estoura depois da reivindicação e, nesse meio tempo, outra
    tarefa armou uma pendência nova (uma confirmação já mostrada ao usuário),
    gravar por cima deixaria aquela órfã.
    """
    import uuid
    import db
    import db.bills
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    payload = {"bill_id": 41, "bill_name": "Luz"}
    db.set_pending_action(uid, "bill_amount_expected", payload)
    pending = db.get_pending_action(uid)

    def _estoura(u, b, a):
        # a outra tarefa armou a dela enquanto esta trabalhava
        db.set_pending_action(uid, "confirm_recurring_offer", {"name": "Luz"})
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(db.bills, "mark_bill_paid", _estoura)

    with pytest.raises(RuntimeError):
        H.resolve_bill_amount(uid, "132", pending)

    p = db.get_pending_action(uid) or {}
    assert p.get("action_type") == "confirm_recurring_offer", (
        f"a devolução atropelou a pendência mais nova — ficou {p.get('action_type')}")
