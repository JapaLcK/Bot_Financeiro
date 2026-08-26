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

    Os três últimos entraram nesta rodada: quando a porta 1 passou a aceitar
    "tem um número dentro" (a aceitação frouxa das portas 2/3/4), medido,
    "codigo 8888 valor 132" pagava R$ 8.888,00, "no dia 20 foram 450" pagava
    R$ 20,00 e "dia 12/05 132" pagava R$ 12,00. Esta porta é a estrita: a
    mensagem INTEIRA tem que ser o valor.
    """
    import uuid
    import db
    import db.bills as B
    from core.handlers import bills as H

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    for comando in ("132 no mercado", "gastei 132 no mercado",
                    "apaga o gasto 132", "quanto gastei em 132",
                    "codigo 8888 valor 132", "no dia 20 foram 450",
                    "dia 12/05 132"):
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

    # A recusa passou a ser a mesma da porta de texto ("maior que zero", em vez
    # de "Não peguei o valor"): as duas portas dividem o `valor_perigoso`,
    # e "0,001" arredonda para 0,00 — dizer que o valor precisa ser maior que
    # zero é mais exato que dizer que não foi entendido.
    assert "maior que zero" in respostas[-1], respostas
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
    e devolvia 13250.0. Quem limpa é o `limpa_pontuacao_final`, no
    `limpa_pontuacao_final` — quem escreve "132,50." está respondendo."""
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
# nada checava o arranjo dos pontos. Erro de digitação logo
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
    from utils_text import agrupamento_de_milhar_ok, limpa_pontuacao_final
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

    A correção (o texto não sugere mais isso) continua valendo, e a enumeração
    do #133 continua inteira: a escotilha de abandono desta rodada só larga a
    pergunta quando a resposta não fala de valor nenhum, e "paguei luz 132,50"
    TEM valor. As 8 pendências seguem engolindo a forma completa.
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

    # E o motivo do texto degradado continua verdadeiro: a `clarification`
    # engole a forma completa, a conta NÃO é paga e o 132,50 vira o lançamento
    # velho. É por isso que este texto não pode anunciar "paguei luz 132,50".
    _diga(uid, "paguei luz 132,50")

    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"
    mercado = [l for l in db.list_launches(uid, limit=10)
               if "mercado" in (l.get("alvo") or l.get("nota") or "").lower()]
    assert mercado, "o 132,50 tinha que ter virado o lançamento do mercado"
    assert abs(float(mercado[0]["valor"])) == 132.5, mercado[0]


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



# ---------------------------------------------------------------------------
# PR "porta genérica": as QUATRO portas da MESMA pergunta de valor passaram a
# dividir o filtro de DANO (`utils_text.valor_perigoso`) e o passo 1
# (`core.intent_router.abandona_pergunta_de_valor`, onde as quatro estão
# numeradas). Cada porta MANTÉM a sua aceitação — a 1 exige a mensagem inteira,
# as outras três usam o `_extract_valor`/`parse_money`.
#
# T7/T8 ficam aqui (e não no test_full_handler_smoke) porque os helpers da
# conta variável e da porta do botão já moram neste arquivo.
# ---------------------------------------------------------------------------

def test_porta_das_contas_continua_pagando_depois_da_extracao(ia_espia):
    """T7 — controle positivo da porta 1: a extração não mudou nada aqui."""
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)

    pergunta = _diga(uid, "paguei a luz")
    assert "variável" in pergunta or "variavel" in pergunta, pergunta

    resp = _diga(uid, "132")

    assert "Conta paga" in resp and "Luz" in resp, resp
    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "paid" and float(conta["paid_amount"]) == 132.0, conta


def test_negativo_pelo_botao_nao_paga(monkeypatch):
    """T8 — porta 4: `parse_money("-10") == 10.0` pagava R$ 10,00."""
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    respostas = _manda_texto_no_wa(monkeypatch, uid, "-10")

    assert "maior que zero" in respostas[-1], respostas
    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending" and depois["paid_amount"] is None, depois
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_pay_amount", \
        "a pergunta tem que continuar de pé para o usuário responder de novo"


def test_clarification_com_payload_torto_nao_estoura():
    """`payload` não-dict virava AttributeError.

    Nenhum produtor grava isso hoje; o custo do guard é uma linha e o efeito
    seria "erro interno" em loop, com a pendência nunca sendo limpa.
    """
    from core.intent_router import _clarification_abandonada

    assert _clarification_abandonada({"payload": "x"}, "saldo") is False
    assert _clarification_abandonada({}, "saldo") is False


# ---------------------------------------------------------------------------
# O filtro de DANO, medido direto. Ele NÃO decide aceitação — recebe o texto e
# o valor que a porta já aceitou e responde só "isto vira dinheiro errado?".
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entrada,motivo", [
    ("-10", "nao_positivo"),
    ("\u221210", "nao_positivo"),      # U+2212, o traço do teclado do iOS
    ("\u201310", "nao_positivo"),      # en dash do autocorretor
    ("\u201010", "nao_positivo"),      # U+2010, o hífen tipográfico
    ("\u201110", "nao_positivo"),      # hífen não-quebrável de texto colado
    ("\u201410", "nao_positivo"),      # em dash
    ("menos 10", "nao_positivo"),   # o Whisper escreve assim no áudio
    ("10-", "nao_positivo"),
    ("(10)", "nao_positivo"),       # negativo contábil
    ("0", "nao_positivo"),
    ("0,001", "nao_positivo"),      # arredonda para R$ 0,00
    ("1" * 400, "nao_positivo"),    # parse_money devolve inf
    ("132 50", "nao_entendi"),      # 13.250 se passasse
    ("paguei 132 50", "nao_entendi"),  # o dano não some com uma palavra na frente
    ("1.23.456", "nao_entendi"),    # 123.456 se passasse
])
def test_contrato_do_valor_perigoso_recusa(entrada, motivo):
    """Fonte única das quatro portas, do lado que RECUSA.

    O sinal negativo não é um caractere: as SEIS grafias de traço (o ASCII mais
    as cinco do `_TRACOS`) e o "menos 10" falado chegam todos ao `parse_money`
    como positivos.

    O "menos" falado só é visto quando há bloco de dígitos: `menos cinquenta`
    NÃO está aqui porque `valor_perigoso("menos cinquenta", 50.0)` devolve
    `None` — teto documentado no próprio `valor_perigoso`.
    """
    from utils_text import parse_money, limpa_pontuacao_final, valor_perigoso

    valor = parse_money(limpa_pontuacao_final(entrada))
    assert valor_perigoso(entrada, valor) == motivo, (entrada, valor)


# Cada forma abaixo é aceita pelo `_extract_valor` (portas 2 e 3) e/ou pelo
# `parse_money` (porta 4) na `main`. Lista medida a dedo — NÃO é uma
# propriedade geral, é uma tabela de casos conhecidos.
FORMAS_QUE_A_MAIN_ACEITA = [
    ("132 mil", 132000.0), ("2,5 mil", 2500.0), ("10 mil", 10000.0),
    ("3 milhoes", 3000000.0), ("132 mil reais", 132000.0), ("132k", 132.0),
    ("paguei 132", 132.0), ("gastei 132", 132.0), ("foi 132 mil", 132000.0),
    ("ficou em 132", 132.0), ("deu 132 no total", 132.0),
    ("foi uns 132 no total", 132.0), ("total 132", 132.0),
    ("132 no boleto", 132.0), ("cinquenta", 50.0),
    ("132 no cartao", 132.0), ("investi 80", 80.0),
    # C2: o espaço de MILHAR. Recusar todo espaço encolhia a aceitação nestes
    # cinco, que a `main` acerta.
    ("1 500", 1500.0), ("1 500,00", 1500.0), ("R$ 1 500", 1500.0),
    ("12 345", 12345.0), ("1 000 000", 1000000.0),
    ("mais ou menos 10", 10.0),   # "por volta de 10", não "menos 10"
]


