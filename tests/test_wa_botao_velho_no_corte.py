"""
tests/test_wa_botao_velho_no_corte.py — o botão de ONTEM na tela de quem foi
cortado HOJE.

Botão do WhatsApp fica no histórico da conversa para sempre. Os ramos
interativos de `adapters/whatsapp/wa_runtime.process_message` — recategorizar,
desfazer, apagar compra no crédito, "✅ Já paguei" — cada um dá `return` ANTES
do `handle_incoming`, e o `_paywall_gate` vive lá dentro. Quem for cortado rola
a tela, clica, e a escrita acontece sem gate nenhum. O mesmo vale para as
`pending_actions`, consumidas antes do roteamento.

**O corte não é só sobre mandar mensagem: é sobre ESCREVER no banco.** O ramo
mais caro é o `bill_paid:`, que chama `mark_bill_paid` — debita saldo.

**E a isenção é tão obrigatória quanto o gate.** Quem foi cortado tem de
conseguir PARAR de receber mensagem nossa, do mesmo jeito que `/settings`
continua aberto na web para exportar e excluir a conta. Os botões de desligar
relatório e de parar atualizações são a saída de emergência deste canal.

CONTROLES DECLARADOS (`docs/controles_declarados.md`)
────────────────────────────────────────────────────
**Negativo** — em `wa_runtime.process_message`, no gate único que roda antes dos
botões que escrevem, troque a condição por `and False` (o bloco continua lá e
deixa de discriminar; nada é apagado)::

    if (interactive_id.strip().lower() not in _WA_INTERACTIVE_ISENTOS
            and interactive_id not in _WA_INTERACTIVE_ISENTOS
            and False):

VERMELHOS (medido 2026-09-11):
  `test_cortado_clicando_ja_paguei_nao_quita_a_conta`
  `test_cortado_clicando_apagar_nao_apaga_a_compra`
Direção: escrita sem direito — o cortado quita conta (debitando saldo) e apaga
lançamento clicando em botão que o produto mandou antes do corte.

**Negativo da ISENÇÃO** — apague `_WA_INTERACTIVE_ISENTOS` do `if` (deixe só o
`_bloqueado_pelo_corte(...)`). VERMELHO:
  `test_cortado_ainda_consegue_desligar_o_relatorio`
Direção: falso NEGATIVO — o cortado perde a única forma de parar as mensagens,
e continua recebendo o que não pode mais usar.

**Positivo do grupo** (VERDE nas duas injeções, é o que os torna positivos):
  `test_pagante_clicando_ja_paguei_quita_normalmente`
Sem ele, um gate que recusasse TODO MUNDO passaria nos dois negativos — que é
pior que o bug.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import db
from adapters.whatsapp.wa_parse import InboundMessage
from adapters.whatsapp.wa_runtime import process_message
from db.connection import get_conn


@pytest.fixture(autouse=True)
def _gate_ligado(monkeypatch):
    """O corte é o default de produção; o `conftest` roda com o v2 off."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")
    monkeypatch.setenv("PAYWALL_ENABLED", "0")


def _conta(*, cortada: bool) -> int:
    """Conta real, com `plan_selected_at` preenchido para isolar a perna do
    DIREITO: sem isso o `needs_plan_selection` barraria as DUAS e os testes não
    separariam quem cortou de quem nunca escolheu."""
    user = db.register_auth_user(f"btn-{uuid.uuid4().hex[:10]}@t.com", "senha-forte-123")
    uid = int(user["user_id"])
    agora = datetime.now(timezone.utc)
    expira = agora - timedelta(days=1) if cortada else agora + timedelta(days=30)
    status = "canceled" if cortada else "active"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan='pro', plan_expires_at=%s,"
                "       last_payment_status=%s, plan_selected_at=now()"
                " where user_id=%s",
                (expira, status, uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)
    return uid


@pytest.fixture
def bancada(monkeypatch):
    """Isola o runtime do transporte: nada de rede, e o uid é o da conta real.

    `handle_incoming` é substituído por uma sentinela — se um ramo interativo
    cair nele, o teste vê. Nenhum caso daqui deveria chegar lá."""
    respostas: list[str] = []
    caiu_no_roteador: list = []

    monkeypatch.setattr("adapters.whatsapp.wa_runtime.send_typing_indicator",
                        lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._seen_recent",
                        lambda message_id: False)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.log_system_event_sync",
                        lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._send_reply",
                        lambda to, body: respostas.append(body))
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._send_reply_with_optional_buttons",
                        lambda to, body, user_id=None: respostas.append(body))
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.handle_incoming",
                        lambda msg, **k: caiu_no_roteador.append(msg) or [])
    return respostas, caiu_no_roteador


