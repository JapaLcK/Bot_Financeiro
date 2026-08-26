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
    # A gravação da pergunta passa pelo `claim_pending_action`, que respeita a
    # ordem de prioridade da linha única (db/pending.py).
    monkeypatch.setattr(
        "db.claim_pending_action",
        lambda uid, kind, payload: bool(
            pending.update(user_id=uid, action_type=kind, payload=payload) or True
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
    monkeypatch.setattr("db.consume_pending_action", reivindica)

    response = bills.resolve_bill_amount(7, "132,50", pending)

    mark.assert_called_once_with(7, 41, 132.5)
    # A pendência é apagada pela própria reivindicação (grava None se o payload
    # ainda for o lido), não mais por um clear incondicional.
    reivindica.assert_called_once_with(7, pending)
    assert "Conta paga" in response
    assert "R$ 132,50" in response


def test_non_numeric_reply_abandons_bill_question(monkeypatch):
    # O abandono virou condicional (compare-and-swap com o payload lido) para
    # não apagar uma pendência que outra tarefa tenha posto no lugar.
    abandona = Mock(return_value=True)
    mark = Mock()
    monkeypatch.setattr("db.consume_pending_action", abandona)
    monkeypatch.setattr("db.bills.mark_bill_paid", mark)
    pending = {
        "action_type": "bill_amount_expected",
        "payload": {"bill_id": 41, "bill_name": "Luz"},
    }

    assert bills.resolve_bill_amount(7, "mostra meu saldo", pending) is None
    abandona.assert_called_once_with(7, pending)
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


def test_devolucao_repoe_a_pergunta_quando_o_pagamento_estoura(monkeypatch):
    """Controle NEGATIVO que faltava para a devolução da porta de texto.

    Medido: o grupo só tinha a corrida abaixo, e ela passa igual com a
    devolução desligada (com o rollback fora, ninguém escreve e a pergunta nova
    sobrevive do mesmo jeito). Sem este caso, tirar o `with` de
    `resolve_bill_amount` não deixava uma linha vermelha.
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
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(db.bills, "mark_bill_paid", _estoura)

    with pytest.raises(RuntimeError):
        H.resolve_bill_amount(uid, "132", pending)

    volta = db.get_pending_action(uid) or {}
    assert volta.get("action_type") == "bill_amount_expected", (
        f"a pergunta não voltou — ficou {volta.get('action_type')!r}")
    assert volta.get("payload") == payload, volta


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


def test_abandono_nao_apaga_pendencia_mais_nova(monkeypatch):
    """P2 do Codex: o abandono usava clear incondicional.

    Resposta não-monetária abandona a pergunta. Se outra tarefa trocou a
    pendência por uma confirmação nova nesse meio tempo — já mostrada ao
    usuário —, o clear apagava aquela e a deixava órfã.

    Terceira instância da mesma família neste PR (reivindicar, devolver,
    abandonar): toda escrita na linha de pendência precisa ser condicional.
    """
    import uuid
    import db
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    payload = {"bill_id": 41, "bill_name": "Luz"}
    db.set_pending_action(uid, "bill_amount_expected", payload)
    pending = db.get_pending_action(uid)

    # a outra tarefa trocou a pendência depois que esta leu
    db.set_pending_action(uid, "confirm_recurring_offer", {"name": "Luz"})

    assert H.resolve_bill_amount(uid, "sei la", pending) is None

    p = db.get_pending_action(uid) or {}
    assert p.get("action_type") == "confirm_recurring_offer", (
        f"o abandono apagou a pendência mais nova — ficou {p.get('action_type')}")


def test_controle_abandono_normal_ainda_limpa_a_pergunta(monkeypatch):
    """Controle: sem concorrência, abandonar continua apagando de verdade.

    Sem isto, o teste acima passaria num código que nunca abandona nada.
    """
    import uuid
    import db
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    db.set_pending_action(uid, "bill_amount_expected",
                          {"bill_id": 41, "bill_name": "Luz"})
    pending = db.get_pending_action(uid)

    assert H.resolve_bill_amount(uid, "sei la", pending) is None
    assert db.get_pending_action(uid) is None, "a pergunta deveria ter sido abandonada"


# ---------------------------------------------------------------------------
# Comportamento, não texto de arquivo.
#
# As duas versões anteriores destes dois testes liam `db/bills.py` e
# `core/handle_incoming.py` com `pathlib.read_text()` e procuravam palavras.
# Medido: com o gate mutado para `... or True` e com o `and status='pending'`
# removido do UPDATE de reserva, os 13 testes do arquivo passavam. Media
# palavra, não comportamento.
# ---------------------------------------------------------------------------

def _monta_conta_variavel(uid, nome="Luz"):
    """Cria uma recorrente manual de valor variável e a instância do mês."""
    from datetime import date
    import db
    import db.bills as B
    import db.recurring as R

    db.ensure_user(uid)
    rec = R.create_recurring_expense(uid, nome, None, "conta", 10, "account",
                                     payment_mode="manual", variable_amount=True)
    B.ensure_bill_instance(rec["id"], uid, date(2026, 8, 10), 0)
    return [b for b in B.list_bills(uid, include_paid=False)][0]


def _diga(uid, texto):
    from core.types import IncomingMessage
    import core.handle_incoming as HI

    msg = IncomingMessage(platform="whatsapp", user_id=uid, text=texto,
                          message_id="x", attachments=[], external_id="e", raw={})
    saida = HI.handle_incoming(msg)
    return saida[0].text if saida else ""


@pytest.fixture
def ia_espia(monkeypatch):
    """Mocka a IA e devolve a lista de mensagens que chegaram nela."""
    import core.services.ai_chat as AC

    vistas: list[str] = []
    monkeypatch.setattr(AC, "chat",
                        lambda uid, text, **kw: vistas.append(text) or "[IA]")
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda u: True)
    return vistas


def test_reserva_serializa_duas_respostas_e_debita_uma_vez(monkeypatch):
    """P1 do Codex, agora medido no banco em vez de lido no arquivo.

    O `and status='pending'` do UPDATE de reserva é o que serializa duas
    requisições concorrentes. Sem ele, as duas reservam e as duas debitam.
    A barreira dentro do `get_bill` força o intercalamento que produz o bug:
    as DUAS leem a conta ainda pendente antes de qualquer UPDATE.
    """
    import threading
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)

    barreira = threading.Barrier(2, timeout=10)
    ja_sincronizou: set[int] = set()
    real_get_bill = B.get_bill

    def get_bill_sincronizado(u, bid):
        b = real_get_bill(u, bid)
        tid = threading.get_ident()
        if tid not in ja_sincronizou:
            ja_sincronizou.add(tid)
            barreira.wait()      # as duas já leram 'pending'
        return b

    monkeypatch.setattr(B, "get_bill", get_bill_sincronizado)

    resultados = []

    def paga():
        try:
            resultados.append(B.mark_bill_paid(uid, int(conta["id"]), 100.0))
        except Exception as exc:                      # pragma: no cover
            resultados.append(exc)

    ts = [threading.Thread(target=paga) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    lancamentos = db.list_launches(uid, limit=10)
    assert len(lancamentos) == 1, f"debitou {len(lancamentos)}x: {lancamentos}"
    assert sum(r is None for r in resultados) == 1, (
        f"a perdedora deveria receber None: {resultados}")


def test_gate_da_ia_nao_deixa_o_numero_chegar_na_ia(ia_espia):
    """Amarra o COMPORTAMENTO do gate, não a presença do tipo numa lista.

    Com a pergunta viva, "132" tem que pagar a conta sem passar pela IA. Falha
    se `bill_amount_expected` perder o `suprime_ia` no registro — e falharia
    também se o gate voltasse a filtrar a mensagem antes de suprimir.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    assert "valor variável" in _diga(uid, "paguei a luz")
    ia_espia.clear()

    resposta = _diga(uid, "132")

    assert not ia_espia, f"a IA sequestrou a resposta: {ia_espia}"
    assert "Conta paga" in resposta, resposta
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "paid" and float(conta["paid_amount"]) == 132.0
    assert db.get_pending_action(uid) is None


def test_pergunta_de_valor_desaloja_oferta_de_conveniencia(ia_espia):
    """Achado 1 do Tester: a sequência mais comum do produto.

    Qualquer lançamento deixa `recategorize_launch_offer` de pé por 10 min. Com
    a gravação puramente condicional, o "paguei a luz" seguinte não conseguia
    guardar de qual conta falava e o "132" ia pra IA — a issue #132 inteira de
    volta. Oferta de conveniência cede para pergunta (ordem em db/pending.py).
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    _diga(uid, "gastei 50 no mercado")
    assert (db.get_pending_action(uid) or {}).get("action_type") == \
        "recategorize_launch_offer", "premissa do teste sumiu: o lançamento não deixa oferta"

    pergunta = _diga(uid, "paguei a luz")
    assert "só o valor" in pergunta, pergunta
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected"

    ia_espia.clear()
    resposta = _diga(uid, "132")
    assert not ia_espia, f"a IA sequestrou a resposta: {ia_espia}"
    assert "Conta paga" in resposta, resposta
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "paid" and float(conta["paid_amount"]) == 132.0


def test_ordem_de_prioridade_da_linha_de_pendencias():
    """O outro lado da ordem escrita em db/pending.py: pergunta não cede.

    Sem isto, `claim_pending_action` poderia desalojar tudo e o teste acima
    passaria igual — inclusive atropelando a clarification de um lançamento,
    que carrega o valor já informado pelo usuário.
    """
    import uuid
    import db

    nova = {"bill_id": 41, "bill_name": "Luz"}

    # 1. linha livre → arma
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    assert db.claim_pending_action(uid, "bill_amount_expected", nova) is True
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected"

    # 2. ocupada por OFERTA DE CONVENIÊNCIA → desaloja
    for oferta in ("recategorize_launch_offer", "undo_audio"):
        uid = int(uuid.uuid4().int % 1_000_000_000)
        db.ensure_user(uid)
        db.set_pending_action(uid, oferta, {"launch_id": 9})
        assert db.claim_pending_action(uid, "bill_amount_expected", nova) is True, oferta
        atual = db.get_pending_action(uid) or {}
        assert atual.get("action_type") == "bill_amount_expected", oferta
        assert atual.get("payload") == nova, oferta

    # 3. ocupada por PERGUNTA → cede, e a pergunta antiga fica intacta
    # `confirm_recurring_offer` está aqui, e não entre as ofertas: ela pergunta
    # "sim ou não" em texto e o runtime NÃO a consome no turno em que nasce.
    for pergunta in ("clarification", "multi_launch_values", "credit_card_setup",
                     "delete_launch", "confirm_recurring_offer"):
        uid = int(uuid.uuid4().int % 1_000_000_000)
        db.ensure_user(uid)
        db.set_pending_action(uid, pergunta, {"valor": 77.9})
        assert db.claim_pending_action(uid, "bill_amount_expected", nova) is False, pergunta
        atual = db.get_pending_action(uid) or {}
        assert atual.get("action_type") == pergunta, pergunta
        assert atual.get("payload") == {"valor": 77.9}, pergunta

    # 4. ocupada pela MESMA pergunta → substitui. Não é disputa: é o usuário
    # falando da outra conta ("paguei a luz" e depois "paguei a água"), e a
    # primeira pergunta já morreu na tela dele.
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    db.set_pending_action(uid, "bill_amount_expected", {"bill_id": 7, "bill_name": "Agua"})
    assert db.claim_pending_action(uid, "bill_amount_expected", nova) is True
    assert (db.get_pending_action(uid) or {}).get("payload") == nova

    # 5. ocupada pela mesma pergunta feita pela OUTRA PORTA (`bill_pay_amount`,
    # do botão "✅ Já paguei") → também substitui. É "quanto veio a conta?" nas
    # duas, só muda quem consome a resposta. Deixar isso como disputa fazia o
    # botão perder o claim e o número pagar a conta ERRADA.
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    db.set_pending_action(uid, "bill_pay_amount", {"bill_id": 7, "name": "Agua"})
    assert db.claim_pending_action(uid, "bill_amount_expected", nova) is True
    assert (db.get_pending_action(uid) or {}).get("payload") == nova

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    db.set_pending_action(uid, "bill_amount_expected", nova)
    assert db.claim_pending_action(uid, "bill_pay_amount", {"bill_id": 7, "name": "Agua"}) is True
    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "bill_pay_amount"
    assert atual.get("payload") == {"bill_id": 7, "name": "Agua"}


@pytest.mark.parametrize("resposta", [
    "132", "132,50", "R$ 132", "132 reais", "132 real", "132 pila",
    "foi 132", "acho que 132", "uns 132", "veio 132 reais", "deu 132,50",
    "132.50", "1.132,50",
])
def test_formas_faladas_de_responder_o_valor_pagam_a_conta(ia_espia, resposta):
    """Achado 2 do Tester: as 5 formas faladas iam todas para a IA.

    Elas são a resposta natural a "quanto veio este mês?". Antes, o gate
    filtrava por "parece um número" e mandava tudo isso pra IA com a conta em
    aberto — número solto na mão da IA, que é exatamente a issue #132.
    """
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    _diga(uid, "paguei a luz")
    ia_espia.clear()

    texto = _diga(uid, resposta)

    assert not ia_espia, f"{resposta!r} foi parar na IA"
    assert "Conta paga" in texto, f"{resposta!r} → {texto!r}"
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "paid", resposta


@pytest.mark.parametrize("ambiguo,esperado", [
    ("132 50", 13250.0),      # parse_money cola o espaço
    ("1 32", 132.0),
    ("1.2.3.4", None),
    ("1,,,2", None),
    ("132\n50", None),
])
def test_numero_com_espaco_nao_paga_e_mantem_a_pergunta(ambiguo, esperado):
    """`132 50` pagava R$ 13.250,00 sem confirmação.

    A regex antiga aceitava `\\s` dentro do número e o `parse_money` colava.
    Agora esses textos re-perguntam e a conta continua pendente. O `esperado`
    documenta o que o `parse_money` faria com eles se chegassem lá.
    """
    import uuid
    import db
    import db.bills as B
    from utils_text import parse_money

    try:
        assert parse_money(ambiguo) == esperado, "premissa mudou: parse_money"
    except Exception:
        assert esperado is None

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    from core.handlers import bills as H
    from db.bills import list_bills

    conta = list_bills(uid, include_paid=False)[0]
    db.set_pending_action(uid, "bill_amount_expected",
                          {"bill_id": int(conta["id"]), "bill_name": "Luz"})
    r = H.resolve_bill_amount(uid, ambiguo, db.get_pending_action(uid))

    assert r and "Não entendi o valor" in r, f"{ambiguo!r} → {r!r}"
    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending", f"{ambiguo!r} pagou a conta"
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected"
    assert db.list_launches(uid, limit=5) == [] or \
        len(db.list_launches(uid, limit=5)) == 0


def test_numero_dentro_de_outro_comando_nao_paga_a_conta():
    """Mata a mutação `re.fullmatch` → `re.search`.

    Nenhum teste mandava número+palavras ao `resolve_bill_amount`: com `search`,
    "132 no mercado" pagaria a conta de luz. Aqui ele tem que abandonar a
    pergunta e devolver None para o roteamento normal.
    """
    import uuid
    import db
    import db.bills as B
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    for comando in ("132 no mercado", "gastei 132 no mercado",
                    "apaga o gasto 132", "quanto gastei em 132"):
        db.set_pending_action(uid, "bill_amount_expected",
                              {"bill_id": int(conta["id"]), "bill_name": "Luz"})
        assert H.resolve_bill_amount(uid, comando, db.get_pending_action(uid)) is None, \
            f"{comando!r} foi tratado como valor"
        assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending", comando
        assert db.get_pending_action(uid) is None, f"{comando!r} não abandonou a pergunta"


def test_centavos_abaixo_de_um_centavo_nao_viram_pagamento_de_zero():
    """A mensagem dizia "R$ 0,00 lançado" e o dado gravado era 0.001."""
    import uuid
    import db
    import db.bills as B
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    db.set_pending_action(uid, "bill_amount_expected",
                          {"bill_id": int(conta["id"]), "bill_name": "Luz"})

    r = H.resolve_bill_amount(uid, "0,001", db.get_pending_action(uid))

    assert "maior que zero" in r, r
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"


def test_dois_assuntos_diferentes_em_sequencia_pelo_handle_incoming(ia_espia):
    """A classe cega: dois assuntos seguidos, com estado real no banco.

    Os testes com mock e o ponta-a-ponta por `route(classify(...))` pulam o
    `handle_incoming`, e é lá que mora a competição pela linha única de
    `pending_actions` e o gate da IA. Os dois piores achados do Tester só
    apareceram aqui.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    primeiro = _diga(uid, "gastei 50 no mercado")
    assert "Despesa registrada" in primeiro, primeiro

    _diga(uid, "paguei a luz")
    # muda de assunto no meio: a pergunta é abandonada e o comando roda
    saldo = _diga(uid, "saldo")
    assert "Conta Corrente" in saldo, saldo
    assert db.get_pending_action(uid) is None
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"

    # e a conta ainda pode ser paga pela forma completa, sem estado nenhum
    final = _diga(uid, "paguei luz 132,50")
    assert "Conta paga" in final, final
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "paid" and float(conta["paid_amount"]) == 132.5


def test_tool_da_ia_tambem_guarda_de_qual_conta_falava():
    """Achado 3 do Tester: a MESMA pergunta existe em três lugares.

    `quitei a luz` / `ja paguei a luz` classificam out_of_scope e vão pra IA,
    que chama `pay_bill`. Essa versão da pergunta não guardava estado nenhum —
    o número da resposta voltava pra IA sem contexto, reabrindo a issue #132
    pelo lado do Pro. Agora ela arma a mesma pendência do handler.
    """
    import uuid
    import db
    from core.services.ai_chat.tools.bills import _pay_bill_execute

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)

    resposta = _pay_bill_execute(uid, {"name": "luz"})

    assert "valor variável" in resposta, resposta
    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "bill_amount_expected", atual
    assert atual.get("payload") == {"bill_id": int(conta["id"]), "bill_name": "Luz"}

    # e o número seguinte, pelo caminho real, paga a conta
    import db.bills as B
    assert "Conta paga" in _diga(uid, "132")
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "paid"


# ---------------------------------------------------------------------------
# 2ª rodada do Tester.
#
# LIMITE CONHECIDO: nada aqui (nem no resto da suíte) exercita
# `adapters/whatsapp/wa_runtime.py`, que é a única camada que decide se uma
# pendência sobrevive ao turno — o `_send_reply_with_optional_buttons`
# (wa_runtime.py:181-211) consome `undo_audio` e `recategorize_launch_offer`
# antes de enviar a resposta. Cobrir isso pede simular o payload interativo do
# WhatsApp, que nenhum teste do repo monta hoje. Consequência prática: a
# afirmação "oferta de conveniência é consumida no mesmo turno", que é o
# critério da lista em db/pending.py, está verificada por leitura, não por
# teste. Foi lendo esse código que se descobriu que `confirm_recurring_offer`
# NÃO é consumida — o achado 2 desta rodada.
# ---------------------------------------------------------------------------

def test_falha_no_debito_devolve_a_conta_e_a_retentativa_paga(monkeypatch):
    """A inversão (reservar antes de debitar) perdia o gasto para sempre.

    Medido antes do conserto: a conta ficava 'paid' sem lançamento, o saldo não
    era debitado, o gasto sumia do extrato e a retentativa devolvia None
    ("Essa conta não está mais pendente") — sem caminho no bot para reabrir.
    """
    import uuid
    import db
    import db.accounts as ACC
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)

    def _estoura(*_a, **_k):
        raise RuntimeError("pool esgotado")

    monkeypatch.setattr(ACC, "add_launch_and_update_balance", _estoura)
    with pytest.raises(RuntimeError):
        B.mark_bill_paid(uid, int(conta["id"]), 100.0)

    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending", "a conta ficou fechada sem lançamento"
    assert depois["paid_amount"] is None and depois["launch_id"] is None
    assert db.list_launches(uid, limit=5) == []

    monkeypatch.undo()
    pago = B.mark_bill_paid(uid, int(conta["id"]), 100.0)
    assert pago is not None, "a retentativa não conseguiu pagar"
    assert pago["status"] == "paid" and float(pago["paid_amount"]) == 100.0
    assert len(db.list_launches(uid, limit=5)) == 1


