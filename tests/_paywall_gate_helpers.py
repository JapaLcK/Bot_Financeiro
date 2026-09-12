"""Helpers compartilhados dos testes do gate de plano do bot.

Sem prefixo `test_` de propósito: o pytest não coleta este arquivo. Ele existe
porque o assunto virou dois arquivos (o VEREDITO do gate e as ISENÇÕES dele) e
duas cópias de `_diga`/`_barrado` seriam dois testes medindo coisas diferentes
achando que medem a mesma — mesmo motivo do `tests/_billing_grants_helpers.py`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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


def com_plano() -> int:
    """Conta PAGANTE: passou pela /precos E tem plano vigente. O gate deixa
    passar. É o controle positivo dos três arquivos: sem ele, um gate que
    recusa TODO MUNDO passaria verde.

    O `mark_plan_selected` sozinho NÃO basta mais. Ele fecha só a perna do
    `needs_plan_selection`; desde o corte do Grátis a outra perna do gate é
    `has_app_access`, e ela pergunta pelo DIREITO (`plan` + `plan_expires_at`).
    Uma conta "que escolheu plano" e ficou no Grátis é exatamente quem o corte
    barra — usá-la como positivo mediria o gate contra si mesmo.
    """
    uid = cadastro_novo()
    db.mark_plan_selected(uid)
    db.update_user_plan(uid, "pro", datetime.now(timezone.utc) + timedelta(days=30))
    return uid


def so_whatsapp() -> int:
    """A população só-WhatsApp: usa o bot e NUNCA fez cadastro web, logo não tem
    linha em `auth_accounts`. O `core/handle_incoming` a chama de "a maioria
    aqui". Desde o corte ela é barrada (decisão do dono), e a mensagem do gate é
    a única comunicação que ela recebe."""
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    return uid


def diga(uid: int, texto: str, plataforma: str = "whatsapp", anexos=None) -> str:
    """Uma mensagem pelo `handle_incoming` — nunca pelo `_paywall_gate` isolado
    (§3 do CLAUDE.md: rode a conversa, não a função)."""
    out = hi.handle_incoming(IncomingMessage(
        platform=plataforma, user_id=uid, text=texto,
        message_id=uuid.uuid4().hex, attachments=anexos or [], external_id="", raw={},
    ))
    return "\n".join(m.text for m in out)


# A LINHA DO LINK, e não uma frase da copy: as duas formas da mensagem do gate
# (só-WhatsApp × ex-assinante) dizem coisas diferentes de propósito, e a única
# coisa que as duas têm — e que nenhuma outra resposta do bot tem — é este
# convite. `_handle_assinar` também manda para a /precos, mas por link
# autenticado (`/auth/dashboard-token?...`) e sem o 👉.
_MARCA_DO_GATE = "👉 https://pigbankai.com/precos"


def barrado(resposta: str) -> bool:
    return _MARCA_DO_GATE in resposta
