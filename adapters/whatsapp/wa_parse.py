def extract_incoming(payload: dict):
    """
    Retorna (from_phone, text, message_id) ou (None, None, None)
    """
    try:
        entry = payload.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        if not messages:
            return None, None, None

        msg = messages[0]
        from_phone = msg.get("from")  # telefone do usuário
        msg_id = msg.get("id")

        # texto
        txt = None
        if msg.get("type") == "text":
            txt = msg.get("text", {}).get("body", "")

        return from_phone, (txt or "").strip(), msg_id
    except Exception:
        return None, None, None