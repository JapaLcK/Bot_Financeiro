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