@pytest.mark.parametrize("entrada,esperado", FORMAS_QUE_A_MAIN_ACEITA)
def test_vinte_e_tres_formas_medidas_da_main_nao_sao_recusadas(entrada, esperado):
    """As 23 formas MEDIDAS que a `main` aceita, uma a uma.

    O nome diz o que o corpo faz: é uma tabela, não uma propriedade geral. A
    versão anterior deste teste se chamava `..._nao_encolhe_em_relacao_a_main`,
    prometia a propriedade e checava 15 strings — e o contraexemplo ("1 500")
    apareceu na primeira tentativa de quem procurou. Cobertura de verdade das
    portas inteiras está nas duas colunas por `handle_incoming`.
    """
    from parsers import _extract_valor
    from utils_text import parse_money, limpa_pontuacao_final, valor_perigoso

    assert _extract_valor(entrada) == esperado, "premissa: a `main` aceita isto"
    assert valor_perigoso(entrada, parse_money(limpa_pontuacao_final(entrada))) is None


# ---------------------------------------------------------------------------
# Porta 4 (botão "✅ Já paguei"). A `main` (`wa_runtime.py:921-922`) usava
# `parse_money` SEM exigir forma, e é essa aceitação que continua valendo aqui.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resposta,esperado", [
    ("paguei 132", 132.0),
    ("132 da luz", 132.0),
    ("veio 132,50 esse mes", 132.5),
    ("R$ 132,50 total", 132.5),
    ("132 na conta", 132.0),
    ("132 reais da luz", 132.0),
    ("132,50 no debito", 132.5),
    ("gastei 132", 132.0),
    ("132 esse mes", 132.0),
    ("o valor foi 132", 132.0),
    ("1 500", 1500.0),      # C2: milhar por espaço
    ("12 345", 12345.0),
])
def test_botao_ja_paguei_aceita_o_valor_dentro_da_frase(monkeypatch, resposta, esperado):
    """Controle POSITIVO da porta 4: recusar entrada válida é pior que o bug."""
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    respostas = _manda_texto_no_wa(monkeypatch, uid, resposta)

    assert "Conta paga" in respostas[-1], f"{resposta!r} foi recusado: {respostas}"
    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "paid", depois
    assert float(depois["paid_amount"]) == esperado, depois


@pytest.mark.parametrize("resposta,fragmento", [
    ("-10", "maior que zero"),
    ("\u221210", "maior que zero"),          # U+2212
    ("\u201310", "maior que zero"),          # en dash
    ("menos 10", "maior que zero"),
    ("0", "maior que zero"),
    ("0,001", "maior que zero"),
    ("1" * 400, "maior que zero"),        # parse_money devolve inf
    ("132 50", "Não peguei o valor"),     # 13.250,00 se passasse
    ("1.23.456", "Não peguei o valor"),   # 123.456,00 se passasse
])
def test_botao_ja_paguei_recusa_o_perigoso_com_pergunta_viva(monkeypatch, resposta, fragmento):
    """Controle NEGATIVO: afrouxar a FORMA não pode afrouxar o DANO."""
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    respostas = _manda_texto_no_wa(monkeypatch, uid, resposta)

    assert fragmento in respostas[-1], f"{resposta[:20]!r}: {respostas}"
    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending" and depois["paid_amount"] is None, depois
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_pay_amount", \
        "a pergunta tem que continuar de pé para o usuário responder de novo"


def test_escotilha_da_clarification_nao_apaga_pergunta_de_outra_tarefa(monkeypatch):
    """A escotilha de abandono da porta 2 usa CAS, não `clear_pending_action`.

    Mesma forma da porta 1: a linha de `pending_actions` é UMA por usuário,
    então entre ler a `clarification` e abandoná-la outra tarefa pode ter posto
    no lugar uma pergunta nova — que já apareceu na tela. A corrida é injetada
    no próprio predicado.
    """
    import uuid
    import db
    import core.intent_router as IR

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)

    assert "Quanto foi" in _diga(uid, "paguei a luz")
    assert (db.get_pending_action(uid) or {}).get("action_type") == "clarification"

    real = IR._clarification_abandonada
    nova = {"bill_id": 99, "name": "Internet"}

    def com_corrida(clarif, texto):
        decidiu = real(clarif, texto)
        if decidiu:
            # outra tarefa chegou primeiro e já perguntou outra coisa. É um
            # `bill_pay_amount` de propósito: o `route()` não o consome (quem
            # consome é o runtime do WhatsApp), então o que sobrar no fim é
            # efeito da escotilha e de mais nada.
            db.set_pending_action(uid, "bill_pay_amount", nova)
        return decidiu

    monkeypatch.setattr(IR, "_clarification_abandonada", com_corrida)

    _diga(uid, "saldo")

    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "bill_pay_amount", \
        f"a escotilha apagou a pergunta mais nova: {atual}"
    assert atual.get("payload") == nova, atual


# ---------------------------------------------------------------------------
# O passo 1: quem decide é o INTENT, e o conjunto é MUNDO FECHADO.
# ---------------------------------------------------------------------------

# Os oito comandos que sustentam o `ABANDONA`, com o intent medido ao lado.
COMANDOS_QUE_ABANDONAM = [
    ("saldo", "balance.check"),
    ("extrato", "launches.list"),
    ("meus gastos", "launches.list"),
    ("apagar 42", "launches.delete"),          # TEM valor
    ("quanto gastei em 132", "launches.spend_query"),   # TEM valor
    ("quanto gastei em maio", "launches.spend_query"),
    ("caixinhas", "pockets.list"),
    ("resumo do mes", "report.monthly"),
]

# Formas de valor que NUNCA podem abandonar uma pergunta viva.
VALORES_QUE_NAO_ABANDONAM = [
    "132", "132,50", "10 mil", "3 milhoes", "132 mil reais", "paguei 132",
    "gastei 132", "foi uns 132 no total", "total 132", "132 no boleto",
    "cinquenta", "uns 132", "2000000000", "132 reais", "50 pila", "5 conto",
    "132,50 reais", "132 todo mes", "1 500",
    # O BLOQUEANTE da rodada anterior: `credit.handle` 0.95. Com a blacklist
    # ele abandonava e apagava a fila inteira do multi-lançamento.
    "132 no cartao", "investi 80",
]


@pytest.mark.parametrize("comando,intent", COMANDOS_QUE_ABANDONAM)
def test_comandos_do_mundo_fechado_abandonam(comando, intent):
    """O conjunto é medido, e a medição não passa pelo LLM.

    `allow_ai=False` deixa só os tiers 1 e 2, que são regex e dicionário. Sem
    isso, "medir" o classificador dentro da suíte mede o stub de rede do
    `conftest`, não o sistema.
    """
    from core.intent_classifier import classify
    from core.intent_router import ABANDONA, abandona_pergunta_de_valor

    assert classify(comando, allow_ai=False).intent == intent, comando
    assert intent in ABANDONA
    assert abandona_pergunta_de_valor(comando) is True, comando


