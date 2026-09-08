"""Helpers compartilhados dos testes do gate de plano do bot.

Sem prefixo `test_` de propósito: o pytest não coleta este arquivo. Ele existe
porque o assunto virou dois arquivos (o VEREDITO do gate e as ISENÇÕES dele) e
duas cópias de `_diga`/`_barrado` seriam dois testes medindo coisas diferentes
achando que medem a mesma — mesmo motivo do `tests/_billing_grants_helpers.py`.
"""
from __future__ import annotations

import uuid

import pytest

import db
import core.handle_incoming as hi
from core.types import IncomingMessage


@pytest.fixture(autouse=True)
def v2_ligado(monkeypatch):
    """O conftest roda a suíte com PLANS_V2_ENABLED=0. Sem este setenv,
    needs_plan_selection devolve False sempre e os dois arquivos são teatro.

    Importado por cada arquivo de teste do assunto (o pytest só aplica a fixture
    que está no namespace do módulo)."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("PAYWALL_ENABLED", "0")  # a perna legada fica fora do teste


def cadastro_novo() -> int:
    """Conta recém-criada pela web: plan_selected_at NULL → gate fechado.

    O user_id canônico é <= 2e9 (db/users.get_or_create_canonical_user), então
    `_normalize_user_id` não comprime e o uid do teste é o uid do handler."""
    user = db.register_auth_user(f"gatebot-{uuid.uuid4().hex[:12]}@t.com", "senha-forte-123")
    return int(user["user_id"])


def diga(uid: int, texto: str, plataforma: str = "whatsapp", anexos=None) -> str:
    """Uma mensagem pelo `handle_incoming` — nunca pelo `_paywall_gate` isolado
    (§3 do CLAUDE.md: rode a conversa, não a função)."""
    out = hi.handle_incoming(IncomingMessage(
        platform=plataforma, user_id=uid, text=texto,
        message_id=uuid.uuid4().hex, attachments=anexos or [], external_id="", raw={},
    ))
    return "\n".join(m.text for m in out)


def barrado(resposta: str) -> bool:
    return "sua conta precisa estar ativa" in resposta.lower()
