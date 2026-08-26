from adapters.whatsapp.wa_parse import InboundMessage
from adapters.whatsapp.wa_runtime import _pending_supports_confirmation_buttons, process_message
from core.types import OutgoingMessage


def test_delete_pending_uses_confirmation_buttons():
    pending = {"action_type": "delete_launch", "payload": {"launch_id": 42}}

    assert _pending_supports_confirmation_buttons(pending) is True


def test_credit_set_primary_choose_does_not_use_confirmation_buttons():
    pending = {"action_type": "credit_card_set_primary", "payload": {"step": "choose"}}

    assert _pending_supports_confirmation_buttons(pending) is False


def test_credit_set_primary_confirmation_uses_buttons():
    pending = {"action_type": "credit_card_set_primary", "payload": {"card_id": 7}}

    assert _pending_supports_confirmation_buttons(pending) is True


def test_credit_setup_binary_steps_use_buttons():
    pending = {"action_type": "credit_card_setup", "payload": {"step": "reminder_opt_in"}}

    assert _pending_supports_confirmation_buttons(pending) is True


def test_credit_limit_question_stays_as_text_input():
    pending = {"action_type": "credit_card_setup", "payload": {"step": "credit_limit_ask"}}

    assert _pending_supports_confirmation_buttons(pending) is False


def test_credit_purchase_pending_renders_delete_button(monkeypatch):
    """Confirmação de compra no crédito anexa o botão "🗑️ Apagar" (id del_cc:<tx>)
    e consome o pending na 1ª renderização (one-shot)."""
    import adapters.whatsapp.wa_runtime as wr

    sent: list[dict] = []
    cleared: list[int] = []
    monkeypatch.setattr(
        wr, "get_pending_action",
        lambda uid: {"action_type": "delete_credit_purchase", "payload": {"tx_id": 42}},
    )
    monkeypatch.setattr(
        wr, "consume_pending_action",
        lambda uid, pending: cleared.append(uid) or True,
    )
    monkeypatch.setattr(wr, "send_interactive_buttons", lambda **kw: sent.append(kw))

    wr._send_reply_with_optional_buttons("5511999998888", "✅ Compra registrada! CC42", user_id=7)

    assert cleared == [7]
    assert len(sent) == 1
    assert sent[0]["buttons"] == [{"id": "del_cc:42", "title": "🗑️ Apagar"}]


def test_autolink_com_comando_nao_interrompe_para_boas_vindas(monkeypatch):
    replies = []
    handled = []

    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.get_or_create_canonical_user",
        lambda provider, external_id: 111,
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.attempt_whatsapp_phone_link",
        lambda wa_id, current_user_id=None: {"status": "linked", "user_id": 222, "wa_phone": wa_id},
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.send_welcome",
        lambda wa_id: (_ for _ in ()).throw(AssertionError("nao deveria enviar boas-vindas")),
    )
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._seen_recent", lambda message_id: False)
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime._send_reply_with_optional_buttons",
        lambda to, body, user_id=None: replies.append((to, body, user_id)),
    )

    def fake_handle_incoming(msg, **_kw):   # o runtime pode passar `ignora_pendencias`
        handled.append(msg)
        return [OutgoingMessage(text=f"uid={msg.user_id} text={msg.text}")]

    monkeypatch.setattr("adapters.whatsapp.wa_runtime.handle_incoming", fake_handle_incoming)

    process_message(
        InboundMessage(
            wa_id="5565992741873",
            text="saldo",
            timestamp="123",
            attachments=[],
            raw={"id": "wamid.test", "type": "text"},
        )
    )

    assert len(handled) == 1
    assert handled[0].user_id == 222
    assert handled[0].text == "saldo"
    assert replies == [("5565992741873", "uid=222 text=saldo", 222)]