def test_devolucao_nao_reabre_conta_que_ja_tem_lancamento(monkeypatch):
    """Controle do teste acima: a devolução é condicionada, não incondicional.

    Se, entre a reserva e a falha, a conta já tiver um lançamento ligado, ela
    NÃO pode voltar para 'pending' — isso duplicaria o débito na retentativa.
    """
    import uuid
    import db
    import db.accounts as ACC
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    real = ACC.add_launch_and_update_balance

    def _lanca_e_estoura(*a, **k):
        lid, seq, bal = real(*a, **k)
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("update bill_instances set launch_id=%s where id=%s",
                            (lid, int(conta["id"])))
            conn.commit()
        raise RuntimeError("caiu depois de debitar")

    monkeypatch.setattr(ACC, "add_launch_and_update_balance", _lanca_e_estoura)
    with pytest.raises(RuntimeError):
        B.mark_bill_paid(uid, int(conta["id"]), 100.0)

    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "paid", "reabriu uma conta que já tinha lançamento"


@pytest.mark.parametrize("valor", [float("inf"), float("-inf"), float("nan")])
def test_valor_absurdo_e_recusado_antes_da_reserva(valor):
    """`parse_money("1"*400)` devolve `inf` — alcançável sem mock nenhum.

    Sem a guarda, a reserva gravava `paid_amount = Infinity` (o Postgres
    `numeric` aceita) e só o `add_launch` estourava, DEPOIS: conta fechada com
    valor infinito no dashboard.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)

    with pytest.raises(ValueError):
        B.mark_bill_paid(uid, int(conta["id"]), valor)

    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending", f"{valor} reservou a conta"
    assert depois["paid_amount"] is None
    assert db.list_launches(uid, limit=5) == []


def test_valor_gigante_finito_ainda_paga():
    """Controle do teste acima: a guarda de finitude não pode virar teto.

    Um teto de R$ 1 bi chegou a existir neste branch e transformava
    "paguei luz 2000000000" — que a `main` pagava — em "erro interno" com a
    conta ainda pendente. Regra de negócio nova disfarçada de validação.
    """
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)

    pago = B.mark_bill_paid(uid, int(conta["id"]), 2_000_000_000.0)
    assert pago is not None and pago["status"] == "paid"
    assert float(pago["paid_amount"]) == 2_000_000_000.0


def test_numero_gigante_pela_conversa_nao_fecha_a_conta(ia_espia):
    """O caminho real do `inf`: 400 dígitos como resposta da pergunta.

    `parse_money` devolve `inf`, que passa em qualquer `> 0`. A guarda de
    finitude do `resolve_bill_amount` responde a mensagem de valor inválido
    ANTES de reivindicar a pendência: conta pendente, saldo intacto, pergunta
    de pé e nenhum "erro interno" com stack trace no log.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    _diga(uid, "paguei a luz")

    resposta = _diga(uid, "1" * 400)

    assert "maior que zero" in resposta, resposta
    assert "erro interno" not in resposta.lower(), resposta
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "pending" and conta["paid_amount"] is None
    assert db.list_launches(uid, limit=5) == []
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected"