@pytest.mark.parametrize("forma", VALORES_QUE_NAO_ABANDONAM)
def test_vinte_e_uma_formas_de_valor_medidas_nao_abandonam(forma):
    """A outra metade do mundo fechado, e a que sangra quando erra.

    Como o irmão `test_vinte_e_tres_formas_medidas_da_main_nao_sao_recusadas`:
    é uma TABELA de 21 exemplos medidos, não a propriedade "nenhuma forma de
    valor abandona". A propriedade não é demonstrável por enumeração — o que a
    sustenta é o `ABANDONA` ser mundo fechado, e é o teste acima que prende
    isso.
    """
    from core.intent_router import abandona_pergunta_de_valor

    assert abandona_pergunta_de_valor(forma) is False, forma


def test_credit_handle_esta_fora_do_conjunto_por_medicao():
    """Registro do porquê, com os dois números que decidiram.

    "fatura" (1.0) merece abandonar; "132 no cartao" (0.95) é resposta legítima
    e não pode. Mesmo intent. O único corte por confiança que os separa
    (`== 1.0`) derruba junto "fatura do cartao" (0.95), então não é corte
    limpo — e o preço do erro é assimétrico: perder o abandono de "fatura"
    custa uma repetição; abandonar "132 no cartao" apaga a fila do usuário.

    Este teste fica vermelho no dia em que o classificador mudar essas
    confianças, que é quando a decisão precisa ser revista.
    """
    from core.intent_classifier import classify
    from core.intent_router import ABANDONA

    assert classify("fatura", allow_ai=False) .confidence == 1.0
    assert classify("132 no cartao", allow_ai=False).intent == "credit.handle"
    assert classify("132 no cartao", allow_ai=False).confidence == 0.95
    assert classify("fatura do cartao", allow_ai=False).confidence == 0.95
    assert "credit.handle" not in ABANDONA


def test_porta_1_nao_precisa_do_passo_1_alfabeto_inteiro_do_valor_re():
    """Por que a porta 1 NÃO chama o `abandona_pergunta_de_valor`.

    Ela tinha um parâmetro `outro_comando` justificado com "'apagar 0' casa o
    `_VALOR_RE` como zero" — falso: `apagar 0` não casa nem o `_VALOR_RE` nem o
    `_NUMERO_AMBIGUO_RE`. O parâmetro não tinha entrada alcançável e saiu.

    Este teste é a medição que sustenta a remoção, e ele fica vermelho no dia em
    que o `_VALOR_RE` for afrouxado ou um intent novo entrar no `ABANDONA`
    alcançando uma forma de valor. Enumera o alfabeto INTEIRO do `_VALOR_RE`:
    7 números × 9 unidades (vazio + as 8 do `_UNIDADE`) × 703 prefixos (vazio +
    26 do `_ENCHIMENTO` + os 676 pares) = 44.289 strings, todas casando o
    `fullmatch`. ~2,8 s.
    """
    import re
    from core.handlers.bills import _ENCHIMENTO, _UNIDADE, _VALOR_RE
    from core.intent_classifier import classify
    from core.intent_router import ABANDONA

    ench = re.findall(r"[a-z]+", _ENCHIMENTO)
    unis = [""] + [" " + u for u in re.findall(r"[a-z$]+", _UNIDADE)]
    nums = ["0", "42", "132", "132,50", "1.500", "R$ 132", "2000000000"]
    prefixos = [""] + [f"{a} " for a in ench] + [f"{a} {b} " for a in ench
                                                 for b in ench]
    assert (len(nums), len(unis), len(prefixos)) == (7, 9, 703)

    formas = [f"{px}{n}{u}" for n in nums for u in unis for px in prefixos]
    assert len(formas) == 44_289
    assert all(_VALOR_RE.fullmatch(f) for f in formas), "premissa: todas são forma de valor"

    caem = {f for f in formas if classify(f, allow_ai=False).intent in ABANDONA}
    assert caem == set(), sorted(caem)[:10]

    # O que a porta 1 de fato usa para abandonar é o portão de FORMA, herdado
    # da `main` — e é ele que pega o `apagar 0` do docstring antigo.
    assert not _VALOR_RE.fullmatch("apagar 0")


@pytest.mark.parametrize("resposta,esperado", [
    ("132", 132.0), ("132,50", 132.5), ("uns 132", 132.0),
    ("132 reais", 132.0), ("50 pila", 50.0), ("2000000000", 2_000_000_000.0),
])
def test_porta_da_conta_continua_pagando_pelo_handle_incoming(resposta, esperado):
    r"""Coluna 1 da porta 1, pela conversa inteira.

    O que esta porta aceita é a mensagem INTEIRA sendo o valor — a aceitação da
    `main`. Três grupos NÃO estão aqui, todos porque a `main` também não os
    aceita NESTA porta (o `_VALOR_RE` não os casa) e afrouxar para alcançá-los
    foi o que fez "codigo 8888 valor 132" pagar R$ 8.888,00:

    - `10 mil`, `cinquenta`, `132 no boleto` → abandonam a pergunta;
    - `1 500`, `12 345`, `1 000 000` → o `_VALOR_RE` não tem `\s` dentro do
      número, então caem no `_NUMERO_AMBIGUO_RE` e recebem "Não entendi o
      valor" com a pergunta viva. É o comportamento da `main`, e o teste logo
      abaixo prende isso. Nas portas 2/3/4, onde a `main` ACEITA os três, o
      filtro de dano deixa passar (é o conserto C2).
    """
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    _diga(uid, resposta)

    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "paid", (resposta, conta)
    assert float(conta["paid_amount"]) == esperado, (resposta, conta)


@pytest.mark.parametrize("resposta", ["1 500", "12 345", "1 000 000"])
def test_porta_da_conta_repergunta_no_milhar_com_espaco_como_na_main(resposta):
    r"""O `_VALOR_RE` da `main` não tem `\s` dentro do número.

    Não é regressão nem conserto: é a porta estrita fazendo o que sempre fez.
    Nas portas 2/3/4 os mesmos três passam — ver
    `test_botao_ja_paguei_aceita_o_valor_dentro_da_frase`.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    resp = _diga(uid, resposta)

    assert "Não entendi o valor" in resp, resp
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected"


@pytest.mark.parametrize("comando", [c for c, _ in COMANDOS_QUE_ABANDONAM])
def test_porta_1_portao_de_forma_da_main_abandona_comando(comando):
    """REGRESSÃO do comportamento HERDADO, não controle do passo 1.

    Quem abandona aqui é o `not _VALOR_RE.fullmatch(raw)`, que a `main` já
    tinha: medido, nenhum dos oito casa o `_VALOR_RE`. A porta 1 não consulta o
    `abandona_pergunta_de_valor` — este teste continua verde com o passo 1
    desligado, e é assim mesmo. O que ele prende é o portão de forma não ser
    afrouxado por engano (afrouxar para "tem um número dentro" foi o que fez
    "codigo 8888 valor 132" pagar R$ 8.888,00).

    O controle do passo 1 está nas portas 2 e 3
    (`tests/test_full_handler_smoke.py`), onde ele é a única coisa que separa
    "apagar 42" de um lançamento.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    _diga(uid, comando)

    conta = B.list_bills(uid, include_paid=True)[0]
    assert conta["status"] == "pending", (comando, conta)
    assert conta["paid_amount"] is None, (comando, conta)
    assert db.get_pending_action(uid) is None, \
        f"{comando!r} não abandonou a pergunta: {db.get_pending_action(uid)}"