def _clique(uid: int, monkeypatch, botao: str):
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.get_or_create_canonical_user",
                        lambda provider, external_id: uid)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "noop", "user_id": uid})
    process_message(InboundMessage(
        wa_id="5511999990000", text="", timestamp="1", attachments=[],
        raw={"id": f"wamid.{uuid.uuid4().hex[:10]}", "type": "interactive",
             "interactive": {"type": "button_reply",
                             "button_reply": {"id": botao, "title": "x"}}},
    ))


def test_cortado_clicando_ja_paguei_nao_quita_a_conta(monkeypatch, bancada):
    """O ramo mais caro: `mark_bill_paid` debita saldo. O cortado nem chega lá."""
    respostas, _ = bancada
    uid = _conta(cortada=True)

    def _nao_pode(*a, **k):
        raise AssertionError("o cortado alcançou a escrita da conta a pagar")
    monkeypatch.setattr("db.bills.get_bill", _nao_pode)
    monkeypatch.setattr("db.bills.mark_bill_paid", _nao_pode)

    _clique(uid, monkeypatch, "bill_paid:1")

    assert respostas, "o cortado clicou e não recebeu resposta nenhuma"
    assert "plano" in respostas[0].lower(), respostas


def test_cortado_clicando_apagar_nao_apaga_a_compra(monkeypatch, bancada):
    """O irmão: `del_cc:` apaga um lançamento de crédito. Mesma classe, mesmo
    gate — sem este caso, "resolvi o `bill_paid`" viraria "resolvi a categoria"
    (§2)."""
    respostas, _ = bancada
    uid = _conta(cortada=True)

    import core.handlers.credit as h_credit
    monkeypatch.setattr(h_credit, "handle", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("o cortado alcançou o handler de crédito")))

    _clique(uid, monkeypatch, "del_cc:7")

    assert respostas, "o cortado clicou e não recebeu resposta nenhuma"
    assert "plano" in respostas[0].lower(), respostas


def test_cortado_ainda_consegue_desligar_o_relatorio(monkeypatch, bancada):
    """A SAÍDA DE EMERGÊNCIA deste canal: opt-out sobrevive ao corte.

    Sem esta isenção o gate estaria certo e o produto errado — o cortado
    continuaria recebendo mensagem sem poder mandar parar."""
    respostas, _ = bancada
    uid = _conta(cortada=True)
    desligou: list[int] = []

    import core.handlers.report as h_report
    monkeypatch.setattr(h_report, "disable",
                        lambda u: desligou.append(u) or "Relatório desligado.")

    _clique(uid, monkeypatch, "daily_report_disable")

    assert desligou == [uid], f"o opt-out do cortado foi barrado: {respostas}"
    assert respostas == ["Relatório desligado."], respostas


def test_pagante_clicando_ja_paguei_quita_normalmente(monkeypatch, bancada):
    """POSITIVO do grupo: o gate DISCRIMINA, não recusa todo mundo."""
    respostas, _ = bancada
    uid = _conta(cortada=False)
    quitou: list[int] = []

    import db.bills as bills
    monkeypatch.setattr(bills, "get_bill",
                        lambda u, b: {"id": b, "name": "Luz", "amount": 100.0,
                                      "status": "pending", "variable_amount": False})
    monkeypatch.setattr(bills, "mark_bill_paid",
                        lambda u, b, *a, **k: quitou.append(b) or {
                            "name": "Luz", "paid_amount": 100.0, "amount": 100.0})

    _clique(uid, monkeypatch, "bill_paid:1")

    assert quitou == [1], f"o pagante foi barrado pelo gate do corte: {respostas}"


# ── As PENDÊNCIAS: o mesmo gate, e o CUSTO dele ─────────────────────────────
#
# As duas interceptações de `pending_actions` feitas antes do `handle_incoming`
# — recategorizar por texto e responder o valor do boleto — também escrevem e
# também dão `return`. O gate delas tem uma restrição a mais que o dos botões:
# ele roda no caminho de TEXTO, que é o mais quente do produto, e cada chamada
# custa um `db.get_plan_gate_state`. Gateando antes de ler a pendência, quem tem
# acesso pagava a consulta DUAS vezes por mensagem (uma aqui, outra dentro do
# `handle_incoming`) por causa de uma pendência que quase nunca existe.
#
# CONTROLES DECLARADOS (`docs/controles_declarados.md`), e são DOIS porque o
# conserto tem duas metades que se medem em direções opostas.
#
# **(a) o gate existe** — em `wa_runtime`, troque
# `pending_recat.get("action_type") in _PENDENCIAS_QUE_ESCREVEM` por
# `... not in _PENDENCIAS_QUE_ESCREVEM` (troca de operador; nada apagado).
# VERMELHO:
#   `test_cortado_com_pendencia_de_boleto_nao_paga_respondendo_o_valor`
# Direção: escrita sem direito — o cortado quita a conta, debitando saldo,
# respondendo o número a uma pergunta feita antes do corte.
#
# **(b) o gate não roda à toa** — apague os dois primeiros termos do `if`,
# deixando `if _bloqueado_pelo_corte(uid, reply_to, message.text or ""):`
# (é literalmente a versão anterior deste código). VERMELHO:
#   `test_mensagem_comum_de_quem_tem_acesso_paga_o_veredito_uma_vez`
# Direção: custo — duas consultas por mensagem no caminho principal.
#
# As duas metades se medem sozinhas: (a) é falso negativo de gate, (b) é
# desperdício. Uma injeção que consertasse (b) quebrando (a) fica vermelha em
# (a), e vice-versa — é o par que impede "resolvi tirando o gate".


