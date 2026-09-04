"""Nome do usuário é CONTEÚDO, não marcação do WhatsApp (#146, fatia 1 de 2).

O WhatsApp NÃO tem caractere de escape: `\\*` não existe, entidade HTML aparece
literal na tela. E neutralizar o delimitador do usuário com um WORD JOINER
(U+2060) também não resolve — MEDIDO pelo dono no cliente real do WhatsApp,
`*a*<WJ>b*` renderiza `ab*`, idêntico ao que a `main` já mostra: o WJ cega a
ABERTURA, mas o FECHAMENTO olha o caractere ANTERIOR. Ou seja, era inerte
justamente quando o delimitador do usuário é igual ao do template — o `a*b`
dentro de `*{...}*`, que é o caso do título da issue.

O mecanismo é `wrap_wa_markup` (core/response_formatter.py): quando o texto do
usuário contém `*`, `_`, `~` ou crase, a mensagem sai SEM o embrulho de
marcação. Custo aceito: perde-se o negrito nesses nomes — e nesse mesmo caso a
formatação já estava quebrada antes, então não se perde nada que funcione.

CONTROLE NEGATIVO — faça `wrap_wa_markup` embrulhar sempre
(`return f"{delim}{text}{delim}"`) e rode este arquivo: os 7 testes de
não-embrulho ficam VERMELHOS (medido em 2026-09-04: `7 failed, 6 passed`; com o
conserto de pé, `13 passed`). Os 7 estavam VERDES com o conserto — nenhum foi
injetado num caso já vermelho. Os 6 que continuam verdes são os controles
positivos, e é exatamente o que se espera deles.

CONTROLE POSITIVO — o conserto RESTRINGE (passa a omitir o negrito), então
precisa provar que não engole o negrito de quem escreveu certo: `McDonald's`,
`café & pão` e `Cartão Nubank` (a mesma lista de tests/test_export_pdf_escape.py,
#145) saem COM o par de asteriscos, e a pendência legada sai com as crases. O
controle do controle, medido no mesmo dia: com `return text` (helper que nunca
embrulha) são os 6 positivos que ficam vermelhos — `6 failed, 7 passed`.

CLASSE CEGA: a RENDERIZAÇÃO do cliente WhatsApp (Android/iOS) não é exercitável
aqui. O que se mede é a estrutura da STRING que sai da função real do handler —
se o embrulho de marcação está presente ou ausente.

ESCOPO: os 7 sítios de `adapters/whatsapp/wa_runtime.py`, o
`pergunta_de_valor_sem_contexto` (core/handlers/bills.py, chamado de
wa_runtime.py:800) e o `core/handlers/pending.py:90`. Ficam FORA:
`core/handlers/bills.py:174` e `:295`, que têm a string idêntica à de
wa_runtime.py:822/1020 — a outra porta da MESMA pergunta —, e os 125 sítios
restantes em ~20 arquivos. Tudo isso é a fatia 2.
"""
from __future__ import annotations

import pytest

import db
from adapters.whatsapp import wa_runtime
from core.handlers import bills as h_bills
from core.handlers import pending as h_pending
from core.response_formatter import format_for_platform

# A lista de nomes legítimos do #145 (tests/test_export_pdf_escape.py:105).
NOMES_LEGITIMOS = [("McDonald's", "mcdonald's"),
                   ("café & pão", "café & pão"),
                   ("Cartão Nubank", "cartão nubank")]


def _novo_launch(user_id: int) -> int:
    launch_id, _seq, _bal = db.add_launch_and_update_balance(
        user_id=user_id, tipo="despesa", valor=39.90,
        alvo="mercado", nota="mercado", categoria="outros",
    )
    return launch_id


# ─── wa_runtime:352 — categoria (a linha citada na issue) ────────────────────


@pytest.mark.parametrize("nome", [
    "a*b",            # delimitador IGUAL ao do template: o caso que o WJ não fechava
    "meta_casa_nova",  # dois underscores pareiam e o WhatsApp italiza "casa"
    "conta_",          # termina em delimitador: com WJ o negrito do bot vazava
])
def test_categoria_com_marcacao_sai_sem_negrito(pro_user_id, nome):
    launch_id = _novo_launch(pro_user_id)

    msg = wa_runtime._apply_recategorize(pro_user_id, launch_id, nome)

    assert msg.endswith(f"atualizada para {nome}.")   # o nome inteiro, na tela
    assert f"*{nome}*" not in msg                     # e sem o embrulho


@pytest.mark.parametrize("nome,canon", NOMES_LEGITIMOS)
def test_categoria_legitima_mantem_o_negrito(pro_user_id, nome, canon):
    """CONTROLE POSITIVO: sem isto, um helper que nunca embrulha passaria."""
    launch_id = _novo_launch(pro_user_id)

    msg = wa_runtime._apply_recategorize(pro_user_id, launch_id, nome)

    assert msg.endswith(f"atualizada para *{canon}*.")


# ─── wa_runtime:822/1020 — nome da conta no caminho do dinheiro ─────────────


def test_conta_paga_com_asterisco_no_nome(monkeypatch):
    """Botão "✅ Já paguei" numa conta de valor fixo chamada `luz *casa*`."""
    replies: list[tuple[str, str]] = []
    _mock_wa_boot(monkeypatch, replies, uid=4242)
    monkeypatch.setattr(
        "db.bills.get_bill",
        lambda uid, bid: {"id": bid, "name": "luz *casa*", "status": "pending",
                          "variable_amount": False, "amount": 132.5},
    )
    monkeypatch.setattr(
        "db.bills.mark_bill_paid",
        lambda uid, bid: {"name": "luz *casa*", "paid_amount": 132.5},
    )

    wa_runtime.process_message(_botao(wa_runtime.WA_BILL_PAID_PREFIX + "41"))

    assert len(replies) == 1
    body = replies[0][1]
    assert "Conta paga: luz *casa* —" in body
    assert "*luz *casa**" not in body