@pytest.mark.parametrize("comando", ["saldo", "extrato", "apagar 42",
                                     "quanto gastei em 132"])
def test_botao_ja_paguei_abandona_comando(monkeypatch, comando):
    """Porta 4, que roda ANTES do `handle_incoming`.

    Aqui a `main` NUNCA abandonava: qualquer texto sem valor re-perguntava para
    sempre, e "apagar 42" pagava a conta com R$ 42,00.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    _manda_texto_no_wa(monkeypatch, uid, comando)

    depois = B.list_bills(uid, include_paid=True)[0]
    assert depois["status"] == "pending" and depois["paid_amount"] is None, (comando, depois)
    assert db.get_pending_action(uid) is None, \
        f"{comando!r} não abandonou a pergunta: {db.get_pending_action(uid)}"


def test_passo_1_nao_chama_o_llm(monkeypatch):
    """O `allow_ai=False` é medido, não prometido no comentário.

    "132" cai no tier 3; com `allow_ai=True` cada conta paga pela porta 4
    custaria uma chamada de LLM ANTES de qualquer coisa acontecer — e o oráculo
    do passo 1 voltaria a ser ilimitado.

    A segunda metade é o controle de que a primeira não é vazia: com o
    `allow_ai=True` o mesmo "132" CHEGA no tier 3.
    """
    import core.intent_classifier as C
    from core.intent_router import abandona_pergunta_de_valor

    chamou = []
    monkeypatch.setattr(C, "_classify_llm_call",
                        lambda *a, **k: chamou.append(1) or None)

    assert abandona_pergunta_de_valor("132") is False
    assert chamou == [], "o passo 1 chamou o tier 3 (LLM)"

    C.classify("132", allow_ai=True)
    assert chamou, "controle vazio: '132' não chega no tier 3 nem com allow_ai"


# --- Controles negativos: cada conserto desligado num caso VERDE. -----------

def test_controle_negativo_conjunto_vazio_deixa_o_comando_pagar(monkeypatch):
    """Desligar o `ABANDONA` tem que fazer "apagar 42" pagar a conta de novo.

    Injetado no caso verde `test_botao_ja_paguei_abandona_comando[apagar 42]`.
    """
    import uuid
    import core.intent_router as IR
    import db.bills as B

    monkeypatch.setattr(IR, "ABANDONA", set())

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    _manda_texto_no_wa(monkeypatch, uid, "apagar 42")

    paga = B.list_bills(uid, include_paid=True)[0]
    assert paga["status"] == "paid" and float(paga["paid_amount"]) == 42.0, \
        "sem o passo 1 o comando TINHA que pagar a conta (o bug que ele veio matar)"


def test_controle_negativo_credit_handle_no_conjunto_sequestra_o_cartao(monkeypatch):
    """O BLOQUEANTE, reproduzido: pôr `credit.handle` no conjunto perde a resposta.

    Injetado no caso verde `test_botao_ja_paguei_aceita_o_valor_dentro_da_frase`
    — só que com "132 no cartao", que é `credit.handle` 0.95.
    """
    import uuid
    import core.intent_router as IR
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))
    # Verde primeiro: com o conjunto de verdade, a resposta paga.
    _manda_texto_no_wa(monkeypatch, uid, "132 no cartao")
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "paid"

    monkeypatch.setattr(IR, "ABANDONA", IR.ABANDONA | {"credit.handle"})

    uid2 = int(uuid.uuid4().int % 1_000_000_000)
    conta2 = _monta_conta_variavel(uid2)
    _toca_ja_paguei(monkeypatch, uid2, int(conta2["id"]))
    _manda_texto_no_wa(monkeypatch, uid2, "132 no cartao")

    assert B.list_bills(uid2, include_paid=True)[0]["status"] == "pending"
    assert db.get_pending_action(uid2) is None, \
        "com credit.handle no conjunto a pergunta TINHA que ser largada"


def test_controle_negativo_sem_normalizar_o_traco_o_menos_unicode_paga(monkeypatch):
    """Desligar a normalização de traço tem que ressuscitar o pagamento de −10.

    Injetado no caso verde
    `test_botao_ja_paguei_recusa_o_perigoso_com_pergunta_viva[−10]`.
    """
    import uuid
    import utils_text as U
    import db.bills as B

    monkeypatch.setattr(U, "_TRACOS", {})   # str.translate aceita dict vazio

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    _manda_texto_no_wa(monkeypatch, uid, "\u221210")

    paga = B.list_bills(uid, include_paid=True)[0]
    assert paga["status"] == "paid" and float(paga["paid_amount"]) == 10.0, \
        "sem a normalização, −10 TINHA que pagar R$ 10,00"


def test_controle_negativo_espaco_ambiguo_sem_a_regra_dos_3_digitos(monkeypatch):
    """Recusar TODO espaço tem que quebrar o caso verde "1 500".

    Injetado em `test_botao_ja_paguei_aceita_o_valor_dentro_da_frase[1 500]`.
    """
    import uuid
    import utils_text as U
    import db.bills as B

    monkeypatch.setattr(U, "_espaco_ambiguo", lambda bloco: " " in bloco.strip())

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    respostas = _manda_texto_no_wa(monkeypatch, uid, "1 500")

    assert "Não peguei o valor" in respostas[-1], respostas
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"


def test_passo_1_da_porta_4_nao_apaga_pergunta_de_outra_tarefa(monkeypatch):
    """Mesma regra na porta 4: CAS, não `clear_pending_action`.

    Aqui o `pending_recat` foi lido linhas acima, no `process_message`; entre
    aquela leitura e o abandono cabe a pergunta de outra tarefa.
    """
    import uuid
    import adapters.whatsapp.wa_runtime as wr
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    # O botão de OUTRA conta chegou no meio — mesmo `action_type`, payload
    # diferente, que é o caso real e o que o CAS compara. Depois do abandono
    # o `pending_recat` local vira None, então este não é consumido no mesmo
    # turno: o que sobrar é efeito do passo 1 e de mais nada.
    nova = {"bill_id": 99, "name": "Internet"}
    real = wr.abandona_pergunta_de_valor

    def com_corrida(texto):
        decidiu = real(texto)
        if decidiu:
            db.set_pending_action(uid, "bill_pay_amount", nova)
        return decidiu

    monkeypatch.setattr(wr, "abandona_pergunta_de_valor", com_corrida)

    _manda_texto_no_wa(monkeypatch, uid, "saldo")

    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "bill_pay_amount", \
        f"o passo 1 apagou a pergunta mais nova: {atual}"
    assert atual.get("payload") == nova, atual
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# RODADA FINAL — os três consertos mecânicos.
# ---------------------------------------------------------------------------

# BLOQUEANTE 1: o `limpa_pontuacao_final` não chegava ao `valor_perigoso`.
#
# Os testes de ponto final acima (`C4`) só parametrizam "132,50." e "0,50." —
# as DUAS com vírgula. Sem vírgula o `agrupamento_de_milhar_ok` muda de ramo:
# "132." vira `["132", ""]`, o grupo vazio tem `len 0` ∉ (1,2,3) e a recusa
# saía onde a `main` pagava. Estas são as formas SEM vírgula.
PONTO_FINAL_SEM_VIRGULA = [("132.", 132.0), ("1.500.", 1500.0),
                           ("foi 132.", 132.0), ("paguei 132.  ", 132.0)]


@pytest.mark.parametrize("resposta,esperado", PONTO_FINAL_SEM_VIRGULA)
def test_porta_1_ponto_final_sem_virgula_paga_como_na_main(resposta, esperado):
    """Porta 1. Medido na `main`: os quatro pagam. No branch, recusavam."""
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    _diga(uid, resposta)

    conta = [b for b in B.list_bills(uid, include_paid=True) if b["name"] == "Luz"][0]
    assert (conta["status"], float(conta["paid_amount"] or 0)) == ("paid", esperado)


@pytest.mark.parametrize("resposta,esperado", PONTO_FINAL_SEM_VIRGULA)
def test_porta_4_ponto_final_sem_virgula_paga_como_na_main(monkeypatch, resposta, esperado):
    """Porta 4, o botão — mesmo furo, mesma cura."""
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    _manda_texto_no_wa(monkeypatch, uid, resposta)

    depois = [b for b in B.list_bills(uid, include_paid=True) if b["name"] == "Luz"][0]
    assert (depois["status"], float(depois["paid_amount"] or 0)) == ("paid", esperado)


@pytest.mark.parametrize("resposta,esperado", PONTO_FINAL_SEM_VIRGULA)
def test_porta_2_ponto_final_sem_virgula_registra(resposta, esperado):
    """Porta 2, a única que já limpava antes — aqui só para as quatro
    concordarem no MESMO texto, que é o ponto do PR."""
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    assert "Quanto foi" in _diga(uid, "paguei a luz")

    _diga(uid, resposta)

    lancs = db.list_launches(uid, limit=3)
    assert len(lancs) == 1 and abs(float(lancs[0]["valor"])) == esperado, lancs


# "paguei 132." fica de fora: ele NÃO casa o `_VALOR_RE` (o verbo não está no
# `_ENCHIMENTO`), abandona a pergunta antes do dano e é pago pelo
# `try_pay_from_text` no roteamento normal — a limpeza não participa. Os três
# abaixo são os que realmente atravessam o dano da porta 1.
@pytest.mark.parametrize("resposta,esperado",
                         [c for c in PONTO_FINAL_SEM_VIRGULA
                          if not c[0].startswith("paguei")])
def test_controle_negativo_sem_limpar_a_pontuacao_a_porta_1_recusa(
        monkeypatch, resposta, esperado):
    """Controle: desliga a limpeza NA PORTA 1 e o caso verde fica vermelho."""
    import uuid
    import db
    import db.bills as B
    import core.handlers.bills as BH

    monkeypatch.setattr(BH, "limpa_pontuacao_final", lambda s: s or "")

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    resp = _diga(uid, resposta)

    conta = [b for b in B.list_bills(uid, include_paid=True) if b["name"] == "Luz"][0]
    assert conta["status"] == "pending", (resposta, resp)
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected"


def test_controle_negativo_sem_limpar_a_pontuacao_a_porta_4_recusa(monkeypatch):
    """Mesmo controle na porta 4 — o `import` dela é local, então o alvo do
    monkeypatch é o `utils_text` (que a porta 1 NÃO enxerga: ela importa no
    topo do módulo)."""
    import uuid
    import utils_text
    import db.bills as B

    monkeypatch.setattr(utils_text, "limpa_pontuacao_final", lambda s: s or "")

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    resp = _manda_texto_no_wa(monkeypatch, uid, "132.")

    assert "Não peguei o valor" in resp[-1], resp
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"


# BLOQUEANTE 2: na porta 1 o DANO rodava antes do passo 1 e prendia comandos.
#
# Os comandos de `COMANDOS_QUE_ABANDONAM` não pegaram o defeito porque nenhum
# deles é PERIGOSO pelo `valor_perigoso` ("apagar 42" -> 42.0, limpo). Estes
# são os que o dano recusava ANTES de o abandono ter chance:
#   'apagar 0'                 zero            -> nao_positivo
#   'quanto gastei em 12 05'   espaço ambíguo  -> nao_entendi
#   'resumo do mes 08 2026'    espaço ambíguo  -> nao_entendi
#   'extrato 01-2026'          traço à direita -> nao_positivo
#   'relatorio 2026-08'        traço à direita -> nao_positivo
#   'apagar 1.23.456'          milhar torto    -> nao_entendi
COMANDOS_QUE_O_DANO_PRENDIA = [
    "apagar 0", "quanto gastei em 12 05", "resumo do mes 08 2026",
    "extrato 01-2026", "relatorio 2026-08", "apagar 1.23.456",
    "pix 11-99999-8888", "boleto 12-2026",
]


@pytest.mark.parametrize("comando", COMANDOS_QUE_O_DANO_PRENDIA)
def test_porta_1_portao_de_forma_vem_antes_do_dano(comando):
    """Preso = a pendência fica na linha e a repetição dá a mesma recusa.

    Medido na `main`: os oito abandonam. Numa rodada anterior deste branch o
    DANO rodava na frente e os oito viravam "Não entendi o valor da *Luz*" /
    "precisa ser maior que zero" com a pergunta de pé — só sairia por outro
    texto ou por timeout.

    Quem abandona é o portão de forma (`_VALOR_RE`), herdado da `main`:
    medido, nenhum dos oito casa `_VALOR_RE` nem `_NUMERO_AMBIGUO_RE`. O que
    este PR conserta é a ORDEM, e é o controle negativo logo abaixo que a mede
    — não este teste sozinho.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    _diga(uid, comando)

    conta = B.list_bills(uid, include_paid=True)[0]
    assert (conta["status"], conta["paid_amount"]) == ("pending", None), (comando, conta)
    assert db.get_pending_action(uid) is None, \
        f"{comando!r} ficou preso na pergunta: {db.get_pending_action(uid)}"


