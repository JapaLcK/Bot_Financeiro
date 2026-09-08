"""POST /billing/select-free recusa: o Grátis morreu como porta de entrada.

Regra de produto: quem se cadastra pela web tem de ASSINAR pra entrar. A rota
continua existindo só pra recusar (apagá-la devolveria 404/405 em HTML a um
cliente antigo em cache, e o front lê `detail.message`), e nenhum caminho do
frontend a chama mais — o `[data-free-cta]`/`selectFree` saíram da precos.html
no mesmo commit (`tests/frontend/precos_sem_plano_gratis.test.mjs`).

O que discrimina o conserto é a 3ª asserção: o gate CONTINUA fechado depois da
chamada. Com a guarda removida, o corpo antigo chamava `mark_plan_selected` e
`needs_plan_selection` virava False — que é exatamente o buraco que se fechou.
O `test_controle_positivo_*` é o par dela: prova que `needs_plan_selection` não
é sempre True (senão a asserção passaria num sistema onde ninguém entra nunca).
"""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import core.services.plan_service as plan_service
import frontend.finance_bot_websocket_custom as dashboard

_CSRF_TOKEN = "test-csrf-token-select-free"
_CSRF_HEADERS = {dashboard.CSRF_HEADER_NAME: _CSRF_TOKEN}


@pytest.fixture(autouse=True)
def _v2_e_rate_limit(monkeypatch):
    """v2 ligado (o gate de escolha só existe nele) e limiter zerado por teste —
    /billing/select-free tem 20/hora com storage em memória compartilhado."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass


def _cadastro_novo(sufixo: str):
    """Usuário recém-criado: plan_selected_at NULL → needs_plan_selection True."""
    email = f"selectfree-{sufixo}@t.com"
    user = db.register_auth_user(email, "senha-forte-123")
    user_id = int(user["user_id"])
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_TOKEN)
    return user_id, client


def test_select_free_recusa_e_nao_fecha_o_gate():
    user_id, client = _cadastro_novo("recusa")
    assert plan_service.needs_plan_selection(user_id) is True, "pré-condição"

    r = client.post("/billing/select-free", headers=_CSRF_HEADERS)

    assert 400 <= r.status_code < 500, f"devolveu {r.status_code}, esperava 4xx"
    corpo = r.json()
    # Mesmo formato das outras rotas de billing: detail.error + detail.message.
    assert corpo["detail"]["error"] == "free_plan_discontinued"
    assert corpo["detail"]["message"], "sem mensagem legível pro usuário"
    # A que discrimina: o gate NÃO fechou. É o que vira False se a guarda sair.
    assert plan_service.needs_plan_selection(user_id) is True, \
        "a rota fechou o gate — a guarda não está valendo"


def test_controle_positivo_o_gate_ainda_fecha_pelo_caminho_do_checkout():
    """Par positivo: `mark_plan_selected` (o que o webhook
    checkout.session.completed chama) continua liberando o usuário.

    Sem este caso, a asserção de cima passaria num sistema onde
    needs_plan_selection é True para sempre e ninguém entra nunca.

    Chama a função de banco direto de propósito — o que este caso prova é só
    que `needs_plan_selection` sabe virar False. Quem prova que a ÚNICA saída
    do funil funciona pelo caminho real (POST /billing/webhook) é o
    `test_checkout_completed_fecha_o_gate_de_escolha`, em
    tests/test_billing_webhook_lifecycle.py.
    """
    user_id, _ = _cadastro_novo("positivo")
    assert plan_service.needs_plan_selection(user_id) is True

    db.mark_plan_selected(user_id)

    assert plan_service.needs_plan_selection(user_id) is False