def test_sim_da_oferta_de_gasto_fixo_sobrevive_a_pergunta_de_conta(ia_espia):
    """Achado 2: `confirm_recurring_offer` é PERGUNTA, não oferta descartável.

    Classificada como oferta, ela era desalojada pelo "paguei a luz" e o "sim"
    seguinte levava "não entendi bem o que você quis fazer" — derrubando as
    DUAS pendências: o Spotify não virava gasto fixo e a pergunta da conta
    morria junto. Agora a conta é que cede, para o texto degradado.

    Este teste também mata a mutação que apaga o ramo `if not guardou:`: sem
    ele a resposta seria "Pode mandar só o valor" com a pergunta NÃO salva, e o
    "132" cairia na IA com a conta em aberto.

    O texto degradado pede para terminar a oferta primeiro — e o final do teste
    mostra por quê: é DEPOIS do "sim" (linha livre) que a forma completa paga.
    """
    import uuid
    import db
    import db.bills as B
    import db.recurring as R

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    db.set_pending_action(uid, "confirm_recurring_offer", {
        "name": "Spotify", "amount": 21.9, "category": "assinaturas",
        "due_day": 10, "merchant_key": "spotify",
    })

    pergunta = _diga(uid, "paguei a luz")
    assert "outra pergunta minha" in pergunta, f"não degradou: {pergunta!r}"
    assert "paguei luz" not in pergunta.lower(), pergunta
    assert (db.get_pending_action(uid) or {}).get("action_type") == \
        "confirm_recurring_offer", "a oferta de gasto fixo foi desalojada"

    confirmacao = _diga(uid, "sim")
    assert "gasto fixo" in confirmacao, confirmacao
    assert "Spotify" in [r["name"] for r in R.list_recurring_expenses(uid)]

    # e a conta, que degradou, ainda é pagável pela forma completa
    final = _diga(uid, "paguei luz 132,50")
    assert "Conta paga" in final, final
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "paid"