@pytest.mark.parametrize("comando", COMANDOS_QUE_O_DANO_PRENDIA)
def test_controle_negativo_dano_antes_do_abandono_prende_o_comando(monkeypatch, comando):
    """Controle: com o DANO de volta na frente, os oito ficam presos.

    A ordem antiga está reproduzida aqui de propósito — é o que a asserção
    acima precisa distinguir. Se este teste passar a ver abandono, a asserção
    de cima virou decoração.
    """
    import uuid
    import db
    import core.handlers.bills as BH

    real = BH.resolve_bill_amount

    def ordem_antiga(user_id, text, pending):
        raw = (text or "").strip().translate(BH._TRACOS)
        limpo = BH.limpa_pontuacao_final(raw)
        if BH.valor_perigoso(limpo, BH.parse_money(limpo)):
            return "recusa da ordem antiga"
        return real(user_id, text, pending)

    import core.handlers.bills
    monkeypatch.setattr(core.handlers.bills, "resolve_bill_amount", ordem_antiga)
    import core.intent_router as IR
    monkeypatch.setattr(IR.h_bills, "resolve_bill_amount", ordem_antiga)

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    resp = _diga(uid, comando)

    assert resp == "recusa da ordem antiga", (comando, resp)
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected", \
        "controle vazio: a ordem antiga não prendeu o comando"