def _texto(uid: int, monkeypatch, corpo: str):
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.get_or_create_canonical_user",
                        lambda provider, external_id: uid)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "noop", "user_id": uid})
    process_message(InboundMessage(
        wa_id="5511999990000", text=corpo, timestamp="1", attachments=[],
        raw={"id": f"wamid.{uuid.uuid4().hex[:10]}", "type": "text"},
    ))


def test_cortado_com_pendencia_de_boleto_nao_paga_respondendo_o_valor(monkeypatch, bancada):
    """A pergunta ficou de pé de ontem; o corte veio hoje; ele responde "132,50".

    Sem o gate, `mark_bill_paid` roda e o saldo é debitado — escrita de dinheiro
    por uma conta sem direito."""
    respostas, _ = bancada
    uid = _conta(cortada=True)

    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "get_pending_action", lambda u: {
        "action_type": "bill_pay_amount",
        "payload": {"bill_id": 1, "name": "Luz"},
    })
    monkeypatch.setattr(wr, "consume_pending_action", lambda *a, **k: (
        _ for _ in ()).throw(AssertionError("o cortado consumiu a pendência")))
    import db.bills as bills
    monkeypatch.setattr(bills, "mark_bill_paid", lambda *a, **k: (
        _ for _ in ()).throw(AssertionError("o cortado quitou a conta")))

    _texto(uid, monkeypatch, "132,50")

    assert respostas, "o cortado respondeu e não recebeu nada"
    assert "plano" in respostas[0].lower(), respostas


def test_cortado_com_pendencia_de_boleto_e_o_pagante_nao(monkeypatch, bancada):
    """POSITIVO do par (a): mesma pendência, conta com direito, a conta é paga."""
    respostas, _ = bancada
    uid = _conta(cortada=False)
    quitou: list[int] = []

    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "get_pending_action", lambda u: {
        "action_type": "bill_pay_amount",
        "payload": {"bill_id": 1, "name": "Luz"},
    })
    monkeypatch.setattr(wr, "consume_pending_action", lambda *a, **k: True)
    import db.bills as bills
    monkeypatch.setattr(bills, "mark_bill_paid",
                        lambda u, b, *a, **k: quitou.append(b) or {
                            "name": "Luz", "paid_amount": 132.5, "amount": 132.5})

    _texto(uid, monkeypatch, "132,50")

    assert quitou == [1], f"o pagante foi barrado na pendência: {respostas}"


def test_mensagem_comum_de_quem_tem_acesso_paga_o_veredito_uma_vez(monkeypatch, bancada):
    """O CUSTO, contado: "gastei 50 no mercado" sem pendência nenhuma.

    Conta as chamadas a `db.get_plan_gate_state`, que é o SELECT que o
    `_paywall_gate` paga por avaliação. Uma é o `handle_incoming` real; a
    segunda seria o gate daqui rodando à toa.

    **O `handle_incoming` aqui é o DE VERDADE** — a `bancada` o substitui por
    uma sentinela, e com ela o número seria 0 com e sem o conserto, que é a
    medição que não mede nada (§3).
    """
    _, _ = bancada
    uid = _conta(cortada=False)

    import core.handle_incoming as hi
    import db as dbmod
    chamadas: list[int] = []
    real = dbmod.get_plan_gate_state

    def _contando(u):
        chamadas.append(u)
        return real(u)

    monkeypatch.setattr(dbmod, "get_plan_gate_state", _contando)
    monkeypatch.setattr(hi.db, "get_plan_gate_state", _contando, raising=False)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.handle_incoming",
                        hi.handle_incoming)
    import adapters.whatsapp.wa_runtime as wr
    monkeypatch.setattr(wr, "get_pending_action", lambda u: None)

    _texto(uid, monkeypatch, "gastei 50 no mercado")

    assert len(chamadas) == 1, (
        f"o veredito foi consultado {len(chamadas)}x numa mensagem comum "
        "(esperado 1: só o do handle_incoming)")
