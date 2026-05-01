import asyncio

from adapters.whatsapp import wa_app


def _payload(text: str = "gastei 50 mercado") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "contacts": [{"wa_id": "5511999999999"}],
                            "messages": [
                                {
                                    "id": "test-msg-001",
                                    "from": "5511999999999",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


def test_wa_simulate_simulation_only_nao_processa_payload(monkeypatch):
    monkeypatch.setenv("WA_SIMULATION_ONLY", "true")

    def fail_if_called(payload):
        raise AssertionError("process_payload nao deve ser chamado em WA_SIMULATION_ONLY")

    monkeypatch.setattr(wa_app, "process_payload", fail_if_called)

    result = asyncio.run(wa_app.wa_simulate(_payload()))

    assert result["ok"] is True
    assert result["simulation_only"] is True
    assert result["processed_messages"] == 0
    assert result["would_process_messages"] == 1
    assert result["messages"][0]["wa_id"] == "5511999999999"
    assert result["messages"][0]["text"] == "gastei 50 mercado"


def test_wa_simulate_sem_simulation_only_processa_payload(monkeypatch):
    monkeypatch.delenv("WA_SIMULATION_ONLY", raising=False)

    def fake_process_payload(payload):
        return 1

    monkeypatch.setattr(wa_app, "process_payload", fake_process_payload)

    result = asyncio.run(wa_app.wa_simulate(_payload()))

    assert result == {"ok": True, "simulation_only": False, "processed_messages": 1}