@pytest.mark.parametrize("resposta,fragmento", [
    ("-10", "maior que zero"), ("−10", "maior que zero"),
    ("0", "maior que zero"), ("132 50", "Não entendi o valor"),
    ("1.23.456", "Não entendi o valor"),
])
def test_porta_1_com_o_abandono_na_frente_o_perigoso_ainda_recusa(resposta, fragmento):
    """A outra metade da inversão: `−10` (U+2212) classifica FORA do `ABANDONA`
    e não casa o `_VALOR_RE`, então sem a normalização de traço na FORMA ele
    voltaria a abandonar a pergunta em silêncio."""
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    resp = _diga(uid, resposta)

    assert fragmento in resp, (resposta, resp)
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"
    assert (db.get_pending_action(uid) or {}).get("action_type") == "bill_amount_expected", \
        f"{resposta!r} abandonou a pergunta em vez de recusar com ela viva"


# BLOQUEANTE 3: a quinta escrita de pendência era incondicional.

@pytest.mark.parametrize("resposta", ["-10", "132 50", "0", "0,001"])
def test_recusa_da_porta_2_nao_apaga_pergunta_de_outra_tarefa(monkeypatch, resposta):
    """O ramo `perigo` re-armava com `set_pending_action` (upsert incondicional).

    A corrida é injetada no `valor_perigoso`, que roda DEPOIS do
    `clear_pending_action` do topo do `_resolve_clarification` e ANTES do
    re-armamento — exatamente a janela onde a pergunta de outra tarefa cabe.
    """
    import uuid
    import db
    import core.intent_router as IR

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    assert "Quanto foi" in _diga(uid, "paguei a luz")

    nova = {"bill_id": 99, "name": "Internet"}
    real = IR.valor_perigoso

    def com_corrida(texto, valor):
        perigo = real(texto, valor)
        if perigo:
            db.set_pending_action(uid, "bill_pay_amount", nova)
        return perigo

    monkeypatch.setattr(IR, "valor_perigoso", com_corrida)

    _diga(uid, resposta)

    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "bill_pay_amount", \
        f"a recusa apagou a pergunta mais nova: {atual}"
    assert atual.get("payload") == nova, atual
    assert not db.list_launches(uid, limit=3), db.list_launches(uid, limit=3)


@pytest.mark.parametrize("resposta", ["-10", "132 50", "0", "0,001"])
def test_recusa_da_porta_2_sem_corrida_mantem_a_pergunta_viva(resposta):
    """A outra metade: sem corrida, a `clarification` TEM que voltar.

    É o que separa o primitivo certo do errado — o `clear_pending_action` do
    topo já apagou a linha, então um `advance_pending_action` (CAS sobre
    `old_payload`) não acharia nada para atualizar e a pergunta morreria aqui.
    """
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    assert "Quanto foi" in _diga(uid, "paguei a luz")

    resp = _diga(uid, resposta)

    assert "Quanto foi" in resp, (resposta, resp)
    assert (db.get_pending_action(uid) or {}).get("action_type") == "clarification", \
        f"{resposta!r} perdeu a pergunta: {db.get_pending_action(uid)}"


def test_controle_negativo_upsert_incondicional_atropela_a_pergunta_nova(monkeypatch):
    """Controle: com o `set_pending_action` de volta, a pergunta nova some."""
    import uuid
    import db
    import core.intent_router as IR

    monkeypatch.setattr(
        IR.db, "create_pending_action_if_absent",
        lambda uid_, tipo, payload, minutes=10: db.set_pending_action(
            uid_, tipo, payload, minutes) or True)

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    assert "Quanto foi" in _diga(uid, "paguei a luz")

    nova = {"bill_id": 99, "name": "Internet"}
    real = IR.valor_perigoso

    def com_corrida(texto, valor):
        perigo = real(texto, valor)
        if perigo:
            db.set_pending_action(uid, "bill_pay_amount", nova)
        return perigo

    monkeypatch.setattr(IR, "valor_perigoso", com_corrida)

    _diga(uid, "-10")

    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "clarification", \
        f"controle vazio: o upsert não atropelou a pergunta nova ({atual})"


def test_as_quatro_portas_nao_ganharam_escrita_incondicional():
    """A varredura da "sexta escrita", presa em teste em vez de em prosa.

    `set_pending_action` é upsert incondicional (`db/pending.py:198`) e atropela
    a pergunta que outra tarefa acabou de pôr na linha única. Nenhuma das quatro
    portas pode usá-lo.

    O `_resolve_clarification` tem DUAS ocorrências herdadas, e este PR não as
    introduziu. A checagem olha o CONTEXTO de cada uma, não o total: contar
    `== 2` deixaria passar "apagou uma herdada e pôs uma nova". As duas:

    - `funds.add_ask` sem destino reconhecido — idêntica à `main:568`;
    - "ainda sem valor reconhecível" — MESMA chamada e mesmo papel da
      `main:649`, mas NÃO byte a byte: a indentação e o alcance mudaram (na
      `main` só se chegava lá quando
      `not (entities["valor"] or _extract_valor(orig_text))`; este branch tirou
      o braço do `orig_text`, ver `_ja_tem_o_valor`).
    """
    import inspect
    import adapters.whatsapp.wa_runtime as wr
    import core.handlers.bills as BH
    import core.handlers.launches as LH
    import core.intent_router as IR

    def conta(fn):
        return inspect.getsource(fn).count("set_pending_action(")

    assert conta(BH.resolve_bill_amount) == 0            # porta 1
    assert conta(LH.resolve_multi_launch_value) == 0     # porta 3
    # 1 = o PRODUTOR da `clarification` (`needs_clarification`), da `main`.
    # A escotilha de abandono que este PR pôs no `route` usa CAS.
    assert conta(IR.route) == 1
    # Identidade, não contagem: cada `set_pending_action` do
    # `_resolve_clarification` tem que ser uma das DUAS herdadas, reconhecida
    # pela linha que a precede. Uma terceira (ou uma troca) quebra aqui.
    src = inspect.getsource(IR._resolve_clarification).splitlines()
    herdadas = ("# destino não reconhecido → re-arma a pergunta pra não perder o valor",
                "# pending pra não perder a descrição original.")
    achadas = [src[i - 1].strip() for i, ln in enumerate(src)
               if "set_pending_action(" in ln]
    assert achadas == list(herdadas), achadas
    # Porta 4: o bloco `bill_pay_amount` vive dentro do `process_message`, que
    # tem outros `set_pending_action` legítimos (o `recategorize_launch_text`).
    # Recorta só o trecho da pergunta de valor.
    src = inspect.getsource(wr.process_message)
    trecho = src[src.index("Passo 1 da pergunta de valor"):src.index("mark_bill_paid")]
    assert "set_pending_action(" not in trecho, trecho


# ---------------------------------------------------------------------------
# RODADA DO CODEX — P2 #2: espaço DEPOIS do separador decimal.
#
# A regra dos 3 dígitos do `_espaco_ambiguo` lia o "50" de "132, 50" como grupo
# de milhar malformado e recusava — mas o espaço vem depois da VÍRGULA, e o
# `parse_money` devolve 132.5. As 15 combinações abaixo foram medidas em DUAS
# COLUNAS (`main` cf54ffb × branch), nas quatro portas, 60 células:
#
#   antes do conserto  21 células recusavam o que a `main` aceitava
#                      (7 formas × portas 2, 3 e 4)
#   depois             a ÚNICA célula diferente da `main` é o alvo do PR,
#                      "132 50" → R$ 13.250,00, recusado nas portas 2, 3 e 4.
#
# Os testes de espaço que já existiam só tinham espaço ENTRE grupos ("1 500",
# "12 345"), nunca depois do separador — por isso as 21 células atravessaram
# quatro ataques.
# ---------------------------------------------------------------------------