def test_conta_paga_legitima_mantem_o_negrito(monkeypatch):
    """CONTROLE POSITIVO no caminho do dinheiro."""
    replies: list[tuple[str, str]] = []
    _mock_wa_boot(monkeypatch, replies, uid=4242)
    monkeypatch.setattr(
        "db.bills.get_bill",
        lambda uid, bid: {"id": bid, "name": "Cartão Nubank", "status": "pending",
                          "variable_amount": False, "amount": 132.5},
    )
    monkeypatch.setattr(
        "db.bills.mark_bill_paid",
        lambda uid, bid: {"name": "Cartão Nubank", "paid_amount": 132.5},
    )

    wa_runtime.process_message(_botao(wa_runtime.WA_BILL_PAID_PREFIX + "41"))

    assert "Conta paga: *Cartão Nubank* —" in replies[0][1]


# ─── wa_runtime:805 — pergunta de valor de conta variável ───────────────────


def test_pergunta_de_valor_com_underscore_no_nome(monkeypatch):
    replies: list[tuple[str, str]] = []
    _mock_wa_boot(monkeypatch, replies, uid=4242)
    monkeypatch.setattr(
        "db.bills.get_bill",
        lambda uid, bid: {"id": bid, "name": "conta_de_luz", "status": "pending",
                          "variable_amount": True, "amount": None},
    )
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.claim_pending_action",
                        lambda *a, **k: True)

    wa_runtime.process_message(_botao(wa_runtime.WA_BILL_PAID_PREFIX + "41"))

    assert len(replies) == 1
    body = replies[0][1]
    assert "Quanto veio a conta de conta_de_luz este mês?" in body
    assert "*conta_de_luz*" not in body


# ─── bills.py:63 — o irmão de wa_runtime.py:800, mesma variável ────────────


def test_pergunta_sem_contexto_com_underscore_no_nome(monkeypatch):
    """O `claim` perdeu a linha: o texto vem de core/handlers/bills.py e
    interpola o nome DUAS vezes."""
    monkeypatch.setattr(db, "get_pending_action", lambda uid: {})

    msg = h_bills.pergunta_de_valor_sem_contexto(9, "conta_de_luz")

    assert msg.count("conta_de_luz") == 2
    assert "*conta_de_luz*" not in msg


def test_pergunta_sem_contexto_legitima_mantem_o_negrito(monkeypatch):
    """CONTROLE POSITIVO."""
    monkeypatch.setattr(db, "get_pending_action", lambda uid: {})

    msg = h_bills.pergunta_de_valor_sem_contexto(9, "Cartão Nubank")

    assert msg.count("*Cartão Nubank*") == 2


# ─── core/handlers/pending.py:90 — a mensagem CRUA do usuário, em crase ────


def _pendencia_legada(monkeypatch, texto: str) -> str:
    pend = {"action_type": "confirm_media_launch", "payload": {"text": texto}}
    monkeypatch.setattr(db, "get_pending_action", lambda uid: pend)
    monkeypatch.setattr(db, "consume_pending_action", lambda uid, p: True)
    monkeypatch.setattr("core.services.quick_entry.handle_quick_entry",
                        lambda uid, text: None)
    return h_pending.resolve_delete(9, confirmed=True)


def test_pendencia_legada_com_crase_na_mensagem_crua(monkeypatch):
    """Medido na `main`: com ``paguei a `luz` ``, o `format_for_platform`
    (que remove crase no WhatsApp) pareia errado e sobram DUAS crases soltas
    na tela — `Tente: paguei a luz``."""
    bruto = _pendencia_legada(monkeypatch, "paguei a `luz`")

    assert bruto.endswith("Tente: paguei a `luz`")
    assert format_for_platform(bruto, "whatsapp").count("`") == 0


def test_pendencia_legada_legitima_mantem_a_crase(monkeypatch):
    """CONTROLE POSITIVO: texto sem marcação continua saindo em crase."""
    bruto = _pendencia_legada(monkeypatch, "gastei 50 no mercado")

    assert bruto.endswith("Tente: `gastei 50 no mercado`")


# ─── plumbing do process_message (só monkeypatch de borda) ──────────────────


def _botao(interactive_id: str):
    from adapters.whatsapp.wa_parse import InboundMessage

    return InboundMessage(
        wa_id="5511988887777", text="", timestamp="123", attachments=[],
        raw={"id": "wamid." + interactive_id, "type": "interactive",
             "interactive": {"type": "button_reply",
                             "button_reply": {"id": interactive_id, "title": "✅ Já paguei"}}},
    )


def _mock_wa_boot(monkeypatch, replies: list, *, uid: int) -> None:
    monkeypatch.setattr(wa_runtime, "get_or_create_canonical_user",
                        lambda provider, external_id: uid)
    monkeypatch.setattr(wa_runtime, "attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "linked", "user_id": uid})
    monkeypatch.setattr(wa_runtime, "log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr(wa_runtime, "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wa_runtime, "_seen_recent", lambda message_id: False)
    monkeypatch.setattr(wa_runtime, "_send_reply",
                        lambda to, body: replies.append((to, body)))
