"""Nome do usuário é CONTEÚDO, não marcação do WhatsApp (#146, fatia 1 de 2).

O WhatsApp NÃO tem caractere de escape: `\\*` não existe, entidade HTML aparece
literal na tela. O mecanismo é `escape_wa_markup` (core/response_formatter.py):
um WORD JOINER (U+2060, invisível) logo depois de cada `*`, `_`, `~` e crase que
veio do usuário, quebrando o PAREAMENTO sem alterar o texto visível.

CONTROLE NEGATIVO — faça `escape_wa_markup` devolver a entrada intacta
(`return str(text)`) e rode este arquivo:
os 5 testes de neutralização ficam VERMELHOS (medido: `5 failed, 3 passed`;
com o conserto de pé, `8 passed`). Os 5 estavam VERDES com o conserto — nenhum
foi injetado num caso já vermelho. Os 3 que continuam verdes são os controles
positivos, e é exatamente o que se espera deles.

CONTROLE POSITIVO — `test_pontuacao_legitima_sobrevive_com_negrito_intacto`: o
conserto RESTRINGE (passa a inserir caractere), então precisa provar que não
mutila quem escreveu certo. `McDonald's`, `café & pão` e `Cartão Nubank` saem com
o caractere original E com o par de asteriscos do negrito de pé.

CLASSE CEGA: a RENDERIZAÇÃO do cliente WhatsApp (Android/iOS) não é exercitável
aqui. Nada neste arquivo prova que o U+2060 realmente impede o negrito no
aparelho — isso é verificação pós-deploy. O que se mede aqui é a estrutura da
STRING que sai: quais delimitadores continuam pareáveis.

ESCOPO: os 7 sítios de `adapters/whatsapp/wa_runtime.py` e o de
`core/handlers/pending.py:89`. Os ~90 restantes (`credit.py`, `pockets.py`,
`investments.py`…) são a fatia 2 e NÃO estão cobertos aqui.
"""
from __future__ import annotations

import pytest

import db
from adapters.whatsapp import wa_runtime
from core.handlers import pending as h_pending
from core.response_formatter import format_for_platform

WJ = "⁠"


def _delimitadores_ativos(texto: str) -> str:
    """Os marcadores que o WhatsApp ainda pode parear.

    Um marcador seguido de WORD JOINER está neutralizado; o que sobra é o que
    o cliente ainda enxerga como abertura/fechamento.
    """
    return "".join(
        c for i, c in enumerate(texto)
        if c in "*_~`" and texto[i + 1:i + 2] != WJ
    )


def _novo_launch(user_id: int) -> int:
    launch_id, _seq, _bal = db.add_launch_and_update_balance(
        user_id=user_id, tipo="despesa", valor=39.90,
        alvo="mercado", nota="mercado", categoria="outros",
    )
    return launch_id


# ─── wa_runtime:351 — categoria (a linha citada na issue) ────────────────────


def test_categoria_com_asterisco_nao_abre_negrito(pro_user_id):
    """`a*b` é nome legítimo desde o #143. Os asteriscos ativos têm que ser
    só o par do template — 3 asteriscos ativos é negrito vazando."""
    launch_id = _novo_launch(pro_user_id)

    msg = wa_runtime._apply_recategorize(pro_user_id, launch_id, "a*b")

    assert "a*" + WJ + "b" in msg          # o texto visível é o mesmo
    assert _delimitadores_ativos(msg) == "**"


def test_categoria_com_underscore_nao_italiza(pro_user_id):
    """`meta_casa_nova`: dois underscores pareiam e o WhatsApp italiza "casa".
    É o caractere realmente alcançável — asterisco em nome é raro, underscore não."""
    launch_id = _novo_launch(pro_user_id)

    msg = wa_runtime._apply_recategorize(pro_user_id, launch_id, "meta_casa_nova")

    assert "meta_" + WJ + "casa_" + WJ + "nova" in msg
    assert _delimitadores_ativos(msg) == "**"


@pytest.mark.parametrize("nome,canon", [
    ("McDonald's", "mcdonald's"),
    ("café & pão", "café & pão"),
    ("Cartão Nubank", "cartão nubank"),
])
def test_pontuacao_legitima_sobrevive_com_negrito_intacto(pro_user_id, nome, canon):
    """CONTROLE POSITIVO: o conserto restringe, então tem que provar que o
    caminho legítimo continua — caractere original E negrito de pé."""
    launch_id = _novo_launch(pro_user_id)

    msg = wa_runtime._apply_recategorize(pro_user_id, launch_id, nome)

    assert f"*{canon}*" in msg      # sem WJ nenhum no meio, e o par intacto
    assert WJ not in msg
    assert _delimitadores_ativos(msg) == "**"


# ─── wa_runtime:821/1019 — nome da conta no caminho do dinheiro ─────────────


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
    assert "luz *" + WJ + "casa*" + WJ in body
    assert _delimitadores_ativos(body) == "**"


def test_pergunta_de_valor_com_underscore_no_nome(monkeypatch):
    """wa_runtime:804 — conta de valor variável chamada `conta_de_luz`."""
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
    assert "conta_" + WJ + "de_" + WJ + "luz" in body
    # o template tem 4 asteriscos ativos: *{nome}* e *132,50*
    assert _delimitadores_ativos(body) == "****"


# ─── core/handlers/pending.py:89 — a mensagem CRUA do usuário ───────────────


def test_pendencia_legada_com_crase_na_mensagem_crua(monkeypatch):
    """O pior caso: `text` é a mensagem que o usuário digitou, inteira, e o
    template usa crase.

    Medido na `main`: com o texto ``paguei a `luz` ``, o
    `format_for_platform` (que remove crase no WhatsApp) pareia errado e sobram
    DUAS crases soltas na tela — `Tente: paguei a luz``.
    """
    pend = {"action_type": "confirm_media_launch",
            "payload": {"text": "paguei a `luz`"}}
    monkeypatch.setattr(db, "get_pending_action", lambda uid: pend)
    monkeypatch.setattr(db, "consume_pending_action", lambda uid, p: True)
    monkeypatch.setattr("core.services.quick_entry.handle_quick_entry",
                        lambda uid, text: None)

    bruto = h_pending.resolve_delete(9, confirmed=True)
    saida = format_for_platform(bruto, "whatsapp")

    assert _delimitadores_ativos(saida) == ""


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
