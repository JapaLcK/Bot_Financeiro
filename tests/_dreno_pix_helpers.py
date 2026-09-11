"""Helpers compartilhados dos testes do dreno do Pix (1b-B).

Sem prefixo `test_` de propósito — o pytest não coleta este arquivo. Ele existe
pelo mesmo motivo de `tests/_billing_grants_helpers.py`: quatro arquivos de
teste do mesmo assunto não podem carregar quatro cópias de `entregar`, senão um
dia dois deles medem coisas diferentes achando que medem a mesma.

**Estado REAL no banco, nunca `db` mockado.** O que se testa aqui é a máquina de
estados de dinheiro, e a classe de bug que mais aparece neste repositório é a do
estado que outro fluxo deixou no banco (CLAUDE.md §3). O que é mockado é só o
MUNDO LÁ FORA — GA4, Meta, e-mail, alerta —, porque é ele que se conta.
"""
from __future__ import annotations

import itertools
import secrets
import uuid

from db.connection import get_conn
from db.pix_charges import criar_cobranca
from db.pix_charges_saga import attach_pagamento

# `event_version` estritamente crescente por processo: `upsert_grant` e
# `revoke_grant` comparam versão, e dois eventos com a mesma marca d'água fariam
# o segundo ser descartado por ordem, não pela regra em teste.
_versao = itertools.count(1_700_000_000)


def nova_cobranca(user_id: int, **kw) -> dict:
    """Cobrança em `pending` com id remoto — o estado de onde o `RECEIVED` casa.

    `draft` não serve de ponto de partida: nenhuma célula do §11 concede a
    partir dele, e um teste que partisse de `draft` ficaria verde por não
    transicionar nunca.
    """
    campos = dict(public_token=secrets.token_urlsafe(16), plan="pro_max",
                  plan_stored="pro_max", price_cents=49900, credit_cents=0,
                  amount_cents=49900, duration_days=365)
    campos.update(kw)
    linha = criar_cobranca(user_id, **campos)
    assert linha, "o usuário já tinha cobrança ativa — fixture inválida"
    pagamento = f"pay_{uuid.uuid4().hex[:12]}"
    attach_pagamento(linha["id"], pagamento)
    linha["asaas_payment_id"] = pagamento
    linha["status"] = "pending"
    return linha


def entregar(tipo: str, cobranca: dict, *, payment_id: str | None = None,
             referencia: str | None = None, valor: float | None = None,
             tentativas: int = 0) -> str:
    """Grava o evento na outbox e DRENA — o caminho inteiro, não o handler solto.

    Devolve o `event_id`, que é o que os testes consultam para conferir
    `processed_at`, `attempts` e `last_error`.

    `tentativas` carimba `attempts` ANTES da drenagem: é como se testa o que só
    acontece depois de N falhas (o fallback do `stripe_cancel`, §8.2) sem drenar
    o mesmo evento seis vezes — cada `entregar` cria um `event_id` novo, então
    repetir a chamada mediria seis eventos com `attempts` zero.
    """
    from core.services.pix_drain import drenar_evento
    from db.webhook_outbox import registrar_evento

    event_id = f"evt_{uuid.uuid4().hex[:16]}"
    corpo = {
        "id": event_id,
        "event": tipo,
        "payment": {
            "id": payment_id if payment_id is not None else cobranca["asaas_payment_id"],
            "externalReference": (referencia if referencia is not None
                                  else cobranca["external_reference"]),
            "value": valor if valor is not None else cobranca["amount_cents"] / 100,
            "status": tipo.replace("PAYMENT_", ""),
        },
    }
    registrar_evento(event_id, tipo, corpo, next(_versao))
    if tentativas:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("update pix_webhook_events set attempts = %s"
                        " where event_id = %s", (int(tentativas), event_id))
            conn.commit()
    drenar_evento(event_id)
    return event_id


def ler(charge_id: int) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from pix_charges where id = %s", (int(charge_id),))
        return dict(cur.fetchone())


def evento(event_id: str) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from pix_webhook_events where event_id = %s",
                    (event_id,))
        return dict(cur.fetchone())


def efeitos(payment_id: str) -> set[str]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select effect from pix_payment_effects"
                    " where asaas_payment_id = %s", (payment_id,))
        return {r["effect"] for r in cur.fetchall()}


def mundo_externo(monkeypatch) -> dict:
    """Substitui GA4, Meta, e-mail e alerta por CONTADORES.

    Devolve o dicionário de contagens. Sem isto, os efeitos `ga4`/`capi` saem
    pelo ramo "não configurado" e o teste que afirma "um `send_purchase`" mede
    zero em qualquer versão do código — a tautologia clássica.
    """
    contas = {"ga4": 0, "capi": 0, "email": 0, "alerta": [], "grant": 0,
              # Os kwargs de CADA `send_purchase`, na ordem. Contar chamadas e
              # jogar o conteúdo fora deixa cego para o que VAI no evento — e o
              # `plan` do GA4 é `item_id`/`item_name`, a linha de receita.
              "ga4_kw": [],
              # `send_email` NUNCA levanta: Resend fora do ar e Resend sem chave
              # saem os dois por `return False`. O falso devolve o mesmo `bool`
              # que a produção, senão o efeito `email` seria medido contra uma
              # função que não existe. `email_ok=False` liga o caminho da falha.
              "email_ok": True}

    import core.services.admin_notify as an
    import core.services.email_service as es
    import core.services.ga4_mp as ga4
    import core.services.meta_capi as capi

    def _email(*a, **kw):
        contas["email"] += 1
        return contas["email_ok"]

    def _purchase(**kw):
        contas["ga4"] += 1
        contas["ga4_kw"].append(kw)
        return True

    monkeypatch.setattr(ga4, "mp_configured", lambda: True)
    monkeypatch.setattr(ga4, "send_purchase", _purchase)
    monkeypatch.setattr(capi, "capi_configured", lambda: True)
    monkeypatch.setattr(capi, "send_event",
                        lambda **kw: contas.__setitem__("capi", contas["capi"] + 1))
    monkeypatch.setattr(es, "send_pix_paid_email", _email)
    monkeypatch.setattr(an, "notify_pix_alerta",
                        lambda msg: contas["alerta"].append(msg) or True)
    return contas