# (texto, valores que a `main` registra na porta 2). A porta 2 recombina a
# resposta com o texto original ("gastei <resposta> a luz"), e em cinco delas o
# separador dentro da frase faz o splitter de multi-lançamento criar DOIS
# lançamentos — é o que a `main` faz, e o branch tem que fazer igual.
ESPACO_PORTA_2 = [
    ("132, 50",   [50.0, 132.0]),
    ("132 , 50",  [50.0, 132.0]),
    ("132 ,50",   [132.5]),
    ("132. 50",   [132.5]),
    ("132 . 50",  [132.5]),
    ("1.234, 56", [56.0, 1234.0]),
    ("1 234,56",  [1234.56]),
    ("1.234 ,56", [1234.56]),
    ("132, 5",    [5.0, 132.0]),
    ("132, 500",  [132.0, 500.0]),
    ("132,50 ",   [132.5]),
    ("1 500",     [1500.0]),
    ("12 345",    [12345.0]),
    ("1 500, 50", [50.0, 1500.0]),
]

# Portas 3 e 4 recebem a resposta sozinha: um valor só, o mesmo nas duas.
ESPACO_PORTA_3_E_4 = [
    ("132, 50", 132.5), ("132 , 50", 132.5), ("132 ,50", 132.5),
    ("132. 50", 132.5), ("132 . 50", 132.5), ("1.234, 56", 1234.56),
    ("1 234,56", 1234.56), ("1.234 ,56", 1234.56), ("132, 5", 132.5),
    ("132, 500", 132.5), ("132,50 ", 132.5), ("1 500", 1500.0),
    ("12 345", 12345.0), ("1 500, 50", 1500.5),
]

TODAS_AS_COMBINACOES = [t for t, _ in ESPACO_PORTA_2] + ["132 50"]


def _pergunta_e_responde(porta: int, monkeypatch, texto: str):
    """Arma a pergunta de valor da `porta` e responde `texto`.

    Devolve `(valores registrados, resposta)` — nas portas 1 e 4 o valor pago
    da conta (lista vazia = não pagou), nas portas 2 e 3 os lançamentos.
    """
    import uuid
    import db
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    if porta == 1:
        _monta_conta_variavel(uid)
        assert "só o valor" in _diga(uid, "paguei a luz")
        resp = _diga(uid, texto)
    elif porta == 2:
        assert "Quanto foi" in _diga(uid, "paguei a luz")
        resp = _diga(uid, texto)
    elif porta == 3:
        assert "Faltou o valor de *aluguel*" in _diga(
            uid, "gastei 30 no mercado e paguei o aluguel")
        resp = _diga(uid, texto)
    else:
        conta = _monta_conta_variavel(uid)
        _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))
        resp = (_manda_texto_no_wa(monkeypatch, uid, texto) or [""])[-1]

    if porta in (1, 4):
        conta = [b for b in B.list_bills(uid, include_paid=True)
                 if b["name"] == "Luz"][0]
        vals = ([float(conta["paid_amount"])]
                if conta["status"] == "paid" else [])
    else:
        vals = sorted(abs(float(l["valor"]))
                      for l in db.list_launches(uid, limit=6))
        if porta == 3:
            vals.remove(30.0)   # o item que já vinha com valor na frase
    return vals, resp


@pytest.mark.parametrize("texto,esperado", ESPACO_PORTA_2)
def test_espaco_depois_do_separador_registra_na_porta_2(monkeypatch, texto, esperado):
    vals, resp = _pergunta_e_responde(2, monkeypatch, texto)
    assert vals == esperado, (texto, resp)


@pytest.mark.parametrize("texto,esperado", ESPACO_PORTA_3_E_4)
def test_espaco_depois_do_separador_registra_na_porta_3(monkeypatch, texto, esperado):
    vals, resp = _pergunta_e_responde(3, monkeypatch, texto)
    assert vals == [esperado], (texto, resp)


@pytest.mark.parametrize("texto,esperado", ESPACO_PORTA_3_E_4)
def test_espaco_depois_do_separador_paga_na_porta_4(monkeypatch, texto, esperado):
    vals, resp = _pergunta_e_responde(4, monkeypatch, texto)
    assert vals == [esperado], (texto, resp)


@pytest.mark.parametrize("texto", TODAS_AS_COMBINACOES)
def test_porta_1_decide_pela_forma_e_nao_muda_com_o_conserto(monkeypatch, texto):
    """A porta 1 é inerte ao conserto — medido, não presumido.

    O `_VALOR_RE.fullmatch` não aceita `\\s` dentro do número, então as 15
    combinações caem no `_NUMERO_AMBIGUO_RE` ANTES de o `valor_perigoso` ser
    consultado. A única que paga é a que não tem espaço nenhum no meio
    ("132,50 ", espaço só no fim). Idêntico à `main` nas 15.
    """
    vals, resp = _pergunta_e_responde(1, monkeypatch, texto)
    if texto == "132,50 ":
        assert vals == [132.5], resp
    else:
        assert vals == [] and "Não entendi o valor da *Luz*" in resp, (texto, resp)


@pytest.mark.parametrize("porta", [2, 3, 4])
def test_alvo_132_50_continua_recusado(monkeypatch, porta):
    """O que o PR foi consertar não pode ter voltado: "132 50" → R$ 13.250,00.

    Na `main` as três portas registram 13250.0 (medido). Aqui, nenhuma.
    """
    vals, resp = _pergunta_e_responde(porta, monkeypatch, "132 50")
    assert vals == [], (porta, resp)
    assert "ntendi o valor" in resp or "Não peguei o valor" in resp, resp


def _espaco_ambiguo_antes_do_conserto(bloco: str) -> bool:
    """A versão do commit 1cc0955: sem a exceção do separador decimal."""
    import re
    partes = bloco.split()
    return any(len(re.match(r"\d*", p).group(0)) != 3
               for p in partes[1:] if p[:1].isdigit())


@pytest.mark.parametrize("porta", [2, 3, 4])
def test_controle_negativo_sem_a_excecao_do_decimal_a_porta_recusa(monkeypatch, porta):
    """Controle negativo, injetado em caso VERDE e uma vez POR PORTA.

    Desliga só a exceção nova e o caso que a `main` aceita fica vermelho nas
    três portas. Sem este controle, a regressão passava despercebida — foi
    exatamente o que aconteceu por quatro ataques.
    """
    import utils_text
    monkeypatch.setattr(utils_text, "_espaco_ambiguo",
                        _espaco_ambiguo_antes_do_conserto)

    vals, resp = _pergunta_e_responde(porta, monkeypatch, "132, 50")

    assert vals == [], (porta, resp)