def test_gravacao_em_linha_livre_nao_atropela_quem_chegou_no_meio(monkeypatch):
    """A linha livre grava condicional, não com `set_pending_action`.

    Entre esta tarefa ler "linha livre" e escrever, outra armou uma pergunta
    que já apareceu na tela do usuário. O insert condicional não pega, e é a
    dela que fica.
    """
    import uuid
    import db
    import db.pending as P

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    real = P.get_pending_action

    def leu_livre(u):
        lido = real(u)                       # None: a linha está livre
        db.set_pending_action(uid, "clarification", {"valor": 77.9})
        return lido

    monkeypatch.setattr(P, "get_pending_action", leu_livre)

    assert db.claim_pending_action(uid, "bill_amount_expected",
                                   {"bill_id": 41, "bill_name": "Luz"}) is False
    monkeypatch.undo()
    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "clarification", atual
    assert atual.get("payload") == {"valor": 77.9}


def test_desalojamento_e_condicionado_ao_que_foi_lido(monkeypatch):
    """O desalojamento é compare-and-swap, não `set_pending_action`.

    Esta tarefa leu uma oferta de conveniência (desalojável), mas antes de
    escrever a linha virou uma PERGUNTA. O CAS não pega, e a pergunta nova
    sobrevive — sem ele, a oferta lida autorizava atropelar a pergunta.
    """
    import uuid
    import db
    import db.pending as P

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    db.set_pending_action(uid, "recategorize_launch_offer", {"launch_id": 9})
    real = P.get_pending_action

    def leu_a_oferta(u):
        lido = real(u)                       # a oferta, desalojável
        db.set_pending_action(uid, "clarification", {"valor": 77.9})
        return lido

    monkeypatch.setattr(P, "get_pending_action", leu_a_oferta)

    assert db.claim_pending_action(uid, "bill_amount_expected",
                                   {"bill_id": 41, "bill_name": "Luz"}) is False
    monkeypatch.undo()
    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "clarification", atual
    assert atual.get("payload") == {"valor": 77.9}


