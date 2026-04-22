from adapters.whatsapp.wa_parse import extract_messages


def test_extract_text_message():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.123",
                                    "from": "5511999999999",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": "gastei 10 cafe"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    messages = extract_messages(payload)

    assert len(messages) == 1
    assert messages[0].wa_id == "5511999999999"
    assert messages[0].text == "gastei 10 cafe"
    assert messages[0].attachments == []


def test_extract_document_message():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.456",
                                    "from": "5511888888888",
                                    "timestamp": "1710000001",
                                    "type": "document",
                                    "document": {
                                        "id": "media-1",
                                        "filename": "extrato.ofx",
                                        "mime_type": "application/octet-stream",
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    messages = extract_messages(payload)

    assert len(messages) == 1
    assert messages[0].wa_id == "5511888888888"
    assert len(messages[0].attachments) == 1
    assert messages[0].attachments[0].media_id == "media-1"
    assert messages[0].attachments[0].filename == "extrato.ofx"