def test_controle_negativo_a_porta_1_nao_muda_com_a_versao_antiga(monkeypatch):
    """A outra metade do controle: na porta 1 desligar a exceção não muda NADA.

    É a prova de que o portão de forma vem antes — e a razão de não existir
    controle negativo da porta 1 para este conserto.
    """
    import utils_text
    monkeypatch.setattr(utils_text, "_espaco_ambiguo",
                        _espaco_ambiguo_antes_do_conserto)

    vals, resp = _pergunta_e_responde(1, monkeypatch, "132, 50")

    assert vals == [] and "Não entendi o valor da *Luz*" in resp, resp


# ---------------------------------------------------------------------------
# RODADA DO CODEX — P2 #1: o CAS que falha vale para o TURNO, não só para o
# DELETE.
#
# Os testes de corrida que já existiam punham como substituta um
# `bill_pay_amount` — que o `route()` NÃO consome —, então provavam só que a
# linha não era apagada. A substituta destes é um `multi_launch_values` com
# fila: se o comando velho ainda alcançar as guardas de pendência, a fila
# inteira do usuário é apagada pela porta 3, e isso aparece.
# ---------------------------------------------------------------------------

# `desc` diferente do item que a porta 3 tem em mão de propósito: o CAS compara
# o PAYLOAD, e uma fila igual à lida faria o compare-and-swap VENCER — a corrida
# não seria injetada e o teste mediria o caminho normal. Medido: com
# `desc="aluguel"` (o mesmo item), o teste da porta 3 passa a apagar a fila.
FILA_DE_OUTRA_TAREFA = {"queue": [{"desc": "internet", "tipo": "despesa"}],
                        "platform": "whatsapp"}


def _corrida_no_cas(monkeypatch, alvo, uid, ignora_resultado=False):
    """Injeta a pergunta de OUTRA tarefa entre a leitura e o CAS de abandono.

    `ignora_resultado=True` reproduz o código de antes do conserto: o CAS roda
    (e falha), mas quem chamou trata como se tivesse funcionado.

    Os DOIS bindings do `advance_pending_action`: as quatro portas passam pelo
    `db.consume_pending_action`, que resolve o nome nos globais de `db.pending`
    — só o `db` não pega nada. O `db` fica junto porque o CAS da fila
    (`core/handlers/launches.py`) chama por lá; ele nunca tem `new is None`,
    então a injeção não dispara nele.
    """
    import db
    import db.pending

    real = db.pending.advance_pending_action

    def com_corrida(u, tipo, old, new, *a, **k):
        if tipo == alvo and new is None:
            db.set_pending_action(uid, "multi_launch_values", FILA_DE_OUTRA_TAREFA)
        ok = real(u, tipo, old, new, *a, **k)
        return True if ignora_resultado else ok

    monkeypatch.setattr(db.pending, "advance_pending_action", com_corrida)
    monkeypatch.setattr(db, "advance_pending_action", com_corrida)


def _fila_intacta(uid):
    import db
    atual = db.get_pending_action(uid) or {}
    assert atual.get("action_type") == "multi_launch_values", \
        f"o comando velho encostou na pergunta nova: {atual}"
    assert atual.get("payload") == FILA_DE_OUTRA_TAREFA, atual


def test_cas_perdido_na_porta_2_nao_toca_a_pergunta_nova(monkeypatch):
    """Porta 2: o CAS falha → o turno roteia "saldo" SEM as guardas de pendência.

    Sem o conserto, o `get_pending_action` seguinte recarregava a substituta e a
    porta 3 apagava a fila que outra tarefa acabou de mostrar ao usuário.
    """
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    assert "Quanto foi" in _diga(uid, "paguei a luz")

    _corrida_no_cas(monkeypatch, "clarification", uid)
    resp = _diga(uid, "saldo")

    _fila_intacta(uid)
    assert "Conta Corrente" in resp, f"o comando velho não foi respondido: {resp!r}"


def test_controle_negativo_porta_2_ignorando_o_cas_a_fila_some(monkeypatch):
    """Controle negativo da porta 2, injetado no caso VERDE acima."""
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    assert "Quanto foi" in _diga(uid, "paguei a luz")

    _corrida_no_cas(monkeypatch, "clarification", uid, ignora_resultado=True)
    _diga(uid, "saldo")

    assert db.get_pending_action(uid) is None, \
        "sem o conserto a fila da outra tarefa TEM que sumir — o controle não mede nada"


def test_cas_perdido_na_porta_4_nao_toca_a_pergunta_nova(monkeypatch):
    """Porta 4: o CAS falha e o `handle_incoming` relê `pending_actions`.

    Sem o `ignora_pendencias`, "saldo" entrava na porta 3 da fila nova e a
    apagava — a porta 4 roda ANTES do `handle_incoming`, então o `pending_recat`
    local não protege nada.
    """
    import uuid
    import db.bills as B

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    _corrida_no_cas(monkeypatch, "bill_pay_amount", uid)
    respostas = _manda_texto_no_wa(monkeypatch, uid, "saldo")

    _fila_intacta(uid)
    assert "Conta Corrente" in (respostas[-1] if respostas else ""), respostas
    assert B.list_bills(uid, include_paid=True)[0]["status"] == "pending"


def test_controle_negativo_porta_4_ignorando_o_cas_a_fila_some(monkeypatch):
    """Controle negativo da porta 4, injetado no caso VERDE acima."""
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    conta = _monta_conta_variavel(uid)
    _toca_ja_paguei(monkeypatch, uid, int(conta["id"]))

    _corrida_no_cas(monkeypatch, "bill_pay_amount", uid,
                    ignora_resultado=True)
    _manda_texto_no_wa(monkeypatch, uid, "saldo")

    assert db.get_pending_action(uid) is None, \
        "sem o conserto a fila da outra tarefa TEM que sumir — o controle não mede nada"


def test_cas_perdido_na_porta_1_ja_nao_tocava_a_pergunta_nova(monkeypatch):
    """Porta 1: o irmão que NÃO precisava de conserto — medido, não presumido.

    O `resolve_bill_amount` devolve `None` quando o CAS falha, e o `route()`
    zera o `pending` local nessa linha, então as guardas seguintes já ficavam
    de fora. O controle negativo desta porta é o CAS em si: trocá-lo por um
    `clear_pending_action` apaga a fila (é o `test_abandono_...` acima).
    """
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    _monta_conta_variavel(uid)
    assert "só o valor" in _diga(uid, "paguei a luz")

    _corrida_no_cas(monkeypatch, "bill_amount_expected", uid)
    resp = _diga(uid, "saldo")

    _fila_intacta(uid)
    assert "Conta Corrente" in resp, resp


def test_cas_perdido_na_porta_3_ja_nao_tocava_a_pergunta_nova(monkeypatch):
    """Porta 3: mesmo irmão, mesma medição.

    O ramo `outro_comando` devolve `None` e o `route()` zera o `pending` local.
    A substituta aqui é uma fila DIFERENTE (payload diferente), que é o que o
    CAS compara.

    O comando é "saldo", não "apagar 42": medido, o `launches.delete` arma a
    SUA própria confirmação com `set_pending_action` e sobrescreve a linha
    sozinho — escrita incondicional herdada (~48 no repositório, ver
    `db/pending.py`), fora das quatro portas e fora deste PR.
    """
    import uuid
    import db

    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    assert "Faltou o valor de *aluguel*" in _diga(
        uid, "gastei 30 no mercado e paguei o aluguel")

    _corrida_no_cas(monkeypatch, "multi_launch_values", uid)
    resp = _diga(uid, "saldo")

    _fila_intacta(uid)
    assert "Conta Corrente" in resp, resp