def test_nan_nao_paga_silenciosamente_o_valor_estimado_da_conta():
    """`nan` não é "sem valor informado".

    `float("nan") > 0` é False, então sem a guarda de finitude o `nan` cai no
    fallback `else float(bill["amount"])` e paga o valor estimado da conta sem
    ninguém pedir. Só aparece em conta que TEM valor estimado — por isso este
    teste não usa a conta variável (estimado 0) dos outros.
    """
    import uuid
    from datetime import date
    import db
    import db.bills as B
    import db.recurring as R

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    rec = R.create_recurring_expense(uid, "Internet", 100.0, "conta", 10, "account",
                                     payment_mode="manual")
    B.ensure_bill_instance(rec["id"], uid, date(2026, 8, 10), 100.0)
    conta = [b for b in B.list_bills(uid) if b["status"] == "pending"][0]

    with pytest.raises(ValueError):
        B.mark_bill_paid(uid, int(conta["id"]), float("nan"))

    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending", "nan pagou o valor estimado da conta"
    assert db.list_launches(uid, limit=5) == []


def test_controle_a_espia_da_ia_dispara_de_verdade(ia_espia):
    """Controle POSITIVO da fixture `ia_espia`.

    Os outros usos dela são todos `assert not ia_espia`. Se o `monkeypatch`
    errasse o alvo (nome do módulo, função renomeada), a lista ficaria vazia
    para sempre e os seis passariam de graça, sem medir nada. Aqui a mensagem
    é justamente a que TEM que cair na IA: usuário sem pendência nenhuma,
    pergunta fora do escopo financeiro.
    """
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)

    resposta = _diga(uid, "qual a capital da mongolia")

    assert ia_espia == ["qual a capital da mongolia"], (
        f"a espiã não capturou nada — o monkeypatch não está no alvo: {ia_espia}")
    assert resposta == "[IA]", resposta


# ── C1: o centavo invisível nos QUATRO caminhos de pagamento ────────────────
# Todos convergem para `db.bills.mark_bill_paid`, e é lá que o arredondamento
# para centavos mora. Estes testes batem em cada porta de entrada, porque a
# tradução do erro (a MENSAGEM que o usuário lê) é responsabilidade de cada
# chamador — e era ela que dizia "R$ 0,00 lançado".

def test_centavo_invisivel_pelo_texto_nao_paga(monkeypatch):
    """"paguei luz 0,001" respondia "✅ Conta paga — R$ 0,00" com 0.001 no banco."""
    import uuid
    import db.bills as B
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    r = H.try_pay_from_text(uid, "paguei luz 0,001")

    assert r and "maior que zero" in r, r
    assert "0,00 lançado" not in r
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "pending" and conta["paid_amount"] is None


def test_controle_valor_normal_pelo_texto_ainda_paga():
    """Controle negativo do teste acima: a guarda não pode recusar o caso bom."""
    import uuid
    import db.bills as B
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    r = H.try_pay_from_text(uid, "paguei luz 132,50")

    assert "Conta paga" in r, r
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "paid" and float(conta["paid_amount"]) == 132.5


def test_centavo_invisivel_pela_tool_da_ia_nao_paga():
    import uuid
    import db.bills as B
    from core.services.ai_chat.tools.bills import _pay_bill_execute

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    r = _pay_bill_execute(uid, {"name": "Luz", "amount": 0.001})

    assert "maior que zero" in r, r
    assert "VALOR_INVALIDO" not in r, "o texto cru da exceção vazou para o usuário"
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "pending" and conta["paid_amount"] is None


def test_controle_valor_normal_pela_tool_da_ia_ainda_paga():
    import uuid
    import db.bills as B
    from core.services.ai_chat.tools.bills import _pay_bill_execute

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    r = _pay_bill_execute(uid, {"name": "Luz", "amount": 132.5})

    assert "Conta paga" in r, r
    assert float(B.list_bills(uid, include_paid=True)[0]["paid_amount"]) == 132.5


def test_centavo_invisivel_direto_no_mark_bill_paid_nao_reserva():
    """O quarto caminho é a rota web (`/recurring-bills/.../pay`), que chama o
    `mark_bill_paid` direto e já traduz o `ValueError`."""
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)

    with pytest.raises(ValueError):
        B.mark_bill_paid(uid, int(conta["id"]), 0.001)

    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending" and depois["paid_amount"] is None
    assert db.list_launches(uid, limit=5) == []


def test_valor_gigante_finito_pela_conversa_paga_como_na_main(ia_espia):
    """C3: o teto de R$ 1 bi transformava isto em "erro interno" e a conta
    ficava pendente. Na `main` paga; tem que continuar pagando."""
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    r = _diga(uid, "paguei luz 2000000000")

    assert "Conta paga" in r, r
    assert "erro interno" not in r.lower()
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "paid" and float(conta["paid_amount"]) == 2_000_000_000.0


# ── C2: o botão "✅ Já paguei" também disputa a linha única de pendências ────

