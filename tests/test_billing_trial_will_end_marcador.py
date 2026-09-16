"""
tests/test_billing_trial_will_end_marcador.py — o marcador `trial_ending_email_sent`
só é gravado quando o e-mail de fim de trial SAIU (#441).

O ramo `customer.subscription.trial_will_end` de `POST /billing/webhook` gravava
`trial_ending_email_sent` INCONDICIONALMENTE depois de chamar `_fire_email`. Mas
`_fire_email` tem três saídas sem e-mail (dedupe interna, conta sem e-mail,
remetente devolvendo False — Resend fora do ar), e o marcador de fora é
justamente o que o `engagement_scheduler` consulta antes de mandar o fallback:
gravá-lo sem e-mail calava o scheduler por 6 dias e o usuário ficava SEM aviso.

Agora `_fire_email` devolve True só na linha que grava a chave interna, e o ramo
grava o marcador de fora dentro de `if await _fire_email(...)`.

CONTROLE NEGATIVO DECLARADO — em `frontend/finance_bot_websocket_custom.py`,
voltar o ramo `trial_will_end` ao `log_system_event` incondicional (tirar o `if`):
    VERMELHO: test_marcador_nao_gravado_quando_o_envio_falha[send_devolve_false]
              test_marcador_nao_gravado_quando_o_envio_falha[sem_email]

CONTROLE POSITIVO: `test_trial_will_end_reentregue_nao_manda_segundo_email`
(`tests/test_billing_webhook_event_loop.py`) — envio OK → marcador gravado. Se
o `return True` do `_fire_email` for esquecido, ele cai. Não duplicado aqui.

Vive em arquivo próprio porque `test_billing_webhook_event_loop.py` está perto
do teto de `tests/test_max_lines_python.py`.
"""
from __future__ import annotations

import pytest

import db
from _billing_grants_helpers import garantir_system_event_logs
from test_billing_webhook_event_loop import _trial_will_end
from test_billing_webhook_lifecycle import _cleanup_trial, _post, _setup


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _espiao(chamadas: list, resultado: bool):
    """`__name__` preservado: é a chave da dedupe interna do `_fire_email`."""
    def _send(*a, **kw):
        chamadas.append(a)
        return resultado
    _send.__name__ = "send_trial_ending_email"
    return _send


@pytest.mark.parametrize("modo", ["send_devolve_false", "sem_email"])
def test_marcador_nao_gravado_quando_o_envio_falha(user_id, monkeypatch, modo):
    """Envio que não sai (remetente devolve False / conta sem e-mail) → NENHUM
    dos dois marcadores gravado; no tick seguinte, com o envio OK, o e-mail sai
    e o marcador passa a existir — lido pelo MESMO predicado que o scheduler usa.
    """
    from core.observability import recent_event_exists
    from core.services import email_service

    chamadas: list = []
    monkeypatch.setattr(email_service, "send_trial_ending_email",
                        _espiao(chamadas, False))
    # `_user_email` faz `from db import get_auth_user` a cada chamada, então o
    # atributo do pacote é o que vale na hora do POST.
    conta = {"email": ""}
    if modo == "sem_email":
        monkeypatch.setattr(db, "get_auth_user", lambda uid: conta)
    esperado_1 = 1 if modo == "send_devolve_false" else 0

    uid, client, fake = _setup(monkeypatch, f"twm-{modo[:4]}-{user_id}")
    try:
        r = _post(client, fake, _trial_will_end(uid))
        assert r.status_code == 200, r.text
        assert len(chamadas) == esperado_1, chamadas
        assert recent_event_exists("trial_ending_email_sent", uid, 6) is False, (
            "marcador de fora gravado SEM e-mail — o scheduler ficaria mudo 6 dias")
        assert recent_event_exists("send_trial_ending_email_sent", uid, 1) is False, (
            "chave interna gravada sem envio confirmado")

        # Tick seguinte: envio volta a funcionar → o e-mail sai e o marcador entra.
        monkeypatch.setattr(email_service, "send_trial_ending_email",
                            _espiao(chamadas, True))
        conta["email"] = f"twm-{uid}@t.local"
        r = _post(client, fake, _trial_will_end(uid))
        assert r.status_code == 200, r.text
        assert len(chamadas) == esperado_1 + 1, chamadas
        assert recent_event_exists("trial_ending_email_sent", uid, 6) is True
    finally:
        _cleanup_trial(uid)