def test_numero_sem_conta_comando_bloqueia_e_convida_cadastro(monkeypatch):
    """Número sem conta que manda um COMANDO (não saudação, não código) recebe o
    convite de cadastro e NÃO cai no fluxo principal — sem isso o dado iria pra
    uma conta fantasma invisível (ou pra mensagem de paywall que assume conta)."""
    replies = []

    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.get_or_create_canonical_user",
        lambda provider, external_id: 111,
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.attempt_whatsapp_phone_link",
        lambda wa_id, current_user_id=None: {"status": "no_match", "wa_phone": wa_id},
    )
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._autolink_warning_already_sent", lambda *a, **k: True)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._seen_recent", lambda message_id: False)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._send_reply", lambda to, body: replies.append((to, body)))
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.handle_incoming",
        lambda msg: (_ for _ in ()).throw(AssertionError("não deveria chamar o fluxo principal")),
    )

    process_message(
        InboundMessage(
            wa_id="5511988887777",
            text="gastei 50 no mercado",
            timestamp="123",
            attachments=[],
            raw={"id": "wamid.no-account", "type": "text"},
        )
    )

    assert len(replies) == 1
    to, body = replies[0]
    assert to == "5511988887777"
    assert "não tenho uma conta" in body.lower()
    assert "/cadastro" in body


def test_numero_sem_conta_com_codigo_de_vinculo_passa_pro_handler(monkeypatch):
    """"link 123456" de um número sem conta NÃO pode ser barrado — é justamente
    como quem se cadastrou sem telefone vincula o WhatsApp. Deve seguir pro
    handle_incoming pra o código ser consumido."""
    replies = []
    handled = []

    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.get_or_create_canonical_user",
        lambda provider, external_id: 111,
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.attempt_whatsapp_phone_link",
        lambda wa_id, current_user_id=None: {"status": "no_match", "wa_phone": wa_id},
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime._send_no_account_notice",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria bloquear um código de vínculo")),
    )
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._seen_recent", lambda message_id: False)
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime._send_reply_with_optional_buttons",
        lambda to, body, user_id=None: replies.append((to, body, user_id)),
    )

    def fake_handle_incoming(msg, **_kw):   # o runtime pode passar `ignora_pendencias`
        handled.append(msg)
        return [OutgoingMessage(text="✅ WhatsApp vinculado!")]

    monkeypatch.setattr("adapters.whatsapp.wa_runtime.handle_incoming", fake_handle_incoming)

    process_message(
        InboundMessage(
            wa_id="5511988887777",
            text="link 123456",
            timestamp="123",
            attachments=[],
            raw={"id": "wamid.link-code", "type": "text"},
        )
    )

    assert len(handled) == 1
    assert handled[0].text == "link 123456"
    assert replies == [("5511988887777", "✅ WhatsApp vinculado!", 111)]


def test_is_link_code_attempt_espelha_normalizacao_do_classificador():
    """Formas que o intent_classifier normaliza e roteia como vínculo têm que
    passar pela exceção — pontuação e caixa não podem barrar o código."""
    from adapters.whatsapp.wa_runtime import _is_link_code_attempt

    for ok in ("link 123456", "vincular 123456", "link: 123456",
               "link 123456.", "LINK 123456", "  vincular   123456  ", "Vincular: 123456"):
        assert _is_link_code_attempt(ok) is True, ok

    for no in ("gastei 50 no mercado", "link", "vincular", "link 12345",
               "linkar 123456", "link 123456 agora", "oi"):
        assert _is_link_code_attempt(no) is False, no


def test_botao_parar_atualizacoes_desliga_updates_do_whatsapp(monkeypatch):
    replies = []
    updates = []

    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.get_or_create_canonical_user",
        lambda provider, external_id: 111,
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.attempt_whatsapp_phone_link",
        lambda wa_id, current_user_id=None: {"status": "linked", "user_id": 222, "wa_phone": wa_id},
    )
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime.send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._seen_recent", lambda message_id: False)
    monkeypatch.setattr("adapters.whatsapp.wa_runtime._send_reply", lambda to, body: replies.append((to, body)))
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.set_whatsapp_updates_opt_out",
        lambda user_id, opt_out: updates.append((user_id, opt_out)),
    )
    monkeypatch.setattr(
        "adapters.whatsapp.wa_runtime.handle_incoming",
        lambda msg: (_ for _ in ()).throw(AssertionError("não deveria chamar o fluxo principal")),
    )

    process_message(
        InboundMessage(
            wa_id="5565992741873",
            text="Parar atualizações",
            timestamp="123",
            attachments=[],
            raw={
                "id": "wamid.stop-updates",
                "type": "button",
                "button": {"text": "Parar atualizações"},
            },
        )
    )

    assert updates == [(222, True)]
    assert replies == [
        (
            "5565992741873",
            "Pronto, parei as atualizações do Piggy por aqui. Você pode religar quando quiser em Configurações > Notificações.",
        )
    ]