def _toca_ja_paguei(monkeypatch, uid, bill_id):
    """Roda o `process_message` real com o clique do botão do lembrete."""
    import adapters.whatsapp.wa_runtime as wr
    from adapters.whatsapp.wa_parse import InboundMessage

    respostas: list[str] = []
    monkeypatch.setattr(wr, "get_or_create_canonical_user", lambda p, e: uid)
    monkeypatch.setattr(wr, "attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "already_linked", "user_id": uid})
    monkeypatch.setattr(wr, "log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr(wr, "_send_reply", lambda to, body: respostas.append(body))
    wr.process_message(InboundMessage(
        wa_id="5511999998888", text="", timestamp="1", attachments=[],
        raw={"id": f"wamid.{bill_id}", "type": "interactive",
             "interactive": {"type": "button_reply",
                             "button_reply": {"id": f"bill_paid:{bill_id}"}}},
    ))
    return respostas


def _manda_texto_no_wa(monkeypatch, uid, texto):
    """Mesma porta, mensagem de texto (a resposta com o valor)."""
    import adapters.whatsapp.wa_runtime as wr
    from adapters.whatsapp.wa_parse import InboundMessage

    respostas: list[str] = []
    monkeypatch.setattr(wr, "get_or_create_canonical_user", lambda p, e: uid)
    monkeypatch.setattr(wr, "attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "already_linked", "user_id": uid})
    monkeypatch.setattr(wr, "log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr(wr, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wr, "_seen_recent", lambda message_id: False)
    monkeypatch.setattr(wr, "_send_reply", lambda to, body: respostas.append(body))
    monkeypatch.setattr(wr, "_send_reply_with_optional_buttons",
                        lambda to, body, user_id=None: respostas.append(body))
    wr.process_message(InboundMessage(
        wa_id="5511999998888", text=texto, timestamp="2", attachments=[],
        raw={"id": f"wamid.txt.{texto}", "type": "text"},
    ))
    return respostas


def test_botao_ja_paguei_nao_destroi_pergunta_viva(monkeypatch):
    """C2: `set_pending_action` incondicional apagava QUALQUER pergunta.

    Aqui a vítima é uma `clarification` com o valor que o usuário já digitou —
    perdê-la perde o dinheiro dele. O botão cede e pede que a pergunta viva
    seja terminada primeiro — a forma completa não funciona nesse estado
    (achado do Codex; ver `pergunta_de_valor_sem_contexto`).
    """
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    db.set_pending_action(uid, "clarification", {"valor": 77.9})

    respostas = _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "clarification", atual
    assert atual.get("payload") == {"valor": 77.9}, "o valor já digitado sumiu"
    corpo = respostas[-1] if respostas else ""
    assert "outra pergunta minha" in corpo, respostas
    assert "paguei luz" not in corpo.lower(), corpo


def test_controle_botao_ja_paguei_em_linha_livre_guarda_a_pergunta(monkeypatch):
    """Controle negativo: sem pergunta viva, o botão continua guardando."""
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)

    respostas = _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "bill_pay_amount", atual
    assert atual["payload"]["bill_id"] == int(conta["id"])
    assert "É só mandar o valor" in respostas[-1], respostas


def test_botao_ja_paguei_desaloja_oferta_de_conveniencia(monkeypatch):
    """A sequência comum: lançou algo (deixa a oferta de recategorizar) e toca
    o botão do lembrete. Oferta cede para pergunta."""
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    db.set_pending_action(uid, "recategorize_launch_offer", {"launch_id": 9})

    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_pay_amount"


def test_segundo_botao_ja_paguei_substitui_a_propria_pergunta(monkeypatch):
    """Dois lembretes na tela: toca o de Luz e depois o de Água.

    A mesma pergunta de novo não é disputa — a primeira já morreu na tela. Sem
    esta regra, o `claim` recusaria e a Água só seria pagável por texto até a
    pendência da Luz expirar (30 min).
    """
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    luz = _monta_conta_variavel(uid, "Luz")
    _monta_conta_variavel(uid, "Agua")
    agua = [b for b in db.bills.list_bills(uid, include_paid=False) if b["name"] == "Agua"][0]

    _toca_ja_paguei(monkeypatch, uid, int(luz["id"]))
    _toca_ja_paguei(monkeypatch, uid, int(agua["id"]))

    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "bill_pay_amount"
    assert atual["payload"]["bill_id"] == int(agua["id"]), atual


def test_centavo_invisivel_pelo_botao_do_whatsapp_nao_paga(monkeypatch):
    """Quarto caminho do C1: o valor digitado depois do botão."""
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    respostas = _manda_texto_no_wa(monkeypatch, uid, "0,001")

    assert "Não peguei o valor" in respostas[-1], respostas
    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending" and depois["paid_amount"] is None
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_pay_amount", \
        "a pergunta tem que continuar de pé para o usuário responder de novo"


def test_controle_valor_normal_pelo_botao_do_whatsapp_paga(monkeypatch):
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    respostas = _manda_texto_no_wa(monkeypatch, uid, "132,50")

    assert "Conta paga" in respostas[-1], respostas
    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "paid" and float(depois["paid_amount"]) == 132.5


# ── C3: as DUAS portas da mesma pergunta, no mesmo usuário ──────────────────
# A classe que nenhum teste do arquivo cobria: cada porta era testada sozinha
# (texto com texto, botão com botão). O bug morava exatamente no encadeamento
# das duas.

def test_botao_de_uma_conta_substitui_a_pergunta_de_texto_de_outra(monkeypatch):
    """"paguei a luz" → toca "✅ Já paguei" na ÁGUA → "132,50" paga a ÁGUA.

    A última pergunta é a que está na tela do usuário. Antes, o botão perdia o
    `claim` (tipos diferentes: `bill_amount_expected` × `bill_pay_amount`), a
    pergunta da Luz continuava armada e comia o número: pagava a LUZ, a conta
    errada, com a Água ainda pendente.
    """
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid, "Luz")
    _monta_conta_variavel(uid, "Agua")
    agua = [b for b in B.list_bills(uid, include_paid=False) if b["name"] == "Agua"][0]

    _manda_texto_no_wa(monkeypatch, uid, "paguei a luz")
    _toca_ja_paguei(monkeypatch, uid, int(agua["id"]))
    respostas = _manda_texto_no_wa(monkeypatch, uid, "132,50")

    estado = {b["name"]: (b["status"], float(b.get("paid_amount") or 0))
              for b in B.list_bills(uid, include_paid=True)}
    assert "Agua" in respostas[-1], respostas
    assert estado["Agua"] == ("paid", 132.5), estado
    assert estado["Luz"][0] == "pending", estado


def test_pergunta_de_texto_depois_do_botao_nao_muda_a_conta(monkeypatch):
    """A ordem inversa: o consumidor do botão intercepta o "paguei a luz"
    (não é número → re-pergunta) e a Água continua sendo a conta em jogo."""
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid, "Luz")
    _monta_conta_variavel(uid, "Agua")
    agua = [b for b in B.list_bills(uid, include_paid=False) if b["name"] == "Agua"][0]

    _toca_ja_paguei(monkeypatch, uid, int(agua["id"]))
    _manda_texto_no_wa(monkeypatch, uid, "paguei a luz")
    _manda_texto_no_wa(monkeypatch, uid, "132,50")

    estado = {b["name"]: (b["status"], float(b.get("paid_amount") or 0))
              for b in B.list_bills(uid, include_paid=True)}
    assert estado["Agua"] == ("paid", 132.5), estado
    assert estado["Luz"][0] == "pending", estado


# ── C4: ponto final não multiplica o valor por cem ──────────────────────────

@pytest.mark.parametrize("resposta,esperado", [
    ("132,50.", 132.5),
    ("0,50.", 0.5),
    ("9,99.", 9.99),
    ("R$ 132,50.", 132.5),
    ("132,50!", 132.5),
])
def test_ponto_final_na_resposta_de_texto_nao_paga_cem_vezes(monkeypatch, resposta, esperado):
    """"132,50." tem vírgula E ponto: o `parse_money` lia a vírgula como milhar
    e devolvia 13250.0. O `_VALOR_RE` aceita a pontuação final de propósito, então
    quem limpa é o `resolve_bill_amount`."""
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    _manda_texto_no_wa(monkeypatch, uid, "paguei a luz")

    _manda_texto_no_wa(monkeypatch, uid, resposta)

    conta = [b for b in B.list_bills(uid, include_paid=True) if b["name"] == "Luz"][0]
    assert (conta["status"], float(conta["paid_amount"] or 0)) == ("paid", esperado)


@pytest.mark.parametrize("resposta,esperado", [
    ("132,50.", 132.5),
    ("0,50.", 0.5),
])
def test_ponto_final_na_resposta_do_botao_nao_paga_cem_vezes(monkeypatch, resposta, esperado):
    """Mesma pergunta, outra porta: o consumidor do botão tinha o mesmo furo."""
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    _manda_texto_no_wa(monkeypatch, uid, resposta)

    depois = [b for b in B.list_bills(uid, include_paid=True) if b["name"] == "Luz"][0]
    assert (depois["status"], float(depois["paid_amount"] or 0)) == ("paid", esperado)


# ── Milhar malformado: "1.23.456" pagava R$ 123.456,00 ──────────────────────
# O `parse_money` apaga TODOS os pontos quando o último grupo tem 3 dígitos, e
# o `_VALOR_RE` aceitava qualquer arranjo de pontos. Erro de digitação logo
# depois de "manda só o número" virava conta paga com valor inflado, calado.

_MALFORMADOS = ["1.23.456", "1.2.345", "1.23.456,00"]
# Os legítimos importam mais: recusar entrada válida é pior que o bug.
_LEGITIMOS = [("1.200", 1200.0), ("132.50", 132.5), ("132,50", 132.5),
              ("1.132,50", 1132.5), ("12.345", 12345.0),
              ("1.234.567", 1234567.0)]


@pytest.mark.parametrize("resposta", _MALFORMADOS)
def test_milhar_malformado_pelo_texto_nao_paga(monkeypatch, resposta):
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    _manda_texto_no_wa(monkeypatch, uid, "paguei a luz")

    respostas = _manda_texto_no_wa(monkeypatch, uid, resposta)

    assert "Não entendi o valor" in respostas[-1], respostas
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "pending" and conta["paid_amount"] is None
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected", \
        "a pergunta tem que continuar viva para o usuário redigitar"


@pytest.mark.parametrize("resposta,esperado", _LEGITIMOS)
def test_controle_milhar_legitimo_pelo_texto_paga(monkeypatch, resposta, esperado):
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    _manda_texto_no_wa(monkeypatch, uid, "paguei a luz")

    _manda_texto_no_wa(monkeypatch, uid, resposta)

    conta = B.list_bills(uid, include_paid=True)[0]
    assert (conta["status"], float(conta["paid_amount"] or 0)) == ("paid", esperado)


@pytest.mark.parametrize("resposta", _MALFORMADOS)
def test_milhar_malformado_pelo_botao_nao_paga(monkeypatch, resposta):
    """Mesma pergunta, outra porta: o consumidor do botão tinha o mesmo furo."""
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    respostas = _manda_texto_no_wa(monkeypatch, uid, resposta)

    assert "Não peguei o valor" in respostas[-1], respostas
    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending" and depois["paid_amount"] is None
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_pay_amount", \
        "a pergunta tem que continuar viva para o usuário redigitar"


@pytest.mark.parametrize("resposta,esperado", _LEGITIMOS)
def test_controle_milhar_legitimo_pelo_botao_paga(monkeypatch, resposta, esperado):
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    _manda_texto_no_wa(monkeypatch, uid, resposta)

    depois = B.list_bills(uid, include_paid=True)[0]
    assert (depois["status"], float(depois["paid_amount"] or 0)) == ("paid", esperado)


@pytest.mark.parametrize("resposta,esperado", [
    ("1.23,45", 123.45),
    ("1.2,34", 12.34),
    ("12.34,56", 1234.56),
])
def test_ponto_mal_agrupado_com_virgula_paga_o_que_a_pessoa_quis(resposta, esperado):
    """Decisão de produto, apontada na revisão do #133 e mantida de propósito.

    O critério não é "o usuário digitou certo?", é "o erro dele vira dinheiro
    errado?". Aqui não vira: apagar o ponto fora do lugar devolve o valor que a
    pessoa parecia querer. Recusar seria pedir para redigitar algo já entendido.

    Contraste com `1.23.456`, que é recusado: lá a leitura muda de ordem de
    grandeza (123.456,00 quando o provável era 1.234,56).
    """
    from core.handlers.bills import agrupamento_de_milhar_ok, limpa_pontuacao_final
    from utils_text import parse_money

    limpo = limpa_pontuacao_final(resposta)
    assert agrupamento_de_milhar_ok(limpo), f"{resposta} deveria ser aceito"
    assert parse_money(limpo) == esperado


def test_claim_perdido_nao_anuncia_comando_que_paga_o_lancamento_errado(ia_espia):
    """Achado do Codex no #133: o texto degradado vendia um comando perigoso.

    Quando o `claim` perde a linha para uma `clarification` que continua de pé,
    o texto antigo dizia "Manda assim: *paguei luz 132,50*". Esse comando NÃO
    chega no `try_pay_from_text` neste estado: `route()` resolve a
    `clarification` antes, e o 132,50 vira o valor do lançamento VELHO — a
    conta fica sem pagar e o dinheiro entra na descrição errada.

    A segunda metade do teste é a prova de que o perigo é real (registra o
    lançamento errado); a primeira é a correção (o texto não sugere mais isso).
    """
    import uuid
    import db
    import db.bills as B
    from core.handlers.bills import pergunta_de_valor_sem_contexto

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    db.set_pending_action(uid, "clarification", {
        "intent": "launches.add",
        "entities": {"tipo": "despesa"},
        "orig_text": "gastei no mercado",
        "question": "Qual foi o valor?",
    })

    texto = pergunta_de_valor_sem_contexto(uid, "Luz")

    assert "paguei luz" not in texto.lower(), texto
    assert "132,50" not in texto, texto
    assert "Qual foi o valor?" in texto, texto

    # Por que não pode sugerir: mandando a forma completa neste estado, o
    # dinheiro vai para o lançamento velho e a conta continua pendente.
    _diga(uid, "paguei luz 132,50")

    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "pending", conta
    errado = [l for l in db.list_launches(uid, limit=10)
              if abs(float(l["valor"])) == 132.5]
    assert errado, "o cenário do Codex parou de reproduzir; reveja o teste"


def test_valor_invalido_continua_sugerindo_a_forma_completa(ia_espia):
    """Controle positivo: onde a forma completa FUNCIONA, ela segue sugerida.

    O `VALOR_INVALIDO` ("paguei luz 0,001") não é estado de claim perdido —
    chegar nele já prova que nenhuma pendência engoliu a mensagem. Medido
    abaixo: a mesma frase sugerida paga a conta no turno seguinte.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert db.get_pending_action(uid) is None

    aviso = _diga(uid, "paguei luz 0,001")
    assert "maior que zero" in aviso, aviso
    assert "*paguei luz 132,50*" in aviso, aviso

    resposta = _diga(uid, "paguei luz 132,50")

    assert "Conta paga" in resposta, resposta
    conta = B.list_bills(uid, include_paid=True)[0]
    assert (conta["status"], float(conta["paid_amount"])) == ("paid", 132.5)


def test_botao_ja_paguei_com_pergunta_viva_tambem_nao_anuncia_o_comando(monkeypatch):
    """O mesmo texto, pela outra porta (wa_runtime.py) — a classe, não a instância.

    Aqui o claim perde SEM corrida: o botão não passa por `route()`, então a
    `clarification` continua de pé quando o clique chega.
    """
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    db.set_pending_action(uid, "clarification", {
        "intent": "launches.add",
        "entities": {"tipo": "despesa"},
        "orig_text": "gastei no mercado",
        "question": "Qual foi o valor?",
    })

    respostas = _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    assert respostas, respostas
    corpo = respostas[-1]
    assert "paguei luz" not in corpo.lower(), corpo
    assert "132,50" not in corpo, corpo
    assert "Qual foi o valor?" in corpo, corpo
    assert (db.get_pending_action(uid) or {}).get("action_type") == "clarification"


# ── devolução da pergunta quando o pagamento estoura (porta do BOTÃO) ────────
# A porta de texto (`resolve_bill_amount`) já devolve desde o PR #133; esta é a
# outra porta da MESMA pergunta e prometia "Tente em instantes" sem ter para
# onde voltar — a pendência tinha sido reivindicada e o próximo número não
# pagaria nada.

def test_botao_devolve_pergunta_quando_o_pagamento_estoura(monkeypatch):
    """Negativo: sem o `with` em wa_runtime.py, a pendência some e isto fica vermelho."""
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))
    payload = (db.get_pending_action(uid) or {}).get("payload")

    def _estoura(*a, **k):
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(B, "mark_bill_paid", _estoura)
    respostas = _manda_texto_no_wa(monkeypatch, uid, "132,50")

    assert "Tente em instantes" in respostas[-1], respostas
    volta = db.get_pending_action(uid) or {}
    assert volta.get("action_type") == "bill_pay_amount", (
        f"a pergunta não voltou — ficou {volta.get('action_type')!r}; "
        "'Tente em instantes' vira mentira")
    assert volta.get("payload") == payload, volta
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"


def test_botao_devolucao_nao_atropela_pergunta_mais_nova(monkeypatch):
    """Corrida: a devolução é condicional, não upsert."""
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    def _estoura(*a, **k):
        db.set_pending_action(uid, "clarification", {"valor": 77.9})
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(B, "mark_bill_paid", _estoura)
    _manda_texto_no_wa(monkeypatch, uid, "132,50")

    volta = db.get_pending_action(uid) or {}
    assert volta.get("action_type") == "clarification", (
        f"a devolução atropelou a pergunta mais nova — ficou {volta.get('action_type')!r}")


def test_commit_ambiguo_do_pagamento_nao_rearma_a_pergunta(monkeypatch):
    """A janela ambígua do P1 do Codex, no caminho da conta.

    `mark_bill_paid` reserva a conta, cria o lançamento que debita e só então
    liga o `launch_id`. Se a conexão cai ENQUANTO o Postgres confirma o COMMIT
    do lançamento, o débito passa e a chamada levanta — e o `except` de
    `mark_bill_paid` devolve a conta para `pending`, achando que nada gravou.

    Medido (`db.connection.commits_ambiguos` sobe 1): saldo 1000 → 900 com a
    conta de volta em `pending`; responder o valor de novo leva a 800. Débito
    dobrado, sem retentativa. Por isso a devolução da pergunta não pode
    acontecer aqui.

    Negativo por causa: com o `if commits_ambiguos() == antes` fora de
    `db/pending.py`, a pendência volta e este caso fica vermelho.
    """
    import contextlib
    import datetime
    import uuid
    import psycopg
    import db
    import db.accounts
    import db.bills
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    db.add_launch_and_update_balance(uid, "receita", 1000.0, None, "seed")
    bill = db.bills.create_boleto(uid, "Luz", 100.0, datetime.date.today(),
                                  category="moradia")
    payload = {"bill_id": int(bill["id"]), "bill_name": "Luz"}
    db.set_pending_action(uid, "bill_amount_expected", payload)
    pending = db.get_pending_action(uid)

    armado = {"on": False}
    commit_real = psycopg.Connection.commit

    def commit_e_perde_a_conexao(self):
        commit_real(self)  # o Postgres CONFIRMA o débito
        if armado["on"]:
            armado["on"] = False
            raise psycopg.OperationalError("connection lost while committing")

    monkeypatch.setattr(psycopg.Connection, "commit", commit_e_perde_a_conexao)

    # Arma só a transação do lançamento (a que move o dinheiro): a reserva da
    # conta e a devolução compensatória usam outros `get_conn`, de db/bills.py.
    gc_real = db.accounts.get_conn

    @contextlib.contextmanager
    def gc_armado():
        with gc_real() as conn:
            armado["on"] = True
            try:
                yield conn
            finally:
                armado["on"] = False

    monkeypatch.setattr(db.accounts, "get_conn", gc_armado)

    with pytest.raises(psycopg.OperationalError):
        H.resolve_bill_amount(uid, "100", pending)

    assert float(db.get_balance(uid)) == 900.0, (
        "o commit não passou — a janela ambígua não foi simulada")
    assert db.get_pending_action(uid) is None, (
        "pergunta re-armada com o débito já confirmado — responder o valor de "
        "novo debitaria a conta duas vezes")
